import os
import sys
import unittest
import json

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline import (
    CitationProvenanceError,
    scrub_urls,
    build_llm_context,
    validate_llm_output,
    extract_source_ids,
    resolve_overview_citations,
    extract_source_tokens,
    SpeakHumanCleaner,
    normalize_url
)


class TestSourceIdProvenance(unittest.TestCase):
    def setUp(self):
        self.sample_candidates = [
            {
                "title": "Claude 3.5 Sonnet Released",
                "url": "https://anthropic.com/news/claude-3-5?utm_source=twitter",
                "source_name": "Anthropic Official",
                "summary": "Anthropic introduces Claude 3.5 Sonnet. Read more at https://docs.anthropic.com/claude",
                "is_serendipity": False
            },
            {
                "title": "Datasette Explain Deep Dive",
                "url": "https://simonwillison.net/2026/datasette-explain/",
                "source_name": "Simon Willison",
                "summary": "Exploring SQLite explain queries. Check https://github.com/simonw/datasette for code.",
                "is_serendipity": True
            }
        ]
        self.candidate_map, self.articles_payload = build_llm_context(self.sample_candidates)

    def test_test_h_input_url_scrubbing(self):
        """Test H: 驗證所有送入 LLM 的欄位皆被徹底脫敏，絕無 URL 外洩"""
        # 1. 驗證 scrub_urls 基礎過濾
        text_with_urls = "請見 https://example.com/test 以及 http://foo.org 與 www.bar.com/doc"
        scrubbed = scrub_urls(text_with_urls)
        self.assertNotIn("https://", scrubbed)
        self.assertNotIn("http://", scrubbed)
        self.assertNotIn("www.", scrubbed)
        self.assertIn("[URL_REMOVED]", scrubbed)

        # 2. 驗證 build_llm_context 產出的 articles_payload 絕不含 URL 欄位
        for p in self.articles_payload:
            self.assertNotIn("url", p)
            self.assertNotIn("original_url", p)
            self.assertNotIn("canonical_url", p)
            # 內文中的 URL 也被替換為 [URL_REMOVED]
            self.assertNotIn("https://", p["content_snippet"])
            self.assertIn("[URL_REMOVED]", p["content_snippet"])

        # 3. 驗證整份 articles_payload 轉為 JSON 後絕無網址
        payload_json = json.dumps(self.articles_payload)
        self.assertNotIn("https://", payload_json)
        self.assertNotIn("http://", payload_json)
        self.assertNotIn("www.", payload_json)

    def test_test_a_output_url_zero_tolerance(self):
        """Test A: LLM 輸出中任何字串欄位若包含 URL，立即拒絕產出並拋出 CitationProvenanceError"""
        # 情況 1: Overview 出現 URL
        invalid_data_1 = {
            "overview": "這是一個重要突破 [S01]，詳情請見 https://anthropic.com/news"
        }
        with self.assertRaises(CitationProvenanceError) as cm:
            validate_llm_output(invalid_data_1, self.candidate_map)
        self.assertIn("非法 URL 輸出", str(cm.exception))

        # 情況 2: Overview 出現 Markdown 式 URL
        invalid_data_2 = {
            "overview": "這是一個重要突破 [Anthropic](https://anthropic.com/news) [S01]"
        }
        with self.assertRaises(CitationProvenanceError):
            validate_llm_output(invalid_data_2, self.candidate_map)

        # 情況 3: Signals 的巢狀欄位出現 URL
        invalid_data_3 = {
            "overview": "這是一個重要突破 [S01]",
            "signals": [
                {
                    "title": "新進展",
                    "summary": "請參考 www.example.com",
                    "source_ids": ["S01"]
                }
            ]
        }
        with self.assertRaises(CitationProvenanceError):
            validate_llm_output(invalid_data_3, self.candidate_map)

    def test_test_b_legacy_citation_zero_tolerance(self):
        """Test B: LLM 輸出舊式帶連結角注格式 [[1]](...) 立即拒絕，但不誤殺普通 Markdown [1]"""
        # 1. 純粹舊式角注語法（完全不含 URL，例如 (foo) 目標）-> 必須獨立被 legacy_pat 拒絕
        pure_legacy_cases = [
            "借鏡騰訊混元的架構 [[1]](foo)",
            "深入解析 SQLite [1](foo)",
            "重要參考 [¹](foo)"
        ]
        for text in pure_legacy_cases:
            with self.subTest(legacy_text=text):
                with self.assertRaises(CitationProvenanceError) as cm:
                    validate_llm_output({"overview": text}, self.candidate_map)
                self.assertIn("舊式帶連結引用格式", str(cm.exception))

        # 2. 舊式角注帶 URL -> 亦必須明確判定為舊式帶連結引用格式拒絕
        with self.assertRaises(CitationProvenanceError) as cm:
            validate_llm_output({"overview": "借鏡騰訊混元的架構 [[1]](https://example.com/article)"}, self.candidate_map)
        self.assertIn("舊式帶連結引用格式", str(cm.exception))

        # 3. 普通 Markdown 數字 [1], [2] -> 不應誤殺，必須 PASS
        valid_data = {
            "overview": "主要有兩個維度：[1] 運算成本優化 [S01]；[2] 狀態同步機制 [S02]。",
        }
        # 應正常通過驗證，不拋出任何異常
        validate_llm_output(valid_data, self.candidate_map)

    def test_test_c_unauthorized_and_invalid_source_id(self):
        """Test C: Overview 出現未授權或格式錯誤的 Source ID 立即拒絕"""
        # 未授權 ID (S99) -> 拒絕 (當 candidate_map 沒有 S99 時)
        invalid_data_1 = {
            "overview": "這是未授權引用 [S99]"
        }
        with self.assertRaises(CitationProvenanceError) as cm:
            validate_llm_output(invalid_data_1, self.candidate_map)
        self.assertIn("未授權的 Source ID [S99]", str(cm.exception))

        # 但若 candidate_map 真有 S99，則必須 PASS
        mock_map_with_s99 = dict(self.candidate_map)
        mock_map_with_s99["S99"] = {
            "source_id": "S99",
            "article_key": "dummy_key",
            "canonical_url": "https://example.com/s99",
            "title": "S99 Title",
            "source_name": "Test Source",
            "is_serendipity": False
        }
        valid_data_s99 = {"overview": "這是合法引用 [S99]"}
        validate_llm_output(valid_data_s99, mock_map_with_s99)

        # Signal 缺少來源或包含未授權來源 -> 拒絕
        invalid_data_3 = {
            "overview": "這是正常 overview [S01]",
            "signals": [
                {
                    "title": "空來源訊號",
                    "source_ids": []
                }
            ]
        }
        with self.assertRaises(CitationProvenanceError) as cm:
            validate_llm_output(invalid_data_3, self.candidate_map)
        self.assertIn("source_ids 不可為空", str(cm.exception))

    def test_malformed_source_id_fail_cases(self):
        """
        FAIL cases 嚴格測試：
        所有畸形、小寫、帶符號的疑似 Source-ID token 必須在 validate_llm_output 階段被阻擋，
        拋出 CitationProvenanceError，絕不能原樣外洩或被略過。
        """
        fail_tokens = [
            "[S1]",
            "[S001]",
            "[S_01]",
            "[S-01]",
            "[s01]",
            "[S01x]",
            "[S 01]"
        ]

        for token in fail_tokens:
            with self.subTest(token=token):
                data = {"overview": f"這是一段測試內文，引用了 {token}。"}
                with self.assertRaises(CitationProvenanceError) as cm:
                    validate_llm_output(data, self.candidate_map)
                # 必須報格式不合規或未授權，絕不能靜默通過
                self.assertTrue(
                    "不合規的 Source ID 格式" in str(cm.exception) or
                    "未授權的 Source ID" in str(cm.exception),
                    f"Token {token} raised unexpected exception message: {cm.exception}"
                )

    def test_valid_source_id_pass_cases(self):
        """
        PASS cases 測試：
        合法格式 [S01], [S02] 必須順利通過驗證，無任何例外。
        """
        valid_data = {
            "overview": "主要發現包含 Claude 3.5 [S01] 以及 SQLite 的深入探討 [S02]。",
            "signals": [
                {
                    "title": "訊號A",
                    "source_ids": ["S01", "S02"]
                }
            ]
        }
        # 應完全通過
        validate_llm_output(valid_data, self.candidate_map)

    def test_normal_markdown_preserved_and_not_misidentified(self):
        """
        普通 Markdown 隔離測試：
        [Summary], [Source], [Strategy], [System], [System Design], [1], [2]
        不得被當成疑似 Source-ID 進行錯誤校驗或誤殺，亦不得在 resolve 時被替換破壞。
        """
        overview = (
            "## [Summary] 本期要點\n"
            "我們依據 [Strategy] 策略制定了兩個方向：\n"
            "[1] 透過 [Source] 評估技術來源；\n"
            "[2] 改善 [System] 架構設計 [System Design]。\n"
            "依據文獻 [S01] 與 [S02] 的實踐結果。"
        )

        data = {"overview": overview}
        # 1. 驗證 validate_llm_output 不得誤殺
        validate_llm_output(data, self.candidate_map)

        # 2. 驗證 extract_source_ids 僅提取 [S01], [S02]
        extracted = extract_source_ids(overview, self.candidate_map)
        self.assertEqual(extracted, ["S01", "S02"])

        # 3. 驗證 resolve_overview_citations 替換後，普通 Markdown 標籤完全保留
        resolved, sources = resolve_overview_citations(overview, self.candidate_map)
        self.assertIn("[Summary]", resolved)
        self.assertIn("[Strategy]", resolved)
        self.assertIn("[Source]", resolved)
        self.assertIn("[System]", resolved)
        self.assertIn("[System Design]", resolved)
        self.assertIn("[1]", resolved)
        self.assertIn("[2]", resolved)
        # S01, S02 則成功轉化為角標
        self.assertNotIn("[S01]", resolved)
        self.assertNotIn("[S02]", resolved)
        self.assertIn("[¹]", resolved)
        self.assertIn("[²]", resolved)

    def test_extract_source_tokens_helper(self):
        """驗證 extract_source_tokens 輔助函式的獨立行為"""
        sample = "分析 [S01] 與 [s01] 以及 [S-01]、[S001]、[S1]、[S 01]、[S01x]，還有 [Summary] 和 [1]"
        tokens = extract_source_tokens(sample)

        token_map = {inner: is_valid for _, inner, is_valid in tokens}
        self.assertTrue(token_map.get("S01"))
        self.assertFalse(token_map.get("s01"))
        self.assertFalse(token_map.get("S-01"))
        self.assertFalse(token_map.get("S001"))
        self.assertFalse(token_map.get("S1"))
        self.assertFalse(token_map.get("S 01"))
        self.assertFalse(token_map.get("S01x"))

        # [Summary] 與 [1] 完全不在 tokens 內
        raw_inners = [inner for _, inner, _ in tokens]
        self.assertNotIn("Summary", raw_inners)
        self.assertNotIn("1", raw_inners)

    def test_test_d_python_provenance_assembly(self):
        """Test D: 驗證 Python 依據 [Sxx] 正確解析角標，並嚴格依首次出現順序建立 1..K citation_sources"""
        raw_overview = (
            "在開發過程中，先借鏡了 Simon Willison 的 SQLite 查詢思維 [S02]；"
            "隨後結合了 Claude 3.5 的推論架構 [S01]。"
            "值得注意的是，SQLite 查詢優化 [S02] 再次證明了本地小工具的威力。"
        )

        # 1. 驗證 extract_source_ids
        extracted = extract_source_ids(raw_overview, self.candidate_map)
        # S02 先出現，S01 後出現，且 S02 去重
        self.assertEqual(extracted, ["S02", "S01"])

        # 2. 驗證 resolve_overview_citations
        resolved_overview, citation_sources = resolve_overview_citations(raw_overview, self.candidate_map)

        # 驗證正文中的角標序號與超連結
        simon_url = self.candidate_map["S02"]["canonical_url"]
        claude_url = self.candidate_map["S01"]["canonical_url"]

        self.assertIn(f"[¹]({simon_url})", resolved_overview)
        self.assertIn(f"[²]({claude_url})", resolved_overview)
        self.assertNotIn("[S01]", resolved_overview)
        self.assertNotIn("[S02]", resolved_overview)

        # 驗證 citation_sources 屬性完整與順序對齊
        self.assertEqual(len(citation_sources), 2)
        # 第 1 個是 S02 (citation_number=1)
        self.assertEqual(citation_sources[0]["citation_number"], 1)
        self.assertEqual(citation_sources[0]["source_id"], "S02")
        self.assertEqual(citation_sources[0]["original_url"], simon_url)
        self.assertTrue(citation_sources[0]["is_serendipity"])

        # 第 2 個是 S01 (citation_number=2)
        self.assertEqual(citation_sources[1]["citation_number"], 2)
        self.assertEqual(citation_sources[1]["source_id"], "S01")
        self.assertEqual(citation_sources[1]["original_url"], claude_url)
        self.assertFalse(citation_sources[1]["is_serendipity"])

        # 驗證 article_key
        for src in citation_sources:
            self.assertTrue(len(src["article_key"]) == 64)

    def test_test_f_humanizer_token_preservation(self):
        """Test F: 驗證 SpeakHumanCleaner 洗滌文字時絕不破壞 [S01] 等代碼，並將 一人操盤手 轉為 獨立開發者"""
        cleaner = SpeakHumanCleaner()
        text = "身為一人操盤手，這是一個高品質的項目 [S01]，底層算法架構非常穩定 [S02]。"
        cleaned = cleaner.clean(text)
        self.assertIn("[S01]", cleaned)
        self.assertIn("[S02]", cleaned)
        self.assertIn("獨立開發者", cleaned)
        self.assertNotIn("一人操盤手", cleaned)

        # 洗滌後再次執行 validate_llm_output 必須完全合規
        validate_llm_output({"overview": cleaned}, self.candidate_map)


if __name__ == "__main__":
    unittest.main()

