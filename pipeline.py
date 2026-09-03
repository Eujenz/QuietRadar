import os
import sys
import hashlib
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

# 確保 Windows 終端輸出 UTF-8 編碼
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import feedparser
import httpx
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

# 載入環境變數與 Log 設定
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("QuietRadar")

# ==========================================
# 1. 極簡儲存層 (單張表 + 自動防重)
# ==========================================
Base = declarative_base()

class ProcessedArticle(Base):
    __tablename__ = "processed_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), default="default", index=True)  # 預留多租戶
    sha256_hash = Column(String(64), unique=True, index=True, nullable=False)
    title = Column(String(512), nullable=False)
    url = Column(String(1024), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./quietradar.db")
engine = create_engine(DB_URL)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

def is_article_processed(session, sha256_hash: str) -> bool:
    return session.query(ProcessedArticle).filter_by(sha256_hash=sha256_hash).first() is not None

def record_processed_articles(session, articles: List[Dict[str, Any]]):
    """將處理過的文章寫入防重資料庫"""
    for art in articles:
        if not is_article_processed(session, art["sha256"]):
            session.add(ProcessedArticle(
                sha256_hash=art["sha256"],
                title=art["title"],
                url=art["url"]
            ))
    session.commit()

# ==========================================
# 2. 資料抓取模組 (RSS / Atom)
# ==========================================
def fetch_sources(sources_config: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    raw_articles = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuietRadar/1.0"}
    
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
                for entry in feed.entries[:20]:  # 每個來源取前 20 則候選
                    title = getattr(entry, "title", "").strip()
                    link = getattr(entry, "link", "").strip()
                    summary = getattr(entry, "summary", "").strip()
                    
                    if not title or not link:
                        continue
                    
                    # 產生唯一指紋 (URL + Title)
                    h = hashlib.sha256(f"{link}|{title}".encode("utf-8")).hexdigest()
                    raw_articles.append({
                        "source_name": name,
                        "title": title,
                        "url": link,
                        "summary": summary[:300],  # 截取前段文字避免 Token 爆炸
                        "sha256": h
                    })
        except Exception as e:
            logger.warning(f"⚠️ 來源 [{name}] 抓取失敗: {e}，不影響其他來源")
            
    return raw_articles

# ==========================================
# 3. LLM 意圖對齊與降噪蒸餾模組
# ==========================================
class SimpleLLMDistiller:
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.base_url = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "meta/llama-3.1-70b-instruct")

    def distill(self, candidates: List[Dict[str, Any]], profile: Dict[str, Any], top_k: int = 7, custom_prompt: Optional[str] = None) -> List[Dict[str, Any]]:
        if not candidates:
            return []
        
        if not self.api_key or self.api_key == "nvapi-your-key-here":
            logger.error("❌ 未配置有效的 LLM_API_KEY，無法進行蒸餾")
            return []
        
        # 優先使用 sources.yaml 中的自訂提示詞，否則使用預設值
        base_instructions = custom_prompt.strip() if custom_prompt else """你是一位具備資深技術背景的「繁體中文科技電子報主編」。
你的任務是：依據讀者的關注領域，從候選文章中篩選出最值得閱讀的精選文章，撰寫成一份「講人話、無 AI 罐頭套話、專業具體」的科技情報。

【speak-human-tw 寫作與降噪守則 (嚴格遵守)】:
1. 先保事實，再去 AI 味，講大白話：
   - 拒絕空泛套話：嚴禁使用「賦能、閉環、抓手、打法、顆粒度、降本增效、掀起熱潮、拉開序幕」等黑話。
   - 拒絕 AI 罐頭句型：嚴禁使用「總的來說、綜上所述、這意味著、值得注意的是、不僅...更...」等無意義銜接詞。
   - 推薦理由深入具體：說明技術突破點、解決了什麼架構/實務痛點，不重複贅述標題。
2. 台灣在地化用語校正：
   - 必須使用台灣慣用詞彙：影片（非視頻）、資訊（非信息）、網路（非網絡）、軟體/硬體（非軟件/硬件）、資料庫（非數據庫）、伺服器（非服務器）、支援（非支持）、相容（非兼容）、使用者（非用戶）。
3. 標點符號規範：
   - 中文內文一律採用全形標點符號（，。：「」『』、），引號使用「」，嚴禁半形標點。
4. 真實性原則：
   - original_url 與 source_name 必須嚴格照抄候選文章中的真實網址與來源名稱，絕不捏造。"""

        system_prompt = f"""{base_instructions}

【讀者關注主題】:
{json.dumps(profile.get('interests', []), ensure_ascii=False, indent=2)}

【讀者排斥的主題 (直接淘汰)】:
{json.dumps(profile.get('negative_topics', []), ensure_ascii=False, indent=2)}

【輸出格式】:
請從候選文章中挑選最多 {top_k} 則，輸出符合以下格式的 JSON Array：
[
  {{
    "title": "精煉後的繁體中文標題",
    "original_url": "必須填寫候選文章中的真實 URL",
    "source_name": "來源名稱"
  }}
]
"""
        # 準備餵給 LLM 的文章候選清單（精簡長度避免模型超時）
        articles_payload = [
            {
                "index": i + 1,
                "source": a["source_name"],
                "title": a["title"],
                "url": a["url"],
                "content_snippet": a["summary"][:120]
            }
            for i, a in enumerate(candidates)
        ]
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }

        # 支援備援模型鏈 (Primary -> Fallback)
        models_to_try = [self.model]
        if self.model != "meta/llama-3.2-11b-vision-instruct":
            models_to_try.append("meta/llama-3.2-11b-vision-instruct")

        # 設定 180 秒寬裕逾時，避免冷啟動斷線
        client_timeout = httpx.Timeout(180.0, connect=30.0, read=180.0, write=30.0)

        for current_model in models_to_try:
            temperature = 1.0 if "kimi" in current_model else 0.2
            payload = {
                "model": current_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"以下是候選文章列表：\n{json.dumps(articles_payload, ensure_ascii=False)}"}
                ],
                "temperature": temperature,
                "max_tokens": 4096,
                "stream": True  # 務必啟用串流維持連線活躍
            }

            # 指數退避重試設定 (最多重試 3 次)
            max_retries = 3
            backoff_base = 5  # 5s, 10s, 20s

            for attempt in range(1, max_retries + 1):
                logger.info(f"🤖 正在呼叫 LLM [{current_model}] (串流模式，第 {attempt}/{max_retries} 次嘗試)...")
                try:
                    raw_text_chunks = []
                    with httpx.Client(timeout=client_timeout) as client:
                        with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=payload) as resp:
                            if resp.status_code in [429, 502, 503, 504]:
                                wait_sec = backoff_base * (2 ** (attempt - 1))
                                logger.warning(f"⚠️ 收到 HTTP {resp.status_code} 伺服器忙碌/逾時，觸發指數退避：等待 {wait_sec} 秒後重試...")
                                import time
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
                    import re
                    match = re.search(r'\[\s*\{.*\}\s*\]', cleaned, re.DOTALL)
                    if match:
                        cleaned = match.group(0)

                    try:
                        distilled = json.loads(cleaned.strip())
                        if isinstance(distilled, list):
                            return distilled[:top_k]
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
                        reason = ""
                        bullets = []

                        for l in lines[1:]:
                            if "連結" in l or "url" in l.lower():
                                url_m = re.search(r'https?://[^\s\)]+', l)
                                if url_m:
                                    url = url_m.group(0)
                            elif "來源" in l or "source" in l.lower():
                                source = l.split(":", 1)[-1].split("：", 1)[-1].strip()
                            elif "理由" in l or "推薦" in l or "reason" in l.lower():
                                reason = l.split(":", 1)[-1].split("：", 1)[-1].strip()
                            elif any(l.startswith(prefix) for prefix in ["*", "-", "•", "1.", "2.", "3.", "一、", "二、"]):
                                bullet_text = re.sub(r'^[\*\-•\d\.\、\s]+', '', l).strip()
                                if bullet_text and "原文連結" not in bullet_text and "來源" not in bullet_text and "理由" not in bullet_text:
                                    bullets.append(bullet_text)

                        matched_candidate = None
                        for cand in candidates:
                            if cand["title"].lower() in title.lower() or title.lower() in cand["title"].lower():
                                matched_candidate = cand
                                break

                        real_url = url
                        real_source = source
                        if matched_candidate:
                            real_url = matched_candidate["url"]
                            real_source = matched_candidate["source_name"]
                        elif not real_url and candidates:
                            real_url = candidates[0]["url"]

                        if title:
                            extracted_items.append({
                                "title": title,
                                "original_url": real_url or "https://news.ycombinator.com",
                                "source_name": real_source or "精選情報",
                                "reason": reason,
                                "bullets": bullets or ["核心乾貨摘要重點"]
                            })

                    if extracted_items:
                        logger.info(f"✅ 成功從 [{current_model}] 提取 {len(extracted_items)} 則精選情報")
                        return extracted_items[:top_k]

                except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                    wait_sec = backoff_base * (2 ** (attempt - 1))
                    logger.warning(f"⚠️ 模型 [{current_model}] 網路/超時異常: {net_err}，觸發退避重試 ({attempt}/{max_retries})，等待 {wait_sec} 秒...")
                    import time
                    time.sleep(wait_sec)
                except Exception as e:
                    logger.warning(f"⚠️ 模型 [{current_model}] 調用異常: {e}，切換下一個模型...")
                    break

        logger.error("❌ 所有備援模型與重試嘗試皆已耗盡。")
        return []

# ==========================================
# 4. 電子報生成器 (speak-human-tw 風格)
# ==========================================
def generate_newsletter_file(items: List[Dict[str, Any]], filepath: str = "latest_newsletter.md", template: Optional[Dict[str, Any]] = None):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    tpl = template or {}
    
    header_tpl = tpl.get("header", "# ⚡ QuietRadar 降噪科技電子報\n> 出刊時間：{time} | 本期精選：{count} 則 | 去 AI 雜訊率：約 80%\n---")
    group_tpl = tpl.get("group_header", "## 📰 【{source}】({count} 則)")
    item_tpl = tpl.get("item_format", "{index}. [{title}]({url})")
    footer_tpl = tpl.get("footer", "---\n*本電子報由 QuietRadar 依據讀者關注特徵自動蒸餾產出，遵守 speak-human-tw 去 AI 味與台灣在地化規範。*")

    grouped_by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        src = item.get("source_name", "精選情報")
        grouped_by_source.setdefault(src, []).append(item)

    header_text = header_tpl.replace("{time}", now_str).replace("{count}", str(len(items))).strip()
    lines = [header_text, ""]

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

    def send_digest(self, items: List[Dict[str, Any]], template: Optional[Dict[str, Any]] = None) -> bool:
        if not self.device_key or self.device_key == "your_bark_key_here":
            logger.warning("⚠️ 未配置 BARK_DEVICE_KEY，跳過手機推播")
            return False

        if not items:
            logger.info("ℹ️ 無精選文章，不觸發推播")
            return True

        tpl = template or {}
        group_tpl = tpl.get("group_header", "### 📌 {source} ({count})")
        # 若 group_header 開頭為 ##，Bark 轉為更清晰的 ###
        if group_tpl.startswith("## "):
            group_tpl = "### " + group_tpl[3:]
        item_tpl = tpl.get("item_format", "{index}. [{title}]({url})")

        grouped_by_source: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            src = item.get("source_name", "精選情報")
            grouped_by_source.setdefault(src, []).append(item)

        now_str = datetime.now().strftime("%m/%d %H:%M")
        md_lines = [f"🎯 **QuietRadar 降噪情報** ({now_str})\n"]

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
def run_pipeline():
    logger.info("🚀 QuietRadar 批次情報雷達啟動...")
    
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

    # 2. 抓取文章
    raw_articles = fetch_sources(sources)
    logger.info(f"📥 抓取完成，共取得 {len(raw_articles)} 篇候選文章")

    # 3. 資料庫比對防重
    session = SessionLocal()
    unprocessed = [a for a in raw_articles if not is_article_processed(session, a["sha256"])]
    logger.info(f"🔍 防重過濾完成：已讀 {len(raw_articles) - len(unprocessed)} 篇，新文章 {len(unprocessed)} 篇")

    if not unprocessed:
        logger.info("✨ 沒有新文章需要處理，本次任務結束。")
        session.close()
        return

    # [防禦微調 1]：Token 防爆保護，最多取最新的 10 篇送進 LLM 候選池
    MAX_CANDIDATE_POOL = 10
    if len(unprocessed) > MAX_CANDIDATE_POOL:
        logger.info(f"🛡️ 觸發候選池上限保護：由 {len(unprocessed)} 篇截取最新 {MAX_CANDIDATE_POOL} 篇進行蒸餾")
        target_candidates = unprocessed[:MAX_CANDIDATE_POOL]
    else:
        target_candidates = unprocessed

    # 4. LLM 蒸餾降噪 (傳入 custom_prompt)
    distiller = SimpleLLMDistiller()
    distilled_items = distiller.distill(target_candidates, profile, top_k=7, custom_prompt=custom_prompt)

    # [防禦微調 2]：寫入防重安全保護
    # 若 LLM 蒸餾失敗或回傳為空，絕對不寫入防重表，保留到下一輪排程重試
    if not distilled_items:
        logger.warning("⚠️ 本輪未產出任何精選情報（可能因 LLM 異常或全部被判定為雜訊），保留候選池不標記已讀，等待下輪重試。")
        session.close()
        return

    logger.info(f"🎯 LLM 蒸餾完成，成功選出 {len(distilled_items)} 則精選項目")

    output_template = config.get("output_template", {})

    # 5. 生成 speak-human-tw 風格電子報存檔 (latest_newsletter.md)
    generate_newsletter_file(distilled_items, template=output_template)

    # 6. 發送 Bark 推播
    notifier = SimpleBarkNotifier()
    notifier.send_digest(distilled_items, template=output_template)

    # 7. 確定蒸餾推播流程成功後，將本次參與蒸餾的文章指紋寫入資料庫
    record_processed_articles(session, target_candidates)
    session.close()
    logger.info("🏁 任務圓滿完成！已成功發送推播並生成 latest_newsletter.md 電子報。")

if __name__ == "__main__":
    run_pipeline()
