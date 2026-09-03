import os
import sys
import json
import time
import hashlib
import logging
import re
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional

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

def is_article_processed(session, sha256_hash: str) -> bool:
    return session.query(ProcessedArticle).filter(ProcessedArticle.sha256_hash == sha256_hash).first() is not None

def record_processed_articles(session, articles: List[Dict[str, Any]]):
    for a in articles:
        if not is_article_processed(session, a["sha256"]):
            record = ProcessedArticle(
                sha256_hash=a["sha256"],
                title=a["title"][:500],
                url=a["url"][:1000],
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
            with httpx.Client(headers=headers, timeout=15.0, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"⚠️ 來源 [{name}] 回應狀態碼異常: {resp.status_code}，略過")
                    continue
                
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:25]:  # 擴大候選池掃描範圍
                    title = getattr(entry, "title", "").strip()
                    link = getattr(entry, "link", "").strip()
                    
                    if not title or not link:
                        continue
                    
                    total_scanned += 1
                    title_lower = title.lower()

                    # 【標題前置漏斗第一關】：負面黑名單硬性攔截（不讀取正文）
                    if filter_enabled and negative_kws:
                        hit_neg = [kw for kw in negative_kws if kw in title_lower]
                        if hit_neg:
                            logger.info(f"🚫 [標題漏斗攔截] 命中排斥詞「{hit_neg[0]}」：【{title[:35]}】-> 跳過不讀取正文")
                            dropped_negative += 1
                            continue

                    # 【標題前置漏斗第二關】：關注主題相關性審查
                    relevance_score = 0
                    if filter_enabled and interest_kws:
                        matched_int = [kw for kw in interest_kws if kw in title_lower]
                        relevance_score = len(matched_int)

                        if filter_mode == "strict" and relevance_score == 0:
                            logger.info(f"⏭️ [標題漏斗過濾] 嚴格模式未命中關注關鍵詞：【{title[:35]}】-> 跳過不讀取正文")
                            dropped_strict += 1
                            continue

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
                        "relevance_score": relevance_score
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
        
        # 優先使用 sources.yaml 中的自訂提示詞，否則使用預設值
        base_instructions = custom_prompt.strip() if custom_prompt else """你是一位具備資深技術洞察力、文筆犀利扎實的「繁體中文科技電子報總編輯」。
你的任務是：深入遍歷本期所有候選文章，融會貫通其中的技術脈絡，撰寫出一篇約 500~600 字、具備深度雜誌/電子報風格的「核心情報彙整與趨勢論述 (Overview)」，並精選出最具閱讀價值的文章。

【電子報風格 Overview 撰寫守則 (約 500~600 字)】:
1. 風格與調性：
   - 如同知名科技專欄主編卷首語（Editor's Dispatch），兼具宏觀趨勢視野、工程底層技術洞察與實戰觀點。
   - 拒絕流水帳：嚴禁逐條羅列文章標題（絕對不可寫「本期第一篇談了...第二篇談了...」）。
   - 融會貫通：將各篇文章的散點技術串聯成一個清晰的「技術生態演進」或「架構痛點解答」。
2. 結構與段落（請分成 2~3 個流暢段落，總篇幅控制在 500~600 字左右）：
   - 【引言與產業趨勢痛點】（約 150 字）：點出這批情報反映出當前技術圈面對的實質挑戰或焦點轉變（例如：由盲目追求模型參數轉向追求工程效能與快取成本，或開發者面對 AI 生成代碼時的架構轉型）。
   - 【核心技術脈絡與突破拆解】（約 250~300 字）：深入探討文章中提到的工程解法或核心機制（如 AI 快取、KV Cache 複用、系統架構優化等），具體說明這些做法如何解決瓶頸。
   - 【主編觀點與工程落地思考】（約 150 字）：給軟體工程師一個清晰、具備行動指引價值的思維結論，直言哪些是炒作、哪些是真正該投資的底層能力。
3. speak-human-tw 降噪鐵律：
   - 徹底消除 AI 罐頭廢話：嚴禁使用「賦能、閉環、抓手、顆粒度、掀起熱潮、拉開序幕、不可否認、總的來說、提供了豐富資訊、深入分析」等空泛贅字。
   - 台灣在地化用語：嚴格採用「軟體工程師」（非程序員）、快取（非緩存）、資料庫（非數據庫）、伺服器（非服務器）、影片（非視頻）、資訊（非信息）、相容（非兼容）、使用者（非用戶）。
   - 標點符號：中文一律使用全形標點符號（，。：「」『』、），引號一律用「」，嚴禁半形標點，嚴禁破折號。
4. 真實性原則：
   - original_url 與 source_name 必須嚴格照抄候選文章真實內容，絕不捏造。"""

        system_prompt = f"""{base_instructions}

【讀者關注主題】:
{json.dumps(profile.get('interests', []), ensure_ascii=False, indent=2)}

【讀者排斥的主題 (直接淘汰)】:
{json.dumps(profile.get('negative_topics', []), ensure_ascii=False, indent=2)}

【輸出格式】:
請從候選文章中挑選最多 {top_k} 則，並針對本期重點融會貫通撰寫出約 500~600 字電子報/雜誌風格的深度彙整論述，輸出符合以下格式的 JSON：
{{
  "overview": "融會貫通所有文章後的電子報專欄風格深度論述（繁體中文，約 500~600 字，分 2~3 段，具備技術深度與工程洞察，嚴禁標題流水帳）",
  "items": [
    {{
      "title": "精煉後的繁體中文標題",
      "original_url": "必須填寫候選文章中的真實 URL",
      "source_name": "來源名稱"
    }}
  ]
}}
"""
        # 準備餵給 LLM 的文章候選清單（依據 snippet_length 提供充足內文讓模型深度研讀）
        articles_payload = [
            {
                "index": i + 1,
                "source": a["source_name"],
                "title": a["title"],
                "url": a["url"],
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
# 4. 電子報生成器 (speak-human-tw 風格)
# ==========================================
def generate_newsletter_file(items: List[Dict[str, Any]], filepath: str = "latest_newsletter.md", template: Optional[Dict[str, Any]] = None, overview: str = ""):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    tpl = template or {}
    
    header_tpl = tpl.get("header", "# ⚡ QuietRadar 降噪科技電子報\n> 出刊時間：{time} | 本期精選：{count} 則\n---")
    overview_tpl = tpl.get("overview", "> 💡 **核心情報彙整論述**：\n> {overview}\n---")
    group_tpl = tpl.get("group_header", "## 📰 【{source}】({count} 則)")
    item_tpl = tpl.get("item_format", "{index}. [{title}]({url})")
    footer_tpl = tpl.get("footer", "")

    grouped_by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        src = item.get("source_name", "精選情報")
        grouped_by_source.setdefault(src, []).append(item)

    header_text = header_tpl.replace("{time}", now_str).replace("{count}", str(len(items))).strip()
    lines = [header_text, ""]

    if overview and overview_tpl.strip():
        overview_text = overview_tpl.replace("{overview}", overview).strip()
        lines.append(overview_text)
        lines.append("")

    global_idx = 1
    for source_name, source_items in grouped_by_source.items():
        grp_text = group_tpl.replace("{source}", source_name).replace("{count}", str(len(source_items))).strip()
        lines.append(grp_text)
        for item in source_items:
            title = item.get("title", "").strip()
            url = item.get("original_url", "").strip()
            item_text = item_tpl.replace("{index}", str(global_idx)).replace("{title}", title).replace("{url}", url).replace("{source}", source_name)
            lines.append(item_text)
            global_idx += 1
        lines.append("")

    if footer_tpl.strip():
        lines.append(footer_tpl.replace("{time}", now_str).replace("{count}", str(len(items))).strip())
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip())
    logger.info(f"📄 已成功生成電子報檔案: {filepath}")

# ==========================================
# 5. Bark 推播模組
# ==========================================
class SimpleBarkNotifier:
    def __init__(self):
        self.server_url = os.getenv("BARK_SERVER_URL", "https://api.day.app").rstrip("/")
        self.device_key = os.getenv("BARK_DEVICE_KEY", "")

    def send_digest(self, items: List[Dict[str, Any]], template: Optional[Dict[str, Any]] = None, overview: str = "") -> bool:
        if not self.device_key or self.device_key == "your_bark_key_here":
            logger.warning("⚠️ 未配置 BARK_DEVICE_KEY，跳過手機推播")
            return False

        if not items:
            logger.info("ℹ️ 無精選文章，不觸發推播")
            return True

        tpl = template or {}
        overview_tpl = tpl.get("overview", "")
        group_tpl = tpl.get("group_header", "### 📌 {source} ({count})")
        if group_tpl.startswith("## "):
            group_tpl = "### " + group_tpl[3:]
        item_tpl = tpl.get("item_format", "{index}. [{title}]({url})")

        grouped_by_source: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            src = item.get("source_name", "精選情報")
            grouped_by_source.setdefault(src, []).append(item)

        now_str = datetime.now().strftime("%m/%d %H:%M")
        md_lines = [f"🎯 **QuietRadar 降噪情報** ({now_str})\n"]

        if overview and overview_tpl.strip():
            overview_clean = overview_tpl.replace("{overview}", overview).strip()
            md_lines.append(overview_clean)
            md_lines.append("")

        global_idx = 1
        for source_name, source_items in grouped_by_source.items():
            grp_line = group_tpl.replace("{source}", source_name).replace("{count}", str(len(source_items)))
            md_lines.append(grp_line)
            for item in source_items:
                title = item.get("title", "").strip()
                url = item.get("original_url", "").strip()
                item_line = item_tpl.replace("{index}", str(global_idx)).replace("{title}", title).replace("{url}", url).replace("{source}", source_name)
                md_lines.append(item_line)
                global_idx += 1
            md_lines.append("")

        body_markdown = "\n".join(md_lines).strip()

        payload = {
            "title": f"⚡ QuietRadar 精選 ({len(items)} 則)",
            "markdown": body_markdown,
            "group": "QuietRadar",
            "icon": "https://cdn-icons-png.flaticon.com/512/3208/3208726.png",
            "sound": "calypso",
            "isArchive": "1",
            "device_key": self.device_key
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(f"{self.server_url}/push", json=payload)
                if resp.status_code == 200:
                    logger.info("📱 Bark 推播發送成功！")
                    return True
                else:
                    logger.error(f"❌ Bark 推播失敗: {resp.status_code} - {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"❌ Bark 連線異常: {e}")
            return False

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
    custom_prompt = config.get("custom_prompt", None)
    pipeline_settings = config.get("pipeline_settings", {})

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
    max_pool = pipeline_settings.get("max_candidate_pool", 10)
    snippet_len = pipeline_settings.get("content_snippet_length", 800)

    # 候選池上限保護
    if len(unprocessed) > max_pool:
        logger.info(f"🛡️ 觸發候選池上限設定：由 {len(unprocessed)} 篇截取最新 {max_pool} 篇進行深度研讀")
        target_candidates = unprocessed[:max_pool]
    else:
        target_candidates = unprocessed

    # 計算本輪餵給大模型的實際字數統計，並清晰記錄於 LOG
    total_chars = sum(len(a.get("summary", "")[:snippet_len] if snippet_len > 0 else a.get("summary", "")) for a in target_candidates)
    snippet_desc = f"{snippet_len} 字" if snippet_len > 0 else "完整內文 (不截斷)"
    logger.info(f"📚 LLM 研讀池就緒：共送入 {len(target_candidates)} 篇候選文章，每篇內文上限 {snippet_desc}（本輪總計向大模型投餵約 {total_chars:,} 字元實質正文）")

    # 4. LLM 蒸餾降噪 (傳入 custom_prompt 與 snippet_length)
    distiller = SimpleLLMDistiller()
    distill_res = distiller.distill(target_candidates, profile, top_k=7, custom_prompt=custom_prompt, snippet_length=snippet_len)
    
    overview = ""
    distilled_items = []
    if isinstance(distill_res, dict):
        overview = distill_res.get("overview", "")
        distilled_items = distill_res.get("items", [])
    elif isinstance(distill_res, list):
        distilled_items = distill_res

    # [防禦微調 2]：寫入防重安全保護
    if not distilled_items:
        logger.warning("⚠️ 本輪未產出任何精選情報（可能因 LLM 異常或全部被判定為雜訊），保留候選池不標記已讀，等待下輪重試。")
        session.close()
        return

    logger.info(f"🎯 LLM 蒸餾完成，成功選出 {len(distilled_items)} 則精選項目")

    output_template = config.get("output_template", {})

    # 5. 生成 speak-human-tw 風格電子報存檔 (latest_newsletter.md)
    generate_newsletter_file(distilled_items, template=output_template, overview=overview)

    # 6. 發送 Bark 推播
    notifier = SimpleBarkNotifier()
    notifier.send_digest(distilled_items, template=output_template, overview=overview)

    # 7. 確定蒸餾推播流程成功後，將本次參與蒸餾的文章指紋寫入資料庫
    if test_mode:
        logger.info("🧪 【測試模式】本次測試不將指紋寫入防重資料庫，確保您可隨時重複測試 OUTPUT！")
    else:
        record_processed_articles(session, target_candidates)
    session.close()
    logger.info("🏁 任務圓滿完成！已成功發送推播並生成 latest_newsletter.md 電子報。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuietRadar 批次情報雷達")
    parser.add_argument("--test", "-t", action="store_true", help="測試模式：無新文章時自動使用現有文章進行 OUTPUT 測試，且不記錄防重")
    parser.add_argument("--force", "-f", action="store_true", help="強制模式：忽略已讀記錄，強制重新蒸餾並產出")
    args = parser.parse_args()
    run_pipeline(test_mode=args.test, force=args.force)
