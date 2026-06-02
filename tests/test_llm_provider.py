import os
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_client import CodexLLMClient
from gemini_client import GEMINI_API_KEY_ENV, GeminiLLMClient
from llm_provider import LLMClient, LLM_PROVIDER_ENV, create_llm_client


class LLMClientTest(unittest.TestCase):
    def test_generate_text_routes_to_selected_client(self):
        class FakeProviderClient:
            provider = "fake"
            default_model = "fake-default"

            def __init__(self):
                self.calls = []

            def generate_text(self, prompt, output_dir, model=None, reasoning_effort=None):
                self.calls.append(
                    {
                        "prompt": prompt,
                        "output_dir": output_dir,
                        "model": model,
                        "reasoning_effort": reasoning_effort,
                    }
                )
                return "fake-output"

        provider_client = FakeProviderClient()
        client = LLMClient(provider="fake", clients={"fake": provider_client})

        result = client.generate_text(
            prompt="prompt",
            output_dir=Path("output"),
            model="model",
            reasoning_effort="low",
        )

        self.assertEqual(result, "fake-output")
        self.assertEqual(client.default_model, "fake-default")
        self.assertEqual(provider_client.calls[0]["model"], "model")

    def test_create_llm_client_rejects_unsupported_provider(self):
        with self.assertRaisesRegex(ValueError, "Unsupported LLM provider"):
            create_llm_client("unsupported")

    def test_create_llm_client_uses_codex_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            client = create_llm_client()

        self.assertEqual(client.provider, "codex")
        self.assertIsInstance(client.selected_client, CodexLLMClient)

    def test_create_llm_client_supports_gemini_provider(self):
        client = create_llm_client("gemini")

        self.assertIsInstance(client, LLMClient)
        self.assertEqual(client.provider, "gemini")
        self.assertIsInstance(client.selected_client, GeminiLLMClient)

    def test_create_llm_client_uses_env_provider_and_api_key(self):
        env = {
            LLM_PROVIDER_ENV: "gemini",
            GEMINI_API_KEY_ENV: "secret",
        }

        with patch.dict(os.environ, env):
            client = create_llm_client()

        self.assertEqual(client.provider, "gemini")
        self.assertIsInstance(client.selected_client, GeminiLLMClient)
        self.assertEqual(client.selected_client.api_key, "secret")


if __name__ == "__main__":
    unittest.main()
