import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from main import (
    NEWS_MIN_ARTICLE_COUNT_ENV,
    REQUIRED_ENV_NAMES,
    extract_news_keyword_candidates,
    format_news_keyword_candidates_message,
    format_selected_news_keyword_message,
    main as run_main,
    parse_args,
    resolve_min_news_article_count,
    resolve_min_news_keyword_candidate_count,
    validate_news_article_count,
    validate_args,
)
from news_keyword import NEWS_KEYWORD_COUNT
from utils.common_util import validate_required_environment


REQUIRED_ENV_VALUES = {
    "LLM_PROVIDER": "codex",
    "LLM_MODEL": "gpt-test",
    NEWS_MIN_ARTICLE_COUNT_ENV: "3",
    "TELEGRAM_BOT_TOKEN": "telegram-token",
    "TELEGRAM_CHAT_ID": "telegram-chat-id",
}


class FakeLLMClient:
    provider = "fake"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate_text(
        self,
        prompt,
        output_dir,
        model=None,
        reasoning_effort=None,
        response_json_schema=None,
        response_mime_type=None,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "output_dir": output_dir,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "response_json_schema": response_json_schema,
                "response_mime_type": response_mime_type,
            }
        )
        return self.outputs.pop(0)


class MainLLMFlowTest(unittest.TestCase):
    def test_parse_args_has_no_llm_provider_override(self):
        args = parse_args([])

        self.assertFalse(hasattr(args, "llm_provider"))

    def test_parse_args_accepts_insert_publish_content_flag(self):
        args = parse_args(["--insert-publish-content"])

        self.assertTrue(args.insert_publish_content)

    def test_parse_args_accepts_insert_news_quiz_flag(self):
        args = parse_args(["--insert-news-quiz"])

        self.assertTrue(args.insert_news_quiz)

    def test_validate_args_allows_default_final_selection_flow(self):
        args = parse_args([])

        self.assertIsNone(validate_args(args))

    def test_validate_required_environment_requires_requested_variable(self):
        with self.assertRaises(ValueError) as context:
            validate_required_environment(REQUIRED_ENV_NAMES, env={})

        message = str(context.exception)
        for required_name in REQUIRED_ENV_NAMES:
            self.assertIn(required_name, message)

    def test_validate_required_environment_allows_requested_variable(self):
        result = validate_required_environment(
            REQUIRED_ENV_NAMES,
            env=REQUIRED_ENV_VALUES,
        )

        self.assertIsNone(result)

    def test_validate_required_environment_allows_empty_required_list(self):
        result = validate_required_environment([], env={})

        self.assertIsNone(result)

    def test_resolve_min_news_keyword_candidate_count_uses_fixed_threshold(self):
        args = SimpleNamespace()

        self.assertEqual(resolve_min_news_keyword_candidate_count(args), NEWS_KEYWORD_COUNT)

    def test_resolve_min_news_article_count_reads_required_environment(self):
        self.assertEqual(
            resolve_min_news_article_count({NEWS_MIN_ARTICLE_COUNT_ENV: "3"}),
            3,
        )

    def test_resolve_min_news_article_count_rejects_invalid_values(self):
        with self.assertRaisesRegex(ValueError, NEWS_MIN_ARTICLE_COUNT_ENV):
            resolve_min_news_article_count({NEWS_MIN_ARTICLE_COUNT_ENV: "abc"})

        self.assertEqual(
            resolve_min_news_article_count({NEWS_MIN_ARTICLE_COUNT_ENV: "1"}),
            1,
        )

        with self.assertRaisesRegex(ValueError, "greater than 0"):
            resolve_min_news_article_count({NEWS_MIN_ARTICLE_COUNT_ENV: "0"})

    def test_validate_news_article_count_requires_configured_minimum(self):
        articles = [
            {"title": "one", "url": "https://example.com/1"},
            {"title": "two", "url": "https://example.com/2"},
        ]

        self.assertIsNone(validate_news_article_count(articles, 2))
        with self.assertRaisesRegex(RuntimeError, "Expected at least 3 news articles"):
            validate_news_article_count(articles, 3)

    def test_format_selected_news_keyword_message_includes_selected_fields(self):
        selected = {
            "keyword": "ETF",
            "source_title": "ETF & IPO <title>",
            "source_url": 'https://example.com/a?x=1&name="ETF"',
            "reason": "candidate reason",
            "selection_reason": "selection & reason <safe>",
            "keyword_description": "ETF desc",
            "quiz": {
                "question": "ETF question",
                "option_a": "right",
                "option_b": "wrong",
                "answer": "A",
                "explanation": "right가 기사 맥락에 맞기 때문입니다.",
            },
        }

        message = format_selected_news_keyword_message(selected)

        self.assertIn("<b>오늘의 경제뉴스 퀴즈</b>", message)
        self.assertNotIn("<b>최종 경제 키워드</b>", message)
        self.assertIn("<b>1. 뉴스제목</b>\nETF &amp; IPO &lt;title&gt;", message)
        self.assertIn(
            '<b>2. 뉴스링크</b>\n<a href="https://example.com/a?x=1&amp;name=&quot;ETF&quot;">원문 보기</a>',
            message,
        )
        self.assertIn("<b>3. 키워드</b>\n<b>ETF</b>", message)
        self.assertIn("<b>4. 한줄설명</b>\nETF desc", message)
        self.assertIn("<b>5. 퀴즈</b>\nQ. ETF question", message)
        self.assertIn("<b>6. 해설</b>\n정답: <b>A</b>\nright가 기사 맥락에 맞기 때문입니다.", message)
        self.assertNotIn("선정 사유", message)
        self.assertNotIn("selection &amp; reason", message)
        self.assertLess(message.index("<b>1. 뉴스제목</b>"), message.index("<b>2. 뉴스링크</b>"))
        self.assertLess(message.index("<b>2. 뉴스링크</b>"), message.index("<b>3. 키워드</b>"))
        self.assertLess(message.index("<b>3. 키워드</b>"), message.index("<b>4. 한줄설명</b>"))
        self.assertLess(message.index("<b>4. 한줄설명</b>"), message.index("<b>5. 퀴즈</b>"))
        self.assertLess(message.index("<b>5. 퀴즈</b>"), message.index("<b>6. 해설</b>"))
        self.assertIn("</b>\n\n<b>1. 뉴스제목</b>", message)

    def test_format_news_keyword_candidates_message_includes_quiz_fields(self):
        candidates = [
            {
                "keyword": "ETF",
                "source_title": "ETF title",
                "source_url": "https://example.com/a",
                "reason": "candidate reason",
                "keyword_description": "ETF desc",
                "quiz": {
                    "question": "ETF question",
                    "option_a": "right",
                    "option_b": "wrong",
                    "answer": "A",
                    "explanation": "right가 기사 맥락에 맞기 때문입니다.",
                },
            }
        ]

        message = format_news_keyword_candidates_message(candidates)

        self.assertIn("<b>한줄설명</b>\nETF desc", message)
        self.assertIn("<b>미니 퀴즈</b>", message)
        self.assertIn("Q. ETF question", message)
        self.assertIn("A. right", message)
        self.assertIn("B. wrong", message)
        self.assertIn("<b>정답: A</b>", message)
        self.assertIn("<b>해설</b>\nright가 기사 맥락에 맞기 때문입니다.", message)
        self.assertNotIn("선정 근거", message)
        self.assertNotIn("candidate reason", message)

    def test_main_returns_error_when_required_environment_is_missing(self):
        with (
            patch("main.load_dotenv"),
            patch.dict(os.environ, {}, clear=True),
            patch("main.create_llm_client") as create_llm_client,
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()) as stderr,
        ):
            exit_code = run_main([])

        self.assertEqual(exit_code, 2)
        for required_name in REQUIRED_ENV_NAMES:
            self.assertIn(required_name, stderr.getvalue())
        create_llm_client.assert_not_called()

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
            "news_quiz_insert_result": None,
        }

        with (
            patch("main.load_dotenv"),
            patch.dict(os.environ, REQUIRED_ENV_VALUES, clear=True),
            patch("main.create_llm_client", return_value=SimpleNamespace(default_model="test")),
            patch("main.suggest_news_keyword_candidates", return_value=candidates),
            patch("main.print_news_keyword_candidates"),
            patch("main.run_news_keyword_selection_process", return_value=selection_result) as run_selection,
            patch("main.print_news_keyword_selection_insert_result"),
            patch("main.send_news_keyword_candidates_to_telegram") as send_candidates,
            patch("main.send_selected_news_keyword_to_telegram") as send_selected,
            redirect_stdout(StringIO()),
        ):
            exit_code = run_main(["--send-telegram"])

        self.assertEqual(exit_code, 0)
        self.assertFalse(run_selection.call_args.kwargs["insert_publish_content"])
        self.assertFalse(run_selection.call_args.kwargs["insert_news_quiz"])
        send_candidates.assert_not_called()
        send_selected.assert_called_once_with(selected, use_test_chat=False)

    def test_main_can_insert_publish_content_when_flag_is_enabled(self):
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
            "news_quiz_insert_result": None,
        }

        with (
            patch("main.load_dotenv"),
            patch.dict(os.environ, REQUIRED_ENV_VALUES, clear=True),
            patch("main.create_llm_client", return_value=SimpleNamespace(default_model="test")),
            patch("main.suggest_news_keyword_candidates", return_value=candidates),
            patch("main.print_news_keyword_candidates"),
            patch("main.run_news_keyword_selection_process", return_value=selection_result) as run_selection,
            patch("main.print_news_keyword_selection_insert_result"),
            redirect_stdout(StringIO()),
        ):
            exit_code = run_main(["--insert-publish-content"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(run_selection.call_args.kwargs["insert_publish_content"])
        self.assertFalse(run_selection.call_args.kwargs["insert_news_quiz"])

    def test_main_can_insert_news_quiz_when_flag_is_enabled(self):
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
            "news_quiz_insert_result": {
                "table": "mq_news_quiz",
                "affected_rows": 1,
                "lastrowid": 7,
            },
        }

        with (
            patch("main.load_dotenv"),
            patch.dict(os.environ, REQUIRED_ENV_VALUES, clear=True),
            patch("main.create_llm_client", return_value=SimpleNamespace(default_model="test")),
            patch("main.suggest_news_keyword_candidates", return_value=candidates),
            patch("main.print_news_keyword_candidates"),
            patch("main.run_news_keyword_selection_process", return_value=selection_result) as run_selection,
            patch("main.print_news_keyword_selection_insert_result"),
            redirect_stdout(StringIO()),
        ):
            exit_code = run_main(["--insert-news-quiz"])

        self.assertEqual(exit_code, 0)
        self.assertFalse(run_selection.call_args.kwargs["insert_publish_content"])
        self.assertTrue(run_selection.call_args.kwargs["insert_news_quiz"])

    def test_extract_news_keyword_candidates_retries_with_injected_llm_client(self):
        articles = [
            {"title": "코스피 8000선 돌파", "url": "https://example.com/a"},
            {"title": "ETF 신규 상장", "url": "https://example.com/b"},
            {"title": "IPO 상장 흥행", "url": "https://example.com/c"},
            {"title": "가상자산 법인계좌 확대", "url": "https://example.com/d"},
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

        self.assertEqual(len(candidates), NEWS_KEYWORD_COUNT)
        self.assertEqual(output, second_output)
        self.assertEqual([call["reasoning_effort"] for call in llm_client.calls], ["low", "medium"])
        self.assertEqual([call["model"] for call in llm_client.calls], ["test-model", "test-model"])


    def test_extract_news_keyword_candidates_can_require_eight_quiz_candidates(self):
        articles = [
            {"title": f"K{index} title", "url": f"https://example.com/{index}"}
            for index in range(8)
        ]
        output = json.dumps(
            [
                {
                    "keyword": f"K{index}",
                    "source_url": f"https://example.com/{index}",
                    "reason": f"reason {index}",
                    "keyword_description": f"description {index}",
                    "quiz": {
                        "question": f"question {index}",
                        "option_a": "plausible A",
                        "option_b": "plausible B",
                        "answer": "A",
                        "explanation": f"explanation {index}",
                    },
                }
                for index in range(8)
            ],
            ensure_ascii=False,
        )
        args = SimpleNamespace(news_keyword_model="test-model")
        llm_client = FakeLLMClient([output])

        candidates, _ = extract_news_keyword_candidates(
            args=args,
            output_dir=Path("output"),
            articles=articles,
            prompt="prompt",
            llm_client=llm_client,
            candidate_count=8,
            include_learning_content=True,
        )

        self.assertEqual(len(candidates), 8)
        self.assertEqual(candidates[0]["quiz"]["explanation"], "explanation 0")
        self.assertEqual(llm_client.calls[0]["response_json_schema"]["minItems"], 8)
        self.assertIn("quiz", llm_client.calls[0]["response_json_schema"]["items"]["required"])
        self.assertEqual(llm_client.calls[0]["response_mime_type"], "application/json")


if __name__ == "__main__":
    unittest.main()
