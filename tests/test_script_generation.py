import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from script_generation import (
    SCRIPT_PROMPT_FILES,
    build_news_context_text,
    build_news_script_prompt,
    build_publish_texts,
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
    split_quiz_answer,
    strip_previous_quiz_answer,
    update_generated_script_content,
    uses_news_context,
    validate_content_length,
)


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
        ) as news, patch(f"{module}.update_content_by_idx", return_value=1) as update, patch(
            f"{module}.update_content_and_comment_by_idx", return_value=1
        ) as update_both:
            with redirect_stdout(StringIO()):
                result = run_script_generation(
                    db_config={},
                    llm_client=llm,
                    root_dir=ROOT_DIR,
                    template_key=template,
                    keyword=keyword,
                    model="m",
                )
        self.update_both = update_both
        return result, news, update

    def test_generates_and_saves_for_plain_template(self):
        llm = FakeLLM(explain_script())
        row = {"idx": 7, "keyword": "금리", "content": None, "comment": None}

        result, news, update = self.run_flow("explain_child", row, llm)

        self.assertEqual(
            result, {"idx": 7, "script": explain_script(), "comment": None, "db_content_updated": 1}
        )
        update.assert_called_once_with({}, 7, explain_script())
        self.update_both.assert_not_called()
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
        content, answer = split_quiz_answer(quiz_script("관세", ("관세", "환율", "기준금리", "무역수지")))
        expected_comment = f"{answer}\n\nhttps://n.news/1"
        update.assert_not_called()
        self.update_both.assert_called_once_with({}, 8, content, expected_comment)
        self.assertEqual(result["script"], content)
        self.assertEqual(result["comment"], expected_comment)

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
        content, answer = split_quiz_answer(quiz_script("ETF", ("ETF", "채권", "예금", "보험")))
        update.assert_not_called()
        self.update_both.assert_called_once_with({}, 3, content, answer)

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


class QuizAnswerSplitTest(unittest.TestCase):
    def test_moves_answer_and_explanation_out_of_body_and_keeps_cta(self):
        content, answer = split_quiz_answer(quiz_script())

        self.assertEqual(answer, "정답: B) 환율\n\n서로 다른 돈을 바꾸는 비율이에요.")
        self.assertNotIn("정답:", content)
        self.assertNotIn("서로 다른 돈을 바꾸는 비율이에요.", content)
        self.assertTrue(content.startswith("뉴스에서 자주 보이죠?"))
        self.assertIn("D) 무역수지\n\n헷갈렸다면 저장", content)
        self.assertTrue(content.endswith(f"헷갈렸다면 저장 {PIN}"))

    def test_without_cta_everything_after_answer_goes_to_answer(self):
        script = quiz_script().replace(f"\n\n헷갈렸다면 저장 {PIN}", "")

        content, answer = split_quiz_answer(script)

        self.assertTrue(content.endswith("D) 무역수지"))
        self.assertTrue(answer.endswith("비율이에요."))

    def test_missing_answer_line_raises(self):
        with self.assertRaisesRegex(ValueError, "정답:"):
            split_quiz_answer("Q) 문제\nA) 가")

    def test_build_publish_texts_puts_answer_before_existing_comment(self):
        content, comment = build_publish_texts("news_3s_quiz", quiz_script(), " https://n.news/1 ")

        self.assertNotIn("정답:", content)
        self.assertTrue(comment.startswith("정답: B) 환율\n\n서로 다른"))
        self.assertTrue(comment.endswith("\n\nhttps://n.news/1"))
        self.assertEqual(extract_news_source_url(comment), "https://n.news/1")

    def test_build_publish_texts_without_existing_comment(self):
        _content, comment = build_publish_texts("3s_quiz", quiz_script(), None)

        self.assertEqual(comment, "정답: B) 환율\n\n서로 다른 돈을 바꾸는 비율이에요.")

    def test_strip_previous_quiz_answer_keeps_plain_comment(self):
        self.assertEqual(strip_previous_quiz_answer(" https://n.news/1 "), "https://n.news/1")
        self.assertEqual(strip_previous_quiz_answer(None), "")
        self.assertEqual(strip_previous_quiz_answer("메모\nhttps://n.news/1"), "메모\nhttps://n.news/1")

    def test_strip_previous_quiz_answer_removes_old_answer_block(self):
        old_comment = "정답: A) 금리\n\n돈을 빌린 값이에요.\n\nhttps://n.news/1"
        self.assertEqual(strip_previous_quiz_answer(old_comment), "https://n.news/1")
        self.assertEqual(strip_previous_quiz_answer("정답: A) 금리\n\n돈을 빌린 값이에요."), "")

    def test_regenerate_replaces_old_answer_without_duplicating(self):
        _content, first = build_publish_texts("news_3s_quiz", quiz_script(), "https://n.news/1")
        _content, second = build_publish_texts("news_3s_quiz", quiz_script(), first)

        self.assertEqual(second, first)
        self.assertEqual(second.count("정답:"), 1)

        new_script = quiz_script("기준금리", ("물가상승률", "환율", "기준금리", "무역수지"))
        _content, replaced = build_publish_texts("news_3s_quiz", new_script, first)

        self.assertTrue(replaced.startswith("정답: C) 기준금리\n\n"))
        self.assertNotIn("정답: B) 환율", replaced)
        self.assertTrue(replaced.endswith("\n\nhttps://n.news/1"))

    def test_regenerate_without_url_replaces_whole_comment(self):
        _content, first = build_publish_texts("3s_quiz", quiz_script(), None)
        _content, second = build_publish_texts("3s_quiz", quiz_script(), first)

        self.assertEqual(second, first)

    def test_build_publish_texts_leaves_explain_templates_unchanged(self):
        for key in ("explain_child", "news_explain_child"):
            self.assertEqual(
                build_publish_texts(key, explain_script(), "https://n.news/1"),
                (explain_script(), None),
            )


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
        ) as update, patch(
            "script_generation.update_content_and_comment_by_idx"
        ) as update_both, redirect_stdout(StringIO()):
            with self.assertRaises(ValueError):
                run_script_generation(
                    db_config={}, llm_client=llm, root_dir=ROOT_DIR, template_key="3s_quiz"
                )
        update.assert_not_called()
        update_both.assert_not_called()


if __name__ == "__main__":
    unittest.main()
