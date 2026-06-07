import json
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

from keyword_selection import (
    build_keyword_exists_query,
    build_selected_news_quiz_insert_params,
    build_selected_news_keyword_insert_params,
    insert_selected_news_quiz,
    insert_selected_news_keyword,
    mark_news_keyword_duplicates,
    parse_selected_viral_keyword_candidate,
    quote_mysql_identifier,
    resolve_news_keyword_storage_config,
    run_news_keyword_selection_process,
    select_viral_news_keyword_candidate,
)


class FakeSelectionLLMClient:
    default_model = "test-model"

    def __init__(self, output):
        self.output = output
        self.calls = []

    def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.output


class FakeCursor:
    def __init__(self):
        self.executions = []
        self.lastrowid = 0

    def execute(self, query, params):
        self.executions.append((query, params))
        self.lastrowid += 1
        return 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class KeywordSelectionTest(unittest.TestCase):
    def test_build_keyword_exists_query_quotes_identifiers(self):
        query = build_keyword_exists_query("app.keywords", "keyword")

        self.assertIn("FROM `app`.`keywords`", query)
        self.assertIn("WHERE `keyword` = %(keyword)s", query)

    def test_quote_mysql_identifier_rejects_invalid_identifier(self):
        with self.assertRaises(ValueError):
            quote_mysql_identifier("keywords; DROP TABLE keywords", "table")

    @patch("keyword_selection.fetch_one")
    def test_mark_news_keyword_duplicates_marks_existing_rows(self, fetch_one):
        def fake_fetch_one(db_config, query, params):
            return {"exists_flag": 1} if params["keyword"] == "ETF" else None

        fetch_one.side_effect = fake_fetch_one
        candidates = [
            {"keyword": "ETF", "source_url": "https://example.com/a", "reason": "one"},
            {"keyword": "IPO", "source_url": "https://example.com/b", "reason": "two"},
        ]

        checked = mark_news_keyword_duplicates(
            candidates,
            db_config={"host": "localhost"},
            table="news_keywords",
            column="keyword",
        )

        self.assertTrue(checked[0]["exists_in_db"])
        self.assertFalse(checked[1]["exists_in_db"])
        self.assertEqual(fetch_one.call_count, 2)

    def test_select_viral_keyword_candidate_uses_llm_choice(self):
        candidates = [
            {
                "keyword": "ETF",
                "source_title": "ETF title",
                "source_url": "https://example.com/a",
                "reason": "candidate reason",
                "keyword_description": "LLM ETF 설명",
                "quiz": {
                    "question": "LLM ETF 질문",
                    "option_a": "LLM 선택 A",
                    "option_b": "LLM 선택 B",
                    "answer": "A",
                    "explanation": "LLM 선택 A가 맞는 이유입니다.",
                },
            },
            {
                "keyword": "IPO",
                "source_title": "IPO title",
                "source_url": "https://example.com/b",
                "reason": "candidate reason",
            },
        ]
        output = json.dumps(
            {
                "keyword": "IPO",
                "source_url": "https://example.com/b",
                "reason": "selection reason",
            }
        )
        llm_client = FakeSelectionLLMClient(output)

        selected = select_viral_news_keyword_candidate(
            candidates,
            output_dir=Path("output"),
            llm_client=llm_client,
        )

        self.assertEqual(selected["keyword"], "IPO")
        self.assertEqual(selected["source_title"], "IPO title")
        self.assertEqual(selected["selection_reason"], "selection reason")
        self.assertEqual(llm_client.calls[0]["response_mime_type"], "application/json")

    def test_parse_selected_candidate_allows_unique_keyword_match(self):
        candidates = [
            {
                "keyword": "ETF",
                "source_title": "ETF title",
                "source_url": "https://example.com/a",
                "reason": "candidate reason",
            }
        ]
        output = json.dumps({"keyword": "ETF", "reason": "selection reason"})

        selected = parse_selected_viral_keyword_candidate(output, candidates)

        self.assertEqual(selected["source_url"], "https://example.com/a")
        self.assertEqual(selected["selection_reason"], "selection reason")

    @patch("keyword_selection.connect_mysql")
    def test_insert_selected_news_keyword_inserts_two_categories(self, connect_mysql):
        connection = FakeConnection()

        @contextmanager
        def fake_connect(db_config):
            yield connection

        connect_mysql.side_effect = fake_connect
        selected = {
            "keyword": "ETF",
            "source_title": "ETF title",
            "source_url": "https://example.com/a",
            "reason": "candidate reason",
            "selection_reason": "selection reason",
        }

        result = insert_selected_news_keyword(
            db_config={"host": "localhost"},
            selected_candidate=selected,
            target_date="2026-06-01",
        )

        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertEqual([item["category"] for item in result], ["3초퀴즈", "자녀에게설명하기"])
        params = [execution[1] for execution in connection.cursor_instance.executions]
        self.assertEqual([param["keyword"] for param in params], ["ETF", "ETF"])
        self.assertEqual(
            [param["target_date"] for param in params],
            ["2026-06-01", "2026-06-01"],
        )

    def test_build_selected_insert_params_falls_back_to_candidate_reason(self):
        params = build_selected_news_keyword_insert_params(
            {"keyword": "ETF", "reason": "candidate reason"},
            category="3초퀴즈",
            target_date="2026-06-01",
        )

        self.assertEqual(params["reason"], "candidate reason")
        self.assertEqual(params["category"], "3초퀴즈")
        self.assertEqual(params["target_date"], "2026-06-01")

    def test_build_news_quiz_insert_params_serializes_readable_korean_json(self):
        params = build_selected_news_quiz_insert_params(
            {
                "keyword": "금리",
                "source_name": "서울경제",
                "source_title": "금리 인하 기대 커졌다",
                "source_url": "https://example.com/a",
                "reason": "후보 사유",
                "selection_reason": "선정 사유",
                "keyword_description": "금리는 돈을 빌리는 비용이라 소비와 투자 흐름을 함께 바꿀 수 있어요.",
                "quiz": {
                    "question": "금리가 내려갈 때 더 타당한 해석은 무엇일까요?",
                    "option_a": "대출 부담이 줄어 소비와 투자가 늘 가능성이 있어요.",
                    "option_b": "예금 매력이 줄어도 기업 투자와는 별 관련이 없어요.",
                    "answer": "A",
                    "explanation": "금리 하락은 자금 조달 비용을 낮춰 소비와 투자 판단에 영향을 줄 수 있어요.",
                },
            },
            target_date="2026-06-06",
        )

        self.assertEqual(params["mq_news_date"], "2026-06-06")
        self.assertEqual(params["mq_company"], "서울경제")
        self.assertEqual(params["mq_keyword"], "금리")
        self.assertIn("금리가 내려갈 때", params["mq_quiz_content"])
        self.assertNotIn("\\u", params["mq_quiz_content"])
        self.assertEqual(json.loads(params["mq_quiz_content"])["answer"], "A")

    @patch("keyword_selection.connect_mysql")
    def test_insert_selected_news_quiz_inserts_one_row(self, connect_mysql):
        connection = FakeConnection()

        @contextmanager
        def fake_connect(db_config):
            yield connection

        connect_mysql.side_effect = fake_connect
        selected = {
            "keyword": "금리",
            "source_name": "서울경제",
            "source_title": "금리 인하 기대 커졌다",
            "source_url": "https://example.com/a",
            "selection_reason": "선정 사유",
            "keyword_description": "금리는 돈을 빌리는 비용이라 소비와 투자 흐름을 함께 바꿀 수 있어요.",
            "quiz": {
                "question": "질문",
                "option_a": "보기 A",
                "option_b": "보기 B",
                "answer": "A",
                "explanation": "해설",
            },
        }

        result = insert_selected_news_quiz(
            db_config={"host": "localhost"},
            selected_candidate=selected,
            target_date="2026-06-06",
        )

        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertEqual(result["table"], "mq_news_quiz")
        query, params = connection.cursor_instance.executions[0]
        self.assertIn("INSERT INTO mq_news_quiz", query)
        self.assertEqual(params["mq_keyword"], "금리")

    @patch("keyword_selection.mysql_connect_kwargs", return_value={"host": "localhost"})
    @patch("keyword_selection.resolve_news_keyword_storage_config")
    @patch("keyword_selection.insert_selected_news_keyword", return_value=[])
    @patch("keyword_selection.select_viral_news_keyword_candidate")
    @patch("keyword_selection.mark_news_keyword_duplicates")
    def test_selection_process_uses_existing_quiz_on_selected_candidate(
        self,
        mark_news_keyword_duplicates,
        select_viral_news_keyword_candidate,
        insert_selected_news_keyword,
        resolve_news_keyword_storage_config,
        mysql_connect_kwargs,
    ):
        candidates = [
            {
                "keyword": "ETF",
                "source_title": "ETF 신규 상장",
                "source_url": "https://example.com/a",
                "reason": "candidate reason",
                "keyword_description": "LLM ETF 설명",
                "quiz": {
                    "question": "LLM ETF 질문",
                    "option_a": "LLM 선택 A",
                    "option_b": "LLM 선택 B",
                    "answer": "A",
                    "explanation": "LLM 선택 A가 맞는 이유입니다.",
                },
            }
        ]
        mark_news_keyword_duplicates.return_value = candidates
        select_viral_news_keyword_candidate.return_value = dict(candidates[0])
        resolve_news_keyword_storage_config.return_value = type(
            "Config",
            (),
            {
                "dedupe_table": "n8n_publish_content",
                "dedupe_column": "keyword",
                "target_date": "2026-06-01",
            },
        )()
        args = type("Args", (), {"news_keyword_model": "test-model"})()
        llm_client = FakeSelectionLLMClient(
            json.dumps(
                {
                    "keyword_description": "LLM ETF 설명",
                    "reason": "LLM ETF 선정 근거",
                    "quiz": {
                        "question": "LLM ETF 질문",
                        "option_a": "LLM 선택 A",
                        "option_b": "LLM 선택 B",
                        "answer": "A",
                        "explanation": "LLM 선택 A가 맞는 이유입니다.",
                    },
                },
                ensure_ascii=False,
            )
        )

        result = run_news_keyword_selection_process(
            candidates=candidates,
            args=args,
            root_dir=Path("."),
            output_dir=Path("output"),
            llm_client=llm_client,
            insert_publish_content=True,
        )

        selected = result["selected_candidate"]
        self.assertIn("keyword_description", selected)
        self.assertEqual(selected["keyword_description"], "LLM ETF 설명")
        self.assertEqual(selected["selection_reason"], "candidate reason")
        self.assertEqual(selected["quiz"]["answer"], "A")
        self.assertEqual(selected["quiz"]["explanation"], "LLM 선택 A가 맞는 이유입니다.")
        self.assertIn("quiz", result["checked_candidates"][0])
        insert_selected_news_keyword.assert_called_once()
        inserted_candidate = insert_selected_news_keyword.call_args.kwargs["selected_candidate"]
        self.assertIn("quiz", inserted_candidate)

    def test_resolve_storage_config_uses_fixed_insert_target_and_target_date(self):
        config = resolve_news_keyword_storage_config()

        self.assertEqual(config.dedupe_table, "n8n_publish_content")
        self.assertEqual(config.dedupe_column, "keyword")
        self.assertEqual(config.target_date, date.today().isoformat())


if __name__ == "__main__":
    unittest.main()

