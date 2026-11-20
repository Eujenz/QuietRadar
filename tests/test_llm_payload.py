"""
test_llm_payload.py - OpenRouter 請求內容測試

1. ERNIE：固定 baidu 供應商、不允許改路由、開啟 reasoning、提高 max_tokens
2. Claude（anthropic/）：不送 temperature
3. 其他模型：保留 temperature，不加 provider / reasoning
4. 未設定 LLM_MODEL 時預設為 ERNIE
"""

import os
import unittest
from unittest.mock import patch

from pipeline import (
    build_llm_payload,
    DEFAULT_LLM_MODEL,
    DEFAULT_MAX_TOKENS,
    REASONING_MAX_TOKENS,
    SimpleLLMDistiller,
)

MESSAGES = [{"role": "user", "content": "hi"}]


class TestBuildLlmPayload(unittest.TestCase):
    def test_ernie_pins_provider_and_enables_reasoning(self):
        p = build_llm_payload("baidu/ernie-5.5-flash-preview:free", MESSAGES)
        self.assertEqual(p["provider"], {"only": ["baidu"], "allow_fallbacks": False})
        self.assertEqual(p["reasoning"], {"enabled": True})
        self.assertEqual(p["max_tokens"], REASONING_MAX_TOKENS)
        self.assertTrue(p["stream"])
        self.assertEqual(p["response_format"], {"type": "json_object"})

    def test_payload_overrides_are_not_shared_between_calls(self):
        p1 = build_llm_payload("baidu/ernie-5.5-flash-preview:free", MESSAGES)
        p1["provider"]["only"].append("other")
        p2 = build_llm_payload("baidu/ernie-5.5-flash-preview:free", MESSAGES)
        self.assertEqual(p2["provider"]["only"], ["baidu"])

    def test_claude_omits_temperature(self):
        p = build_llm_payload("anthropic/claude-sonnet-5.5", MESSAGES)
        self.assertNotIn("temperature", p)
        self.assertNotIn("provider", p)

    def test_other_models_keep_temperature(self):
        p = build_llm_payload("nvidia/nemotron-3-ultra-550b-a55b:free", MESSAGES)
        self.assertEqual(p["temperature"], 0.2)
        self.assertEqual(p["max_tokens"], DEFAULT_MAX_TOKENS)
        self.assertNotIn("reasoning", p)


class TestDefaultModel(unittest.TestCase):
    def test_defaults_to_ernie_when_unset_or_blank(self):
        for value in (None, "", "  "):
            env = {"LLM_API_KEY": "x"}
            if value is not None:
                env["LLM_MODEL"] = value
            with patch.dict(os.environ, env, clear=False):
                if value is None:
                    os.environ.pop("LLM_MODEL", None)
                self.assertEqual(SimpleLLMDistiller().model, DEFAULT_LLM_MODEL)
        self.assertEqual(DEFAULT_LLM_MODEL, "baidu/ernie-5.5-flash-preview:free")


if __name__ == "__main__":
    unittest.main()
