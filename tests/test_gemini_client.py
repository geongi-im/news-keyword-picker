import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from gemini_client import (
    GeminiLLMClient,
    build_gemini_generation_config,
    run_gemini_generate_content,
)


class GeminiLLMClientTest(unittest.TestCase):
    def test_generate_text_delegates_to_gemini_generate_content(self):
        with patch(
            "gemini_client.run_gemini_generate_content",
            return_value='[{"keyword":"ETF","source_url":"https://x","reason":"r"}]',
        ) as run:
            client = GeminiLLMClient(
                api_key="secret",
                default_model="gemini-env",
                response_json_schema={"type": "array"},
            )
            result = client.generate_text(prompt="prompt", model=None)

        self.assertEqual(result, '[{"keyword":"ETF","source_url":"https://x","reason":"r"}]')
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["prompt"], "prompt")
        self.assertEqual(run.call_args.kwargs["model"], "gemini-env")
        self.assertEqual(run.call_args.kwargs["api_key"], "secret")
        self.assertEqual(run.call_args.kwargs["response_json_schema"], {"type": "array"})

    def test_generate_text_raises_when_model_is_missing(self):
        client = GeminiLLMClient()

        with self.assertRaisesRegex(ValueError, "LLM model is required"):
            client.generate_text(prompt="prompt", output_dir=Path("output"))

    def test_generate_text_raises_when_gemini_returns_empty_response(self):
        with patch("gemini_client.run_gemini_generate_content", return_value=""):
            client = GeminiLLMClient()

            with self.assertRaisesRegex(RuntimeError, "empty response"):
                client.generate_text(
                    prompt="prompt",
                    output_dir=Path("output"),
                    model="gemini-env",
                )


class GeminiGenerationTest(unittest.TestCase):
    def test_build_gemini_generation_config_includes_json_schema(self):
        schema = {"type": "array"}

        config = build_gemini_generation_config(response_json_schema=schema)

        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertEqual(config["response_json_schema"], schema)

    def test_run_gemini_generate_content_uses_google_genai_client(self):
        calls = []

        class FakeModels:
            def generate_content(self, **kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(text=" result ")

        class FakeClient:
            def __init__(self, api_key=None):
                calls.append({"api_key": api_key})
                self.models = FakeModels()

        google_module = types.ModuleType("google")
        genai_module = types.ModuleType("genai")
        genai_module.Client = FakeClient
        google_module.genai = genai_module

        with patch.dict(sys.modules, {"google": google_module, "google.genai": genai_module}):
            result = run_gemini_generate_content(
                prompt="prompt",
                model="gemini-test",
                api_key="secret",
                response_json_schema={"type": "array"},
            )

        self.assertEqual(result, "result")
        self.assertEqual(calls[0], {"api_key": "secret"})
        self.assertEqual(calls[1]["model"], "gemini-test")
        self.assertEqual(calls[1]["contents"], "prompt")
        self.assertEqual(calls[1]["config"]["response_mime_type"], "application/json")
        self.assertEqual(calls[1]["config"]["response_json_schema"], {"type": "array"})


if __name__ == "__main__":
    unittest.main()
