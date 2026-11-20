"""
test_editorial_format.py - 專欄格式守門測試

1. strip_structure_labels：移除「定錨：」「案例一：」「主筆總結」等格式標籤，保留正常小標
2. find_banned_body_phrases：偵測 Micro-TAM、驗證道具等已淘汰寫法
3. SimpleLLMDistiller：LLM_MODEL 指定的模型排在第一順位
4. prompts/editorial.md 不再要求輸出格式標籤，且已移除舊框架
"""

import os
import unittest
from unittest.mock import patch

from pipeline import (
    strip_structure_labels,
    find_banned_body_phrases,
    FALLBACK_LLM_MODELS,
    SimpleLLMDistiller,
)


class TestStripStructureLabels(unittest.TestCase):
    def test_strips_label_prefix_and_keeps_rest(self):
        self.assertEqual(strip_structure_labels("### 定錨：從模型競賽轉向利潤防衛"), "### 從模型競賽轉向利潤防衛")
        self.assertEqual(strip_structure_labels("### 案例一：T0 開源時序模型"), "### T0 開源時序模型")
        self.assertEqual(strip_structure_labels("### 專欄｜當 AI 能逆向 Adobe"), "### 當 AI 能逆向 Adobe")
        self.assertEqual(strip_structure_labels("### 【定錨 / 編輯微評】：誰受益"), "### 誰受益")
        self.assertEqual(strip_structure_labels("### 一、 基準測試的幻覺"), "### 基準測試的幻覺")

    def test_drops_label_only_heading_but_keeps_paragraph(self):
        text = "導言。\n\n## 拆解\n\n這是段落。"
        out = strip_structure_labels(text)
        self.assertNotIn("拆解", out)
        self.assertIn("這是段落。", out)

    def test_keeps_normal_heading_that_starts_with_label_word(self):
        self.assertEqual(strip_structure_labels("### 拆解供應鏈的新邏輯"), "### 拆解供應鏈的新邏輯")

    def test_strips_bold_label(self):
        self.assertEqual(strip_structure_labels("**折射**：這意味著利潤池移動。"), "這意味著利潤池移動。")

    def test_body_text_untouched(self):
        body = "# 大標題\n\n導言段落提到定錨效應 [S01]。"
        self.assertEqual(strip_structure_labels(body), body)


class TestBannedPhrases(unittest.TestCase):
    def test_detects_old_framework_phrases(self):
        self.assertEqual(find_banned_body_phrases("這筆 micro-tam 怎麼算"), ["Micro-TAM"])
        self.assertIn("驗證道具", find_banned_body_phrases("驗證道具不必寫程式"))
        self.assertIn("防守邊界", find_banned_body_phrases("一人公司的防守邊界"))

    def test_clean_text_passes(self):
        self.assertEqual(find_banned_body_phrases("定價權正從模型公司移向雲端廠商 [S02]。"), [])


class TestModelOrder(unittest.TestCase):
    def test_llm_model_env_is_tried_first(self):
        with patch.dict(os.environ, {"LLM_MODEL": "anthropic/claude-sonnet-5.5", "LLM_API_KEY": "x"}):
            d = SimpleLLMDistiller()
        self.assertEqual(d.model, "anthropic/claude-sonnet-5.5")
        order = [d.model] + [m for m in FALLBACK_LLM_MODELS if m != d.model]
        self.assertEqual(order[0], "anthropic/claude-sonnet-5.5")
        self.assertEqual(order[1:], FALLBACK_LLM_MODELS)


class TestEditorialPromptFile(unittest.TestCase):
    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prompts", "editorial.md"), encoding="utf-8") as f:
            self.prompt = f.read()

    def test_requires_fact_discipline_and_single_column(self):
        self.assertIn("只能使用候選文章裡出現的事實", self.prompt)
        self.assertIn("每期只寫一篇專欄", self.prompt)

    def test_solopreneur_template_archived(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(os.path.exists(os.path.join(root, "prompts", "solopreneur.md")))


if __name__ == "__main__":
    unittest.main()
