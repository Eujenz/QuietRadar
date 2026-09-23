import os
import sys
import re
import json
import logging
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple, Set

# 確保 Windows 控制台輸出支援 UTF-8 (Emoji 與繁體中文)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logger = logging.getLogger("QuietRadar.SignalLedger")

VALID_NOVELTY_VALUES: Set[str] = {"NEW", "KNOWN", "EVOLVING"}
DEFAULT_SIGNALS_DIR: str = "data/signals"


def normalize_title_for_matching(title: str) -> str:
    """
    標準化標題以供同日重跑時進行比對：
    移除非字母數字與中文標點符號、轉小寫、去除多餘空白。
    """
    if not title:
        return ""
    # 保留中文字、英數字
    cleaned = re.sub(r'[^\w\u4e00-\u9fff]+', '', title.strip().lower())
    return cleaned


def load_recent_signals(
    signals_dir: str = DEFAULT_SIGNALS_DIR, 
    days: int = 30, 
    max_signals: int = 20,
    reference_date: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """
    從 signals_dir 讀取近 `days` 天內的歷史 Signals。
    依日期由新至舊排序，最多回傳 `max_signals` 則。
    冷啟動時（目錄不存在或無檔案）回傳空清單。
    """
    if not os.path.exists(signals_dir):
        return []

    if reference_date is None:
        reference_date = datetime.now()

    cutoff_date = reference_date - timedelta(days=days)
    recent_signals: List[Dict[str, Any]] = []

    try:
        filenames = sorted(os.listdir(signals_dir), reverse=True)
    except Exception as e:
        logger.warning(f"⚠️ 無法讀取 signals 目錄 {signals_dir}: {e}")
        return []

    date_file_pat = re.compile(r'^(\d{4}-\d{2}-\d{2})\.json$')

    for fname in filenames:
        m = date_file_pat.match(fname)
        if not m:
            continue
        file_date_str = m.group(1)
        try:
            file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
        except ValueError:
            continue

        if file_date < cutoff_date:
            # 檔案按倒序排列，一旦早於 cutoff 可提前終止
            continue

        filepath = os.path.join(signals_dir, fname)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                signals_list = data.get("signals", [])
                for sig in signals_list:
                    if isinstance(sig, dict):
                        recent_signals.append(sig)
                        if len(recent_signals) >= max_signals:
                            return recent_signals
        except Exception as e:
            logger.warning(f"⚠️ 讀取歷史 Signal 檔案 {filepath} 失敗: {e}")
            continue

    return recent_signals


def format_historical_signals_context(recent_signals: List[Dict[str, Any]]) -> str:
    """
    將近 30 天歷史 Signals 壓縮格式化為 Prompt 注入上下文。
    具有嚴格字元上限防護，避免擠壓 LLM 研讀池。
    """
    if not recent_signals:
        return (
            "【歷史情報庫（近 30 天已追蹤訊號）】：\n"
            "（目前為冷啟動階段，尚無歷史訊號記錄。請將所有合規訊號的 novelty 標註為 NEW）\n"
        )

    lines = [
        "【歷史情報庫（近 30 天已追蹤訊號）】：",
        "請參考以下近期已辨識訊號，嚴格評估本期發現是 NEW（新發現）、KNOWN（已存在且無重大進展）還是 EVOLVING（既有訊號出現新變革/策略推進）："
    ]

    for sig in recent_signals:
        sig_id = sig.get("signal_id", "SIG-UNKNOWN")
        date_str = sig.get("date", "")
        title = sig.get("title", "").strip()
        novelty = sig.get("novelty", "NEW")
        summary = sig.get("summary", "").strip()
        if len(summary) > 80:
            summary = summary[:80] + "..."
        lines.append(f"- [{date_str}] {sig_id}: {title} (novelty: {novelty}) — {summary}")

    lines.append("\n【重要原則】：歷史情報僅用於判定 novelty 與撰寫 novelty_basis，絕不可列為本期 source_ids！source_ids 必須為本次候選池中授權之 S01..SN。")
    return "\n".join(lines) + "\n"


def validate_signals_output(
    signals_data: Any, 
    candidate_map: Dict[str, Any],
    valid_source_id_re: Optional[re.Pattern] = None
) -> None:
    """
    驗證 LLM 回傳之 signals 欄位合規性：
    1. 必須為 List，數量在 0~6 條之間。
    2. 每條 Signal 必須包含 title, summary, why_it_matters, novelty, novelty_basis, source_ids。
    3. novelty 必須為 NEW, KNOWN, EVOLVING 之一（大小寫不拘，後續統一規格化）。
    4. 每條 Signal 的 source_ids 不可為空，且每一項均必須在 candidate_map 中。
    """
    if "__main__" in sys.modules and hasattr(sys.modules["__main__"], "CitationProvenanceError"):
        CitationProvenanceError = sys.modules["__main__"].CitationProvenanceError
    else:
        from pipeline import CitationProvenanceError
    from pipeline import iter_strings

    if signals_data is None:
        return

    if not isinstance(signals_data, list):
        raise CitationProvenanceError("LLM 輸出之 signals 必須為清單 (List)")

    if len(signals_data) > 6:
        raise CitationProvenanceError(f"LLM 輸出之 signals 數量超過上限 6 條 (實際: {len(signals_data)} 條)")

    if valid_source_id_re is None:
        valid_source_id_re = re.compile(r'^S(?:0[1-9]|[1-9]\d{1,2})$')

    for idx, sig in enumerate(signals_data, 1):
        if not isinstance(sig, dict):
            raise CitationProvenanceError(f"第 {idx} 條 Signal 格式錯誤：必須為 JSON 物件")

        # 1. source_ids 優先嚴格檢驗 (≥1 個當期授權來源)
        s_ids = sig.get("source_ids")
        if not s_ids or not isinstance(s_ids, list):
            raise CitationProvenanceError(f"Signal 缺少授權來源：source_ids 不可為空 (Signal: {sig.get('title', '')})")

        for sid in s_ids:
            if not isinstance(sid, str) or not valid_source_id_re.match(sid):
                raise CitationProvenanceError(f"Signal 包含不合規的 Source ID 格式 [{sid}] (Signal: {sig.get('title', '')})")
            if sid not in candidate_map:
                raise CitationProvenanceError(f"Signal 引用了未授權的 Source ID [{sid}] (Signal: {sig.get('title', '')})")

        # 2. 檢驗 novelty (若提供)
        if "novelty" in sig:
            raw_novelty = sig.get("novelty", "").strip().upper()
            if raw_novelty not in VALID_NOVELTY_VALUES:
                raise CitationProvenanceError(
                    f"第 {idx} 條 Signal 的 novelty 值 [{sig.get('novelty')}] 不合規，必須為 NEW, KNOWN 或 EVOLVING"
                )


def derive_signal_evidence(
    raw_signal: Dict[str, Any], 
    candidate_map: Dict[str, Any]
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    從 run-local 的 source_ids 導出跨次穩定的 evidence_keys (article_key)，
    並關聯來源 metadata。
    """
    from pipeline import sanitize_taiwan_terms

    s_ids = raw_signal.get("source_ids", [])
    evidence_keys: List[str] = []
    sources: List[Dict[str, Any]] = []

    for sid in s_ids:
        cand = candidate_map[sid]
        art_key = cand["article_key"]
        if art_key not in evidence_keys:
            evidence_keys.append(art_key)
        sources.append({
            "source_id": sid,
            "article_key": art_key,
            "title": sanitize_taiwan_terms(cand.get("title", "")),
            "canonical_url": cand.get("canonical_url", ""),
            "source_name": sanitize_taiwan_terms(cand.get("source_name", "")),
            "is_serendipity": cand.get("is_serendipity", False)
        })

    return evidence_keys, sources


def reconcile_and_build_signals(
    raw_signals: List[Dict[str, Any]],
    candidate_map: Dict[str, Any],
    date_str: str,
    existing_daily_signals: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    組裝正式 Signal 記錄清單，並保證同日重跑之 Signal ID 穩定性 (Replay Idempotency)：
    1. 轉換 evidence_keys 與 sources。
    2. 若當日已有既有訊號記錄，依據 normalized title 與 evidence_keys 比對。
       若命中則沿用原本已分配之 signal_id。
    3. 全新訊號依序分配新的 SIG-YYYYMMDD-NNN。
    """
    from pipeline import sanitize_taiwan_terms

    if not raw_signals:
        return []

    # 建立已存在訊號之對照索引
    existing_by_title: Dict[str, str] = {}
    existing_by_evidence: Dict[frozenset, str] = {}
    used_ids: Set[str] = set()
    highest_seq = 0

    id_pat = re.compile(rf'^SIG-{date_str.replace("-", "")}-(\d{{3}})$')

    if existing_daily_signals:
        for esig in existing_daily_signals:
            eid = esig.get("signal_id", "")
            if eid:
                used_ids.add(eid)
                m = id_pat.match(eid)
                if m:
                    seq = int(m.group(1))
                    if seq > highest_seq:
                        highest_seq = seq

            etitle_norm = normalize_title_for_matching(esig.get("title", ""))
            if etitle_norm:
                existing_by_title[etitle_norm] = eid

            eevid = frozenset(esig.get("evidence_keys", []))
            if eevid:
                existing_by_evidence[eevid] = eid

    built_signals: List[Dict[str, Any]] = []
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")

    for raw in raw_signals:
        evidence_keys, sources = derive_signal_evidence(raw, candidate_map)
        title = sanitize_taiwan_terms(raw.get("title", "").strip())
        summary = sanitize_taiwan_terms(raw.get("summary", "").strip())
        why_it_matters = sanitize_taiwan_terms(raw.get("why_it_matters", "").strip())
        asset_potential = sanitize_taiwan_terms(raw.get("asset_potential", "").strip())
        interpretation = sanitize_taiwan_terms(raw.get("interpretation", "").strip())
        unknowns = sanitize_taiwan_terms(raw.get("unknowns", "").strip())
        novelty = raw.get("novelty", "NEW").strip().upper()
        novelty_basis = sanitize_taiwan_terms(raw.get("novelty_basis", "").strip())
        source_ids = raw.get("source_ids", [])

        # 嘗試從歷史現存訊號比對 signal_id
        matched_id: Optional[str] = None
        norm_title = normalize_title_for_matching(title)
        evid_set = frozenset(evidence_keys)

        if norm_title in existing_by_title:
            matched_id = existing_by_title[norm_title]
        elif evid_set and evid_set in existing_by_evidence:
            matched_id = existing_by_evidence[evid_set]

        # 若未命中已有 ID，則分配新序號
        if not matched_id or matched_id in [s["signal_id"] for s in built_signals]:
            highest_seq += 1
            date_token = date_str.replace("-", "")
            matched_id = f"SIG-{date_token}-{highest_seq:03d}"

        signal_dict = {
            "signal_id": matched_id,
            "date": date_str,
            "title": title,
            "summary": summary,
            "why_it_matters": why_it_matters,
            "interpretation": interpretation,
            "unknowns": unknowns,
            "novelty": novelty,
            "novelty_basis": novelty_basis,
            "source_ids": source_ids,
            "evidence_keys": evidence_keys,
            "sources": sources,
            "created_at": now_iso
        }
        if asset_potential:
            signal_dict["asset_potential"] = asset_potential

        built_signals.append(signal_dict)

    return built_signals


def load_daily_signals(date_str: str, signals_dir: str = DEFAULT_SIGNALS_DIR) -> List[Dict[str, Any]]:
    """
    讀取指定日期的現有訊號記錄（供同日重跑 reconciliation 使用）。
    """
    filepath = os.path.join(signals_dir, f"{date_str}.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("signals", [])
    except Exception as e:
        logger.warning(f"⚠️ 讀取當日已有訊號檔 {filepath} 失敗: {e}")
        return []


def save_daily_signals(
    signals: List[Dict[str, Any]],
    date_str: str,
    signals_dir: str = DEFAULT_SIGNALS_DIR,
    test_mode: bool = False
) -> Optional[str]:
    """
    將訊號儲存至 data/signals/YYYY-MM-DD.json。
    當 test_mode=True 時，嚴格跳過寫入磁碟，維持測試隔離。
    """
    if test_mode:
        logger.info("🧪 【測試模式】跳過寫入 data/signals/ 訊號記錄檔，確保磁碟零污染")
        return None

    os.makedirs(signals_dir, exist_ok=True)
    filepath = os.path.join(signals_dir, f"{date_str}.json")

    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    payload = {
        "date": date_str,
        "generated_at": now_iso,
        "signal_count": len(signals),
        "signals": signals
    }

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 [Signal Ledger] 成功存檔 {len(signals)} 則訊號至 {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"❌ 儲存訊號記錄檔至 {filepath} 失敗: {e}")
        raise e


def generate_quality_report(
    days: int = 30,
    signals_dir: str = DEFAULT_SIGNALS_DIR
) -> Dict[str, Any]:
    """
    被動品質觀測統計 (Passive Quality Monitoring - Step 3B)
    純粹計算指標，絕不進行在線修改或 Prompt 自動調校。
    指標包含：
    1. 訊號與出刊天數分佈 (Signal Frequency & Zero-signal days)
    2. 新穎性分佈 (Novelty Distribution: NEW / KNOWN / EVOLVING)
    3. 佐證密度 (Evidence Density: Avg / Min / Max evidence keys per signal)
    4. 信源集中度 (Source Concentration & Top Sources)
    """
    cutoff_date = datetime.now() - timedelta(days=days)
    if not os.path.exists(signals_dir):
        return {
            "days_requested": days,
            "inspected_days": 0,
            "total_signals": 0,
            "zero_signal_days": 0,
            "signals_per_day_avg": 0.0,
            "novelty_counts": {"NEW": 0, "KNOWN": 0, "EVOLVING": 0},
            "evidence_density": {"avg": 0.0, "min": 0, "max": 0},
            "source_concentration": {
                "total_distinct_sources": 0,
                "top_20pct_share": 0.0,
                "top_sources": []
            }
        }

    filenames = sorted(os.listdir(signals_dir), reverse=True)
    date_file_pat = re.compile(r'^(\d{4}-\d{2}-\d{2})\.json$')

    all_signals = []
    inspected_days = 0
    zero_signal_days = 0

    for fname in filenames:
        m = date_file_pat.match(fname)
        if not m:
            continue
        file_date_str = m.group(1)
        try:
            file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
        except ValueError:
            continue

        if file_date < cutoff_date:
            continue

        inspected_days += 1
        filepath = os.path.join(signals_dir, fname)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                sig_list = data.get("signals", [])
                if not sig_list:
                    zero_signal_days += 1
                for sig in sig_list:
                    if isinstance(sig, dict):
                        all_signals.append(sig)
        except Exception as e:
            logger.warning(f"⚠️ 讀取歷史 Signal 檔案 {filepath} 失敗: {e}")

    total_signals = len(all_signals)

    # 1. Novelty Distribution
    novelty_counts = {"NEW": 0, "KNOWN": 0, "EVOLVING": 0}
    for s in all_signals:
        nov = s.get("novelty", "NEW").strip().upper()
        if nov in novelty_counts:
            novelty_counts[nov] += 1
        else:
            novelty_counts["NEW"] += 1

    # 2. Evidence Density
    evidence_counts = [len(s.get("evidence_keys", [])) for s in all_signals]
    avg_ev = round(sum(evidence_counts) / total_signals, 2) if total_signals > 0 else 0.0
    min_ev = min(evidence_counts) if evidence_counts else 0
    max_ev = max(evidence_counts) if evidence_counts else 0

    # 3. Source Concentration
    source_freq: Dict[str, int] = {}
    for s in all_signals:
        for src in s.get("sources", []):
            s_name = src.get("source_name", "未知來源")
            source_freq[s_name] = source_freq.get(s_name, 0) + 1

    sorted_sources = sorted(source_freq.items(), key=lambda x: x[1], reverse=True)
    total_source_citations = sum(source_freq.values())

    # 前 20% 核心來源佔比
    top_20pct_count = max(1, int(len(sorted_sources) * 0.2)) if sorted_sources else 0
    top_20pct_citations = sum(cnt for _, cnt in sorted_sources[:top_20pct_count]) if top_20pct_count > 0 else 0
    top_20pct_share = round(top_20pct_citations / total_source_citations, 4) if total_source_citations > 0 else 0.0

    return {
        "days_requested": days,
        "inspected_days": inspected_days,
        "total_signals": total_signals,
        "zero_signal_days": zero_signal_days,
        "signals_per_day_avg": round(total_signals / inspected_days, 2) if inspected_days > 0 else 0.0,
        "novelty_counts": novelty_counts,
        "evidence_density": {
            "avg": avg_ev,
            "min": min_ev,
            "max": max_ev
        },
        "source_concentration": {
            "total_distinct_sources": len(sorted_sources),
            "top_20pct_share": top_20pct_share,
            "top_sources": [
                {"source": name, "citations": cnt, "share": round(cnt / total_source_citations, 4)}
                for name, cnt in sorted_sources[:10]
            ]
        }
    }


def format_quality_report_text(report: Dict[str, Any]) -> str:
    """將品質觀測指標結構格式化為終端文字報告"""
    lines = []
    lines.append("=" * 60)
    lines.append(f"📊 QuietRadar Signal 品質觀測報告 (最近 {report['days_requested']} 天)")
    lines.append("=" * 60)
    lines.append(f"📅 掃描歷史天數: {report['inspected_days']} 天 (其中 {report['zero_signal_days']} 天為零訊號出刊)")
    lines.append(f"📡 累積訊號總數: {report['total_signals']} 則 (平均每日 {report.get('signals_per_day_avg', 0.0)} 則)")
    lines.append("")
    lines.append("1. 新穎性分佈 (Novelty Distribution):")
    total = max(1, report['total_signals'])
    for nov, count in report['novelty_counts'].items():
        pct = (count / total) * 100
        lines.append(f"   - {nov:8s}: {count:3d} 則 ({pct:5.1f}%)")
    lines.append("")
    lines.append("2. 佐證密度 (Evidence Density):")
    ev = report['evidence_density']
    lines.append(f"   - 平均每則佐證數: {ev['avg']} 篇 (最低: {ev['min']} 篇, 最高: {ev['max']} 篇)")
    lines.append("")
    lines.append("3. 信源集中度 (Source Concentration):")
    sc = report['source_concentration']
    lines.append(f"   - 涵蓋獨立信源數: {sc['total_distinct_sources']} 家")
    lines.append(f"   - 前 20% 核心信源佔比: {sc['top_20pct_share'] * 100:.1f}%")
    if sc['top_sources']:
        lines.append("   - 前五大引證來源:")
        for s in sc['top_sources'][:5]:
            lines.append(f"     * {s['source']:20s}: {s['citations']:2d} 次 ({s['share'] * 100:.1f}%)")
    lines.append("=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="QuietRadar Signal Ledger 工具")
    parser.add_argument("--stats", nargs="?", const=30, type=int, help="輸出最近 N 天的 Signal 品質觀測報告 (預設 30 天)")
    args = parser.parse_args()

    if args.stats is not None:
        rep = generate_quality_report(days=args.stats)
        print(format_quality_report_text(rep))
        sys.exit(0)
    else:
        parser.print_help()

