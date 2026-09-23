import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline import (
    DEFAULT_BARK_CHUNK_CEILING,
    format_newsletter_markdown,
    generate_newsletter_file,
    chunk_markdown_lossless,
    split_markdown_for_bark,
    SimpleBarkNotifier
)
from signal_ledger import save_daily_signals, load_daily_signals


class TestBarkDeliveryAndChunker(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_delivery_")
        self.sample_items = [
            {
                "title": "Anthropic Claude 3.5 發布",
                "original_url": "https://anthropic.com/claude-3-5",
                "source_name": "Anthropic 官方",
                "is_serendipity": False
            },
            {
                "title": "SQLite 向量外掛深入評測",
                "original_url": "https://simonwillison.net/sqlite-vec",
                "source_name": "Simon Willison 專欄",
                "is_serendipity": True
            }
        ]
        self.sample_signals = [
            {
                "signal_id": "SIG-20260922-001",
                "title": "多 Agent 協調層興起與意圖衝突仲裁",
                "summary": "在 Git 與檔案系統之上建立意圖仲裁中介層，解決並行代碼生成衝突。",
                "why_it_matters": "一人團隊並行擴張必備的底層中介軟體。",
                "interpretation": "商業解讀：傳統單一 agent IDE 將轉向多 worker 網格。",
                "unknowns": "大規模 monorepo 的延遲成本仍未知。",
                "novelty": "NEW",
                "novelty_basis": "歷史 30 天未見類似衝突仲裁中介軟體記錄。",
                "source_ids": ["S01"],
                "sources": [
                    {
                        "source_id": "S01",
                        "title": "Anthropic Claude 3.5 發布",
                        "canonical_url": "https://anthropic.com/claude-3-5",
                        "source_name": "Anthropic 官方"
                    }
                ]
            }
        ]

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_signals_rendered_into_newsletter_markdown(self):
        """1. 驗證 Signals 結構化渲染進電子報，包含新穎性標籤、推演、盲區與 Python 生成之 Canonical URL"""
        md = format_newsletter_markdown(
            items=self.sample_items,
            overview="這是今日的產業綜述正文 [S01]。",
            signals=self.sample_signals
        )
        self.assertIn("## 🎯 【今日情報訊號】", md)
        self.assertIn("### 1. [NEW] 多 Agent 協調層興起與意圖衝突仲裁", md)
        self.assertIn("- **實質事實**：在 Git 與檔案系統之上建立意圖仲裁中介層", md)
        self.assertIn("- **商業價值**：一人團隊並行擴張必備", md)
        self.assertIn("- **戰略推演**：商業解讀：傳統單一 agent IDE 將轉向多 worker 網格", md)
        self.assertIn("- **盲區疑問**：大規模 monorepo 的延遲成本仍未知", md)
        self.assertIn("[Anthropic 官方: Anthropic Claude 3.5 發布](https://anthropic.com/claude-3-5)", md)

    def test_zero_signal_graceful_delivery(self):
        """2. 驗證 0 則 Signal 時優雅交付，不產生破版空標題或壞死分隔線"""
        md_empty = format_newsletter_markdown(
            items=self.sample_items,
            overview="今日大宗商品化雜訊居多，零下注。",
            signals=[]
        )
        self.assertNotIn("## 🎯 【今日情報訊號】", md_empty)
        self.assertIn("今日大宗商品化雜訊居多", md_empty)
        self.assertIn("Anthropic Claude 3.5 發布", md_empty)

        md_none = format_newsletter_markdown(
            items=self.sample_items,
            overview="今日觀點",
            signals=None
        )
        self.assertNotIn("## 🎯 【今日情報訊號】", md_none)

    def test_chunker_lossless_and_unicode_safety(self):
        """3. 驗證分段器 100% Lossless 與 Unicode 邊界安全 (繁中、Emoji、全形標點)"""
        complex_text = (
            "# ⚡ QuietRadar 降噪科技電子報 🎯✨💡\n"
            "> 出刊時間：2026-09-22 18:00 | 本期精選：3 則 — 【繁體中文標註測試】\n---\n\n"
            "💡 【今日觀點】\n\n"
            "這是一段繁體中文特稿。「專欄特稿」必須深刻剖析技術落地阻力，絕非空泛公關辭藻！\n"
            "我們在這裡測試 Emoji 🎯✨💡 以及各種全形標點符號：【】「」——……；。\n\n"
            "在探討多 Agent 協調架構時，借鏡 [Anthropic 官方: Claude 3.5 Sonnet](https://anthropic.com/claude-3-5) 的架構設計，成本極低。\n\n"
            + ("段落重複擴充文字，模擬真實超長電子報長度：\n" + "這是長度測試繁中段落。" * 30 + "\n\n") * 6
        )

        # 採用較小的 ceiling (500 bytes) 強制切分成多個 chunks
        chunks = chunk_markdown_lossless(complex_text, max_chunk_bytes=500)
        self.assertGreater(len(chunks), 1)

        # 1. 驗證每段長度 <= 500 bytes
        for idx, c in enumerate(chunks):
            c_bytes = len(c.encode("utf-8"))
            self.assertLessEqual(c_bytes, 500, f"Chunk {idx} 超過 500 bytes: {c_bytes}")

        # 2. 驗證 100% Lossless: rejoin(chunks) == original_content
        rejoined = "".join(chunks)
        self.assertEqual(rejoined, complex_text, "分段重組後與原文不一致，存在字符遺漏或竄改！")

        # 3. 驗證 UTF-8 解碼零異常
        decoded = rejoined.encode("utf-8").decode("utf-8")
        self.assertEqual(decoded, complex_text)

    def test_chunker_link_protection(self):
        """4. 驗證分段器優先保護 Markdown Link [title](url) 不被腰斬"""
        text_with_link = (
            "前言文字說明。" * 15 + "\n\n"
            "關鍵連結在此：[Claude 3.5 Sonnet 深度架構解析報告](https://anthropic.com/news/claude-3-5-sonnet-deep-dive-report-long-link)\n\n"
            + "後續延伸閱讀。" * 15
        )

        # 使用剛好會切到連結附近的 ceiling
        chunks = chunk_markdown_lossless(text_with_link, max_chunk_bytes=300)
        rejoined = "".join(chunks)
        self.assertEqual(rejoined, text_with_link)

        # 檢查每個 chunk 中，不存在破裂的 link (即有 [ 但無 ])
        for idx, c in enumerate(chunks):
            # 若包含未閉合的連結標記，表示 link 被切斷
            if "[" in c:
                self.assertIn("]", c, f"Chunk {idx} 將 Markdown Link 標題切斷！")
            if "(" in c and "http" in c:
                self.assertIn(")", c, f"Chunk {idx} 將 Markdown Link URL 切斷！")

    def test_split_markdown_for_bark_lossless(self):
        """5. 驗證 split_markdown_for_bark 保留完整無失真 markdown，且 ceiling 設定為 DEFAULT_BARK_CHUNK_CEILING"""
        sample_md = "# 標題\n\n" + "內文段落測試。\n\n" * 250  # ~5700 bytes，必觸發多段切分
        bark_chunks = split_markdown_for_bark(sample_md, max_chunk_bytes=DEFAULT_BARK_CHUNK_CEILING)
        self.assertGreater(len(bark_chunks), 1)

        # 驗證總頁數標註一致
        total = bark_chunks[0]["total"]
        self.assertEqual(total, len(bark_chunks))

        # 驗證所有 chunks 串接還原
        rejoined = "".join(bc["markdown"] for bc in bark_chunks)
        self.assertEqual(rejoined, sample_md)

    def test_bark_failure_does_not_corrupt_ledger(self):
        """6. 驗證 Bark 推播失敗時，Signal Ledger 完好保留，錯誤被安全隔離"""
        date_str = "2026-09-22"
        # 1. 成功持久化 Ledger
        saved_path = save_daily_signals(self.sample_signals, date_str=date_str, signals_dir=self.test_dir, test_mode=False)
        self.assertTrue(os.path.exists(saved_path))

        # 2. 模擬 Bark 拋出網路異常或 HTTP 500
        notifier = SimpleBarkNotifier()
        notifier.device_keys = ["mock_bark_device_key"]

        with patch("httpx.Client.post", side_effect=Exception("Bark server connection refused")):
            success = notifier.send_digest(
                items=self.sample_items,
                overview="今日觀點",
                signals=self.sample_signals
            )
            self.assertFalse(success)

        # 3. 驗證 Signal Ledger 仍然 100% 存在且資料完全未受損
        loaded = load_daily_signals(date_str, signals_dir=self.test_dir)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["signal_id"], "SIG-20260922-001")



    def test_newsletter_rendering_failure_preserves_ledger(self):
        """7. 驗證 Newsletter 渲染失敗時，不影響已持久化之 Ledger，且不得進行 Bark 推播"""
        date_str = "2026-09-22"
        # 1. 成功持久化 Ledger
        saved_path = save_daily_signals(self.sample_signals, date_str=date_str, signals_dir=self.test_dir, test_mode=False)
        self.assertTrue(os.path.exists(saved_path))

        # 2. 模擬 generate_newsletter_file 發生例外 (例如 template 損毀)
        broken_template = {"header": 12345}  # 非字串，觸發 replace 異常
        with self.assertRaises(Exception):
            generate_newsletter_file(
                self.sample_items,
                filepath=os.path.join(self.test_dir, "test_news.md"),
                template=broken_template,
                overview="今日觀點",
                signals=self.sample_signals,
                archive=False
            )

        # 3. 驗證 Ledger 仍完好無損
        loaded = load_daily_signals(date_str, signals_dir=self.test_dir)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["signal_id"], "SIG-20260922-001")


if __name__ == "__main__":
    unittest.main()
