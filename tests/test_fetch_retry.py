"""
test_fetch_retry.py - RSS 抓取暫時性失敗重試測試

1. 第一次連線例外、第二次成功 -> 回傳成功回應
2. 第一次 503、第二次 200 -> 回傳 200
3. 404 為永久錯誤 -> 不重試
4. 兩次皆例外 -> 拋出最後的例外
5. 兩次皆 503 -> 回傳最後的 503，不無限重試
"""

import unittest
from unittest.mock import MagicMock, patch

import httpx

from pipeline import get_with_transient_retry


def _resp(code):
    r = MagicMock()
    r.status_code = code
    return r


@patch("pipeline.time.sleep")
class TestTransientRetry(unittest.TestCase):
    def test_retries_after_connection_error(self, mock_sleep):
        client = MagicMock()
        client.get.side_effect = [httpx.ConnectError("reset"), _resp(200)]
        resp = get_with_transient_retry(client, "https://example.com/rss")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(client.get.call_count, 2)
        mock_sleep.assert_called_once()

    def test_retries_after_503(self, mock_sleep):
        client = MagicMock()
        client.get.side_effect = [_resp(503), _resp(200)]
        resp = get_with_transient_retry(client, "https://example.com/rss")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(client.get.call_count, 2)

    def test_does_not_retry_404(self, mock_sleep):
        client = MagicMock()
        client.get.side_effect = [_resp(404), _resp(200)]
        resp = get_with_transient_retry(client, "https://example.com/rss")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(client.get.call_count, 1)
        mock_sleep.assert_not_called()

    def test_raises_after_exhausting_retries(self, mock_sleep):
        client = MagicMock()
        client.get.side_effect = [httpx.ReadTimeout("t1"), httpx.ReadTimeout("t2")]
        with self.assertRaises(httpx.ReadTimeout):
            get_with_transient_retry(client, "https://example.com/rss")
        self.assertEqual(client.get.call_count, 2)

    def test_returns_last_transient_response(self, mock_sleep):
        client = MagicMock()
        client.get.side_effect = [_resp(503), _resp(503), _resp(200)]
        resp = get_with_transient_retry(client, "https://example.com/rss")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(client.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
