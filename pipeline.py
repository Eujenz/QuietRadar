import os
import sys
import json
import time
import hashlib
import logging
import re
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import yaml
import httpx
import feedparser
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

# 確保 Windows 控制台輸出支援 UTF-8 (Emoji 與繁體中文)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 載入 .env 環境變數
load_dotenv()

# 設定日誌輸出
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("QuietRadar")

# ==========================================
# 1. 資料庫模組 (SQLite 本機防重)
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./quietradar.db")
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class ProcessedArticle(Base):
    __tablename__ = "processed_articles"

    id = Column(Integer, primary_key=True, index=True)
    sha256_hash = Column(String(64), unique=True, index=True, nullable=False)
    title = Column(String(512))
    url = Column(String(1024))
    user_id = Column(String(64), default="default")
    created_at = Column(DateTime, default=datetime.now)

Base.metadata.create_all(bind=engine)

from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

# ==========================================
# 0. URL 正規化與追蹤參數抗噪模組 (借鏡 TrendRadar)
# ==========================================
COMMON_TRACKING_PARAMS = {
    # UTM 行銷追蹤參數
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    # 常見推薦與管道參數
    "ref", "referrer", "source", "channel", "spm", "from",
    # 時間戳記與隨機快取擊穿參數
    "_t", "timestamp", "_", "random", "t",
    # 社群分享參數
    "share_token", "share_id", "share_from",
}

def normalize_url(url: str) -> str:
    """
    標準化 URL，去除行銷追蹤參數、時間戳記與 #fragment，並按字母序重排參數。
    確保 SQLite 防重指紋 100% 穩定，避免因 URL 動態帶參導致重複研讀。
    """
    if not url:
        return url
    try:
        parsed = urlparse(url.strip())
        if not parsed.query:
            # 移除 fragment
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", ""))

        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {
            k: v for k, v in params.items()
            if k.lower() not in COMMON_TRACKING_PARAMS and not k.lower().startswith("utm_")
        }

        if not filtered:
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", ""))

        sorted_params = []
        for k in sorted(filtered.keys()):
            for v in filtered[k]:
                sorted_params.append((k, v))

        new_query = urlencode(sorted_params)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, ""))
    except Exception:
        return url.strip()

def is_article_processed(session, sha256_hash: str) -> bool:
    return session.query(ProcessedArticle).filter(ProcessedArticle.sha256_hash == sha256_hash).first() is not None

def record_processed_articles(session, articles: List[Dict[str, Any]]):
    for a in articles:
        if not is_article_processed(session, a["sha256"]):
            norm_url = normalize_url(a.get("url", ""))
            record = ProcessedArticle(
                sha256_hash=a["sha256"],
                title=a["title"][:500],
                url=norm_url[:1000],
                user_id="default",
                created_at=datetime.now()
            )
            session.add(record)
    session.commit()

def extract_keywords_from_list(items: List[str]) -> List[str]:
    kws = set()
    for item in items:
        parts = re.split(r'[（）()、，,／/\s+、。\-—:：]+', item)
        for p in parts:
            p = p.strip()
            if len(p) >= 2:
                kws.add(p.lower())
    return list(kws)

def fetch_sources(sources_config: List[Dict[str, Any]], profile: Optional[Dict[str, Any]] = None, pipeline_settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    raw_articles = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuietRadar/1.0"}
    
    settings = pipeline_settings or {}
    filter_enabled = settings.get("title_filter_enabled", True)
    filter_mode = settings.get("title_filter_mode", "smart")

    prof = profile or {}
    negative_kws = extract_keywords_from_list(prof.get("negative_topics", [])) if filter_enabled else []
    interest_kws = extract_keywords_from_list(prof.get("interests", [])) if filter_enabled else []

    total_scanned = 0
    dropped_negative = 0
    dropped_strict = 0
    
    for src in sources_config:
        if not src.get("enabled", True):
            continue
        name = src["name"]
        url = src["url"]
        logger.info(f"📡 正在抓取來源: [{name}] ({url})")
        
        try:
            urls_to_try = [url]
            if url.startswith("http://"):
                urls_to_try.append(url.replace("http://", "https://", 1))

            resp_content = None
            for target_url in urls_to_try:
                try:
                    with httpx.Client(headers=headers, timeout=15.0, follow_redirects=True, verify=False) as client:
                        resp = client.get(target_url)
                        if resp.status_code == 200:
                            resp_content = resp.content
                            if target_url != url:
                                logger.info(f"🔄 來源 [{name}] 自動由 HTTP 升級至 HTTPS 抓取成功！")
                            break
                        else:
                            logger.warning(f"⚠️ 來源 [{name}] ({target_url}) 回應狀態碼異常: {resp.status_code}")
                except Exception as err:
                    logger.warning(f"⚠️ 來源 [{name}] ({target_url}) 連線失敗: {err}")

            if not resp_content:
                logger.warning(f"⚠️ 來源 [{name}] 抓取失敗，跳過此來源")
                continue

            feed = feedparser.parse(resp_content)
            max_age_days = settings.get("max_article_age_days", 7)
                    
            for entry in feed.entries[:25]:  # 擴大候選池掃描範圍
                title = getattr(entry, "title", "").strip()
                raw_link = getattr(entry, "link", "").strip()
                
                # 1. 空標題與無效條目過濾（借鏡 TrendRadar 兜底機制）
                if not title or not raw_link or len(title) < 3:
                    continue
                
                # 2. 純廣告與贊助標籤前置剔除
                if any(ad_tag in title for ad_tag in ["[推廣]", "[廣告]", "[贊助]", "[AD]", "【廣告】", "【推廣】"]):
                    continue

                # 3. URL 規範化抗噪（借鏡 TrendRadar normalize_url）
                link = normalize_url(raw_link)

                # 4. 文章發布時效過期防護（借鏡 TrendRadar is_within_days）
                if max_age_days and max_age_days > 0:
                    pub_tuple = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
                    if pub_tuple:
                        try:
                            pub_time = time.mktime(pub_tuple)
                            age_days = (time.time() - pub_time) / 86400.0
                            if age_days > max_age_days:
                                # 超過時效上限，略過陳舊歷史文章
                                continue
                        except Exception:
                            pass
                
                total_scanned += 1
                title_lower = title.lower()

                # 若為跨界漫遊來源 (Serendipity)，特赦跳過一人公司關鍵詞與排斥詞漏斗，保留意外靈感
                is_serendipity = src.get("serendipity", False)
                relevance_score = 0

                if not is_serendipity:
                    # 【標題前置漏斗第一關】：負面黑名單硬性攔截（不讀取正文）
                    if filter_enabled and negative_kws:
                        hit_neg = [kw for kw in negative_kws if kw in title_lower]
                        if hit_neg:
                            logger.info(f"🚫 [標題漏斗攔截] 命中排斥詞「{hit_neg[0]}」：【{title[:35]}】-> 跳過不讀取正文")
                            dropped_negative += 1
                            continue

                    # 【標題前置漏斗第二關】：關注主題相關性審查
                    if filter_enabled and interest_kws:
                        matched_int = [kw for kw in interest_kws if kw in title_lower]
                        relevance_score = len(matched_int)

                        if filter_mode == "strict" and relevance_score == 0:
                            logger.info(f"⏭️ [標題漏斗過濾] 嚴格模式未命中關注關鍵詞：【{title[:35]}】-> 跳過不讀取正文")
                            dropped_strict += 1
                            continue
                else:
                    relevance_score = 5  # 跨界漫遊文章預設賦予基礎優先級

                # 通過標題檢驗後，才深入抓取並清洗正文（大幅節省網路與記憶體）
                raw_content = ""
                if hasattr(entry, "content") and entry.content:
                    raw_content = entry.content[0].get("value", "")
                if not raw_content:
                    raw_content = getattr(entry, "summary", "") or getattr(entry, "description", "")
                
                clean_text = re.sub(r'<[^>]+>', ' ', raw_content)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                
                h = hashlib.sha256(f"{link}|{title}".encode("utf-8")).hexdigest()
                raw_articles.append({
                    "source_name": name,
                    "title": title,
                    "url": link,
                    "summary": clean_text[:4000],
                    "sha256": h,
                    "relevance_score": relevance_score,
                    "is_serendipity": is_serendipity
                })
        except Exception as e:
            logger.warning(f"⚠️ 來源 [{name}] 抓取失敗: {e}，不影響其他來源")
            
    if filter_enabled and total_scanned > 0:
        logger.info(f"🌪️ 標題前置漏斗成效：共掃描 {total_scanned} 則標題，命中排斥詞攔截 {dropped_negative} 篇，嚴格過濾跳過 {dropped_strict} 篇，放行 {len(raw_articles)} 篇進入正文研讀池")

    # 若為 smart 模式，優先將命中關注主題分數高的文章排在前面
    if filter_mode == "smart":
        raw_articles.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    return raw_articles

# ==========================================
# 3. LLM 意圖對齊與降噪蒸餾模組
# ==========================================
class SimpleLLMDistiller:
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.base_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "minimax/minimax-m3:free")

    def distill(self, candidates: List[Dict[str, Any]], profile: Dict[str, Any], top_k: int = 7, custom_prompt: Optional[str] = None, snippet_length: int = 800) -> Dict[str, Any]:
        if not candidates:
            return {"overview": "", "items": []}
            
        if not self.api_key or self.api_key == "your_key_here":
            logger.error("❌ 未配置有效的 LLM_API_KEY，無法進行蒸餾")
            return {"overview": "", "items": []}
        
        # 100% 以 Prompt 模板庫為單一真理源（Single Source of Truth），徹底消除 Python 程式碼與模板的衝突
        if not custom_prompt or not custom_prompt.strip():
            fallback_template_path = os.path.join("prompts", "solopreneur.md")
            if os.path.exists(fallback_template_path):
                with open(fallback_template_path, "r", encoding="utf-8") as f:
                    base_instructions = f.read().strip()
            else:
                base_instructions = "你是一位具備資深技術洞察力與商業架構思維的繁體中文科技專欄主編。"
        else:
            base_instructions = custom_prompt.strip()

        # 計算槓鈴配額（7~8成核心 : 2~3成跨界）
        # 槓鈴挑選原則（動態比例：約 70~80% 核心 : 20~30% 跨界，質量優先）
        has_serendipity = any(c.get("is_serendipity") for c in candidates)
        if has_serendipity:
            quota_rule = f"""1. 【槓鈴策略精選原則 (約 7~8成核心 : 2~3成跨界，上限 {top_k} 則，質量優先)】:
   請深入研讀下方候選文章，挑選出真正具備商業啟發性、新奇且可行的情報（最多 {top_k} 則，寧缺毋濫；若本期值得探討的文章多，可挑選 8~{top_k} 則；若素材平庸則精煉挑選）。
   在精選出的情報項目中，請嚴格維持槓鈴比例：
   - 🎯 核心業務情報：約佔 70% ~ 80%
   - ✨ 跨界漫遊靈感：約佔 20% ~ 30%（必須從清單標題帶有 ✨ 的文章中挑選）
   嚴禁全部挑選核心文章而遺漏跨界靈感！"""
        else:
            quota_rule = f"1. 請精選出最具閱讀價值的候選文章（最多 {top_k} 則，質量優先），並完全依據上方守則撰寫獨立深度論述正文。"

        system_prompt = f"""{base_instructions}

---
【本期檢索動態偏好】:
- 讀者關注主題：{json.dumps(profile.get('interests', []), ensure_ascii=False)}
- 讀者排斥的主題 (直接淘汰)：{json.dumps(profile.get('negative_topics', []), ensure_ascii=False)}

---
【系統輸出格式契約 (JSON) 與論文式文內注釋鐵律】:
{quota_rule}
2. 【論文式文內注釋鐵律】：
   - 在 overview 論述正文中，凡提及、借鏡特定文章的商業案例、技術架構、實驗數據或跨界理論時，必須在該名詞或觀點句後方標註論文式可點擊角標連結：`[[編號]](文章真實URL)`。
   - 範例：借鏡騰訊混元 Hy4 preview 的成本壓制 [[1]](http://...)...
   - 範例：樹德收納「增工加料」的反脆弱定價法 [[6]](https://...)...
   - 引用編號（如 [[1]]、[[2]]）必須與你輸出 JSON 中的 items 項目編號 (1~N) 嚴格一對一對齊！
   - 當讀者閱讀觀點時，點擊上標即可直接跳轉進入全文深度閱讀。
3. 請嚴格輸出符合以下結構的合法 JSON，不要附加額外說明或 Markdown 代碼塊標籤：
{{
  "overview": "完全遵照上方模板規則撰寫的【今日觀點】獨立深度正文（單指正文 800~2,000 字，內文帶有 [[編號]](url) 論文式引用連結，不含文末引用清單）",
  "items": [
    {{
      "title": "精煉後的繁體中文標題（若為跨界文章請保留開頭的 ✨）",
      "original_url": "必須填寫候選文章中的真實 URL",
      "source_name": "來源名稱"
    }}
  ]
}}"""
        # 準備餵給 LLM 的文章候選清單（依據 snippet_length 提供充足內文讓模型深度研讀）
        articles_payload = [
            {
                "index": i + 1,
                "source": a["source_name"],
                "title": f"✨ {a['title']}" if a.get("is_serendipity") else a["title"],
                "url": a["url"],
                "is_serendipity": a.get("is_serendipity", False),
                "content_snippet": a["summary"][:snippet_length] if (snippet_length and snippet_length > 0) else a["summary"]
            }
            for i, a in enumerate(candidates)
        ]
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://github.com/Eujenz/QuietRadar",
            "X-Title": "QuietRadar"
        }

        # 支援高容錯免費模型備援鏈 (Primary -> Multi-tier Free Fallback)
        models_to_try = [self.model]
        if "openrouter" in self.base_url.lower():
            openrouter_free_backups = [
                "minimax/minimax-m3:free",
                "minimax/minimax-m2.7:free",
                "google/gemma-4-31b-it:free",
                "nvidia/nemotron-3.5-lightning:free"
            ]
            for b in openrouter_free_backups:
                if b not in models_to_try:
                    models_to_try.append(b)
        else:
            if "meta/llama-3.2-11b-vision-instruct" not in models_to_try:
                models_to_try.append("meta/llama-3.2-11b-vision-instruct")

        # 設定 180 秒寬裕逾時，避免冷啟動斷線
        client_timeout = httpx.Timeout(180.0, connect=30.0, read=180.0, write=30.0)

        for current_model in models_to_try:
            temperature = 0.2
            payload = {
                "model": current_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"以下是候選文章列表：\n{json.dumps(articles_payload, ensure_ascii=False)}"}
                ],
                "temperature": temperature,
                "max_tokens": 4096,
                "stream": True  # 啟用串流維持連線活躍
            }

            max_retries = 2
            backoff_base = 5

            for attempt in range(1, max_retries + 1):
                logger.info(f"🤖 正在呼叫 LLM [{current_model}] (串流模式，第 {attempt}/{max_retries} 次嘗試)...")
                try:
                    raw_text_chunks = []
                    with httpx.Client(timeout=client_timeout) as client:
                        with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=payload) as resp:
                            if resp.status_code in [429, 502, 503, 504]:
                                wait_sec = backoff_base * attempt
                                logger.warning(f"⚠️ 收到 HTTP {resp.status_code} 限速/逾時，觸發指數退避：等待 {wait_sec} 秒後重試...")
                                time.sleep(wait_sec)
                                continue
                            elif resp.status_code != 200:
                                logger.warning(f"⚠️ 模型 [{current_model}] 回應狀態碼異常: {resp.status_code}")
                                break

                            # 串流讀取 SSE Token
                            for line in resp.iter_lines():
                                if not line or not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                    delta = chunk["choices"][0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        raw_text_chunks.append(content)
                                except Exception:
                                    pass

                    raw_text = "".join(raw_text_chunks).strip()
                    if not raw_text:
                        logger.warning(f"⚠️ 模型 [{current_model}] 串流回傳為空，嘗試重試...")
                        continue

                    logger.info(f"📝 LLM [{current_model}] 串流接收完成，共 {len(raw_text)} 字元")
                    
                    cleaned = raw_text
                    match_obj = re.search(r'\{.*\}', cleaned, re.DOTALL)
                    match_arr = re.search(r'\[\s*\{.*\}\s*\]', cleaned, re.DOTALL)

                    try:
                        if match_obj:
                            data = json.loads(match_obj.group(0))
                            if isinstance(data, dict) and "items" in data:
                                return {"overview": data.get("overview", ""), "items": data["items"][:top_k]}
                    except Exception:
                        pass

                    try:
                        target_str = match_arr.group(0) if match_arr else cleaned
                        data = json.loads(target_str.strip())
                        if isinstance(data, list):
                            return {"overview": "", "items": data[:top_k]}
                        elif isinstance(data, dict):
                            return {"overview": data.get("overview", ""), "items": data.get("items", [])[:top_k]}
                    except Exception:
                        pass

                    # Fallback Markdown 提取器
                    extracted_items = []
                    blocks = re.split(r'\n(?=\d+\.\s+)', raw_text)
                    for b in blocks:
                        if not re.match(r'^\d+\.\s+', b.strip()):
                            continue
                        lines = [line.strip() for line in b.strip().split('\n') if line.strip()]
                        title_match = re.search(r'^\d+\.\s+\*?\*?(.*?)\*?\*?$', lines[0])
                        title = title_match.group(1).replace('**', '').strip() if title_match else lines[0]
                        
                        url = ""
                        source = "精選情報"
                        for l in lines[1:]:
                            if "http" in l:
                                m_url = re.search(r'https?://[^\s\)]+', l)
                                if m_url:
                                    url = m_url.group(0)
                            if "來源" in l or "source" in l.lower():
                                source = l.split(":")[-1].replace("【", "").replace("】", "").strip()

                        if not url:
                            for cand in candidates:
                                if cand["title"] in title or title in cand["title"]:
                                    url = cand["url"]
                                    source = cand["source_name"]
                                    break

                        extracted_items.append({
                            "title": title,
                            "original_url": url,
                            "source_name": source
                        })

                    if extracted_items:
                        logger.info(f"✅ 成功從 [{current_model}] 提取 {len(extracted_items)} 則精選情報")
                        return {"overview": "", "items": extracted_items[:top_k]}

                except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                    wait_sec = backoff_base * attempt
                    logger.warning(f"⚠️ 模型 [{current_model}] 網路/超時異常: {net_err}，等待 {wait_sec} 秒...")
                    time.sleep(wait_sec)
                except Exception as e:
                    logger.warning(f"⚠️ 模型 [{current_model}] 調用異常: {e}，切換下一個模型...")
                    break

        logger.error("❌ 所有備援模型與重試嘗試皆已耗盡。")
        return {"overview": "", "items": []}

# ==========================================
# 3.4 確定性繁中在地化與黑話洗滌字典
# ==========================================
BUZZWORD_REPLACEMENTS = [
    # 典型大陸黑話與空泛虛詞（轉換為大白話）
    (r"執行閉環", "完整交付工作流"),
    (r"商業閉環", "商業運作機制"),
    (r"形成閉環", "跑通完整流程"),
    (r"閉環", "工作流"),
    (r"全面賦能", "實質提升"),
    (r"賦能", "協助"),
    (r"抓手", "切入點"),
    (r"打法", "做法"),
    (r"顆粒度", "精細度"),
    (r"心智", "認知"),
    (r"下沉市場", "基層市場"),
    (r"下沉", "深入基層"),
    (r"沉澱", "累積"),
    (r"搓出一個", "做出一套"),
    (r"搓出", "做出一套"),
    (r"業務飛輪", "正向循環"),
    (r"飛輪", "正向循環"),
    (r"載體", "工具"),
    (r"組合拳", "多元策略"),
    (r"背書", "保證"),
    (r"調優", "調校"),
    (r"對標", "參考"),
    (r"拉齊", "同步"),
    (r"複盤", "檢討"),
    (r"佈局", "規劃"),
    (r"賽道", "領域"),
    (r"痛點", "實質困擾"),

    # 大陸日常與科技用語 ➔ 台灣在地用語
    (r"互聯網", "網路"),
    (r"群聊", "群組"),
    (r"批註", "註記"),
    (r"運營", "營運"),
    (r"行業", "產業"),
    (r"搭建", "建構"),
    (r"緩存", "快取"),
    (r"算法", "演算法"),
    (r"服務器", "伺服器"),
    (r"項目", "專案"),
    (r"用戶", "使用者"),
    (r"信息", "資訊"),
    (r"視頻", "影片"),
    (r"音頻", "音訊"),
    (r"網關", "閘道"),
    (r"鏈接", "連結"),
    (r"默認", "預設"),
    (r"屏幕", "螢幕"),
    (r"內存", "記憶體"),
    (r"接口", "介面"),
    (r"標品", "標準品"),
    (r"軟件", "軟體"),
    (r"硬件", "硬體"),
    (r"支持", "支援"),
    (r"渠道", "管道"),
    (r"質量", "品質"),
    (r"水平", "水準"),
]

def sanitize_taiwan_terms(text: str) -> str:
    """
    確定性過濾：清除大陸黑話與轉換為台灣在地用語。
    """
    if not text:
        return text
    for pattern, repl in BUZZWORD_REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    return text

# ==========================================
# 3.5 Stage 2: speak-human-tw 無人值守語言洗滌模組
# ==========================================
class SpeakHumanCleaner:
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.base_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "minimax/minimax-m3:free")

    def clean(self, raw_overview: str) -> str:
        """
        對 Stage 1 生成的 Overview 進行第二階段去 AI 味與繁中在地化清洗。
        遵循 speak-human-tw 核心準則，採用非互動式無人值守直接套用模式。
        """
        if not raw_overview or not raw_overview.strip():
            return raw_overview
            
        if not self.api_key or self.api_key == "your_key_here":
            return sanitize_taiwan_terms(raw_overview)

        logger.info("✨ 啟動 Stage 2 [speak-human-tw] 語言洗滌器：去 AI 味、在地化與人味注魂...")

        cleaner_system_prompt = """你是一位文字簡練、語感極度敏銳的「台灣資深科技專欄主筆兼總編輯」。
你的唯一任務：將傳入草稿中的「AI 生成味」、「中國大廠黑話」與「僵硬句型」徹底洗除，【用你自己的話打散重寫】為自然、成熟、像資深工程師在私下分享洞察的台灣繁體中文。

【核心重寫規範】：
1. 嚴禁表面校對！請將整篇草稿打散，用清晰自然的敘事邏輯重新組織，拒絕照抄生硬句式。
2. 斬斷四大 AI 腔（違者一律打散重寫）：
   - 嚴禁否定平行句（「不是 A 而是 B」、「不是 A 是 B」）！請直接說「是 B」，徹底刪除「不是 A」。
   - 嚴禁公式化列點（「第一是...第二是...第三是...」、「一是...二是...三是...」、「首先...其次...最後...」）！請融入段落自然敘事。
   - 嚴禁機械式總結（「這幾個案例的共同點是...」、「這三條路的共同特點很一致：...」）！
   - 嚴禁口號式結尾（「這才叫...」、「這才算...」、「這無疑是...」）！請用平實落地的觀察收尾。
3. 絕不使用任何中國大廠黑話：
   - 嚴禁出現：閉環、賦能、抓手、打法、顆粒度、心智、下沉、沉澱、飛輪、搓出、載體、組合拳、痛點、賽道、佈局。必須換為大白話。
4. 台灣在地用語對照：
   - 互聯網➔網路、群聊➔群組、批註➔註記、運營➔營運、行業➔產業、搭建➔打造/建構、緩存➔快取、算法➔演算法、服務器➔伺服器、項目➔專案、用戶➔使用者、信息➔資訊。
5. 排版規範：
   - 保留 2~3 個流暢段落，每個段落上方獨立配有一行【自訂精煉論點小標題】。
   - 小標題內絕不可有「引言/拆解/結論」標籤，也絕不可使用「不是...是...」句型。
   - 小標題與內文、各段落之間皆以空行隔開。
6. 【論文式文內注釋保護鐵律】：
   - 草稿內文中出現的所有 [[編號]](url)（例如 `[[1]](https://...)`、`[[2]](https://...)`）是讀者跳轉查證全文的重要入口，在去 AI 味改寫時【必須 100% 精準保留】在相應論點或案例名詞後方，絕不可刪除任何引用編號或變更網址！

直接輸出改寫後終稿，絕不附加任何說明或額外字句。"""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Eujenz/QuietRadar",
            "X-Title": "QuietRadar-Humanizer"
        }

        models_to_try = [self.model]
        if "openrouter" in self.base_url.lower():
            for b in ["minimax/minimax-m3:free", "minimax/minimax-m2.7:free", "google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-lightning:free"]:
                if b not in models_to_try:
                    models_to_try.append(b)

        client_timeout = httpx.Timeout(90.0, connect=20.0, read=90.0, write=20.0)

        for current_model in models_to_try:
            payload = {
                "model": current_model,
                "messages": [
                    {"role": "system", "content": cleaner_system_prompt},
                    {"role": "user", "content": f"請將以下這段充滿 AI 味與大廠黑話的草稿，【徹底用你自己的話重新改寫成地道自然的台灣繁體中文】。特別注意：這段【今日觀點】正文篇幅必須維持在約 800~2,000 字左右（不含引用來源），保持充分的論述展開與實例細節，嚴禁過度濃縮或閹割篇幅。段落上方保留自訂【精煉論點小標題】（請勿帶有引言/拆解/結論等標籤）。內文中的 [[編號]](url) 論文式引用連結必須 100% 原樣保留：\n\n{raw_overview}"}
                ],
                "temperature": 0.4,
                "max_tokens": 4096
            }

            try:
                with httpx.Client(timeout=client_timeout) as client:
                    resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        cleaned_text = data["choices"][0]["message"]["content"].strip()
                        if cleaned_text.startswith("```"):
                            cleaned_text = re.sub(r'^```[a-zA-Z]*\n?', '', cleaned_text)
                            cleaned_text = re.sub(r'\n?```$', '', cleaned_text).strip()
                        # 自動清除小標題中可能殘留的「引言：」、「拆解：」、「結論：」等機械標籤
                        cleaned_text = re.sub(r'【(?:引言|拆解|結論|總結|背景|行動指南|技術解構|工程思考|市場現狀|產品策略)[：:]\s*', '【', cleaned_text)
                        # 確保每個【小標題】前後皆有標準空行
                        cleaned_text = re.sub(r'([^\n])\n(【[^\n]+】)', r'\1\n\n\2', cleaned_text)
                        cleaned_text = re.sub(r'(【[^\n]+】)\n([^\n])', r'\1\n\n\2', cleaned_text)
                        # 確定性黑話與台灣用語洗滌保險
                        cleaned_text = sanitize_taiwan_terms(cleaned_text)

                        if cleaned_text and len(cleaned_text) >= 100:
                            logger.info(f"✅ Stage 2 語言洗滌完成（模型: {current_model}，潤飾後共 {len(cleaned_text)} 字）")
                            return cleaned_text
                    else:
                        logger.warning(f"⚠️ Stage 2 模型 [{current_model}] 回應異常 ({resp.status_code})，嘗試備援模型...")
            except Exception as e:
                logger.warning(f"⚠️ Stage 2 呼叫 [{current_model}] 失敗: {e}")
                continue

        logger.warning("⚠️ Stage 2 備援鏈全部耗盡，自動回退並執行確定性辭彙洗滌")
        return sanitize_taiwan_terms(raw_overview)


def load_prompt_template(config: Dict[str, Any]) -> str:
    """
    自 prompts/ 目錄動態讀取指定的提示詞模板；若找不到則回退至 sources.yaml 中的 custom_prompt 或預設值。
    """
    settings = config.get("pipeline_settings", {})
    template_name = settings.get("active_prompt_template", "solopreneur")
    
    prompts_dir = "prompts"
    template_file = os.path.join(prompts_dir, f"{template_name}.md")
    
    if os.path.exists(template_file):
        try:
            with open(template_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    logger.info(f"📂 成功載入 Prompt 模板: [{template_name}] ({template_file})")
                    return content
        except Exception as e:
            logger.warning(f"⚠️ 讀取 Prompt 模板 {template_file} 失敗: {e}")
            
    # 回退到 sources.yaml 內的 custom_prompt
    if config.get("custom_prompt"):
        logger.info("📂 使用 sources.yaml 中的自訂提示詞")
        return config.get("custom_prompt")
        
    return ""

# ==========================================
# 4. 電子報格式化與生成器 (speak-human-tw 風格)
# ==========================================
def to_superscript_citations(text: str, style: str = "A") -> str:
    """
    將 Markdown 正文中的注釋連結 [[1]](url) 或 [1](url) 自動轉為精緻的右上角小標。
    - 'A': [¹](url) - 純右上角上標數字（論文經典風，最簡潔）
    - 'B': [⁽¹⁾](url) - 右上角上標帶小括號
    - 'C': [[¹]](url) - 方括號內置上標
    """
    superscript_map = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

    def repl(match):
        num_str = match.group(1)
        url = match.group(2)
        sup_num = num_str.translate(superscript_map)
        if style == "A":
            return f"[{sup_num}]({url})"
        elif style == "B":
            return f"[⁽{sup_num}⁾]({url})"
        else:
            return f"[[{sup_num}]]({url})"

    return re.sub(r'\[+([0-9]+)\]+\((https?://[^\)]+)\)', repl, text)


def format_newsletter_markdown(items: List[Dict[str, Any]], template: Optional[Dict[str, Any]] = None, overview: str = "", now_str: Optional[str] = None) -> str:
    """
    統一生成電子報 Markdown 內容，供本地檔案存檔 (latest_newsletter.md) 與 Bark 推播共用，確保格式 100% 一致。
    """
    if not now_str:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    tpl = template or {}
    
    header_tpl = tpl.get("header", "# ⚡ QuietRadar 降噪科技電子報\n> 出刊時間：{time} | 本期精選：{count} 則\n---")
    overview_tpl = tpl.get("overview", "💡 【今日觀點】\n\n{overview}\n\n---")
    group_tpl = tpl.get("group_header", "## 📰 【{source}】({count} 則)")
    item_tpl = tpl.get("item_format", "{index}. [{title}]({url})")
    footer_tpl = tpl.get("footer", "---")

    grouped_by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        src = item.get("source_name", "精選情報")
        grouped_by_source.setdefault(src, []).append(item)

    header_text = header_tpl.replace("{time}", now_str).replace("{count}", str(len(items))).strip()
    lines = [header_text, ""]

    if overview and overview_tpl.strip():
        # 防禦性清理：確保 overview 替換後，分隔線 --- 前方必有空行，避免 Markdown Setext Heading 語法將前一行/整段文字誤判為 <h2> 粗體大標題
        cleaned_overview = sanitize_taiwan_terms(overview.strip())
        # 自動將注釋標籤轉為右上角小標
        citation_style = tpl.get("citation_style", "A")
        cleaned_overview = to_superscript_citations(cleaned_overview, style=citation_style)
        overview_text = overview_tpl.replace("{overview}", cleaned_overview).strip()
        overview_text = re.sub(r'([^\n])\n---', r'\1\n\n---', overview_text)
        lines.append(overview_text)
        lines.append("")

    global_idx = 1
    for source_name, source_items in grouped_by_source.items():
        grp_text = group_tpl.replace("{source}", source_name).replace("{count}", str(len(source_items))).strip()
        lines.append(grp_text)
        lines.append("")
        for item in source_items:
            title = sanitize_taiwan_terms(item.get("title", "").strip())
            url = item.get("original_url", "").strip()
            # 徹底移除 [跨界靈感]、[跨界漫遊] 等贅字，純粹保留 EMOJI ✨
            title = re.sub(r'\[跨界[^\]]*\]\s*', '', title).strip()
            if item.get("is_serendipity"):
                if not title.startswith("✨"):
                    title = f"✨ {title}"
            else:
                title = re.sub(r'^✨\s*', '', title).strip()
            item_text = item_tpl.replace("{index}", str(global_idx)).replace("{title}", title).replace("{url}", url).replace("{source}", source_name).strip()
            # 移除無效 html <br> 標籤，純粹使用 Markdown 標準換行語法
            item_text = re.sub(r'<\s*br\s*/?\s*>', '', item_text).strip()
            # 關鍵：行尾帶有雙空格 (  ) 且各項目以空行分隔，100% 保證任何 Markdown 解析器（含 Bark/iOS）絕對各自換行！
            lines.append(f"{item_text}  ")
            lines.append("")
            global_idx += 1

    if footer_tpl.strip():
        lines.append(footer_tpl.replace("{time}", now_str).replace("{count}", str(len(items))).strip())
    
    full_markdown = "\n".join(lines).strip()
    # 全域防禦：再次確保任何 --- 或 === 分隔線前方皆有空行隔離，並執行最終字典級在地化洗滌
    full_markdown = re.sub(r'([^\n])\n(---|===)', r'\1\n\n\2', full_markdown)
    full_markdown = sanitize_taiwan_terms(full_markdown)
    return full_markdown


def generate_newsletter_file(items: List[Dict[str, Any]], filepath: str = "latest_newsletter.md", template: Optional[Dict[str, Any]] = None, overview: str = "") -> str:
    content = format_newsletter_markdown(items, template=template, overview=overview)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"📄 已成功生成最新電子報檔案: {filepath}")

    # 資產封存機制 (Archive Asset)：
    # 每一期產出的電子報都是寶貴的智力資產，自動按時間戳封存至 data/archive/
    archive_dir = os.path.join("data", "archive")
    try:
        os.makedirs(archive_dir, exist_ok=True)
        timestamp_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        archive_path = os.path.join(archive_dir, f"newsletter_{timestamp_str}.md")
        with open(archive_path, "w", encoding="utf-8") as af:
            af.write(content)
        logger.info(f"📦 已將本期電子報作為資產封存至: {archive_path}")
    except Exception as e:
        logger.warning(f"⚠️ 封存電子報至 data/archive 失敗: {e}")

    return content


def chunk_text_by_bytes(text: str, max_chunk_bytes: int = 2400) -> List[str]:
    """
    通用、嚴格保證每段都不超過 max_chunk_bytes 的分段器。
    優先在段落雙換行 (\n\n) 切分；
    若單段過長，在單換行 (\n) 切分；
    若單行仍過長，在中文標點分界切分。
    保證每一段的 UTF-8 位元組數 <= max_chunk_bytes！
    """
    text = text.strip()
    if not text:
        return []

    def get_bytes(s: str) -> int:
        return len(s.encode("utf-8"))

    if get_bytes(text) <= max_chunk_bytes:
        return [text]

    chunks = []
    rem = text
    while get_bytes(rem) > max_chunk_bytes:
        est_chars = int(max_chunk_bytes / 2.5)
        cut = rem[:est_chars]

        last_break = cut.rfind("\n\n")
        if last_break == -1 or last_break < int(est_chars * 0.3):
            last_break = cut.rfind("\n")
        if last_break == -1 or last_break < int(est_chars * 0.3):
            for punc in ["。", "！", "？", "；", "；\n"]:
                pos = cut.rfind(punc)
                if pos > last_break and pos >= int(est_chars * 0.3):
                    last_break = pos + len(punc)
        if last_break == -1 or last_break < 30:
            last_break = est_chars

        cand = rem[:last_break].strip()
        while get_bytes(cand) > max_chunk_bytes and len(cand) > 30:
            cand = cand[:int(len(cand) * 0.85)].strip()
            sub_break = max(cand.rfind("\n"), cand.rfind("。"))
            if sub_break > 30:
                cand = cand[:sub_break + 1].strip()

        if cand:
            chunks.append(cand)
            rem = rem[len(cand):].strip()
        else:
            break

    if rem:
        chunks.append(rem)

    return chunks


def split_markdown_for_bark(content: str, max_chunk_bytes: int = 2400) -> List[Dict[str, Any]]:
    """
    將超長 Markdown 電子報依據 UTF-8 位元組大小（嚴格限制 <= 2400 bytes，徹底防禦 Apple APNs 4KB 與 Bark Nginx 413 限制）
    安全拆分為多個推播批次。
    回傳清單，每個元素包含：
    {
        "page": int,
        "total": int,
        "is_first": bool,
        "type": "overview" | "continuation" | "sources" | "full",
        "markdown": str
    }
    """
    if not content or not content.strip():
        return [{"page": 1, "total": 1, "is_first": True, "type": "full", "markdown": ""}]

    def get_bytes(s: str) -> int:
        return len(s.encode("utf-8"))

    if get_bytes(content) <= max_chunk_bytes:
        return [{"page": 1, "total": 1, "is_first": True, "type": "full", "markdown": content}]

    # 準確切分「今日觀點正文」與「文末引用來源清單」
    # 尋找第一個引用條目 (例如 1. [ 或 [1] [)
    item_match = re.search(r'\n(?:\[1\]|1\.)\s*\[', content)
    if item_match:
        prefix = content[:item_match.start()]
        div_pos = prefix.rfind("\n---")
        if div_pos != -1:
            part1 = content[:div_pos].rstrip()
            part2 = content[div_pos:].lstrip("-").strip()
        else:
            part1 = prefix.rstrip()
            part2 = content[item_match.start():].strip()
    else:
        part1 = content
        part2 = ""

    # 核心修復：對 part1 (觀點) 與 part2 (引用清單) 皆嚴格執行 <= max_chunk_bytes 分塊，絕不遺漏！
    part1_chunks = chunk_text_by_bytes(part1, max_chunk_bytes)
    part2_chunks = chunk_text_by_bytes(part2, max_chunk_bytes) if part2 else []

    total_chunks = []
    for i, c in enumerate(part1_chunks):
        total_chunks.append({
            "is_first": (i == 0),
            "type": "overview" if i == 0 else "continuation",
            "markdown": c
        })
    for c in part2_chunks:
        total_chunks.append({
            "is_first": False,
            "type": "sources",
            "markdown": c
        })

    total_count = len(total_chunks)
    for i, item in enumerate(total_chunks):
        item["page"] = i + 1
        item["total"] = total_count

    return total_chunks


# ==========================================
# 5. Bark 推播模組 (支援長文智慧分段推送)
# ==========================================
class SimpleBarkNotifier:
    def __init__(self):
        self.server_url = os.getenv("BARK_SERVER_URL", "https://api.day.app").rstrip("/")
        raw_keys = os.getenv("BARK_DEVICE_KEY", "")
        # 支援多組 Key：以逗號 (,)、分號 (;)、換行 (\n) 或空格分隔
        self.device_keys = [
            k.strip() for k in re.split(r'[,;\s]+', raw_keys)
            if k.strip() and k.strip() != "your_bark_key_here"
        ]
        self.device_key = self.device_keys[0] if self.device_keys else ""

    def send_digest(self, items: List[Dict[str, Any]], template: Optional[Dict[str, Any]] = None, overview: str = "", full_markdown: Optional[str] = None) -> bool:
        if not self.device_keys:
            logger.warning("⚠️ 未配置有效的 BARK_DEVICE_KEY，跳過手機推播")
            return False

        if not items:
            logger.info("ℹ️ 無精選文章，不觸發推播")
            return True

        # 若未直接傳入已格式化的 full_markdown，則調用統一格式化函式生成
        body_markdown = full_markdown or format_newsletter_markdown(items, template=template, overview=overview)
        chunks = split_markdown_for_bark(body_markdown, max_chunk_bytes=2400)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        overall_success = True

        # 1. 取得由 GUI 輸出結構框架 (output_template) 定義的各段推播標題模板
        tpl = template or {}
        header_raw = tpl.get("header", "# ⚡ QuietRadar | 每日素材\n")
        header_first_line = re.sub(r'^[#>\s]+', '', header_raw.strip().split("\n")[0]).strip()
        default_title = header_first_line or "⚡ QuietRadar | 每日素材"

        bark_title_fmt = (tpl.get("bark_title") or "").strip()
        bark_continue_fmt = (tpl.get("bark_continuation_title") or "").strip()
        bark_sources_fmt = (tpl.get("bark_sources_title") or "").strip()

        def render_bark_title(fmt: str, p: int, tot: int) -> str:
            if not fmt:
                return ""
            res = fmt.replace("{title}", default_title)
            res = res.replace("{page}", str(p))
            res = res.replace("{total}", str(tot))
            res = res.replace("{count}", str(len(items)))
            res = res.replace("{time}", now_str)
            if tot == 1:
                res = re.sub(r'\s*[\(\[]1/1[\)\]]', '', res).strip()
            return res.strip()

        # 2. 若為多段推播，採用倒序推送（例如 4 -> 3 -> 2 -> 1）
        push_queue = list(reversed(chunks)) if len(chunks) > 1 else chunks

        # 3. 支援推送至多台 Bark 裝置（依序個別完整倒序推送，避免訊息交錯亂序）
        total_devices = len(self.device_keys)
        logger.info(f"📱 準備向 {total_devices} 台 Bark 裝置發送推播...")

        for d_idx, d_key in enumerate(self.device_keys):
            masked_key = d_key[:4] + "..." + d_key[-4:] if len(d_key) > 8 else "***"
            logger.info(f"📲 [裝置 {d_idx + 1}/{total_devices}] 開始推播至 ({masked_key})...")

            for idx, chunk_info in enumerate(push_queue):
                page = chunk_info["page"]
                total = chunk_info["total"]
                c_type = chunk_info["type"]
                c_md = chunk_info["markdown"]

                if total == 1:
                    title = render_bark_title(bark_title_fmt, 1, 1)
                elif chunk_info["is_first"]:
                    title = render_bark_title(bark_title_fmt, page, total)
                elif c_type == "sources":
                    title = render_bark_title(bark_sources_fmt, page, total)
                else:
                    title = render_bark_title(bark_continue_fmt, page, total)

                payload = {
                    "markdown": c_md,
                    "group": "QuietRadar",
                    "icon": "https://cdn-icons-png.flaticon.com/512/3208/3208726.png",
                    "sound": "calypso",
                    "isArchive": "1",
                    "device_key": d_key
                }

                if title:
                    payload["title"] = title
                    if chunk_info["is_first"]:
                        payload["body"] = f"出刊時間：{now_str} | 本期精選 {len(items)} 則情報"
                    elif c_type == "sources":
                        payload["body"] = f"共 {len(items)} 則精選引用來源"
                    else:
                        payload["body"] = f"第 {page}/{total} 頁"

                try:
                    with httpx.Client(timeout=15.0) as client:
                        resp = client.post(f"{self.server_url}/push", json=payload)
                        if resp.status_code == 200:
                            lbl = f"標題: {title}" if title else f"第 {page}/{total} 頁 (無標題，純正文)"
                            logger.info(f"📱 [{masked_key}] 推播成功！[{lbl}]")
                        else:
                            logger.error(f"❌ [{masked_key}] 推播失敗: {resp.status_code} - {resp.text}")
                            overall_success = False
                except Exception as e:
                    logger.error(f"❌ [{masked_key}] 連線異常: {e}")
                    overall_success = False

                if len(push_queue) > 1 and idx < len(push_queue) - 1:
                    time.sleep(0.8)  # 多批次推送微幅間隔，確保手機通知時間戳嚴格井然

        return overall_success

# ==========================================
# 6. 主排程管線 (Pipeline Main)
# ==========================================
def run_pipeline(test_mode: bool = False, force: bool = False):
    logger.info(f"🚀 QuietRadar 批次情報雷達啟動... {'[🧪 測試模式]' if test_mode else ''}{'[⚡ 強制模式]' if force else ''}")
    
    # 1. 讀取配置
    config_path = "sources.yaml"
    if not os.path.exists(config_path):
        logger.error(f"❌ 找不到 {config_path} 設定檔！")
        return
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    sources = config.get("sources", [])
    profile = config.get("profile", {})
    pipeline_settings = config.get("pipeline_settings", {})
    custom_prompt = load_prompt_template(config)

    # 2. 抓取文章（傳入 profile 與 pipeline_settings 執行標題前置漏斗過濾）
    raw_articles = fetch_sources(sources, profile=profile, pipeline_settings=pipeline_settings)
    logger.info(f"📥 抓取與漏斗篩選完成，共取得 {len(raw_articles)} 篇候選文章")

    # 3. 資料庫比對防重
    session = SessionLocal()
    if force:
        logger.info("⚡ 【強制模式】忽略資料庫防重紀錄，所有爬取文章均視為候選！")
        unprocessed = list(raw_articles)
    else:
        unprocessed = [a for a in raw_articles if not is_article_processed(session, a["sha256"])]
        logger.info(f"🔍 防重過濾完成：已讀 {len(raw_articles) - len(unprocessed)} 篇，新文章 {len(unprocessed)} 篇")

    if not unprocessed:
        if test_mode:
            logger.warning("🧪 【測試模式觸發】未發現全新文章，為驗證 OUTPUT 產出流程，自動啟用候選回退機制！")
            if raw_articles:
                logger.info(f"🔄 [回退來源 1] 取用本次爬取的既有文章（共 {len(raw_articles)} 篇）作為 OUTPUT 測試樣本...")
                unprocessed = list(raw_articles)
            else:
                db_recent = session.query(ProcessedArticle).order_by(ProcessedArticle.id.desc()).limit(10).all()
                if db_recent:
                    logger.info(f"📦 [回退來源 2] 本次未抓到任何文章，從資料庫讀取最近 {len(db_recent)} 篇歷史文章作為測試樣本...")
                    unprocessed = [
                        {
                            "source_name": "歷史紀錄",
                            "title": p.title,
                            "url": p.url,
                            "summary": f"{p.title} (此為測試回退之歷史存檔摘要，用於驗證 OUTPUT 流程)",
                            "sha256": p.sha256_hash,
                            "relevance_score": 1
                        }
                        for p in db_recent
                    ]
                else:
                    logger.info("📦 [回退來源 3] 資料庫無紀錄，啟用內建 Mock 測試樣本進行 OUTPUT 測試...")
                    unprocessed = [
                        {
                            "source_name": "範例來源",
                            "title": "測試樣本：AI Agent 工作流在一人公司的落地實踐與商業閉環",
                            "url": "https://example.com/test-article-1",
                            "summary": "這是一篇用於 OUTPUT 測試的範例文章。探討一人公司如何運用自動化管線與 AI 決策層降低營運工時，避開現場硬體維護負債...",
                            "sha256": "mock_hash_001",
                            "relevance_score": 5
                        }
                    ]
        else:
            logger.info("✨ 沒有新文章需要處理，本次任務結束。")
            logger.info("💡 提示：若要進行 OUTPUT 測試，請加上 '--test' (如: python pipeline.py --test) 或在 Web 控制台點擊「測試 OUTPUT」！")
            session.close()
            return

    # 讀取研讀深度與候選池上限設定
    pipeline_settings = config.get("pipeline_settings", {})
    max_pool = pipeline_settings.get("max_candidate_pool", 30)
    snippet_len = pipeline_settings.get("content_snippet_length", 900)
    serendipity_enabled = pipeline_settings.get("serendipity_enabled", True)
    serendipity_ratio = pipeline_settings.get("serendipity_ratio", 0.28)
    serendipity_quota = pipeline_settings.get("serendipity_quota")
    if serendipity_quota is None:
        # 動態比例：若總池 30 篇，保障 28% = 8~9 篇跨界
        serendipity_quota = max(2, int(max_pool * serendipity_ratio)) if serendipity_enabled else 0

    # 槓鈴策略配額組裝 (Barbell Candidate Pool Assembly)
    core_articles = [a for a in unprocessed if not a.get("is_serendipity", False)]
    serendipity_articles = [a for a in unprocessed if a.get("is_serendipity", False)]

    # 跨界靈感蓄水池保障 (Serendipity Longevity Reservoir)：
    # 跨界文章（農業科技、商業周刊、經理人）發文頻率通常比高頻科技部落格慢得多。
    # 若本輪未讀的新跨界文章不足 serendipity_quota，自動從本次爬取的既有跨界文章 (raw_articles) 補足！
    # 確保每次出刊永遠具備足額的跨界靈感素材進行撞擊！
    if len(serendipity_articles) < serendipity_quota:
        seen_urls = {a["url"] for a in serendipity_articles}
        raw_cross = [a for a in raw_articles if a.get("is_serendipity", False) and a["url"] not in seen_urls]
        needed_cross = serendipity_quota - len(serendipity_articles)
        supplement_cross = raw_cross[:needed_cross]
        if supplement_cross:
            logger.info(f"💡 [跨界蓄水池啟動] 全新跨界文章僅 {len(serendipity_articles)} 篇，自動從本期爬取之跨界情報中調用 {len(supplement_cross)} 篇補足配額！")
            serendipity_articles.extend(supplement_cross)

    # 跨界文章來源去中心化輪轉，避免單一來源佔滿所有跨界配額
    if serendipity_articles:
        source_buckets: Dict[str, List[Dict[str, Any]]] = {}
        for a in serendipity_articles:
            source_buckets.setdefault(a["source_name"], []).append(a)
        diversified_serendipity = []
        while len(diversified_serendipity) < len(serendipity_articles):
            added = False
            for s_name, s_list in source_buckets.items():
                if s_list:
                    diversified_serendipity.append(s_list.pop(0))
                    added = True
            if not added:
                break
        serendipity_articles = diversified_serendipity

    # 理想配額：保障 serendipity_quota 篇跨界文章，其餘分配給核心文章
    # 當某一方數量不足時，動態由另一方填補，確保研讀池始終飽和
    if serendipity_articles and core_articles:
        selected_serendipity = serendipity_articles[:serendipity_quota]
        remaining_slots = max_pool - len(selected_serendipity)
        selected_core = core_articles[:remaining_slots]
        if len(selected_core) < remaining_slots and len(serendipity_articles) > len(selected_serendipity):
            extra_needed = remaining_slots - len(selected_core)
            selected_serendipity += serendipity_articles[serendipity_quota : serendipity_quota + extra_needed]
    elif serendipity_articles:
        selected_serendipity = serendipity_articles[:max_pool]
        selected_core = []
    elif core_articles:
        selected_core = core_articles[:max_pool]
        selected_serendipity = []
    else:
        selected_core = unprocessed[:max_pool]
        selected_serendipity = []

    target_candidates = selected_core + selected_serendipity
    logger.info(f"⚖️ 槓鈴候選池組裝：核心業務文章 {len(selected_core)} 篇 ({len(selected_core)/len(target_candidates)*100:.0f}%) + 跨界漫遊文章 {len(selected_serendipity)} 篇 ({len(selected_serendipity)/len(target_candidates)*100:.0f}%)（總計 {len(target_candidates)} 篇送入大模型）")

    # 計算本輪餵給大模型的實際字數統計，並清晰記錄於 LOG
    total_chars = sum(len(a.get("summary", "")[:snippet_len] if snippet_len > 0 else a.get("summary", "")) for a in target_candidates)
    snippet_desc = f"{snippet_len} 字" if snippet_len > 0 else "完整內文 (不截斷)"
    logger.info(f"📚 LLM 研讀池就緒：共送入 {len(target_candidates)} 篇候選文章，每篇內文上限 {snippet_desc}（本輪總計向大模型投餵約 {total_chars:,} 字元實質正文）")

    # 4. LLM 蒸餾降噪 (依據候選文章深度與質量動態精選，上限 max_output_items)
    max_output_items = pipeline_settings.get("max_output_items", 15)
    distiller = SimpleLLMDistiller()
    distill_res = distiller.distill(target_candidates, profile, top_k=max_output_items, custom_prompt=custom_prompt, snippet_length=snippet_len)
    
    overview = ""
    distilled_items = []
    if isinstance(distill_res, dict):
        overview = distill_res.get("overview", "")
        distilled_items = distill_res.get("items", [])
    elif isinstance(distill_res, list):
        distilled_items = distill_res

    # 將原始候選文章屬性（如 is_serendipity）回填至 distilled_items
    for item in distilled_items:
        orig_url = item.get("original_url", "").strip()
        matched = next((c for c in target_candidates if c.get("url") == orig_url or (orig_url and orig_url in c.get("url", ""))), None)
        if matched:
            item["is_serendipity"] = matched.get("is_serendipity", False)
            if not item.get("source_name"):
                item["source_name"] = matched.get("source_name", "精選情報")

    # 槓鈴比例動態保底：確保跨界靈感不被漏選，同時完全解放篇數限制（質量優先）
    has_cross_candidates = any(c.get("is_serendipity") for c in target_candidates)
    if has_cross_candidates and distilled_items:
        llm_core = [it for it in distilled_items if not it.get("is_serendipity")]
        llm_cross = [it for it in distilled_items if it.get("is_serendipity")]

        # 只要總挑選數大於 0，確保至少 20%~30% 的跨界靈感入選（至少 1~2 篇）
        target_cross_min = max(1, round(len(distilled_items) * 0.25))
        if len(llm_cross) < target_cross_min:
            used_urls = {it.get("original_url") for it in distilled_items}
            cand_cross = [c for c in target_candidates if c.get("is_serendipity") and c.get("url") not in used_urls]
            needed = target_cross_min - len(llm_cross)
            for supp in cand_cross[:needed]:
                clean_t = re.sub(r'\[跨界[^\]]*\]\s*', '', supp['title']).strip()
                llm_cross.append({
                    "title": f"✨ {clean_t}" if not clean_t.startswith("✨") else clean_t,
                    "original_url": supp["url"],
                    "source_name": supp["source_name"],
                    "is_serendipity": True
                })
                logger.info(f"⚖️ [槓鈴保底] 自動補足跨界漫遊文章：【{supp['title'][:30]}】({supp['source_name']})")

        # 若總數超過使用者設定之上限，進行安全裁剪
        if len(llm_core) + len(llm_cross) > max_output_items:
            max_cross = max(1, int(max_output_items * 0.28))
            max_core = max_output_items - max_cross
            llm_cross = llm_cross[:max_cross]
            llm_core = llm_core[:max_core]

        distilled_items = llm_core + llm_cross

    # [防禦微調 2]：寫入防重安全保護
    if not distilled_items:
        logger.warning("⚠️ 本輪未產出任何精選情報（可能因 LLM 異常或全部被判定為雜訊），保留候選池不標記已讀，等待下輪重試。")
        session.close()
        return

    logger.info(f"🎯 LLM 蒸餾完成，成功選出 {len(distilled_items)} 則精選項目 (核心: {len(distilled_items)-len([i for i in distilled_items if i.get('is_serendipity')])} 則, 跨界: {len([i for i in distilled_items if i.get('is_serendipity')])} 則)")

    # Stage 2: speak-human-tw 無人值守語言洗滌器 (若啟用且 overview 非空)
    enable_humanizer = pipeline_settings.get("enable_two_stage_humanizer", True)
    if enable_humanizer and overview:
        cleaner = SpeakHumanCleaner()
        overview = cleaner.clean(overview)

    output_template = config.get("output_template", {})

    # 5. 生成 speak-human-tw 風格電子報存檔 (latest_newsletter.md) 並持久化蒸餾結果以利模板即時重繪
    newsletter_content = generate_newsletter_file(distilled_items, template=output_template, overview=overview)
    try:
        distilled_data = {
            "overview": overview,
            "items": distilled_items,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        with open("latest_distilled.json", "w", encoding="utf-8") as df:
            json.dump(distilled_data, df, ensure_ascii=False, indent=2)

        # 同步封存 JSON 資料結構
        archive_dir = os.path.join("data", "archive")
        os.makedirs(archive_dir, exist_ok=True)
        ts_now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        with open(os.path.join(archive_dir, f"distilled_{ts_now}.json"), "w", encoding="utf-8") as adf:
            json.dump(distilled_data, adf, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"⚠️ 寫入或封存 distilled json 失敗: {e}")

    # 6. 發送 Bark 推播（使用完全相同的 Markdown 內容）
    notifier = SimpleBarkNotifier()
    notifier.send_digest(distilled_items, template=output_template, overview=overview, full_markdown=newsletter_content)

    # 7. 確定蒸餾推播流程成功後，將本次參與蒸餾的文章指紋寫入資料庫
    if test_mode:
        logger.info("🧪 【測試模式】本次測試不將指紋寫入防重資料庫，確保您可隨時重複測試 OUTPUT！")
    else:
        record_processed_articles(session, target_candidates)
    session.close()
    logger.info("🏁 任務圓滿完成！已成功發送推播並生成 latest_newsletter.md 電子報。")

def run_doctor() -> bool:
    """
    一鍵環境與連線健康自檢診斷（借鏡 TrendRadar doctor 命令）。
    0 秒快速診斷 .env、OpenRouter、Bark、SQLite 與設定檔。
    """
    print("\n🩺 正在進行 QuietRadar 系統環境健康體檢...")
    print("=" * 60)
    all_pass = True

    def _check(name: str, passed: bool, detail: str, is_critical: bool = True):
        nonlocal all_pass
        if passed:
            print(f"✅ {name:16}: {detail}")
        else:
            if is_critical:
                all_pass = False
                print(f"❌ {name:16}: {detail}")
            else:
                print(f"⚠️ {name:16}: {detail}")

    # 1. 檢查 sources.yaml
    if os.path.exists("sources.yaml"):
        try:
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            src_count = len(cfg.get("sources", []))
            active_tpl = cfg.get("pipeline_settings", {}).get("active_prompt_template", "solopreneur")
            _check("配置檔案", True, f"sources.yaml 正常解析，包含 {src_count} 個訂閱來源，啟用模板: [{active_tpl}]")
        except Exception as e:
            _check("配置檔案", False, f"sources.yaml 解析失敗: {e}")
    else:
        _check("配置檔案", False, "找不到 sources.yaml 檔案")

    # 2. 檢查 prompts 目錄
    if os.path.exists("prompts") and os.path.isdir("prompts"):
        md_files = [f for f in os.listdir("prompts") if f.endswith(".md")]
        _check("Prompt 模板庫", len(md_files) > 0, f"prompts/ 包含 {len(md_files)} 個可用模板 ({', '.join(f[:-3] for f in md_files)})")
    else:
        _check("Prompt 模板庫", False, "找不到 prompts/ 資料夾", is_critical=False)

    # 3. 檢查 SQLite 本機資料庫
    try:
        session = SessionLocal()
        count = session.query(ProcessedArticle).count()
        session.close()
        _check("SQLite 資料庫", True, f"quietradar.db 正常連線，歷史已讀指紋庫共 {count} 筆紀錄")
    except Exception as e:
        _check("SQLite 資料庫", False, f"資料庫存取失敗: {e}")

    # 4. 檢查 LLM 金鑰與 OpenRouter API 連通性
    llm_key = os.getenv("LLM_API_KEY", "")
    llm_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    llm_model = os.getenv("LLM_MODEL", "minimax/minimax-m3:free")

    if not llm_key or llm_key.startswith("your_"):
        _check("LLM API 金鑰", False, "尚未在 .env 中設定有效的 LLM_API_KEY")
    else:
        try:
            headers = {
                "Authorization": f"Bearer {llm_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/Eujenz/QuietRadar",
                "X-Title": "QuietRadar-Doctor"
            }
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{llm_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": llm_model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 2
                    }
                )
                if resp.status_code == 200:
                    _check("LLM 服務連線", True, f"成功連線至 {llm_url}，模型 [{llm_model}] 響應正常")
                else:
                    _check("LLM 服務連線", False, f"端點回應異常 HTTP {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            _check("LLM 服務連線", False, f"無法連線至 {llm_url}: {e}")

    # 5. 檢查 Bark 手機推播金鑰與伺服器
    bark_key = os.getenv("BARK_DEVICE_KEY", "")
    bark_url = os.getenv("BARK_SERVER_URL", "https://api.day.app").rstrip("/")

    if not bark_key or bark_key.startswith("your_"):
        _check("Bark 推播配置", False, "尚未在 .env 中設定 BARK_DEVICE_KEY (推播將略過)", is_critical=False)
    else:
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(f"{bark_url}/ping")
                if resp.status_code == 200:
                    _check("Bark 伺服器", True, f"推播伺服器 {bark_url} 連線通暢")
                else:
                    _check("Bark 伺服器", True, f"伺服器回應 HTTP {resp.status_code} (基礎連通)")
        except Exception as e:
            _check("Bark 伺服器", False, f"無法連線至 Bark 伺服器 {bark_url}: {e}", is_critical=False)

    print("=" * 60)
    if all_pass:
        print("🎉 體檢完成：QuietRadar 系統核心狀態良好，隨時可執行批次情報任務！\n")
    else:
        print("⚠️ 體檢完成：發現部分關鍵設定需要修正，請參考上方提示檢查。\n")
    return all_pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuietRadar 批次情報雷達")
    parser.add_argument("--test", "-t", action="store_true", help="測試模式：無新文章時自動使用現有文章進行 OUTPUT 測試，且不記錄防重")
    parser.add_argument("--force", "-f", action="store_true", help="強制模式：忽略已讀記錄，強制重新蒸餾並產出")
    parser.add_argument("--doctor", "-d", action="store_true", help="環境體檢模式：一鍵自檢 API Key、網路端點、資料庫與設定檔健康度")
    args = parser.parse_args()

    if args.doctor:
        run_doctor()
    else:
        run_pipeline(test_mode=args.test, force=args.force)
