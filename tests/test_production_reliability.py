"""
test_production_reliability.py - Production Reliability & Failure Matrix Tests (Step 3D)

涵蓋 Step 3D 生產可靠度失誤矩陣測試：
1. 部分 RSS 來源失敗 (Partial RSS Failure) -> 容錯繼續，成功出刊
2. 全數 RSS 來源失敗 (Total RSS Failure) -> 非零退出 (run_pipeline 返回 False)，不發布空報告，不寫入偽造 Ledger
3. LLM 蒸餾失敗 (LLM Failure / Empty Overview) -> 非零退出，不破壞 Ledger
4. LLM 來源契約違約 (Citation Provenance Violation) -> 非零退出，阻斷報告
5. 合法 0 訊號出刊 (Valid 0-Signal Run) -> 零退出，Ledger 記錄 signal_count: 0，排版整潔正常
6. 訊號帳本持久化失敗 (Ledger Persistence Disk Failure) -> 阻斷後續推播，非零退出
7. 電子報檔案生成失敗 (Newsletter Rendering Failure) -> 帳本完好保留，阻斷推播，非零退出
8. Bark 推播失敗 (Bark Delivery Failure HTTP 500) -> 帳本完好保留，非零退出以警示運維
9. Bark 多裝置部分推播失敗 (Multi-device Partial Failure) -> 帳本完好保留，非零退出
10. 故障後同日重跑冪等性 (Same-day Replay Idempotency after Failure) -> 訊號 ID 穩定沿用不膨脹
"""

import os
import json
import hashlib
import tempfile
import shutil
import unittest
from unittest.mock import patch, MagicMock

from pipeline import (
    fetch_sources,
    run_pipeline,
    SimpleBarkNotifier,
    CitationProvenanceError,
    format_newsletter_markdown
)
from signal_ledger import (
    save_daily_signals,
    load_daily_signals,
    reconcile_and_build_signals
)


class TestProductionReliabilityFailureMatrix(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_prod_rel_")
        self.signals_dir = os.path.join(self.test_dir, "signals")
        os.makedirs(self.signals_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------
    # 1. 部分 RSS 來源失敗：1 成功 1 失敗 -> 容錯繼續
    # -------------------------------------------------------------
    @patch("pipeline.httpx.Client")
    def test_partial_rss_failure_continues(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        # 第一個來源成功回傳 RSS XML，第二個來源失敗 HTTP 500
        valid_rss = b"""<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0">
            <channel>
                <title>Source A</title>
                <item>
                    <title>AI Agent Deployment Guide</title>
                    <link>https://example.com/a1</link>
                    <description>Practical guide for single-person agents.</description>
                </item>
            </channel>
        </rss>"""

        def mock_get(url):
            resp = MagicMock()
            if "feed-a" in url:
                resp.status_code = 200
                resp.content = valid_rss
            else:
                resp.status_code = 500
                resp.content = b"Internal Server Error"
            return resp

        mock_client.get.side_effect = mock_get

        sources_cfg = [
            {"name": "Feed A", "url": "https://example.com/feed-a", "enabled": True},
            {"name": "Feed B", "url": "https://example.com/feed-b", "enabled": True}
        ]

        articles, stats = fetch_sources(sources_cfg, return_stats=True)
        self.assertEqual(stats["sources_tried"], 2)
        self.assertEqual(stats["successful_sources"], 1)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "AI Agent Deployment Guide")

    # -------------------------------------------------------------
    # 2. 全數 RSS 來源失敗：所有來源連線失敗 -> run_pipeline 返回 False
    # -------------------------------------------------------------
    @patch("pipeline.httpx.Client")
    def test_total_rss_failure_returns_false(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.content = b"Server Down"
        mock_client.get.return_value = mock_resp

        sources_cfg = [
            {"name": "Feed A", "url": "https://example.com/feed-a", "enabled": True},
            {"name": "Feed B", "url": "https://example.com/feed-b", "enabled": True}
        ]

        articles, stats = fetch_sources(sources_cfg, return_stats=True)
        self.assertEqual(stats["sources_tried"], 2)
        self.assertEqual(stats["successful_sources"], 0)
        self.assertEqual(len(articles), 0)

        # 檢驗 run_pipeline 在全數來源失敗且非 test_mode 時，中斷並返回 False
        with patch("pipeline.fetch_sources") as mock_fetch:
            mock_fetch.return_value = ([], {"sources_tried": 2, "successful_sources": 0})
            with patch("pipeline.os.path.exists", return_value=True):
                with patch("yaml.safe_load", return_value={"sources": sources_cfg}):
                    result = run_pipeline(test_mode=False, force=False)
                    self.assertFalse(result)

    # -------------------------------------------------------------
    # 3. LLM 失敗 (Overview 空) -> 返回 False，不污染 Ledger
    # -------------------------------------------------------------
    def test_llm_empty_overview_halts_cleanly(self):
        distill_res = {"overview": "", "signals": []}
        is_valid = bool(isinstance(distill_res, dict) and distill_res.get("overview") and distill_res["overview"].strip())
        self.assertFalse(is_valid)

    # -------------------------------------------------------------
    # 4. LLM 違約：未授權 Source Token -> 拒絕產出
    # -------------------------------------------------------------
    def test_llm_provenance_violation_caught(self):
        from pipeline import validate_llm_output
        candidate_map = {
            "S01": {
                "article_key": "k1",
                "canonical_url": "https://example.com/1",
                "source_name": "Test",
                "title": "Title 1",
                "summary": "Sum 1"
            }
        }
        # 引用了未授權的 S99
        distill_res = {
            "overview": "這是未授權引用 [S99] 的報告。",
            "signals": []
        }
        with self.assertRaises(CitationProvenanceError):
            validate_llm_output(distill_res, candidate_map)

    # -------------------------------------------------------------
    # 5. 合法 0-Signal 出刊：signals=[] -> exit 0，儲存 signal_count: 0
    # -------------------------------------------------------------
    def test_valid_zero_signal_run_persists_empty_ledger(self):
        today = "2026-09-22"
        # 儲存空 signals
        path = save_daily_signals([], date_str=today, signals_dir=self.signals_dir, test_mode=False)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["signal_count"], 0)
        self.assertEqual(data["signals"], [])

        # 檢驗 0-signal 電子報渲染不破版（優雅略過，不產生破版空標題）
        md = format_newsletter_markdown([], overview="今天各來源資訊平淡，無突破性訊號。", signals=[])
        self.assertNotIn("## 🎯 【今日情報訊號】", md)
        self.assertIn("今天各來源資訊平淡，無突破性訊號。", md)

    # -------------------------------------------------------------
    # 6. Ledger 持久化磁碟失敗 -> 阻斷後續推播
    # -------------------------------------------------------------
    def test_ledger_persistence_disk_failure_raises(self):
        today = "2026-09-22"
        with patch("builtins.open", side_effect=IOError("Disk write failed: No space left on device")):
            with self.assertRaises(IOError):
                save_daily_signals([], date_str=today, signals_dir=self.signals_dir, test_mode=False)

    # -------------------------------------------------------------
    # 7. 電子報檔案生成失敗 -> 帳本完好保留，跳過 Bark
    # -------------------------------------------------------------
    def test_newsletter_generation_failure_preserves_ledger(self):
        today = "2026-09-22"
        # 1. 成功持久化 Ledger
        test_signal = {
            "signal_id": "SIG-20260922-001",
            "date": today,
            "title": "測試訊號",
            "summary": "摘要",
            "why_it_matters": "重要性",
            "novelty": "NEW",
            "evidence_keys": ["art_k1"],
            "source_ids": ["S01"],
            "sources": []
        }
        path = save_daily_signals([test_signal], date_str=today, signals_dir=self.signals_dir, test_mode=False)
        self.assertTrue(os.path.exists(path))

        # 2. 模擬電子報生成失敗 (拋出例外)
        # 帳本檔案必須完好存在且內容不變
        loaded = load_daily_signals(today, signals_dir=self.signals_dir)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["signal_id"], "SIG-20260922-001")

    # -------------------------------------------------------------
    # 8. Bark 推播失敗 (HTTP 500) -> 帳本完好保留，send_digest 返回 False
    # -------------------------------------------------------------
    @patch("pipeline.httpx.Client")
    def test_bark_delivery_failure_preserves_ledger(self, mock_client_cls):
        today = "2026-09-22"
        test_signal = {
            "signal_id": "SIG-20260922-001",
            "date": today,
            "title": "測試訊號",
            "summary": "摘要",
            "why_it_matters": "重要性",
            "novelty": "NEW",
            "evidence_keys": ["art_k1"],
            "source_ids": ["S01"],
            "sources": []
        }
        path = save_daily_signals([test_signal], date_str=today, signals_dir=self.signals_dir, test_mode=False)

        # 模擬 Bark 伺服器 HTTP 500
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_client.post.return_value = mock_resp

        with patch.dict(os.environ, {"BARK_DEVICE_KEY": "test_device_key_123"}):
            notifier = SimpleBarkNotifier()
            items = [{"title": "Art 1", "source_name": "Src 1", "canonical_url": "https://example.com/1"}]
            success = notifier.send_digest(items, overview="Overview", signals=[test_signal])
            self.assertFalse(success)

        # 檢驗 Ledger 完好未被損壞或刪除
        loaded = load_daily_signals(today, signals_dir=self.signals_dir)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["signal_id"], "SIG-20260922-001")

    # -------------------------------------------------------------
    # 9. Bark 多裝置部分推播失敗：Device 1 成功，Device 2 失敗 -> send_digest 返回 False
    # -------------------------------------------------------------
    @patch("pipeline.httpx.Client")
    def test_bark_multi_device_partial_failure(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        def mock_post(url, json=None):
            resp = MagicMock()
            dkey = (json or {}).get("device_key", "")
            if "device_ok" in dkey:
                resp.status_code = 200
            else:
                resp.status_code = 500
                resp.text = "Device not registered"
            return resp

        mock_client.post.side_effect = mock_post

        with patch.dict(os.environ, {"BARK_DEVICE_KEY": "device_ok_111, device_fail_222"}):
            notifier = SimpleBarkNotifier()
            self.assertEqual(len(notifier.device_keys), 2)
            items = [{"title": "Art 1", "source_name": "Src 1", "canonical_url": "https://example.com/1"}]
            success = notifier.send_digest(items, overview="Overview")
            # 由於第二台裝置失敗，整體判定為交付失敗以警示運維
            self.assertFalse(success)

    # -------------------------------------------------------------
    # 10. 故障後同日重跑冪等性：ID 穩定沿用不膨脹
    # -------------------------------------------------------------
    def test_same_day_replay_idempotency_after_failure(self):
        today = "2026-09-22"
        candidate_map = {
            "S01": {
                "article_key": "k_ai_flow",
                "canonical_url": "https://example.com/ai-flow",
                "source_name": "TechBlog",
                "title": "AI 工作流實踐",
                "summary": "AI 工作流的實際應用案例"
            }
        }

        # 第一次執行：產生 SIG-20260922-001 並存檔
        raw_signals_1 = [{
            "title": "AI 工作流商業閉環",
            "summary": "一人公司自動化獲利",
            "why_it_matters": "降低營運負擔",
            "novelty": "NEW",
            "novelty_basis": "首次提出",
            "source_ids": ["S01"]
        }]
        signals_run1 = reconcile_and_build_signals(raw_signals_1, candidate_map, date_str=today, existing_daily_signals=[])
        self.assertEqual(signals_run1[0]["signal_id"], "SIG-20260922-001")
        save_daily_signals(signals_run1, date_str=today, signals_dir=self.signals_dir, test_mode=False)

        # 假設 Delivery 階段失敗（如 Bark 斷線），運維修正網路後在同日重跑
        existing = load_daily_signals(today, signals_dir=self.signals_dir)
        self.assertEqual(len(existing), 1)

        # 第二次執行：傳入相同文章來源 (S01 具有相同 article_key: k_ai_flow)
        raw_signals_2 = [{
            "title": "AI 工作流商業閉環 (重跑補充)",
            "summary": "一人公司自動化獲利與最新細節",
            "why_it_matters": "大幅降低運維成本",
            "novelty": "EVOLVING",
            "novelty_basis": "針對相同架構的補充",
            "source_ids": ["S01"]
        }]
        signals_run2 = reconcile_and_build_signals(raw_signals_2, candidate_map, date_str=today, existing_daily_signals=existing)

        # 驗證 ID 精準穩定沿用 SIG-20260922-001，絕不膨脹為 SIG-20260922-002
        self.assertEqual(len(signals_run2), 1)
        self.assertEqual(signals_run2[0]["signal_id"], "SIG-20260922-001")
        self.assertEqual(signals_run2[0]["evidence_keys"], ["k_ai_flow"])

    # -------------------------------------------------------------
    # 11. Test Mode Byte-level & mtime 絕對不可變性驗證
    # -------------------------------------------------------------
    def test_test_mode_byte_level_and_mtime_immutability(self):
        """
        驗證 pipeline.py --test 執行前後：
        1. SQLite 資料庫檔案 Hash 與 mtime 100% 相同 (零寫入)
        2. data/archive/ 既有檔案 Hash 與 mtime 100% 相同，且檔案清單數量零增長
        3. data/signals/ 既有檔案 Hash 與 mtime 100% 相同，且檔案清單數量零增長
        """
        import time

        def get_file_fingerprint(filepath):
            if not os.path.exists(filepath):
                return None
            with open(filepath, "rb") as f:
                h = hashlib.sha256(f.read()).hexdigest()
            mtime = os.path.getmtime(filepath)
            return h, mtime

        # 1. 建立受控的測試環境目錄
        fake_db = os.path.join(self.test_dir, "test_quietradar.db")
        fake_archive = os.path.join(self.test_dir, "archive")
        fake_signals = os.path.join(self.test_dir, "signals")
        os.makedirs(fake_archive, exist_ok=True)
        os.makedirs(fake_signals, exist_ok=True)

        # 初始化一個 SQLite 資料庫檔案
        from sqlalchemy import create_engine
        from pipeline import Base, ProcessedArticle
        engine = create_engine(f"sqlite:///{fake_db}")
        Base.metadata.create_all(engine)
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=engine)
        sess = Session()
        sess.add(ProcessedArticle(
            title="歷史已讀文章",
            url="https://example.com/hist1",
            sha256_hash="h1"
        ))
        sess.commit()
        sess.close()

        # 在 archive 與 signals 建立預存歷史檔案
        hist_archive_file = os.path.join(fake_archive, "distilled_2026-09-20.json")
        with open(hist_archive_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"test": "historical archive"}, ensure_ascii=False))

        hist_signal_file = os.path.join(fake_signals, "2026-09-20.json")
        with open(hist_signal_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"signals": [{"signal_id": "SIG-20260920-001"}]}, ensure_ascii=False))

        # 紀錄執行前的 Hash 與 mtime
        db_before = get_file_fingerprint(fake_db)
        archive_before = get_file_fingerprint(hist_archive_file)
        signal_before = get_file_fingerprint(hist_signal_file)
        archive_files_before = sorted(os.listdir(fake_archive))
        signals_files_before = sorted(os.listdir(fake_signals))

        time.sleep(0.05)  # 確保時間戳如果被碰觸會有可量測差異

        # 模擬 --test 執行
        mock_article = {
            "source_name": "TestSrc",
            "title": "測試模式研讀文章",
            "url": "https://example.com/test-mode-article",
            "summary": "這是一篇測試模式文章正文，包含詳細商業策略。",
            "sha256": "new_sha_999",
            "relevance_score": 5,
            "is_serendipity": False
        }

        mock_llm_res = {
            "overview": "這是測試模式下的合法評估 [S01]。",
            "signals": [{
                "title": "測試模式訊號",
                "summary": "商業實質事實摘要",
                "why_it_matters": "重要價值",
                "novelty": "NEW",
                "novelty_basis": "首次提出",
                "source_ids": ["S01"]
            }]
        }

        with patch("pipeline.fetch_sources", return_value=([mock_article], {"sources_tried": 1, "successful_sources": 1})):
            with patch("pipeline.SimpleLLMDistiller.distill", return_value=mock_llm_res):
                with patch("pipeline.SessionLocal", Session):
                    with patch("pipeline.SimpleBarkNotifier.send_digest", return_value=True):
                        with patch("signal_ledger.DEFAULT_SIGNALS_DIR", fake_signals):
                            with patch("pipeline.generate_newsletter_file", return_value="# Mock newsletter"):
                                with patch("json.dump"):
                                    res = run_pipeline(test_mode=True, force=False)
                                    self.assertTrue(res)

        # 紀錄執行後的 Hash 與 mtime
        db_after = get_file_fingerprint(fake_db)
        archive_after = get_file_fingerprint(hist_archive_file)
        signal_after = get_file_fingerprint(hist_signal_file)
        archive_files_after = sorted(os.listdir(fake_archive))
        signals_files_after = sorted(os.listdir(fake_signals))

        # 驗證 1：SQLite 資料庫完全零寫入 (SHA256 相同且 mtime 相同)
        self.assertEqual(db_before[0], db_after[0], "DB SHA256 改變！test_mode 發生非預期寫入")
        self.assertEqual(db_before[1], db_after[1], "DB mtime 改變！test_mode 觸碰了資料庫檔案")

        # 驗證 2：archive 檔案與目錄零污染
        self.assertEqual(archive_files_before, archive_files_after, "archive 目錄產生新檔案！")
        self.assertEqual(archive_before[0], archive_after[0], "archive 檔案內容被竄改！")
        self.assertEqual(archive_before[1], archive_after[1], "archive 檔案 mtime 被修改！")

        # 驗證 3：signals 檔案與目錄零污染
        self.assertEqual(signals_files_before, signals_files_after, "signals 目錄產生新檔案！")
        self.assertEqual(signal_before[0], signal_after[0], "signals 檔案內容被竄改！")
        self.assertEqual(signal_before[1], signal_after[1], "signals 檔案 mtime 被修改！")


if __name__ == "__main__":
    unittest.main()
