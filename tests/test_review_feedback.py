"""
test_review_feedback.py - Zero-GUI Human Feedback Tests (Step 3C)

涵蓋 Step 3C 人機反饋測試：
1. Append-only JSONL 評審記錄寫入
2. 多次評審追加保留歷史，不覆寫前次記錄
3. 原始 Signal Ledger 絕對不可變性 (Immutability Check)
4. 不合規 rating (非 1/2/3) 或 action 驗證攔截
5. 評審記錄加載與訊號審核覆蓋率比對
"""

import os
import json
import hashlib
import tempfile
import shutil
import unittest

from review import (
    append_review_record,
    load_signals_for_date,
    load_existing_reviews
)
from signal_ledger import save_daily_signals


class TestReviewFeedback(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_review_")
        self.signals_dir = os.path.join(self.test_dir, "signals")
        self.reviews_dir = os.path.join(self.test_dir, "signal_reviews")
        os.makedirs(self.signals_dir, exist_ok=True)
        os.makedirs(self.reviews_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_append_review_record_writes_jsonl(self):
        """驗證審查記錄以合規 JSONL 格式寫入"""
        today = "2026-09-22"
        sid = "SIG-20260922-001"
        out_path = append_review_record(
            signal_id=sid,
            rating=3,
            action="keep",
            note="重大架構變革",
            date_str=today,
            reviews_dir=self.reviews_dir
        )
        self.assertTrue(os.path.exists(out_path))

        with open(out_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["signal_id"], sid)
        self.assertEqual(rec["rating"], 3)
        self.assertEqual(rec["action"], "keep")
        self.assertEqual(rec["note"], "重大架構變革")
        self.assertIn("reviewed_at", rec)

    def test_append_only_preserves_multiple_reviews(self):
        """驗證多次追加記錄不會覆蓋舊記錄，而是按行累加 (Append-only)"""
        today = "2026-09-22"
        sid1 = "SIG-20260922-001"
        sid2 = "SIG-20260922-002"

        append_review_record(sid1, 3, "keep", "第一次評審", today, self.reviews_dir)
        append_review_record(sid2, 1, "drop", "噪音標籤", today, self.reviews_dir)
        append_review_record(sid1, 2, "archive", "二次覆審調整", today, self.reviews_dir)

        filepath = os.path.join(self.reviews_dir, f"{today}.jsonl")
        with open(filepath, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]

        self.assertEqual(len(lines), 3)

        # 檢驗 load_existing_reviews 取最新狀態
        reviews = load_existing_reviews(today, reviews_dir=self.reviews_dir)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[sid1]["rating"], 2)
        self.assertEqual(reviews[sid1]["action"], "archive")
        self.assertEqual(reviews[sid2]["rating"], 1)

    def test_signals_original_ledger_strictly_immutable(self):
        """核心原則驗證：審核操作絕對不可修改或破壞原始 data/signals/*.json 檔案內容"""
        today = "2026-09-22"
        sig = {
            "signal_id": "SIG-20260922-001",
            "title": "原始訊號標題",
            "summary": "原始摘要",
            "why_it_matters": "重要價值",
            "novelty": "NEW",
            "evidence_keys": ["k1"],
            "source_ids": ["S01"],
            "sources": []
        }
        sig_path = save_daily_signals([sig], date_str=today, signals_dir=self.signals_dir, test_mode=False)

        # 計算原始訊號檔的 SHA-256 指紋
        with open(sig_path, "rb") as f:
            original_hash = hashlib.sha256(f.read()).hexdigest()

        # 執行多次人機反饋記錄
        append_review_record("SIG-20260922-001", 1, "drop", "認定為噪音", today, self.reviews_dir)
        append_review_record("SIG-20260922-001", 3, "keep", "改判為核心", today, self.reviews_dir)

        # 再次計算原始訊號檔的 SHA-256 指紋
        with open(sig_path, "rb") as f:
            post_review_hash = hashlib.sha256(f.read()).hexdigest()

        # 驗證原始檔案完全零位元組改變
        self.assertEqual(original_hash, post_review_hash)

    def test_invalid_parameters_raise_value_error(self):
        """不合規評級或動作拋出 ValueError"""
        with self.assertRaises(ValueError):
            append_review_record("SIG-001", rating=5, action="keep", reviews_dir=self.reviews_dir)

        with self.assertRaises(ValueError):
            append_review_record("SIG-001", rating=2, action="invalid_action", reviews_dir=self.reviews_dir)


if __name__ == "__main__":
    unittest.main()
