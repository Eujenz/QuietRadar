"""
test_newsletter_sections.py - 電子報區塊與用字修正測試

1. strip_source_codes / clean_signal_texts：訊號文字不殘留 [S01]
2. build_further_reading：只收未引用、合法、有說明的候選文章，最多 3 則
3. format_newsletter_markdown：渲染「今日其他值得一讀」；不傳 signals 時不出現訊號區
4. sanitize_taiwan_terms：修正 OpenCC 過度轉換（說明瞭、擴充套件、引數、重新整理紀錄）
"""

import unittest

from pipeline import (
    strip_source_codes,
    clean_signal_texts,
    build_further_reading,
    format_newsletter_markdown,
    sanitize_taiwan_terms,
    rate_limit_wait_seconds,
)

CANDIDATES = {
    f"S{i:02d}": {
        "source_id": f"S{i:02d}",
        "canonical_url": f"https://example.com/{i}",
        "title": f"文章{i}",
        "source_name": "來源",
    }
    for i in range(1, 8)
}


class TestSourceCodes(unittest.TestCase):
    def test_strip_codes(self):
        self.assertEqual(strip_source_codes("美團成長逾 200% [S06]；Waymo 受限[S12][S01]。"),
                         "美團成長逾 200%；Waymo 受限。")

    def test_clean_signal_texts_keeps_source_ids(self):
        sigs = [{"summary": "事實 [S06]", "source_ids": ["S06"]}]
        clean_signal_texts(sigs)
        self.assertEqual(sigs[0]["summary"], "事實")
        self.assertEqual(sigs[0]["source_ids"], ["S06"])


class TestFurtherReading(unittest.TestCase):
    def test_filters_and_limits(self):
        raw = [
            {"source_id": "S01", "note": "已引用，應剔除"},
            {"source_id": "S99", "note": "不存在"},
            {"source_id": "S03", "note": ""},
            {"source_id": "S04", "note": "值得讀 [S04]"},
            {"source_id": "S04", "note": "重複"},
            {"source_id": "S05", "note": "第二則"},
            {"source_id": "S06", "note": "第三則"},
            {"source_id": "S07", "note": "超過上限"},
            "garbage",
        ]
        out = build_further_reading(raw, CANDIDATES, cited_ids=["S01", "S02"])
        self.assertEqual([x["source_id"] for x in out], ["S04", "S05", "S06"])
        self.assertEqual(out[0]["note"], "值得讀")
        self.assertEqual(out[0]["original_url"], "https://example.com/4")

    def test_non_list_returns_empty(self):
        self.assertEqual(build_further_reading(None, CANDIDATES, []), [])


class TestRendering(unittest.TestCase):
    def test_further_reading_rendered_and_signals_hidden(self):
        fr = build_further_reading([{"source_id": "S05", "note": "一句說明。"}], CANDIDATES, ["S01"])
        md = format_newsletter_markdown(
            [], overview="# 標題\n\n內文。", signals=None, further_reading=fr, now_str="2026-11-20 07:25"
        )
        self.assertIn("## 今日其他值得一讀", md)
        self.assertIn("[文章5](https://example.com/5) · 來源", md)
        self.assertIn("一句說明。", md)
        self.assertNotIn("今日情報訊號", md)


class TestOpenCCFixes(unittest.TestCase):
    def test_fixes(self):
        self.assertEqual(sanitize_taiwan_terms("這說明了新瓶頸"), "這說明了新瓶頸")
        self.assertIn("說明了", sanitize_taiwan_terms("说明了新瓶颈"))
        self.assertIn("擴展", sanitize_taiwan_terms("难以扩展且成本高"))
        self.assertIn("參數", sanitize_taiwan_terms("模型参数"))
        self.assertIn("刷新紀錄", sanitize_taiwan_terms("跑分可以刷新纪录"))
        self.assertIn("清楚明瞭", sanitize_taiwan_terms("清楚明瞭"))
        self.assertEqual(sanitize_taiwan_terms("是為了解決維護問題"), "是為了解決維護問題")
        self.assertIn("為了瞭解", sanitize_taiwan_terms("為了瞭解市場"))
        self.assertIn("痛點", sanitize_taiwan_terms("長期的痛點"))
        self.assertNotIn("實質困擾", sanitize_taiwan_terms("長期的痛點"))


class TestRateLimitWait(unittest.TestCase):
    def test_waits(self):
        self.assertEqual(rate_limit_wait_seconds(1), 30)
        self.assertEqual(rate_limit_wait_seconds(2), 60)
        self.assertEqual(rate_limit_wait_seconds(3), 90)
        self.assertEqual(rate_limit_wait_seconds(9), 90)
        self.assertEqual(rate_limit_wait_seconds(1, "75"), 75)
        self.assertEqual(rate_limit_wait_seconds(1, "600"), 120)
        self.assertEqual(rate_limit_wait_seconds(1, "abc"), 30)


if __name__ == "__main__":
    unittest.main()
