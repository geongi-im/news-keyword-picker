import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from main import (
    extract_news_keyword_candidates,
    format_selected_news_keyword_message,
    main as run_main,
    parse_args,
)


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

    def test_format_selected_news_keyword_message_includes_selected_fields(self):
        selected = {
            "keyword": "ETF",
            "source_title": "ETF & IPO <title>",
            "source_url": 'https://example.com/a?x=1&name="ETF"',
            "reason": "candidate reason",
            "selection_reason": "selection & reason <safe>",
        }

        message = format_selected_news_keyword_message(selected)

        self.assertIn("<b>최종 경제 키워드</b>", message)
        self.assertIn("키워드: <b>ETF</b>", message)
        self.assertIn("원본 제목: ETF &amp; IPO &lt;title&gt;", message)
        self.assertIn("선정 사유: selection &amp; reason &lt;safe&gt;", message)
        self.assertIn(
            "원본 링크: "
            '<a href="https://example.com/a?x=1&amp;name=&quot;ETF&quot;">원문 보기</a>',
            message,
        )

    def test_main_sends_selected_keyword_when_selection_is_enabled(self):
        candidates = [
            {
                "keyword": "ETF",
                "source_title": "ETF title",
                "source_url": "https://example.com/a",
                "reason": "candidate reason",
            }
        ]
        selected = {
            **candidates[0],
            "selection_reason": "selection reason",
        }
        selection_result = {
            "checked_candidates": candidates,
            "eligible_candidates": candidates,
            "selected_candidate": selected,
            "target_date": "2026-06-02",
            "insert_result": [],
        }

        with (
            patch("main.load_dotenv"),
            patch("main.create_llm_client", return_value=SimpleNamespace(default_model="test")),
            patch("main.suggest_news_keyword_candidates", return_value=candidates),
            patch("main.print_news_keyword_candidates"),
            patch("main.run_news_keyword_selection_insert_process", return_value=selection_result),
            patch("main.print_news_keyword_selection_insert_result"),
            patch("main.send_news_keyword_candidates_to_telegram") as send_candidates,
            patch("main.send_selected_news_keyword_to_telegram") as send_selected,
            redirect_stdout(StringIO()),
        ):
            exit_code = run_main(["--send-telegram", "--select-keyword-and-insert"])

        self.assertEqual(exit_code, 0)
        send_candidates.assert_not_called()
        send_selected.assert_called_once_with(selected, use_test_chat=False)

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
