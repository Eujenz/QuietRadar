"""
test_quality_monitoring.py - Passive Quality Monitoring Tests (Step 3B)

涵蓋 Step 3B 被動品質觀測測試：
1. 冷啟動 / 空目錄品質報告生成
2. 多日訊號、出刊天數、零訊號天數精準統計
3. 新穎性分佈 (NEW / KNOWN / EVOLVING) 計算
4. 佐證密度 (Evidence Density: Avg / Min / Max) 計算
5. 信源集中度 (Source Concentration) 與前 20% 核心信源比例
6. 終端報告文字格式化
"""

import os
import json
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta

from signal_ledger import (
    generate_quality_report,
    format_quality_report_text,
    save_daily_signals
)


class TestQualityMonitoring(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_quality_")
        self.signals_dir = os.path.join(self.test_dir, "signals")
        os.makedirs(self.signals_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_empty_signals_dir_report(self):
        """冷啟動或無資料夾時回傳合規初始統計"""
        rep = generate_quality_report(days=30, signals_dir=os.path.join(self.test_dir, "non_existent"))
        self.assertEqual(rep["inspected_days"], 0)
        self.assertEqual(rep["total_signals"], 0)
        self.assertEqual(rep["zero_signal_days"], 0)
        self.assertEqual(rep["novelty_counts"], {"NEW": 0, "KNOWN": 0, "EVOLVING": 0})
        self.assertEqual(rep["evidence_density"]["avg"], 0.0)

        # 檢驗格式化終端報告不拋出例外
        text = format_quality_report_text(rep)
        self.assertIn("累積訊號總數: 0 則", text)

    def test_quality_metrics_calculation(self):
        """測試真實多日情境下之觀測指標計算"""
        base_date = datetime.now()
        day1_str = (base_date - timedelta(days=2)).strftime("%Y-%m-%d")
        day2_str = (base_date - timedelta(days=1)).strftime("%Y-%m-%d")
        day3_str = base_date.strftime("%Y-%m-%d")

        # Day 1: 2 則訊號 (1 NEW, 1 EVOLVING)
        day1_signals = [
            {
                "signal_id": f"SIG-{day1_str.replace('-', '')}-001",
                "title": "訊號 A",
                "novelty": "NEW",
                "evidence_keys": ["k1", "k2"],
                "sources": [{"source_name": "TechCrunch"}, {"source_name": "Wired"}]
            },
            {
                "signal_id": f"SIG-{day1_str.replace('-', '')}-002",
                "title": "訊號 B",
                "novelty": "EVOLVING",
                "evidence_keys": ["k3"],
                "sources": [{"source_name": "TechCrunch"}]
            }
        ]
        save_daily_signals(day1_signals, date_str=day1_str, signals_dir=self.signals_dir)

        # Day 2: 0 則訊號 (合法零訊號出刊)
        save_daily_signals([], date_str=day2_str, signals_dir=self.signals_dir)

        # Day 3: 1 則訊號 (1 KNOWN)
        day3_signals = [
            {
                "signal_id": f"SIG-{day3_str.replace('-', '')}-001",
                "title": "訊號 C",
                "novelty": "KNOWN",
                "evidence_keys": ["k4", "k5", "k6"],
                "sources": [{"source_name": "arXiv"}]
            }
        ]
        save_daily_signals(day3_signals, date_str=day3_str, signals_dir=self.signals_dir)

        # 執行統計觀測
        rep = generate_quality_report(days=7, signals_dir=self.signals_dir)

        # 驗證基礎天數與訊號數
        self.assertEqual(rep["inspected_days"], 3)
        self.assertEqual(rep["zero_signal_days"], 1)
        self.assertEqual(rep["total_signals"], 3)
        self.assertEqual(rep["signals_per_day_avg"], 1.0)

        # 驗證新穎性分佈
        self.assertEqual(rep["novelty_counts"]["NEW"], 1)
        self.assertEqual(rep["novelty_counts"]["EVOLVING"], 1)
        self.assertEqual(rep["novelty_counts"]["KNOWN"], 1)

        # 驗證佐證密度：(2 + 1 + 3) / 3 = 2.0，min = 1, max = 3
        self.assertEqual(rep["evidence_density"]["avg"], 2.0)
        self.assertEqual(rep["evidence_density"]["min"], 1)
        self.assertEqual(rep["evidence_density"]["max"], 3)

        # 驗證信源集中度：TechCrunch (2次), Wired (1次), arXiv (1次)，共 4 次引用
        sc = rep["source_concentration"]
        self.assertEqual(sc["total_distinct_sources"], 3)
        self.assertEqual(sc["top_sources"][0]["source"], "TechCrunch")
        self.assertEqual(sc["top_sources"][0]["citations"], 2)
        self.assertEqual(sc["top_sources"][0]["share"], 0.5)

        # 驗證報告格式化文字輸出
        text = format_quality_report_text(rep)
        self.assertIn("掃描歷史天數: 3 天 (其中 1 天為零訊號出刊)", text)
        self.assertIn("累積訊號總數: 3 則 (平均每日 1.0 則)", text)
        self.assertIn("NEW     :   1 則 ( 33.3%)", text)
        self.assertIn("平均每則佐證數: 2.0 篇", text)
        self.assertIn("TechCrunch          :  2 次 (50.0%)", text)


if __name__ == "__main__":
    unittest.main()
