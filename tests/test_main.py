import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from main import extract_news_keyword_candidates, parse_args


class FakeLLMClient:
    provider = "fake"

    def __init__(self, outputs):
        self.outputs = list(outputs)
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
        return self.outputs.pop(0)


class MainLLMFlowTest(unittest.TestCase):
    def test_parse_args_leaves_llm_provider_to_env_by_default(self):
        args = parse_args([])

        self.assertIsNone(args.llm_provider)

    def test_extract_news_keyword_candidates_retries_with_injected_llm_client(self):
        articles = [
            {"title": "코스피 8000선 돌파", "url": "https://example.com/a"},
            {"title": "ETF 신규 상장", "url": "https://example.com/b"},
            {"title": "IPO 상장 흥행", "url": "https://example.com/c"},
            {"title": "코인 법인계좌 확대", "url": "https://example.com/d"},
            {"title": "삼전닉스 반도체 급등", "url": "https://example.com/e"},
        ]
        first_output = json.dumps(
            [{"keyword": "코스피", "source_url": "https://example.com/a", "reason": "one"}],
            ensure_ascii=True,
        )
        second_output = json.dumps(
            [
                {"keyword": "코스피", "source_url": "https://example.com/a", "reason": "one"},
                {"keyword": "ETF", "source_url": "https://example.com/b", "reason": "two"},
                {"keyword": "IPO", "source_url": "https://example.com/c", "reason": "three"},
                {"keyword": "가상자산", "source_url": "https://example.com/d", "reason": "four"},
                {"keyword": "반도체", "source_url": "https://example.com/e", "reason": "five"},
            ],
            ensure_ascii=True,
        )
        args = SimpleNamespace(news_keyword_model="test-model")
        llm_client = FakeLLMClient([first_output, second_output])

        with redirect_stdout(StringIO()):
            candidates, output = extract_news_keyword_candidates(
                args=args,
                output_dir=Path("output"),
                articles=articles,
                prompt="prompt",
                llm_client=llm_client,
            )

        self.assertEqual(len(candidates), 5)
        self.assertEqual(output, second_output)
        self.assertEqual([call["reasoning_effort"] for call in llm_client.calls], ["low", "medium"])
        self.assertEqual([call["model"] for call in llm_client.calls], ["test-model", "test-model"])


if __name__ == "__main__":
    unittest.main()
