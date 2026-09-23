import os
import sys
import shutil
import tempfile
import unittest
from datetime import datetime

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline import CitationProvenanceError
from signal_ledger import (
    load_recent_signals,
    format_historical_signals_context,
    validate_signals_output,
    derive_signal_evidence,
    reconcile_and_build_signals,
    save_daily_signals,
    load_daily_signals
)


class TestSignalLedger(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_signals_")
        self.candidate_map = {
            "S01": {
                "source_id": "S01",
                "article_key": "key_claude_35",
                "canonical_url": "https://anthropic.com/claude-3-5",
                "title": "Claude 3.5 Sonnet Released",
                "source_name": "Anthropic",
                "is_serendipity": False
            },
            "S02": {
                "source_id": "S02",
                "article_key": "key_simon_sqlite",
                "canonical_url": "https://simonwillison.net/sqlite",
                "title": "Datasette Explain Deep Dive",
                "source_name": "Simon Willison",
                "is_serendipity": True
            }
        }

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_cold_start_load_recent_signals(self):
        """1. 驗證冷啟動：目錄為空或不存在時安全返回空清單，Prompt 正確標記冷啟動"""
        signals = load_recent_signals(signals_dir=os.path.join(self.test_dir, "nonexistent"))
        self.assertEqual(signals, [])

        context_str = format_historical_signals_context(signals)
        self.assertIn("冷啟動階段", context_str)
        self.assertIn("尚無歷史訊號記錄", context_str)

    def test_validate_signals_valid(self):
        """2. 驗證合法 Signal：NEW, KNOWN, EVOLVING 皆可正常通過"""
        valid_signals = [
            {
                "title": "多 Agent 協調層興起",
                "summary": "Foremerge 等工具探索在 Git 之上建立意圖衝突仲裁機制。",
                "why_it_matters": "解決並行開發衝突，是高槓桿團隊的必備中介軟體。",
                "novelty": "NEW",
                "novelty_basis": "歷史 30 天未見類似多 Agent 協調中介軟體記錄。",
                "source_ids": ["S01", "S02"]
            },
            {
                "title": "邊緣 Python 運行時普及",
                "summary": "Cloudflare Python Workers 讓無伺服器架構更廉價。",
                "why_it_matters": "一人公司可以極低成本運行狀態外掛服務。",
                "novelty": "EVOLVING",
                "novelty_basis": "先前已有 WebAssembly 相關討論，本次進入正式 GA 階段。",
                "source_ids": ["S02"]
            }
        ]
        # 正常通過不拋錯
        validate_signals_output(valid_signals, self.candidate_map)

    def test_validate_signals_empty_allowed(self):
        """3. 驗證容許 0 條 Signal（當期皆為大宗雜訊時，安全放行）"""
        validate_signals_output([], self.candidate_map)
        validate_signals_output(None, self.candidate_map)

    def test_validate_signals_invalid_novelty(self):
        """4. 驗證非法的 novelty 值會被拒絕"""
        invalid_signals = [
            {
                "title": "測試訊號",
                "summary": "摘要內容",
                "why_it_matters": "重要性說明",
                "novelty": "HOT_TOPIC",  # 非 NEW / KNOWN / EVOLVING
                "novelty_basis": "依據說明",
                "source_ids": ["S01"]
            }
        ]
        with self.assertRaises(CitationProvenanceError) as cm:
            validate_signals_output(invalid_signals, self.candidate_map)
        self.assertIn("novelty", str(cm.exception))

    def test_validate_signals_unauthorized_and_malformed_source(self):
        """5. 驗證未授權或格式錯誤的 source_ids 會被拒絕"""
        # 未授權 S99
        unauth_signals = [
            {
                "title": "測試訊號",
                "summary": "摘要內容",
                "why_it_matters": "重要性說明",
                "novelty": "NEW",
                "novelty_basis": "依據說明",
                "source_ids": ["S99"]
            }
        ]
        with self.assertRaises(CitationProvenanceError) as cm:
            validate_signals_output(unauth_signals, self.candidate_map)
        self.assertIn("未授權的 Source ID [S99]", str(cm.exception))

        # 格式錯誤 S-01
        malformed_signals = [
            {
                "title": "測試訊號",
                "summary": "摘要內容",
                "why_it_matters": "重要性說明",
                "novelty": "NEW",
                "novelty_basis": "依據說明",
                "source_ids": ["S-01"]
            }
        ]
        with self.assertRaises(CitationProvenanceError) as cm:
            validate_signals_output(malformed_signals, self.candidate_map)
        self.assertIn("不合規的 Source ID 格式", str(cm.exception))

    def test_validate_signals_max_limit(self):
        """6. 驗證超過 6 條 Signal 時拋出異常阻斷"""
        seven_signals = [
            {
                "title": f"訊號 {i}",
                "summary": "摘要內容",
                "why_it_matters": "重要性說明",
                "novelty": "NEW",
                "novelty_basis": "依據說明",
                "source_ids": ["S01"]
            }
            for i in range(7)
        ]
        with self.assertRaises(CitationProvenanceError) as cm:
            validate_signals_output(seven_signals, self.candidate_map)
        self.assertIn("超過上限 6 條", str(cm.exception))

    def test_derive_signal_evidence(self):
        """7. 驗證從 source_ids 正確映射出穩定 evidence_keys (article_key) 與 metadata"""
        raw_sig = {
            "title": "測試訊號",
            "source_ids": ["S01", "S02"]
        }
        evidence_keys, sources = derive_signal_evidence(raw_sig, self.candidate_map)
        self.assertEqual(evidence_keys, ["key_claude_35", "key_simon_sqlite"])
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["canonical_url"], "https://anthropic.com/claude-3-5")
        self.assertEqual(sources[1]["canonical_url"], "https://simonwillison.net/sqlite")

    def test_reconcile_and_build_signals_same_day_id_stability(self):
        """8. 驗證同日重跑 Signal ID 穩定性 (Replay Idempotency)"""
        date_str = "2026-09-22"
        # 模擬今日上午已產生的訊號記錄
        existing_daily = [
            {
                "signal_id": "SIG-20260922-001",
                "title": "多 Agent 意圖衝突協調層興起",
                "evidence_keys": ["key_claude_35"]
            },
            {
                "signal_id": "SIG-20260922-002",
                "title": "邊緣 Python 運行時 GA",
                "evidence_keys": ["key_simon_sqlite"]
            }
        ]

        # 下午重跑，包含原本的「多 Agent 意圖衝突協調層興起」（標點略異，應命中）與一個全新訊號
        cand_map_with_s03 = dict(self.candidate_map)
        cand_map_with_s03["S03"] = {
            "source_id": "S03",
            "article_key": "key_new_saas_003",
            "canonical_url": "https://example.com/saas",
            "title": "全新微型 SaaS 自動化工具",
            "source_name": "SaaS Digest",
            "is_serendipity": False
        }

        afternoon_raw = [
            {
                "title": "多 Agent 意圖衝突協調層興起！",  # 標點略異，應命中
                "summary": "新摘要...",
                "why_it_matters": "新重要性...",
                "novelty": "EVOLVING",
                "novelty_basis": "依據...",
                "source_ids": ["S01"]
            },
            {
                "title": "全新微型 SaaS 自動化工具",  # 全新訊號
                "summary": "新摘要...",
                "why_it_matters": "新重要性...",
                "novelty": "NEW",
                "novelty_basis": "依據...",
                "source_ids": ["S03"]
            }
        ]

        reconciled = reconcile_and_build_signals(
            raw_signals=afternoon_raw,
            candidate_map=cand_map_with_s03,
            date_str=date_str,
            existing_daily_signals=existing_daily
        )

        self.assertEqual(len(reconciled), 2)
        # 第一個沿用 SIG-20260922-001
        self.assertEqual(reconciled[0]["signal_id"], "SIG-20260922-001")
        # 第二個全新訊號獲得 SIG-20260922-003
        self.assertEqual(reconciled[1]["signal_id"], "SIG-20260922-003")
        # 驗證 interpretation 與 unknowns 容許未提供時預設空字串
        self.assertEqual(reconciled[0]["interpretation"], "")
        self.assertEqual(reconciled[0]["unknowns"], "")

    def test_reconcile_and_build_signals_with_interpretation_and_unknowns(self):
        """8b. 驗證選填欄位 interpretation 與 unknowns 正確記錄與洗滌"""
        date_str = "2026-09-22"
        raw_signals = [
            {
                "title": "多 Agent 意圖衝突協調層興起",
                "summary": "Foremerge 等工具探索在 Git 之上建立意圖衝突仲裁機制。",
                "why_it_matters": "解決並行開發衝突，是高槓桿團隊的必備中介軟體。",
                "interpretation": "第二層效應：Git 原生 merge 工具將被語義層合併取代。",
                "unknowns": "尚待驗證大規模 monorepo 下的 latency 與 token 開銷。",
                "novelty": "NEW",
                "novelty_basis": "歷史未見類似中介軟體。",
                "source_ids": ["S01"]
            }
        ]
        reconciled = reconcile_and_build_signals(
            raw_signals=raw_signals,
            candidate_map=self.candidate_map,
            date_str=date_str
        )
        self.assertEqual(len(reconciled), 1)
        self.assertIn("第二層效應", reconciled[0]["interpretation"])
        self.assertIn("latency", reconciled[0]["unknowns"])

    def test_save_daily_signals_test_mode_isolation(self):
        """9. 驗證 test_mode=True 時零磁碟寫入，隔離性 100%"""
        date_str = "2026-09-22"
        signals = [{"signal_id": "SIG-20260922-001", "title": "測試"}]

        res = save_daily_signals(signals, date_str=date_str, signals_dir=self.test_dir, test_mode=True)
        self.assertIsNone(res)
        self.assertEqual(len(os.listdir(self.test_dir)), 0)

    def test_save_daily_signals_persistence(self):
        """10. 驗證正常模式下正確存檔至 data/signals/YYYY-MM-DD.json"""
        date_str = "2026-09-22"
        signals = [
            {
                "signal_id": "SIG-20260922-001",
                "title": "多 Agent 意圖衝突協調層",
                "summary": "摘要",
                "why_it_matters": "價值",
                "novelty": "NEW",
                "novelty_basis": "無前例",
                "source_ids": ["S01"],
                "evidence_keys": ["key_claude_35"]
            }
        ]

        saved_path = save_daily_signals(signals, date_str=date_str, signals_dir=self.test_dir, test_mode=False)
        self.assertIsNotNone(saved_path)
        self.assertTrue(os.path.exists(saved_path))

        loaded = load_daily_signals(date_str, signals_dir=self.test_dir)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["signal_id"], "SIG-20260922-001")

    def test_asset_potential_preservation_and_rendering(self):
        """11. 驗證資產生成系統之 asset_potential 欄位提取、資產沉澱與 Markdown 呈現"""
        from pipeline import format_newsletter_markdown

        date_str = "2026-09-23"
        raw_signals = [
            {
                "title": "Agent 沙箱安全研究庫",
                "summary": "微型虛擬機安全隔離與憑證防護機制",
                "why_it_matters": "降低一人開發者 auto-approve 的風險",
                "asset_potential": "沉澱 Agent 安全測試基準數據集 (Data) 與審計自動化模組 (Code)，失敗仍留下沙箱研究報告",
                "interpretation": "安全標準將從雲端延伸至本機桌面",
                "unknowns": "Apple Silicon 下效能耗損",
                "novelty": "NEW",
                "novelty_basis": "歷史未見本機 MicroVM 安全沙箱",
                "source_ids": ["S01"]
            }
        ]
        reconciled = reconcile_and_build_signals(
            raw_signals=raw_signals,
            candidate_map=self.candidate_map,
            date_str=date_str
        )
        self.assertEqual(len(reconciled), 1)
        self.assertIn("asset_potential", reconciled[0])
        self.assertIn("Agent 安全測試基準數據集", reconciled[0]["asset_potential"])

        # 驗證 Markdown 格式化器包含「- **資產累積**：」
        md = format_newsletter_markdown([], signals=reconciled)
        self.assertIn("- **商業價值**：降低一人開發者 auto-approve 的風險", md)
        self.assertIn("- **資產累積**：累積 Agent 安全測試基準數據集 (Data) 與審計自動化模組 (Code)，失敗仍留下沙箱研究報告", md)

    def test_asset_potential_no_clear_path_and_omitted(self):
        """12. 驗證無明確資產路徑 (No clear path) 與缺省時的防禦性處理與呈現"""
        from pipeline import format_newsletter_markdown

        date_str = "2026-09-23"
        raw_signals = [
            {
                "title": "基礎研究純理論進展",
                "summary": "某數學猜想的新證明進展",
                "why_it_matters": "學術理論突破",
                "asset_potential": "目前尚無明確資產路徑，需待工程化推論與商業場景浮現",
                "novelty": "NEW",
                "novelty_basis": "歷史未見",
                "source_ids": ["S01"]
            },
            {
                "title": "通用行業快訊",
                "summary": "某巨頭例行功能發布",
                "why_it_matters": "大宗商品化功能",
                # 未填寫 asset_potential
                "novelty": "KNOWN",
                "novelty_basis": "已有前例",
                "source_ids": ["S02"]
            }
        ]
        reconciled = reconcile_and_build_signals(
            raw_signals=raw_signals,
            candidate_map=self.candidate_map,
            date_str=date_str
        )
        self.assertEqual(len(reconciled), 2)
        # 1. 第一筆包含 "目前尚無明確資產路徑"
        self.assertIn("asset_potential", reconciled[0])
        self.assertIn("目前尚無明確資產路徑", reconciled[0]["asset_potential"])

        # 2. 第二筆無 asset_potential 鍵，安全放行
        self.assertNotIn("asset_potential", reconciled[1])

        # 3. 驗證 Markdown 渲染器：第一筆顯示資產累積，第二筆安全略過不破版
        md = format_newsletter_markdown([], signals=reconciled)
        self.assertIn("- **資產累積**：目前尚無明確資產路徑", md)
        self.assertIn("### 2. [KNOWN] 通用產業快訊", md)


if __name__ == "__main__":
    unittest.main()


