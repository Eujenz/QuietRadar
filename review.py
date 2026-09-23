"""
review.py - Zero-GUI Human Feedback CLI (Step 3C)

輕量級人機反饋命令列工具：
1. 嚴格遵守 Zero-GUI 原則：零 Web 界面、零 API、零前端框架。
2. 唯讀存取原始資料：僅讀取 data/signals/YYYY-MM-DD.json，絕不修改或覆寫原始 Signal Ledger。
3. 追加寫入 (Append-only)：反饋記錄寫入 data/signal_reviews/YYYY-MM-DD.jsonl。
4. 支援批次腳本式 (CLI Flags) 與互動式審查 (Interactive CLI)。
5. 評級契約：
   - rating: 1 (噪音/誤報 False Positive), 2 (普通/已知 Neutral), 3 (高價值突破 High Value)
   - action: keep (保留/追蹤), archive (歸檔), drop (捨棄)
   - note: 評審備註
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# 確保 Windows 控制台輸出支援 UTF-8 (Emoji 與繁體中文)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

DEFAULT_SIGNALS_DIR: str = "data/signals"
DEFAULT_REVIEWS_DIR: str = "data/signal_reviews"


def get_default_date() -> str:
    """取得預設審閱日期（今日 YYYY-MM-DD）"""
    return datetime.now().strftime("%Y-%m-%d")


def load_signals_for_date(date_str: str, signals_dir: str = DEFAULT_SIGNALS_DIR) -> List[Dict[str, Any]]:
    """讀取指定日期的 Signals 清單 (唯讀)"""
    filepath = os.path.join(signals_dir, f"{date_str}.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("signals", [])
    except Exception as e:
        print(f"❌ 讀取訊號檔案 {filepath} 失敗: {e}")
        return []


def load_existing_reviews(date_str: str, reviews_dir: str = DEFAULT_REVIEWS_DIR) -> Dict[str, Dict[str, Any]]:
    """讀取指定日期的現有評審記錄 (以 signal_id 為 key，保留最新審查)"""
    filepath = os.path.join(reviews_dir, f"{date_str}.jsonl")
    if not os.path.exists(filepath):
        return {}
    reviews: Dict[str, Dict[str, Any]] = {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rev = json.loads(line)
                    sid = rev.get("signal_id")
                    if sid:
                        reviews[sid] = rev
        return reviews
    except Exception as e:
        print(f"⚠️ 讀取評審記錄 {filepath} 失敗: {e}")
        return {}


def append_review_record(
    signal_id: str,
    rating: int,
    action: str = "keep",
    note: str = "",
    date_str: Optional[str] = None,
    reviews_dir: str = DEFAULT_REVIEWS_DIR
) -> str:
    """
    將人機評審結果以 Append-only 方式寫入 JSONL。
    絕不修改 data/signals/ 原始資料！
    """
    if not date_str:
        # 嘗試從 signal_id 推導 (如 SIG-20260922-001)
        if signal_id.startswith("SIG-") and len(signal_id) >= 12:
            raw_d = signal_id.split("-")[1]
            if len(raw_d) == 8:
                date_str = f"{raw_d[:4]}-{raw_d[4:6]}-{raw_d[6:]}"
        if not date_str:
            date_str = get_default_date()

    if rating not in [1, 2, 3]:
        raise ValueError(f"rating 必須為 1, 2, 或 3 (實際: {rating})")

    action = (action or "keep").strip().lower()
    if action not in ["keep", "archive", "drop"]:
        raise ValueError(f"action 必須為 keep, archive, 或 drop (實際: {action})")

    os.makedirs(reviews_dir, exist_ok=True)
    filepath = os.path.join(reviews_dir, f"{date_str}.jsonl")

    record = {
        "signal_id": signal_id,
        "date": date_str,
        "rating": rating,
        "action": action,
        "note": note.strip(),
        "reviewed_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    }

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return filepath


def list_reviews_summary(days: int = 7, signals_dir: str = DEFAULT_SIGNALS_DIR, reviews_dir: str = DEFAULT_REVIEWS_DIR):
    """列出最近 N 天的訊號審查覆蓋率概況"""
    print("=" * 65)
    print(f"📋 QuietRadar 訊號審查狀態一覽 (最近 {days} 天)")
    print("=" * 65)
    print(f"{'日期':12s} | {'訊號數':6s} | {'已審查':6s} | {'待審查':6s} | {'狀態'}")
    print("-" * 65)

    base = datetime.now()
    for d in range(days):
        dt_str = (base - timedelta(days=d)).strftime("%Y-%m-%d")
        signals = load_signals_for_date(dt_str, signals_dir=signals_dir)
        reviews = load_existing_reviews(dt_str, reviews_dir=reviews_dir)
        sig_count = len(signals)
        rev_count = len(reviews)
        pending = max(0, sig_count - rev_count)

        if sig_count == 0:
            status = "⚪ 0 訊號"
        elif pending == 0:
            status = "✅ 全部完成"
        else:
            status = f"⏳ 待審 {pending} 則"

        print(f"{dt_str:12s} | {sig_count:6d} | {rev_count:6d} | {pending:6d} | {status}")
    print("=" * 65)


def run_interactive_review(date_str: str, signals_dir: str = DEFAULT_SIGNALS_DIR, reviews_dir: str = DEFAULT_REVIEWS_DIR):
    """互動式逐條審閱當日訊號"""
    signals = load_signals_for_date(date_str, signals_dir=signals_dir)
    if not signals:
        print(f"ℹ️ 日期 [{date_str}] 無任何訊號記錄。")
        return

    existing_reviews = load_existing_reviews(date_str, reviews_dir=reviews_dir)
    print("=" * 65)
    print(f"🎯 開始審閱 [{date_str}] 的情報訊號 (共 {len(signals)} 則)")
    print("評級指引: 1=噪音/誤報  2=普通/已知  3=高價值突破  (輸入 s 跳過, q 離開)")
    print("=" * 65)

    for idx, sig in enumerate(signals, 1):
        sid = sig.get("signal_id", f"SIG-{idx:03d}")
        title = sig.get("title", "")
        novelty = sig.get("novelty", "NEW")
        summary = sig.get("summary", "")
        why = sig.get("why_it_matters", "")
        rev = existing_reviews.get(sid)

        print(f"\n[{idx}/{len(signals)}] {sid} [{novelty}] {title}")
        print(f"  📝 事實：{summary}")
        print(f"  💡 價值：{why}")
        if rev:
            print(f"  ⭐ 已有評審: rating={rev.get('rating')} | action={rev.get('action')} | note={rev.get('note')}")

        while True:
            choice = input("  請輸入評分 [1/2/3, s=跳過, q=結束]: ").strip().lower()
            if choice == "q":
                print("👋 結束審閱。")
                return
            elif choice in ["s", ""]:
                print("  ⏭️ 已跳過")
                break
            elif choice in ["1", "2", "3"]:
                rating = int(choice)
                action_input = input("  動作 [keep(預設)/archive/drop]: ").strip().lower()
                action = action_input if action_input in ["archive", "drop"] else "keep"
                note = input("  審查備註 (選填，直接 Enter 略過): ").strip()

                out_path = append_review_record(
                    signal_id=sid,
                    rating=rating,
                    action=action,
                    note=note,
                    date_str=date_str,
                    reviews_dir=reviews_dir
                )
                print(f"  ✅ 審查已追加存檔至: {out_path}")
                break
            else:
                print("  ⚠️ 輸入無效，請輸入 1, 2, 3 或 s/q")

    print("\n🎉 本日所有訊號已審閱完畢！")


def main():
    parser = argparse.ArgumentParser(description="QuietRadar 零 GUI 人機回饋工具 (review.py)")
    parser.add_argument("--date", "-d", default=get_default_date(), help="指定審閱日期 (格式: YYYY-MM-DD，預設今日)")
    parser.add_argument("--signal-id", "-s", help="非互動模式：指定要審查的 Signal ID (如 SIG-20260922-001)")
    parser.add_argument("--rating", "-r", type=int, choices=[1, 2, 3], help="非互動模式：評分 1=噪音, 2=普通, 3=高價值")
    parser.add_argument("--action", "-a", choices=["keep", "archive", "drop"], default="keep", help="非互動模式：處理動作")
    parser.add_argument("--note", "-n", default="", help="非互動模式：審查備註文字")
    parser.add_argument("--list", "-l", nargs="?", const=7, type=int, help="列出最近 N 天的審查概況 (預設 7 天)")
    args = parser.parse_args()

    if args.list is not None:
        list_reviews_summary(days=args.list)
        return

    # 非互動批次模式
    if args.signal_id:
        if args.rating is None:
            print("❌ 錯誤：指定 --signal-id 時必須同時提供 --rating [1/2/3]")
            sys.exit(1)
        out_path = append_review_record(
            signal_id=args.signal_id,
            rating=args.rating,
            action=args.action,
            note=args.note,
            date_str=args.date
        )
        print(f"✅ 成功記錄審查: {args.signal_id} -> rating={args.rating}, action={args.action} (已存至 {out_path})")
        return

    # 互動模式
    run_interactive_review(date_str=args.date)


if __name__ == "__main__":
    main()
