import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from script_generation import (
    SCRIPT_PROMPT_FILES,
    build_news_context_text,
    build_news_script_prompt,
    build_script_prompt,
    clean_script_text,
    extract_news_source_url,
    find_script_problems,
    generate_script_text,
    resolve_news_script_keyword,
    resolve_script_prompt_path,
    resolve_target_date,
    resolve_template_category,
    run_script_generation,
    update_generated_script_content,
    uses_news_context,
    validate_content_length,
)
import script_main


ROOT_DIR = Path(__file__).resolve().parent.parent
GLOBE = chr(0x1F30F)
NEWS = chr(0x1F4F0)
MOM = chr(0x1F469)
KID = chr(0x1F466)
PIN = chr(0x1F4CC)


def explain_script(keyword="금리", first_speaker=None):
    first = first_speaker or KID
    return (
        f"{GLOBE} [{keyword}]을 자녀에게 설명하는 법\n\n"
        f"{first} 엄마, {keyword}이 뭐야?\n"
        f"{MOM} 돈을 빌릴 때 내는 값이야."
    )


def news_explain_script(keyword="관세"):
    return (
        f"{NEWS} [{keyword}] 뉴스로 자녀에게 설명하는 법\n\n"
        f"{KID} 엄마, {keyword}이 뭐야?\n"
        f"{MOM} 다른 나라 물건에 붙는 세금이야."
    )


def quiz_script(keyword="환율", options=("물가상승률", "환율", "기준금리", "무역수지"), cta=None):
    cta = cta or f"헷갈렸다면 저장 {PIN}"
    letters = "ABCD"
    answer_letter = letters[options.index(keyword)]
    option_lines = "\n".join(f"{letter}) {name}" for letter, name in zip(letters, options))
    return (
        "뉴스에서 자주 보이죠?\n\n"
        "Q) 두 나라 돈을 바꾸는 비율을 뜻하는 말은?\n\n"
        f"{option_lines}\n\n"
        f"정답: {answer_letter}) {keyword}\n\n"
        "서로 다른 돈을 바꾸는 비율이에요.\n\n"
        f"{cta}"
    )


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_text(self, prompt, model=None):
        self.calls.append({"prompt": prompt, "model": model})
        return self.response


class PromptFilesTest(unittest.TestCase):
    def test_all_script_prompt_files_exist_and_have_no_forbidden_symbols(self):
        forbidden = "".join(chr(code) for code in (0xB7, 0x2014, 0x2013, 0x2192, 0x201C, 0x201D, 0x2018, 0x2019))
        for key in SCRIPT_PROMPT_FILES:
            text = resolve_script_prompt_path(ROOT_DIR, key).read_text(encoding="utf-8")
            self.assertTrue(text.strip(), key)
            self.assertFalse([c for c in text if c in forbidden], key)

    def test_unknown_template_raises(self):
        with self.assertRaises(ValueError):
            resolve_script_prompt_path(ROOT_DIR, "nope")
        with self.assertRaises(ValueError):
            resolve_template_category("nope")

    def test_category_and_news_context_mapping(self):
        self.assertEqual(resolve_template_category("3s_quiz"), "3초퀴즈")
        self.assertEqual(resolve_template_category("news_explain_child"), "자녀에게설명하기")
        self.assertTrue(uses_news_context("news_3s_quiz"))
        self.assertFalse(uses_news_context("explain_child"))


class PromptBuildTest(unittest.TestCase):
    def test_build_script_prompt_has_keyword_and_limit_without_output_file(self):
        prompt = build_script_prompt("RULES", "금리")
        self.assertIn("<KEYWORD>\n금리\n</KEYWORD>", prompt)
        self.assertIn("RULES", prompt)
        self.assertIn("fewer than 500 characters", prompt)
        self.assertNotIn("OUTPUT_FILE", prompt)

    def test_build_news_script_prompt_has_news_context(self):
        prompt = build_news_script_prompt("RULES", "관세", "뉴스기사제목: 제목")
        self.assertIn("<NEWS_CONTEXT>\n뉴스기사제목: 제목\n</NEWS_CONTEXT>", prompt)
        self.assertIn("Do not browse the web", prompt)

    def test_build_news_context_text_and_keyword(self):
        row = {
            "mq_source_url": "https://n.news/1",
            "mq_title": "제목",
            "mq_keyword": " 관세 ",
            "mq_keyword_description": "설명",
            "mq_selection_reason": None,
        }
        text = build_news_context_text(row)
        self.assertIn("뉴스 URL: https://n.news/1", text)
        self.assertIn("뉴스 선별 이유: ", text)
        self.assertEqual(resolve_news_script_keyword("원래", row), "관세")
        self.assertEqual(resolve_news_script_keyword("원래", {"mq_keyword": "  "}), "원래")


class ExtractUrlTest(unittest.TestCase):
    def test_extracts_and_trims_url(self):
        self.assertEqual(
            extract_news_source_url("원문: (https://n.news/a?b=1)."),
            "https://n.news/a?b=1",
        )
        self.assertEqual(extract_news_source_url("www.a.com/x."), "https://www.a.com/x")

    def test_raises_without_url(self):
        with self.assertRaises(ValueError):
            extract_news_source_url("없음")
        with self.assertRaises(ValueError):
            extract_news_source_url(None)


class ScriptTextTest(unittest.TestCase):
    def test_length_limit_is_strictly_less_than_500(self):
        validate_content_length("가" * 499)
        with self.assertRaisesRegex(ValueError, "actual=500"):
            validate_content_length("가" * 500)

    def test_clean_script_text_strips_code_fence_and_rejects_empty(self):
        self.assertEqual(clean_script_text("```\n본문\n```"), "본문")
        self.assertEqual(clean_script_text("  본문 \n"), "본문")
        with self.assertRaises(ValueError):
            clean_script_text("  ")

    def test_generate_script_text_rejects_too_long_response(self):
        with self.assertRaises(ValueError):
            generate_script_text(FakeLLM("가" * 500), "p")

    def test_resolve_target_date(self):
        self.assertEqual(resolve_target_date("2026-11-04"), "2026-11-04")
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            resolve_target_date("11/04")


class RunScriptGenerationTest(unittest.TestCase):
    def run_flow(self, template, row, llm, keyword=None, direct_row=None):
        module = "script_generation"
        with patch(f"{module}.fetch_publish_content_by_date", return_value=row), patch(
            f"{module}.load_or_insert_direct_keyword_row", return_value=direct_row
        ), patch(
            f"{module}.fetch_news_quiz_by_source_url",
            return_value={"mq_title": "제목", "mq_keyword": "관세"},
        ) as news, patch(f"{module}.update_content_by_idx", return_value=1) as update:
            with redirect_stdout(StringIO()):
                result = run_script_generation(
                    db_config={},
                    llm_client=llm,
                    root_dir=ROOT_DIR,
                    template_key=template,
                    keyword=keyword,
                    model="m",
                )
        return result, news, update

    def test_generates_and_saves_for_plain_template(self):
        llm = FakeLLM(explain_script())
        row = {"idx": 7, "keyword": "금리", "content": None, "comment": None}

        result, news, update = self.run_flow("explain_child", row, llm)

        self.assertEqual(result, {"idx": 7, "script": explain_script(), "db_content_updated": 1})
        update.assert_called_once_with({}, 7, explain_script())
        news.assert_not_called()
        self.assertEqual(llm.calls[0]["model"], "m")
        self.assertIn("금리", llm.calls[0]["prompt"])

    def test_news_template_uses_news_quiz_keyword_and_context(self):
        llm = FakeLLM(quiz_script("관세", ("관세", "환율", "기준금리", "무역수지")))
        row = {"idx": 8, "keyword": "원래", "content": "", "comment": "https://n.news/1"}

        result, news, update = self.run_flow("news_3s_quiz", row, llm)

        news.assert_called_once_with({}, "https://n.news/1")
        self.assertIn("<KEYWORD>\n관세\n</KEYWORD>", llm.calls[0]["prompt"])
        self.assertIn("뉴스기사제목: 제목", llm.calls[0]["prompt"])
        update.assert_called_once_with({}, 8, quiz_script("관세", ("관세", "환율", "기준금리", "무역수지")))

    def test_skips_when_content_already_set(self):
        llm = FakeLLM("새 스크립트")
        row = {"idx": 9, "keyword": "금리", "content": " 기존 ", "comment": None}

        result, _news, update = self.run_flow("explain_child", row, llm)

        self.assertEqual(result["db_content_updated"], None)
        self.assertEqual(result["script"], "기존")
        self.assertEqual(llm.calls, [])
        update.assert_not_called()

    def test_direct_keyword_uses_direct_row(self):
        llm = FakeLLM(quiz_script("ETF", ("ETF", "채권", "예금", "보험")))
        direct_row = {"idx": 3, "keyword": "ETF", "content": None, "comment": None}

        result, _news, update = self.run_flow(
            "3s_quiz", row=None, llm=llm, keyword=" ETF ", direct_row=direct_row
        )

        self.assertEqual(result["idx"], 3)
        update.assert_called_once_with({}, 3, quiz_script("ETF", ("ETF", "채권", "예금", "보험")))

    def test_too_long_response_does_not_write_db(self):
        llm = FakeLLM("가" * 600)
        row = {"idx": 7, "keyword": "금리", "content": None, "comment": None}

        with self.assertRaises(ValueError):
            self.run_flow("explain_child", row, llm)

    def test_update_generated_script_content_rejects_empty_and_long(self):
        with self.assertRaises(ValueError):
            update_generated_script_content({}, "  ", {"idx": 1})
        with self.assertRaises(ValueError):
            update_generated_script_content({}, "가" * 500, {"idx": 1})


class ScriptProblemsTest(unittest.TestCase):
    def test_valid_scripts_have_no_problems(self):
        self.assertEqual(find_script_problems("explain_child", explain_script(), "금리"), [])
        self.assertEqual(find_script_problems("news_explain_child", news_explain_script(), "관세"), [])
        self.assertEqual(find_script_problems("3s_quiz", quiz_script(), "환율"), [])
        self.assertEqual(find_script_problems("news_3s_quiz", quiz_script(), "환율"), [])

    def test_explain_title_and_first_speaker(self):
        self.assertTrue(find_script_problems("explain_child", explain_script("물가"), "금리"))
        self.assertTrue(find_script_problems("explain_child", explain_script(first_speaker=MOM), "금리"))
        self.assertTrue(
            find_script_problems("news_explain_child", news_explain_script().replace("[관세]", "관세"), "관세")
        )

    def test_explain_dialogue_line_limit(self):
        long_script = explain_script() + "\n" + "\n".join(f"{KID} 응?" for _ in range(10))
        self.assertTrue(any("Dialogue" in p for p in find_script_problems("explain_child", long_script, "금리")))

    def test_forbidden_speech_styles(self):
        for word in ("란다", "군요", "합니다", "입니다", "답니다", "맞어"):
            problems = find_script_problems("explain_child", explain_script() + word, "금리")
            self.assertTrue(any("Forbidden" in p for p in problems), word)

    def test_quiz_cta_count_must_be_exactly_one(self):
        two = quiz_script(cta="헷갈렸다면 저장\n맞췄어요?")
        self.assertTrue(any("CTA" in p for p in find_script_problems("3s_quiz", two, "환율")))
        none = quiz_script(cta="끝")
        self.assertTrue(any("CTA" in p for p in find_script_problems("3s_quiz", none, "환율")))

    def test_quiz_question_length_limit(self):
        script = quiz_script().replace("두 나라 돈을 바꾸는 비율을 뜻하는 말은?", "가" * 81)
        self.assertTrue(any("Question" in p for p in find_script_problems("3s_quiz", script, "환율")))

    def test_quiz_options_must_not_overlap(self):
        script = quiz_script("환율", ("환율", "환율 급등", "기준금리", "무역수지"))
        self.assertTrue(any("overlap" in p for p in find_script_problems("3s_quiz", script, "환율")))
        script = quiz_script("환율 급등", ("환율 급등", "환율 하락", "기준금리", "무역수지"))
        self.assertTrue(any("leading word" in p for p in find_script_problems("3s_quiz", script, "환율 급등")))

    def test_quiz_answer_must_be_keyword(self):
        self.assertTrue(
            any("keyword" in p for p in find_script_problems("3s_quiz", quiz_script("환율"), "금리"))
        )


class RetryTest(unittest.TestCase):
    class SequenceLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.prompts = []

        def generate_text(self, prompt, model=None):
            self.prompts.append(prompt)
            return self.responses.pop(0)

    def check(self, script_text):
        return find_script_problems("3s_quiz", script_text, "환율")

    def test_retries_once_with_problems_then_succeeds(self):
        llm = self.SequenceLLM([quiz_script(cta="끝"), quiz_script()])

        result = generate_script_text(llm, "BASE", check=self.check)

        self.assertEqual(result, quiz_script())
        self.assertEqual(len(llm.prompts), 2)
        self.assertIn("<PROBLEMS>", llm.prompts[1])
        self.assertIn("Exactly one CTA line", llm.prompts[1])
        self.assertTrue(llm.prompts[1].startswith("BASE"))

    def test_fails_after_second_invalid_attempt(self):
        llm = self.SequenceLLM([quiz_script(cta="끝"), quiz_script(cta="끝")])

        with self.assertRaisesRegex(ValueError, "failed validation"):
            generate_script_text(llm, "BASE", check=self.check)

        self.assertEqual(len(llm.prompts), 2)

    def test_does_not_write_db_when_validation_fails(self):
        row = {"idx": 7, "keyword": "환율", "content": None, "comment": None}
        llm = self.SequenceLLM(["엉터리", "엉터리"])
        with patch("script_generation.fetch_publish_content_by_date", return_value=row), patch(
            "script_generation.update_content_by_idx"
        ) as update, redirect_stdout(StringIO()):
            with self.assertRaises(ValueError):
                run_script_generation(
                    db_config={}, llm_client=llm, root_dir=ROOT_DIR, template_key="3s_quiz"
                )
        update.assert_not_called()


class ScriptMainTest(unittest.TestCase):
    def test_create_script_llm_client_requests_plain_text(self):
        client = script_main.create_script_llm_client(
            {"GEMINI_API_KEY": "k", "LLM_MODEL": "gemini-x"}
        )
        self.assertEqual(client.response_mime_type, "text/plain")
        self.assertIsNone(client.response_json_schema)
        self.assertEqual(client.default_model, "gemini-x")
        self.assertEqual(client.api_key, "k")

    def test_main_returns_2_when_required_env_missing(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "script_main.load_dotenv"
        ), redirect_stderr(StringIO()) as err:
            code = script_main.main(["--template", "explain_child"])

        self.assertEqual(code, 2)
        self.assertIn("LLM_MODEL", err.getvalue())

    def test_main_prints_result_and_returns_0(self):
        env = {"GEMINI_API_KEY": "k", "LLM_MODEL": "m"}
        result = {"idx": 1, "script": "본문", "db_content_updated": 1}
        with patch.dict("os.environ", env, clear=True), patch("script_main.load_dotenv"), patch(
            "script_main.mysql_connect_kwargs", return_value={}
        ), patch("script_main.run_script_generation", return_value=result) as run:
            out = StringIO()
            with redirect_stdout(out):
                code = script_main.main(["--template", "3s_quiz", "--keyword", "ETF"])

        self.assertEqual(code, 0)
        self.assertIn("db_content_updated: 1", out.getvalue())
        self.assertEqual(run.call_args.kwargs["template_key"], "3s_quiz")
        self.assertEqual(run.call_args.kwargs["keyword"], "ETF")


if __name__ == "__main__":
    unittest.main()
