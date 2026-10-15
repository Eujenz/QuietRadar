import os
import sys
import json
import time
import hashlib
import logging
import re
import argparse
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    TAIPEI_TZ = ZoneInfo("Asia/Taipei")
except Exception:
    TAIPEI_TZ = timezone(timedelta(hours=8))

def get_taipei_now() -> datetime:
    """取得台灣時區 (Asia/Taipei, UTC+8) 當前時間"""
    return datetime.now(TAIPEI_TZ)

from typing import List, Dict, Any, Optional, Tuple, Union

try:
    import opencc
    _OPENCC_CONVERTER = opencc.OpenCC('s2twp.json')
    def to_taiwan_traditional(text: str) -> str:
        if not text:
            return ""
        return _OPENCC_CONVERTER.convert(text)
except Exception:
    def to_taiwan_traditional(text: str) -> str:
        return text or ""

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
    created_at = Column(DateTime, default=get_taipei_now)

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
                created_at=get_taipei_now()
            )
            session.add(record)
    session.commit()

def round_robin_interleave(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    依據信源 (source_name) 進行公平輪詢交叉排列，確保各信源均衡展現，杜絕前序單一信源霸佔候選池。
    """
    if not articles:
        return []
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for a in articles:
        buckets.setdefault(a["source_name"], []).append(a)
    result = []
    while True:
        added = False
        for s_name in list(buckets.keys()):
            if buckets[s_name]:
                result.append(buckets[s_name].pop(0))
                added = True
        if not added:
            break
    return result

def extract_keywords_from_list(items: List[str]) -> List[str]:
    kws = set()
    for item in items:
        parts = re.split(r'[（）()、，,／/\s+、。\-—:：]+', item)
        for p in parts:
            p = p.strip()
            if len(p) >= 2:
                kws.add(p.lower())
    return list(kws)

def fetch_sources(
    sources_config: List[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
    pipeline_settings: Optional[Dict[str, Any]] = None,
    return_stats: bool = False
) -> Union[List[Dict[str, Any]], Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
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
    sources_tried = 0
    successful_sources = 0
    
    for src in sources_config:
        if not src.get("enabled", True):
            continue
        sources_tried += 1
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

            is_feedly_json = False
            feedly_data = None

            # 智慧橋接機制 (Feedly Cloud Fallback)：
            # 若直接請求回傳 404 或失敗 (如數位時代 bnext 將本地 /rss 關閉，但 Feedly 雲端仍持續維護更新)
            if not resp_content:
                try:
                    feedly_url = f"https://cloud.feedly.com/v3/streams/contents?streamId=feed/{url}&count=25"
                    with httpx.Client(headers=headers, timeout=12.0) as client:
                        f_resp = client.get(feedly_url)
                        if f_resp.status_code == 200:
                            f_json = f_resp.json()
                            if f_json.get("items"):
                                logger.info(f"🌐 來源 [{name}] 透過 Feedly Cloud 智慧代理成功取得 {len(f_json['items'])} 篇最新文章！")
                                is_feedly_json = True
                                feedly_data = f_json
                except Exception as fe:
                    logger.debug(f"Feedly fallback error for {name}: {fe}")

            if not resp_content and not feedly_data:
                logger.warning(f"⚠️ 來源 [{name}] 抓取失敗，跳過此來源")
                continue

            successful_sources += 1

            entries = []
            if is_feedly_json and feedly_data:
                for it in feedly_data.get("items", []):
                    link = ""
                    for alt in it.get("alternate", []):
                        if alt.get("href"):
                            link = alt["href"]
                            break
                    if not link:
                        link = it.get("originId", "")
                    summary = it.get("summary", {}).get("content", "") or it.get("content", {}).get("content", "")
                    pub_ts = it.get("published", 0) / 1000.0 if it.get("published") else None
                    entries.append({
                        "title": it.get("title", "").strip(),
                        "link": link.strip(),
                        "summary": summary,
                        "published_parsed": time.gmtime(pub_ts) if pub_ts else None
                    })
            elif "daily_papers" in url or "huggingface.co/api" in url:
                try:
                    hf_json = json.loads(resp_content)
                    if isinstance(hf_json, list):
                        for p_obj in hf_json[:25]:
                            paper = p_obj.get("paper", {})
                            p_id = paper.get("id", "")
                            p_title = paper.get("title", "") or p_obj.get("title", "")
                            p_link = f"https://arxiv.org/abs/{p_id}" if p_id else ""
                            p_summary = paper.get("summary", "") or p_obj.get("summary", "")
                            upvotes = paper.get("upvotes", 0)
                            gh = paper.get("githubRepo", "")
                            meta_prefix = f"[HF Upvotes: {upvotes}"
                            if gh:
                                meta_prefix += f" | GitHub: {gh}"
                            meta_prefix += "]\n"
                            entries.append({
                                "title": p_title.strip(),
                                "link": p_link.strip(),
                                "summary": meta_prefix + p_summary.strip(),
                                "published_parsed": None
                            })
                except Exception as hf_err:
                    logger.warning(f"⚠️ 解析 HuggingFace JSON 失敗: {hf_err}")
            else:
                feed = feedparser.parse(resp_content)
                for entry in feed.entries:
                    entries.append({
                        "title": getattr(entry, "title", "").strip(),
                        "link": getattr(entry, "link", "").strip(),
                        "summary": getattr(entry, "summary", "") or getattr(entry, "description", ""),
                        "published_parsed": getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
                    })

            max_age_days = settings.get("max_article_age_days", 7)

            for entry in entries[:25]:  # 擴大候選池掃描範圍
                raw_title = entry.get("title", "").strip()
                # 徹底清洗 HTML 標籤與屬性殘渣（針對掘金等含 <a>、<div> 或未閉合引號的 RSS）
                clean_title = re.sub(r'<[^>]+>', '', raw_title)
                clean_title = re.sub(r'["\'>]+', '', clean_title)
                title = re.sub(r'\s+', ' ', clean_title).strip()
                title = sanitize_taiwan_terms(title)
                raw_link = entry.get("link", "").strip()
                
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
                    if isinstance(entry, dict):
                        pub_tuple = entry.get("published_parsed")
                    else:
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
                if isinstance(entry, dict):
                    raw_content = entry.get("summary", "")
                else:
                    if hasattr(entry, "content") and entry.content:
                        raw_content = entry.content[0].get("value", "")
                    if not raw_content:
                        raw_content = getattr(entry, "summary", "") or getattr(entry, "description", "")
                
                clean_text = re.sub(r'<[^>]+>', ' ', raw_content)
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                
                h = hashlib.sha256(f"{link}|{title}".encode("utf-8")).hexdigest()
                raw_articles.append({
                    "source_name": sanitize_taiwan_terms(name),
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

    stats = {
        "sources_tried": sources_tried,
        "successful_sources": successful_sources,
        "total_scanned": total_scanned,
        "dropped_negative": dropped_negative,
        "dropped_strict": dropped_strict,
        "accepted_count": len(raw_articles)
    }
    if return_stats:
        return raw_articles, stats
    return raw_articles

# ==========================================
# 2.5 Provenance 核心例外與脫敏工具
# ==========================================
class CitationProvenanceError(Exception):
    """當 LLM 輸出違反來源契約（出現 URL、舊引用格式、未授權 Source ID 等）時拋出此例外，拒絕產出報告。"""
    pass

def scrub_urls(text: str) -> str:
    """
    全域 URL 脫敏過濾器 (Input URL Scrubber)。
    使用粗暴積極阻斷模式將任何疑似 URL 替換為 [URL_REMOVED]，確保 No URL Seen 邊界。
    """
    if not text:
        return text or ""
    return re.sub(r'(?i)(?:https?://|www\.)\S+', '[URL_REMOVED]', text)

def build_llm_context(
    candidates: List[Dict[str, Any]],
    snippet_length: int = 800
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """
    從候選池建立：
    1. candidate_map: 私有字典 (LLM-private)，包含 canonical_url, article_key, title, source_name 等。
    2. articles_payload: 供 LLM 研讀的安全資料清單，徹底拔除 URL，且所有文字欄位經由 scrub_urls 脫敏。
    """
    candidate_map: Dict[str, Dict[str, Any]] = {}
    articles_payload: List[Dict[str, Any]] = []

    for i, a in enumerate(candidates):
        source_id = f"S{i+1:02d}"
        raw_url = a.get("url", "") or a.get("original_url", "")
        norm_url = normalize_url(raw_url)
        art_key = hashlib.sha256(norm_url.encode("utf-8")).hexdigest()
        title = a.get("title", "").strip()
        src_name = a.get("source_name", "精選情報").strip()
        is_serendipity = bool(a.get("is_serendipity", False))
        raw_snippet = a.get("summary", "")
        if snippet_length and snippet_length > 0:
            raw_snippet = raw_snippet[:snippet_length]

        # 1. 登記於 Python 私有 candidate_map (LLM-private)
        candidate_map[source_id] = {
            "source_id": source_id,
            "article_key": art_key,
            "canonical_url": raw_url,
            "title": title,
            "source_name": src_name,
            "is_serendipity": is_serendipity
        }

        # 2. 構建完全不含 URL 的 LLM Payload (所有字串經由 scrub_urls 脫敏)
        display_title = f"✨ {title}" if is_serendipity else title
        articles_payload.append({
            "source_id": source_id,
            "source_name": scrub_urls(src_name),
            "title": scrub_urls(display_title),
            "is_serendipity": is_serendipity,
            "content_snippet": scrub_urls(raw_snippet)
        })

    return candidate_map, articles_payload

# ==========================================
# 3. LLM 意圖對齊與降噪蒸餾模組
# ==========================================
EDITORIAL_SYSTEM_PROMPT = """# QuietRadar｜核心編輯部特稿專欄主筆

你是一位具備 20 年資歷的跨國政經與科技專欄總編輯（深度融合《天下雜誌》的產業縱深、《彭博商業周刊》的商業敏銳度與《日經商業》的結構剖析）。
你的讀者是一群時間極度寶貴、具備高度商業邏輯與技術素養的機構決策者與商業領袖。
你不是公關新聞發布器，不是廉價的科技摘要工具，更不是兜售粗糙創意思維的顧問或吹捧特定技術的啦啦隊。

你的工作是：
> 在喧囂雜訊中冷靜定錨，穿透公關修辭與技術熱潮，拆解表象背後的商業結構、權力轉移與二階效應（Second-order effects）。

---

【編輯鐵律】：
1. 嚴格區分事實（Fact）、解讀（Interpretation）與假設（Hypothesis）。永遠以客觀事實、精確數據對比與產業鏈因果邏輯開展論述。
2. 每日專欄敘事三段論：
   - 【定錨】（1 句話）：直接切入事件本質與衝突焦點，禁止廢話背景與迂迴前言。
   - 【拆解】（1~2 句話）：說明關鍵驅動機制、成本變動數據或商業轉折點。解答「為什麼是現在」以及「解決或製造了什麼摩擦力」。
   - 【折射 / 編輯微評】（1 句話）：將單一事件投射至更大的宏觀趨勢。明確指出誰會實質受惠，誰的既有護城河正在被侵蝕。
3. 刪稿與留白鐵律：不影響核心主線的資訊一律刪除。專欄結尾嚴禁重新列出「行動清單」、「操作指南」或「給決策者的建議」。

---

【嚴格負向約束（最高優先級）】：
- 標點禁令：嚴格禁止出現任何驚嘆號（！或 !）。所有陳述均以句點（。）沉穩收尾。嚴格禁止使用反問句與設問句。
- 詞彙禁令：嚴格禁止使用「重磅」、「顛覆性」、「革命性」、「令人驚豔」、「史詩級」、「大爆發」、「迎來全新時代」、「徹底改變遊戲規則」、「拭目以待」、「讓我們一起來看看」、「無疑是」、「完美解決」、「里程碑」、「天花板」等自嗨與公關吹捧形容詞。
- 空洞總結禁令：嚴格禁止使用「總體而言值得關注」、「未來發展值得持續觀察」、「讓我們拭目以待」等空洞套話。
- Emoji 禁令：嚴格禁止濫用情緒性 Emoji（如 🚀, 🔥, 💡, 🎉, 😱）。除了極少數用於結構排版的簡約符號（如 ▪、｜、——），正文中不得穿插任何裝飾符號。
- 八股段落禁令：嚴格禁止捏造或套用假大空的「Micro-TAM 試算」與「這週末做 Google 表單驗證」之類八股段落。
- 網址隔離禁令：正文與訊號中絕對禁止輸出任何原始 URL 網址。所有引用必須且只能使用系統提供的來源代碼（如 [S01]）。

---

【輸出格式契約 (JSON)】：
請輸出純 JSON 字串：
{
  "overview": "完整繁體中文專欄特稿 Markdown 內文（包含 # 專欄大標題、### 章節小標題，段落分明，凡提及處標註 [S01] 等代碼，嚴禁輸出任何 URL 網址）",
  "signals": [
    {
      "title": "訊號標題",
      "summary": "客觀事實摘要",
      "why_it_matters": "商業結構與產業鏈影響",
      "asset_potential": "長期價值捕獲或資產配置視角",
      "interpretation": "深層因果機制與解讀",
      "unknowns": "現階段尚未明朗的結構性變數或下行風險",
      "novelty": "NEW | KNOWN | EVOLVING",
      "novelty_basis": "判定依據",
      "source_ids": ["S01"]
    }
  ]
}
"""


class SimpleLLMDistiller:
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.base_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")

    def distill(
        self,
        candidates: List[Dict[str, Any]],
        profile: Dict[str, Any],
        top_k: int = 7,
        custom_prompt: Optional[str] = None,
        snippet_length: int = 800,
        historical_context: Optional[str] = None
    ) -> Dict[str, Any]:
        if not candidates:
            return {"overview": "", "signals": []}
            
        if not self.api_key or self.api_key == "your_key_here":
            logger.error("❌ 未配置有效的 LLM_API_KEY，無法進行蒸餾")
            return {"overview": "", "signals": []}
        
        # 100% 以 Prompt 模板庫為單一真理源（Single Source of Truth），徹底消除 Python 程式碼與模板的衝突
        if not custom_prompt or not custom_prompt.strip():
            fallback_template_path = os.path.join("prompts", "editorial.md")
            if os.path.exists(fallback_template_path):
                with open(fallback_template_path, "r", encoding="utf-8") as f:
                    base_instructions = f.read().strip()
            else:
                base_instructions = EDITORIAL_SYSTEM_PROMPT.strip()
        else:
            base_instructions = custom_prompt.strip()

        # 檢查傳入之 candidates 是否已為 articles_payload (包含 source_id)
        if candidates and "source_id" in candidates[0]:
            articles_payload = candidates
        else:
            _, articles_payload = build_llm_context(candidates, snippet_length=snippet_length)

        # 計算槓鈴配額（7~8成核心 : 2~3成跨界）
        has_serendipity = any(c.get("is_serendipity") for c in articles_payload)
        if has_serendipity:
            quota_rule = f"""1. 【槓鈴策略精選原則 (約 7~8成核心 : 2~3成跨界，質量優先)】:
   請深入研讀下方候選文章，挑選出真正具備商業啟發性、新奇且可行的情報（寧缺毋濫）。
   在精選出的情報項目中，請嚴格維持槓鈴比例：
   - 🎯 核心業務情報：約佔 70% ~ 80%
   - ✨ 跨界漫遊靈感：約佔 20% ~ 30%（必須從清單標題帶有 ✨ 的文章中挑選）
   嚴禁全部挑選核心文章而遺漏跨界靈感！"""
        else:
            quota_rule = "1. 請精選出最具閱讀價值的候選文章，並完全依據上方守則撰寫獨立深度論述正文。"

        hist_section = f"\n---\n{historical_context}\n" if historical_context else ""

        system_prompt = f"""{base_instructions}

---
【本期檢索動態偏好】:
- 讀者關注主題：{json.dumps(profile.get('interests', []), ensure_ascii=False)}
- 讀者排斥的主題 (直接淘汰)：{json.dumps(profile.get('negative_topics', []), ensure_ascii=False)}
{hist_section}
---
【系統輸出格式契約 (JSON) 與論文式文內注釋鐵律】:
{quota_rule}
2. 【論文式文內注釋鐵律】：
   - 在 overview 論述正文中，凡提及、借鏡特定文章的商業案例、技術架構、實驗數據或跨界理論時，必須在該名詞或觀點句後方標註候選文章的 Source ID 代碼：`[S01]`、`[S02]`。
   - 範例：借鏡騰訊混元 Hy4 preview 的成本壓制 [S01]...
   - 範例：樹德收納「增工加料」的反脆弱定價法 [S06]...
   - 只能引用候選文章列表中提供給你的 Source ID 代碼，嚴禁自造 ID。若無合適來源則不得引用。
   - 【絕對禁止】：正文與 JSON 中嚴禁輸出任何真實 URL 網址（如 http://、https://、www.）或舊式角注（如 [[1]](url)）。違者將導致整份報告直接作廢！
3. 【JSON 格式契約規範】：
   你必須輸出包含 "overview" 與 "signals" 欄位的合法 JSON 物件：
   - "overview" (字串)：根據上方指示撰寫的【商業排雷與專欄特稿】完整 Markdown 內文（採用科技商業專欄特稿散文體，深入剖析最具代表性訊號，自擬嵌入具體人事實地物的生動專屬小標題，凡引用處標註 [S01] 等代碼，嚴禁輸出任何網址）。
   - "signals" (陣列，0~6 條)：從本期情報中萃取出的高槓桿商業/技術訊號（描述跨來源共同模式、行為/技術/市場變革，非單文摘要）。每條物件結構如下：
     * "title": 訊號精煉標題
     * "summary": 商業/技術實質事實（約 80~120 字，嚴禁空話）
     * "why_it_matters": 商業價值、替代成本或防禦意涵
     * "novelty": 只能為 "NEW"、"KNOWN" 或 "EVOLVING" 之一
     * "novelty_basis": 依據歷史情報庫說明判定為 NEW/KNOWN/EVOLVING 之事實依據
     * "source_ids": 支持此訊號的本期授權代碼清單，例如 ["S01", "S02"]（不可為空，且嚴禁引用歷史代碼作為來源）
"""
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://github.com/Eujenz/QuietRadar",
            "X-Title": "QuietRadar"
        }

        # 嚴格依使用者指定順序，僅嘗試兩款免費模型：
        # 第一順位：nvidia/nemotron-3-ultra-550b-a55b:free
        # 第二順位：nvidia/nemotron-3-super-120b-a12b:free
        models_to_try = [
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3-super-120b-a12b:free"
        ]

        # 設定 240 秒寬裕逾時，避免大模型冷啟動或長推導斷線
        client_timeout = httpx.Timeout(240.0, connect=30.0, read=240.0, write=30.0)

        for current_model in models_to_try:
            temperature = 0.2
            messages_list = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"請直接以繁體中文進行商業排雷審計與降噪蒸餾，直接輸出包含 overview 與 signals 的合法 JSON 物件，凡引用處僅能標註候選文章的 Source ID 代碼 (例如 [S01])，嚴禁輸出任何 URL 網址或舊式角注格式，嚴禁在 JSON 前後輸出任何草稿或非 JSON 文字：\n\n候選文章列表：\n{json.dumps(articles_payload, ensure_ascii=False)}"}
            ]

            # 最終防禦：對所有 messages[*].content 執行全域 URL Scrubbing
            for msg in messages_list:
                if isinstance(msg, dict) and "content" in msg:
                    msg["content"] = scrub_urls(msg["content"])

            payload = {
                "model": current_model,
                "messages": messages_list,
                "temperature": temperature,
                "max_tokens": 16384,
                "response_format": {"type": "json_object"},
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
                            elif resp.status_code == 400 and "response_format" in payload:
                                logger.warning(f"⚠️ 模型 [{current_model}] 不支援 response_format=json_object，自動移除並重試...")
                                payload.pop("response_format", None)
                                continue
                            elif resp.status_code != 200:
                                logger.warning(f"⚠️ 模型 [{current_model}] 回應狀態碼異常: {resp.status_code}")
                                break

                            # 串流讀取 SSE Token
                            finish_reason = None
                            for line in resp.iter_lines():
                                if not line or not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                    if "error" in chunk:
                                        err_msg = chunk["error"].get("message", "")
                                        logger.warning(f"⚠️ 串流收到服務端錯誤: {err_msg}")
                                        break
                                    choices = chunk.get("choices", [])
                                    if choices:
                                        delta = choices[0].get("delta", {})
                                        content = delta.get("content", "")
                                        if content:
                                            raw_text_chunks.append(content)
                                        fr = choices[0].get("finish_reason")
                                        if fr:
                                            finish_reason = fr
                                except Exception:
                                    pass

                    raw_text = "".join(raw_text_chunks).strip()
                    if not raw_text:
                        logger.warning(f"⚠️ 模型 [{current_model}] 串流回傳為空，嘗試重試...")
                        continue

                    # 防截斷熔斷機制：若明確回傳長度超限，捨棄殘稿切換重試/備援
                    if finish_reason == "length":
                        logger.warning(f"⚠️ 模型 [{current_model}] 輸出遭遇長度截斷 (finish_reason=length)，捨棄殘稿，重試或切換備援...")
                        continue

                    logger.info(f"📝 LLM [{current_model}] 串流接收完成，共 {len(raw_text)} 字元 (finish_reason={finish_reason})")
                    
                    # 1. 深度清洗思維鏈標籤 (<think>...</think>, <reasoning>...</reasoning>)
                    cleaned = re.sub(r'<think>[\s\S]*?(?:<\/think>|$)', '', raw_text, flags=re.IGNORECASE)
                    cleaned = re.sub(r'<reasoning>[\s\S]*?(?:<\/reasoning>|$)', '', cleaned, flags=re.IGNORECASE).strip()
                    logger.info(f"🔍 [LLM 內容預覽]:\n{cleaned[:500]}")

                    FORBIDDEN_HEADING_KEYWORDS = [
                        "深度排雷", "戳破願景", "表面熱鬧", "落地摩擦力", "落地實體摩擦力",
                        "巨頭抹殺", "巨頭抹殺路徑", "生死裁決", "特種部隊突圍", "最小切入點",
                        "維度一", "維度二", "維度三", "維度四", "維度五", "SOS 4.2 生死裁決"
                    ]

                    def is_overview_valid_and_complete(ov_text: str) -> bool:
                        if not ov_text:
                            return False
                        stripped = ov_text.strip()
                        # 若命中保真安全閥宣告，即使短字數也屬合法
                        if "海面無魚保真安全閥" in stripped or "本期無合格 Tiny Wedge" in stripped:
                            return True
                        # 一般專欄內文長度至少需 200 字以上
                        if len(stripped) < 200:
                            return False

                        # 檢查標題或粗體中是否偷渡思考綱領的死板禁語
                        header_and_bold_lines = re.findall(r'(?:^#{1,6}\s*.*$|\*\*.*?\*\*)', stripped, flags=re.MULTILINE)
                        for hb in header_and_bold_lines:
                            for kw in FORBIDDEN_HEADING_KEYWORDS:
                                if kw in hb:
                                    logger.warning(f"⚠️ 偵測到標題/粗體中包含死板思考綱領禁語: 【{kw}】 (出現於: {hb[:40]})，判定驗證未通過，觸發重試")
                                    return False

                        # 檢查結尾閉合：包含標準標點、引號、括號、Markdown 標籤等
                        is_closed = bool(re.search(r'[。！？\.\!）」』\"\'\*\*】\)\n\]]\s*$', stripped))
                        if not is_closed:
                            logger.warning(f"⚠️ overview 結尾疑似未正常閉合（末尾字元: {stripped[-30:]}），判定為遭遇微幅截斷")
                            return False
                        return True

                    def _extract_distill_dict(data_obj: Any) -> Optional[Dict[str, Any]]:
                        """安全封裝解析後的資料結構，支援 tiny_wedge 純文字或欄位容錯"""
                        if not isinstance(data_obj, dict):
                            return None
                        ov = data_obj.get("overview", "")
                        # 容錯處理：若模型將 tiny_wedge 作為獨立欄位輸出，乾淨串接避免寫死機械標題
                        tw = data_obj.get("tiny_wedge")
                        if tw and isinstance(tw, str) and tw.strip():
                            if tw.strip() not in ov:
                                ov = f"{ov.strip()}\n\n{tw.strip()}"
                        
                        if not is_overview_valid_and_complete(ov):
                            return None

                        res = {"overview": ov}
                        if "signals" in data_obj and isinstance(data_obj["signals"], list):
                            res["signals"] = data_obj["signals"]
                        return res

                    # 2. 優先提取 Markdown JSON 代碼塊 ```json ... ```
                    block_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', cleaned, flags=re.IGNORECASE)
                    if block_match:
                        try:
                            data = json.loads(block_match.group(1))
                            res = _extract_distill_dict(data)
                            if res and res.get("overview"):
                                return res
                        except Exception:
                            pass

                    # 3. 最外層 JSON 物件比對
                    match_obj = re.search(r'\{[\s\S]*\}', cleaned)
                    if match_obj:
                        try:
                            data = json.loads(match_obj.group(0))
                            res = _extract_distill_dict(data)
                            if res and res.get("overview"):
                                return res
                        except Exception:
                            pass

                    # 3.5 容錯 JSON 提取（防禦尾部被微幅裁切的情況）
                    ov_m = re.search(r'"overview"\s*:\s*"((?:[^"\\]|\\.)*)', cleaned)
                    if ov_m:
                        try:
                            raw_ov_escaped = ov_m.group(1)
                            try:
                                raw_ov = json.loads(f'"{raw_ov_escaped}"')
                            except Exception:
                                raw_ov = raw_ov_escaped.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                            
                            if raw_ov.strip() and is_overview_valid_and_complete(raw_ov):
                                return {"overview": raw_ov}
                        except Exception:
                            pass

                except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                    wait_sec = backoff_base * attempt
                    logger.warning(f"⚠️ 模型 [{current_model}] 網路/超時異常: {net_err}，等待 {wait_sec} 秒...")
                    time.sleep(wait_sec)
                except Exception as e:
                    logger.warning(f"⚠️ 模型 [{current_model}] 調用異常: {e}，切換下一個模型...")
                    break

        logger.error("❌ 第一順位與第二順位模型皆已嘗試完畢，未產出合規內文。")
        return {"overview": ""}


def distill_articles(
    candidates: List[Dict[str, Any]],
    profile: Dict[str, Any],
    top_k: int = 7,
    custom_prompt: Optional[str] = None,
    snippet_length: int = 800,
    historical_context: Optional[str] = None
) -> Dict[str, Any]:
    """
    便利封裝函式，調用 SimpleLLMDistiller 進行文章蒸餾。
    若未指定 custom_prompt 且找不到模板檔案時，以 EDITORIAL_SYSTEM_PROMPT 作為安全備援。
    """
    distiller = SimpleLLMDistiller()
    return distiller.distill(
        candidates=candidates,
        profile=profile,
        top_k=top_k,
        custom_prompt=custom_prompt,
        snippet_length=snippet_length,
        historical_context=historical_context
    )


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
    (r"一人操盤手", "獨立開發者"),
    (r"單人操盤手", "獨立開發者"),
    (r"操盤手", "獨立開發者"),

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
    確定性過濾：清除大陸黑話與轉換為台灣在地用語，並全域轉換為台灣繁體慣用語 (OpenCC s2twp)。
    """
    if not text:
        return text
    # 1. 先用 OpenCC 轉換簡體字並做標準台灣詞彙轉換 (s2twp: Simplified to Traditional Taiwan with Phrases)
    text = to_taiwan_traditional(text)
    # 2. 再套用自訂大廠黑話與特定科技用語洗滌字典
    for pattern, repl in BUZZWORD_REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    return text

# ==========================================
# 3.5 Stage 2: speak-human-tw 本機確定性語言洗滌模組
# ==========================================
class SpeakHumanCleaner:
    def __init__(self):
        pass

    def clean(self, raw_overview: str) -> str:
        """
        純本機確定性語言洗滌器：
        1. OpenCC 台灣繁體正字化 (s2twp)
        2. 大陸黑話與自嗨詞彙正則字典替換 (BUZZWORD_REPLACEMENTS)
        3. 自動清除機械小標籤殘留與段落排版標準化
        完全零網路延遲，100% 免疫 API 限速、逾時與二次截斷。
        """
        if not raw_overview or not raw_overview.strip():
            return raw_overview
            
        logger.info("✨ 啟動本機 [speak-human-tw] 確定性語言洗滌器 (零延遲、免二次呼叫)...")
        cleaned_text = raw_overview.strip()
        # 清除代碼塊包裹
        if cleaned_text.startswith("```"):
            cleaned_text = re.sub(r'^```[a-zA-Z]*\n?', '', cleaned_text)
            cleaned_text = re.sub(r'\n?```$', '', cleaned_text).strip()
        # 確保小標題前後空行標準化
        cleaned_text = re.sub(r'([^\n])\n(【[^\n]+】)', r'\1\n\n\2', cleaned_text)
        cleaned_text = re.sub(r'(【[^\n]+】)\n([^\n])', r'\1\n\n\2', cleaned_text)
        # 確定性黑話與台灣用語洗滌保險
        cleaned_text = sanitize_taiwan_terms(cleaned_text)
        return cleaned_text


def load_prompt_template(config: Dict[str, Any]) -> str:
    """
    自 prompts/ 目錄動態讀取指定的提示詞模板；若找不到則回退至 sources.yaml 中的 custom_prompt 或 EDITORIAL_SYSTEM_PROMPT。
    """
    settings = config.get("pipeline_settings", {})
    template_name = settings.get("active_prompt_template", "editorial")
    
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
        
    return EDITORIAL_SYSTEM_PROMPT

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
    digit_map = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

    def repl(match):
        raw_num = match.group(1).translate(digit_map)
        url = match.group(2)
        sup_num = raw_num.translate(superscript_map)
        if style == "A":
            return f"[{sup_num}]({url})"
        elif style == "B":
            return f"[⁽{sup_num}⁾]({url})"
        else:
            return f"[[{sup_num}]]({url})"

    return re.sub(r'\[+([0-9⁰¹²³⁴⁵⁶⁷⁸⁹]+)\]+\((https?://[^\)]+)\)', repl, text)


def iter_strings(value: Any):
    """遞迴產生字典、列表或純文字中所有的字串值"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from iter_strings(v)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            yield from iter_strings(v)

# 統一 Source-ID 解析規格常數
SUSPECTED_SOURCE_TOKEN_RE = re.compile(r'\[\s*([sS][\-_ ]*?\d[^\]]*?)\s*\]')
VALID_SOURCE_ID_RE = re.compile(r'^S(?:0[1-9]|[1-9]\d{1,2})$')

def extract_source_tokens(text: str) -> List[Tuple[str, str, bool]]:
    r"""
    從文本中掃描所有「疑似 Source-ID」引用標記。
    第一層過濾：只捕捉符合疑似特徵（以 [s/S 開頭並接續數字）的 token。
    第二層過濾：判斷是否嚴格符合標準格式 ^S(0[1-9]|[1-9]\d{1,2})$（例如 S01..S99, S100..S999）。
    回傳清單，每個元素為 (full_raw_token, inner_token, is_valid_format)。
    例:
      '[S01]'   -> ('[S01]', 'S01', True)
      '[s01]'   -> ('[s01]', 's01', False)
      '[S-01]'  -> ('[S-01]', 'S-01', False)
      '[S_01]'  -> ('[S_01]', 'S_01', False)
      '[S001]'  -> ('[S001]', 'S001', False)
      '[S1]'    -> ('[S1]', 'S1', False)
      '[S01x]'  -> ('[S01x]', 'S01x', False)
      '[S 01]'  -> ('[S 01]', 'S 01', False)
    普通 Markdown 如 [Summary], [Source], [Strategy], [System Design], [1], [2] 不會被捕捉。
    """
    if not text:
        return []
    results = []
    for m in SUSPECTED_SOURCE_TOKEN_RE.finditer(text):
        raw = m.group(0)
        inner = m.group(1).strip()
        is_valid = bool(VALID_SOURCE_ID_RE.match(inner))
        results.append((raw, inner, is_valid))
    return results

def validate_llm_output(data: Dict[str, Any], candidate_map: Dict[str, Any]) -> None:
    r"""
    全域 Output Validator (嚴格 Provenance 檢查)：
    1. 遞迴掃描 data 中所有字串值，嚴禁出現 URL (http://, https://, www.)。
    2. 嚴禁出現舊式帶連結的角注格式 (例如 [[1]](http..) 或 [1](http..))，但不誤殺正常 Markdown 列表 [1]。
    3. 檢查 overview 中所有的 [Sxx] 標籤：格式必須為合規格式，且必須全數存在於 candidate_map 中。
       若出現畸形或未授權 token（例如 [S-01], [s01], [S1], [S001], [S99]），一律拒絕！
    4. 若有 signals 欄位，檢查每個 signal 的 source_ids 是否非空、格式合規且皆在 candidate_map 中。
    若違反任何一項，立即拋出 CitationProvenanceError，絕不嘗試任何修復！
    """
    if not isinstance(data, dict):
        raise CitationProvenanceError("LLM 輸出非合法 JSON 物件")

    url_pat = re.compile(r'(?i)(?:https?://|www\.)\S+')
    legacy_pat = re.compile(r'\[+([0-9⁰¹²³⁴⁵⁶⁷⁸⁹]+)\]+\([^\)]+\)')

    # 1 & 2: 全域遞迴字串檢查 (先檢查舊式角注格式，再檢查裸 URL)
    for s in iter_strings(data):
        if legacy_pat.search(s):
            m = legacy_pat.search(s).group(0)
            raise CitationProvenanceError(f"LLM 輸出違反契約：檢測到舊式帶連結引用格式 [{m}]")
        if url_pat.search(s):
            m = url_pat.search(s).group(0)
            raise CitationProvenanceError(f"LLM 輸出違反契約：檢測到非法 URL 輸出 [{m}]")

    # 3. 檢查 overview 中的 [Sxx] 標籤
    overview = data.get("overview", "")
    if overview:
        for raw_token, inner_token, is_valid in extract_source_tokens(overview):
            if not is_valid:
                raise CitationProvenanceError(f"LLM 輸出違反契約：檢測到不合規的 Source ID 格式 [{inner_token}]")
            if inner_token not in candidate_map:
                raise CitationProvenanceError(f"LLM 輸出違反契約：檢測到未授權的 Source ID [{inner_token}]")

    # 4. 若有 signals，檢查合法性與來源
    signals = data.get("signals")
    if signals is not None:
        from signal_ledger import validate_signals_output
        validate_signals_output(signals, candidate_map, VALID_SOURCE_ID_RE)

def extract_source_ids(overview: str, candidate_map: Dict[str, Any]) -> List[str]:
    """
    從 overview 依正文首次出現順序萃取唯一合法的 [Sxx] 清單。
    使用與 validate_llm_output 相同的 extract_source_tokens 解析規範。
    """
    if not overview:
        return []
    seen = set()
    ordered_ids = []
    for raw_token, inner_token, is_valid in extract_source_tokens(overview):
        if is_valid and inner_token in candidate_map and inner_token not in seen:
            seen.add(inner_token)
            ordered_ids.append(inner_token)
    return ordered_ids

def resolve_overview_citations(
    overview: str, 
    candidate_map: Dict[str, Any]
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    將已通過驗證的正文 [Sxx] 依首次出現順序替換為角注 [¹](canonical_url)、[²](canonical_url)...
    並生成專供 Presentation Layer 使用的 citation_sources 清單 (1..K)。
    使用共用之 SUSPECTED_SOURCE_TOKEN_RE 與 VALID_SOURCE_ID_RE 規則。
    """
    if not overview or not overview.strip():
        return overview, []

    ordered_ids = extract_source_ids(overview, candidate_map)
    superscript_map = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

    token_to_idx = {s_id: idx for idx, s_id in enumerate(ordered_ids, 1)}
    citation_sources = []

    for idx, s_id in enumerate(ordered_ids, 1):
        cand = candidate_map[s_id]
        citation_sources.append({
            "citation_number": idx,
            "source_id": s_id,
            "article_key": cand["article_key"],
            "title": sanitize_taiwan_terms(cand["title"]),
            "original_url": cand["canonical_url"],
            "source_name": sanitize_taiwan_terms(cand["source_name"]),
            "is_serendipity": cand.get("is_serendipity", False)
        })

    def repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        if VALID_SOURCE_ID_RE.match(inner):
            idx = token_to_idx.get(inner)
            if idx is not None:
                cand = candidate_map.get(inner, {})
                url = cand.get("canonical_url", "")
                sup_idx = str(idx).translate(superscript_map)
                return f"[{sup_idx}]({url})"
        return m.group(0)

    resolved_overview = SUSPECTED_SOURCE_TOKEN_RE.sub(repl, overview)
    resolved_overview = sanitize_taiwan_terms(resolved_overview)

    logger.info(f"📐 [引用解析模組] 完成解析：正文共標定 {len(ordered_ids)} 則角注，建立 {len(citation_sources)} 則 citation_sources")
    return resolved_overview, citation_sources


def format_newsletter_markdown(
    items: List[Dict[str, Any]],
    template: Optional[Dict[str, Any]] = None,
    overview: str = "",
    signals: Optional[List[Dict[str, Any]]] = None,
    now_str: Optional[str] = None
) -> str:
    """
    統一生成電子報 Markdown 內容，供本地檔案存檔 (latest_newsletter.md) 與 Bark 推播共用，確保格式 100% 一致。
    """
    if not now_str:
        now_str = get_taipei_now().strftime("%Y-%m-%d %H:%M")
    tpl = template or {}
    
    header_tpl = tpl.get("header", "# ⚡ QuietRadar 降噪科技電子報\n> 出刊時間：{time} | 本期精選：{count} 則\n---")
    overview_tpl = tpl.get("overview", "💡 【今日觀點】\n\n{overview}\n\n---")
    group_tpl = tpl.get("group_header", "## 📰 【{source}】({count} 則)")
    item_tpl = tpl.get("item_format", "{index}. [{title}]({url})")
    footer_tpl = tpl.get("footer", "---")

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

    # 3A 交付層：優雅渲染結構化 Signal 清單 (若當期無訊號則乾淨略過，不產生破版空標題)
    if signals:
        lines.append("## 🎯 【今日情報訊號】")
        lines.append("")
        for idx, sig in enumerate(signals, 1):
            s_title = sanitize_taiwan_terms(sig.get("title", "").strip())
            s_novelty = sig.get("novelty", "NEW").strip().upper()
            s_summary = sanitize_taiwan_terms(sig.get("summary", "").strip())
            s_why = sanitize_taiwan_terms(sig.get("why_it_matters", "").strip())
            s_asset = sanitize_taiwan_terms(sig.get("asset_potential", "").strip())
            s_interpretation = sanitize_taiwan_terms(sig.get("interpretation", "").strip())
            s_unknowns = sanitize_taiwan_terms(sig.get("unknowns", "").strip())
            s_sources = sig.get("sources", [])

            lines.append(f"### {idx}. [{s_novelty}] {s_title}")
            lines.append(f"- **實質事實**：{s_summary}")
            lines.append(f"- **商業價值**：{s_why}")
            if s_asset:
                lines.append(f"- **資產累積**：{s_asset}")
            if s_interpretation:
                lines.append(f"- **戰略推演**：{s_interpretation}")
            if s_unknowns:
                lines.append(f"- **盲區疑問**：{s_unknowns}")
            if s_sources:
                src_links = []
                for src in s_sources:
                    src_name = sanitize_taiwan_terms(src.get("source_name", "來源").strip())
                    src_title = sanitize_taiwan_terms(src.get("title", "").strip())
                    src_url = src.get("canonical_url", "").strip()
                    if src_url:
                        src_links.append(f"[{src_name}: {src_title}]({src_url})")
                    else:
                        src_links.append(f"{src_name}: {src_title}")
                lines.append(f"- **佐證來源**：{'、'.join(src_links)}")
            lines.append("")
        lines.append("---")
        lines.append("")

    # 判斷是否具備文內引用序號（若有 overview 且包含論文式注釋）
    # 依序渲染 1..N 項條目，保持 100% 論文式嚴格對齊
    has_citations = bool(overview and re.search(r'\[+([0-9⁰¹²³⁴⁵⁶⁷⁸⁹]+)\]+\(https?://', overview))
    
    if has_citations:
        global_idx = 1
        for item in items:
            title = sanitize_taiwan_terms(item.get("title", "").strip())
            url = item.get("original_url", "").strip()
            source_name = sanitize_taiwan_terms(item.get("source_name", "精選情報").strip())
            # 徹底移除 [跨界靈感]、[跨界漫遊] 等贅字，純粹保留 EMOJI ✨
            title = re.sub(r'\[跨界[^\]]*\]\s*', '', title).strip()
            if item.get("is_serendipity"):
                if not title.startswith("✨"):
                    title = f"✨ {title}"
            else:
                title = re.sub(r'^✨\s*', '', title).strip()

            item_text = item_tpl.replace("{index}", str(global_idx)).replace("{title}", title).replace("{url}", url)
            if "{source}" in item_tpl:
                item_text = item_text.replace("{source}", source_name)
            elif source_name:
                item_text = f"{item_text} · {source_name}"

            # 移除無效 html <br> 標籤，純粹使用 Markdown 標準換行語法
            item_text = re.sub(r'<\s*br\s*/?\s*>', '', item_text).strip()
            lines.append(f"{item_text}")
            lines.append("")
            global_idx += 1
    else:
        # 無引用或無觀點時，依來源分組呈現
        grouped_by_source: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            src = sanitize_taiwan_terms(item.get("source_name", "精選情報"))
            grouped_by_source.setdefault(src, []).append(item)

        global_idx = 1
        for source_name, source_items in grouped_by_source.items():
            grp_text = group_tpl.replace("{source}", source_name).replace("{count}", str(len(source_items))).strip()
            lines.append(grp_text)
            lines.append("")
            for item in source_items:
                title = sanitize_taiwan_terms(item.get("title", "").strip())
                url = item.get("original_url", "").strip()
                title = re.sub(r'\[跨界[^\]]*\]\s*', '', title).strip()
                if item.get("is_serendipity"):
                    if not title.startswith("✨"):
                        title = f"✨ {title}"
                else:
                    title = re.sub(r'^✨\s*', '', title).strip()
                item_text = item_tpl.replace("{index}", str(global_idx)).replace("{title}", title).replace("{url}", url).replace("{source}", source_name).strip()
                item_text = re.sub(r'<\s*br\s*/?\s*>', '', item_text).strip()
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


def generate_newsletter_file(
    items: List[Dict[str, Any]],
    filepath: str = "latest_newsletter.md",
    template: Optional[Dict[str, Any]] = None,
    overview: str = "",
    signals: Optional[List[Dict[str, Any]]] = None,
    archive: bool = True
) -> str:
    content = format_newsletter_markdown(items, template=template, overview=overview, signals=signals)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"📄 已成功生成最新電子報檔案: {filepath}")

    if archive:
        # 資產封存機制 (Archive Asset)：
        # 每一期產出的電子報都是寶貴的智力資產，自動按時間戳封存至 data/archive/
        try:
            archive_dir = os.path.join("data", "archive")
            os.makedirs(archive_dir, exist_ok=True)
            ts_now = get_taipei_now().strftime("%Y-%m-%d_%H%M%S")
            archive_path = os.path.join(archive_dir, f"newsletter_{ts_now}.md")
            with open(archive_path, "w", encoding="utf-8") as af:
                af.write(content)
            logger.info(f"📦 已將本期電子報作為資產封存至: {archive_path}")
        except Exception as e:
            logger.warning(f"⚠️ 封存電子報至 data/archive 失敗: {e}")

    return content


# QuietRadar 應用層保守安全上限 (UTF-8 位元組，確保遠低於 Apple APNs 4KB / 4096-byte 負載限制)
DEFAULT_BARK_CHUNK_CEILING = 2400


def chunk_markdown_lossless(content: str, max_chunk_bytes: int = DEFAULT_BARK_CHUNK_CEILING) -> List[str]:
    """
    通用、嚴格保證每段都不超過 max_chunk_bytes 的確定性無失真分段器。

    切分優先級：
    1. UTF-8 byte safety (由 Python str 字元切片與 UTF-8 位元組檢查保證，絕無 UnicodeDecodeError)
    2. 無失真 (Lossless: "".join(chunks) == content)
    3. 優先避免切斷 Markdown link [title](url)
    4. 段落邊界 (\\n\\n)
    5. 換行邊界 (\\n)
    6. 句子標點邊界 (。、！、？、；)
    7. 硬位元組邊界作為最終安全墊（絕不丟棄字符）
    """
    if not content:
        return []

    def get_bytes(s: str) -> int:
        return len(s.encode("utf-8"))

    if get_bytes(content) <= max_chunk_bytes:
        return [content]

    # 預先尋找所有 Markdown link 的區間 [start, end)
    link_pattern = re.compile(r'\[[^\]]+\]\([^\)]+\)')
    link_spans = [m.span() for m in link_pattern.finditer(content)]

    def is_inside_link(idx: int) -> Optional[Tuple[int, int]]:
        for start, end in link_spans:
            if start < idx < end:
                return (start, end)
        return None

    chunks = []
    pos = 0
    total_len = len(content)

    while pos < total_len:
        # 若剩餘所有內容已小於等於 max_chunk_bytes，直接作為最後一個 chunk
        rem_bytes = get_bytes(content[pos:])
        if rem_bytes <= max_chunk_bytes:
            chunks.append(content[pos:])
            break

        # 1. 透過二分搜尋找到在 content[pos:] 中，位元組數 <= max_chunk_bytes 的最大字元數
        low = 1
        high = total_len - pos
        max_char_idx = low
        while low <= high:
            mid = (low + high) // 2
            if get_bytes(content[pos:pos + mid]) <= max_chunk_bytes:
                max_char_idx = mid
                low = mid + 1
            else:
                high = mid - 1

        hard_cut = pos + max_char_idx
        # 門檻：至少前進 20% 的字元數，避免在極早的位置斷開產生碎片
        min_advance = max(1, int(max_char_idx * 0.2))

        # 2. 尋找最佳語義切分點
        chosen_cut = None

        # 2a. 段落邊界 (\n\n)
        p_para = content.rfind("\n\n", pos + min_advance, hard_cut)
        if p_para != -1:
            chosen_cut = p_para + 2
        else:
            # 2b. 單換行邊界 (\n)
            p_nl = content.rfind("\n", pos + min_advance, hard_cut)
            if p_nl != -1:
                chosen_cut = p_nl + 1
            else:
                # 2c. 句子標點邊界
                best_punc = -1
                punc_len = 0
                for punc in ["。", "！", "？", "；\n", "；"]:
                    p_punc = content.rfind(punc, pos + min_advance, hard_cut)
                    if p_punc > best_punc:
                        best_punc = p_punc
                        punc_len = len(punc)
                if best_punc != -1:
                    chosen_cut = best_punc + punc_len

        # 若找到語義切分點，檢查是否剛好落在 Markdown link 內部
        if chosen_cut is not None:
            span = is_inside_link(chosen_cut)
            if span:
                l_start, l_end = span
                if l_start > pos:
                    chosen_cut = l_start
                else:
                    chosen_cut = None

        # 3. 若無合適語義切分點，使用 hard_cut，但檢查 Markdown link 保護
        if chosen_cut is None:
            span = is_inside_link(hard_cut)
            if span:
                l_start, l_end = span
                if l_start > pos:
                    chosen_cut = l_start
                else:
                    chosen_cut = hard_cut
            else:
                chosen_cut = hard_cut

        # 4. 防禦性確保必須有所進展
        if chosen_cut <= pos:
            chosen_cut = hard_cut

        chunks.append(content[pos:chosen_cut])
        pos = chosen_cut

    return chunks


def split_markdown_for_bark(content: str, max_chunk_bytes: int = DEFAULT_BARK_CHUNK_CEILING) -> List[Dict[str, Any]]:
    """
    將超長 Markdown 電子報依據 UTF-8 位元組大小（以 DEFAULT_BARK_CHUNK_CEILING 為保守安全上限）
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

    raw_chunks = chunk_markdown_lossless(content, max_chunk_bytes=max_chunk_bytes)
    total_count = len(raw_chunks)

    result = []
    for i, c in enumerate(raw_chunks):
        is_first = (i == 0)
        if total_count == 1:
            c_type = "full"
        elif is_first:
            c_type = "overview"
        elif "## 📰" in c or re.search(r'\n(?:\[1\]|1\.)\s*\[', c):
            c_type = "sources"
        else:
            c_type = "continuation"

        result.append({
            "page": i + 1,
            "total": total_count,
            "is_first": is_first,
            "type": c_type,
            "markdown": c
        })

    return result


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

    def send_digest(self, items: List[Dict[str, Any]], template: Optional[Dict[str, Any]] = None, overview: str = "", signals: Optional[List[Dict[str, Any]]] = None, full_markdown: Optional[str] = None) -> bool:
        if not self.device_keys:
            logger.warning("⚠️ 未配置有效的 BARK_DEVICE_KEY，跳過手機推播")
            return True

        if not items:
            logger.info("ℹ️ 無精選文章，不觸發推播")
            return True

        # 若未直接傳入已格式化的 full_markdown，則調用統一格式化函式生成
        body_markdown = full_markdown or format_newsletter_markdown(items, template=template, overview=overview, signals=signals)
        chunks = split_markdown_for_bark(body_markdown, max_chunk_bytes=DEFAULT_BARK_CHUNK_CEILING)

        now_str = get_taipei_now().strftime("%Y-%m-%d %H:%M")
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
                    "markdown": c_md.strip(),
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
        return False
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    sources = config.get("sources", [])
    profile = config.get("profile", {})
    pipeline_settings = config.get("pipeline_settings", {})
    custom_prompt = load_prompt_template(config)

    # 2. 抓取文章（傳入 profile 與 pipeline_settings 執行標題前置漏斗過濾）
    raw_articles, fetch_stats = fetch_sources(sources, profile=profile, pipeline_settings=pipeline_settings, return_stats=True)
    logger.info(f"📥 抓取與漏斗篩選完成，共取得 {len(raw_articles)} 篇候選文章")

    # 全數訂閱來源連線失敗判定 (Total RSS Failure)
    if fetch_stats["sources_tried"] > 0 and fetch_stats["successful_sources"] == 0:
        logger.error(f"❌ 全數 RSS 訂閱源連線失敗 (0/{fetch_stats['sources_tried']} 成功)，判定為重大連線中斷，中斷任務且不發布空報告。")
        if not test_mode:
            return False

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
            return True

    # 讀取研讀深度與候選池上限設定
    pipeline_settings = config.get("pipeline_settings", {})
    max_pool = pipeline_settings.get("max_candidate_pool", 15)
    snippet_len = pipeline_settings.get("content_snippet_length", 900)
    serendipity_enabled = pipeline_settings.get("serendipity_enabled", True)
    serendipity_ratio = pipeline_settings.get("serendipity_ratio", 0.28)
    serendipity_quota = pipeline_settings.get("serendipity_quota")
    if serendipity_quota is None:
        # 動態比例：若總池 30 篇，保障 28% = 8~9 篇跨界
        serendipity_quota = max(2, int(max_pool * serendipity_ratio)) if serendipity_enabled else 0

    # 槓鈴策略配額組裝 (Barbell Candidate Pool Assembly)
    # 對核心與跨界文章分別執行信源公平輪詢交錯排序 (Round-Robin)，杜絕前序信源霸佔整個候選池
    core_articles = round_robin_interleave([a for a in unprocessed if not a.get("is_serendipity", False)])
    serendipity_articles = round_robin_interleave([a for a in unprocessed if a.get("is_serendipity", False)])

    # 跨界靈感蓄水池保障 (Serendipity Longevity Reservoir)：
    # 跨界文章（農業科技、商業周刊、經理人、arXiv）發文頻率通常比高頻科技部落格慢得多。
    # 若本輪未讀的新跨界文章不足 serendipity_quota，自動從本次爬取的既有跨界文章 (raw_articles) 補足！
    # 確保每次出刊永遠具備足額的跨界靈感素材進行撞擊！
    if len(serendipity_articles) < serendipity_quota:
        seen_urls = {a["url"] for a in serendipity_articles}
        raw_cross = round_robin_interleave([a for a in raw_articles if a.get("is_serendipity", False) and a["url"] not in seen_urls])
        needed_cross = serendipity_quota - len(serendipity_articles)
        supplement_cross = raw_cross[:needed_cross]
        if supplement_cross:
            logger.info(f"💡 [跨界蓄水池啟動] 全新跨界文章僅 {len(serendipity_articles)} 篇，自動從本期爬取之跨界情報中調用 {len(supplement_cross)} 篇補足配額！")
            serendipity_articles.extend(supplement_cross)
            serendipity_articles = round_robin_interleave(serendipity_articles)

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

    # 4. 建立 LLM 隔離上下文 (build_llm_context) 與執行 LLM 蒸餾
    candidate_map, articles_payload = build_llm_context(target_candidates, snippet_length=snippet_len)

    # Step 2: 載入近 30 天歷史 Signals 上下文
    from signal_ledger import (
        load_recent_signals,
        format_historical_signals_context,
        reconcile_and_build_signals,
        save_daily_signals,
        load_daily_signals
    )
    recent_signals = load_recent_signals(days=30, max_signals=20)
    historical_context = format_historical_signals_context(recent_signals)

    max_output_items = pipeline_settings.get("max_output_items", 15)
    distiller = SimpleLLMDistiller()
    distill_res = distiller.distill(
        articles_payload,
        profile,
        top_k=max_output_items,
        custom_prompt=custom_prompt,
        snippet_length=snippet_len,
        historical_context=historical_context
    )
    
    if not isinstance(distill_res, dict) or not distill_res.get("overview") or not distill_res["overview"].strip():
        logger.warning("⚠️ 第一順位與第二順位模型皆未產出合規內文 (overview 為空)，依指示中斷本次任務，不產出報告、不推播、不記錄防重。")
        session.close()
        return False

    # 若大模型產出訊號數量大於 6 條，自動取前 6 條精選訊號以符合規格契約
    if isinstance(distill_res.get("signals"), list) and len(distill_res["signals"]) > 6:
        logger.info(f"ℹ️ LLM 回傳之 signals 數量 ({len(distill_res['signals'])}) 超過 6 條上限，自動保留前 6 條核心訊號以符合規格契約")
        distill_res["signals"] = distill_res["signals"][:6]

    # 4.1 第一次全域嚴格校驗 (Raw Output Validation)
    try:
        validate_llm_output(distill_res, candidate_map)
    except Exception as e:
        if isinstance(e, CitationProvenanceError) or e.__class__.__name__ == "CitationProvenanceError":
            logger.error(f"❌ [Provenance 驗證失敗] LLM 輸出違反來源契約: {e}，中斷任務且絕不產出報告。")
            session.close()
            return False
        raise e

    overview = distill_res["overview"]

    # 4.2 speak-human-tw 語言洗滌器 (若啟用且 overview 非空)
    enable_humanizer = pipeline_settings.get("enable_two_stage_humanizer", True)
    if enable_humanizer and overview:
        cleaner = SpeakHumanCleaner()
        overview = cleaner.clean(overview)

    # 4.3 第二次全域嚴格校驗 (Post-Humanizer Validation)
    try:
        validate_llm_output({"overview": overview}, candidate_map)
    except Exception as e:
        if isinstance(e, CitationProvenanceError) or e.__class__.__name__ == "CitationProvenanceError":
            logger.error(f"❌ [Provenance 驗證失敗] 洗滌後文本標籤受損或違約: {e}，中斷任務且絕不產出報告。")
            session.close()
            return False
        raise e

    # 4.4 引用解析與來源清單建構 (resolve_overview_citations)
    overview, citation_sources = resolve_overview_citations(overview, candidate_map)
    # Step 1: 暫時保持 items == citation_sources，確保電子報渲染相容
    distilled_items = citation_sources

    logger.info(f"🎯 引用解析與來源建構完成：正文引用 {len(citation_sources)} 則來源")

    # Step 2: 處理與組裝 Signals (Reconcile & Build)
    raw_signals = distill_res.get("signals") or []
    today_str = get_taipei_now().strftime("%Y-%m-%d")
    existing_daily = load_daily_signals(today_str)
    reconciled_signals = reconcile_and_build_signals(
        raw_signals=raw_signals,
        candidate_map=candidate_map,
        date_str=today_str,
        existing_daily_signals=existing_daily
    )

    # 存檔至 data/signals/YYYY-MM-DD.json (test_mode 時嚴格跳過磁碟寫入)
    # 核心規章：Ledger persistence MUST succeed before newsletter rendering or Bark delivery!
    try:
        save_daily_signals(reconciled_signals, date_str=today_str, test_mode=test_mode)
        logger.info(f"📊 [Signal Ledger] 本期產出 {len(reconciled_signals)} 則結構化商業/技術訊號")
    except Exception as e:
        logger.error(f"❌ [Signal Ledger 儲存失敗] {e}，中斷任務且絕不產出後續內容。")
        session.close()
        return False

    output_template = config.get("output_template", {})

    # 5. 生成電子報存檔 (latest_newsletter.md)，test_mode 時不寫入 data/archive/
    # 核心規章：Newsletter rendering failure 必須阻止 Bark delivery，但不得 rollback 或修改已成功持久化的 Ledger
    newsletter_content = None
    try:
        newsletter_content = generate_newsletter_file(
            distilled_items,
            template=output_template,
            overview=overview,
            signals=reconciled_signals,
            archive=not test_mode
        )
    except Exception as e:
        logger.error(f"❌ 生成電子報檔案失敗: {e}（已持久化之 Signal Ledger 完好保留，跳過後續 Bark 推播）")
        newsletter_content = None

    try:
        distilled_data = {
            "date": get_taipei_now().strftime("%Y-%m-%d"),
            "generated_at": get_taipei_now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "overview": overview,
            "citation_sources": citation_sources,
            "items": distilled_items,
            "signal_count": len(reconciled_signals),
            "signals": reconciled_signals,
            "time": get_taipei_now().strftime("%Y-%m-%d %H:%M")
        }
        with open("latest_distilled.json", "w", encoding="utf-8") as df:
            json.dump(distilled_data, df, ensure_ascii=False, indent=2)

        # 同步封存 JSON 資料結構 (僅非 test_mode 時封存至 archive)
        if not test_mode:
            archive_dir = os.path.join("data", "archive")
            os.makedirs(archive_dir, exist_ok=True)
            ts_now = get_taipei_now().strftime("%Y-%m-%d_%H%M%S")
            with open(os.path.join(archive_dir, f"distilled_{ts_now}.json"), "w", encoding="utf-8") as adf:
                json.dump(distilled_data, adf, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"⚠️ 寫入或封存 distilled json 失敗: {e}")

    # 6. 發送 Bark 推播（使用完全相同的 Markdown 內容）
    # 核心規章：Bark failure 僅記錄日誌，絕不得修改或破壞 Signal Ledger
    bark_ok = True
    if newsletter_content:
        try:
            notifier = SimpleBarkNotifier()
            bark_ok = notifier.send_digest(
                distilled_items,
                template=output_template,
                overview=overview,
                signals=reconciled_signals,
                full_markdown=newsletter_content
            )
        except Exception as e:
            logger.error(f"❌ Bark 推播過程異常: {e}（Signal Ledger 完好不受影響）")
            bark_ok = False
    else:
        logger.warning("⚠️ 由於電子報生成失敗，跳過 Bark 手機推播（Signal Ledger 資產已安全持久化）")
        bark_ok = False

    # 7. 確定蒸餾推播流程成功後，將本次參與蒸餾的文章指紋寫入資料庫
    if test_mode:
        logger.info("🧪 【測試模式】本次測試不將指紋寫入防重資料庫，確保您可隨時重複測試 OUTPUT！")
    else:
        record_processed_articles(session, target_candidates)
    session.close()

    if not newsletter_content or not bark_ok:
        logger.error("❌ 交付環節遭遇失敗（電子報生成失敗或 Bark 推播失敗），Signal Ledger 已安全持久化，返回非零狀態碼以警示運維。")
        return False

    logger.info("🏁 任務圓滿完成！已成功發送推播並生成 latest_newsletter.md 電子報。")
    return True

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
    llm_model = os.getenv("LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")

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
        ok = run_doctor()
        sys.exit(0 if ok else 1)
    else:
        success = run_pipeline(test_mode=args.test, force=args.force)
        sys.exit(0 if success else 1)
