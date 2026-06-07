import json
import unittest

from news_keyword import (
    NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT,
    build_news_keyword_prompt,
    build_news_keyword_response_json_schema,
    extract_naver_newspaper_front_page_articles,
    filter_news_keyword_candidates,
    has_complete_candidate_learning_content,
    parse_news_keyword_candidates,
)


class NewsKeywordParsingTest(unittest.TestCase):
    def test_extracts_unique_front_page_newspaper_articles(self):
        html_text = """
        <div class="newspaper_brick_item _start_page">
          <a href="https://n.news.naver.com/article/newspaper/014/0000000001">
            <strong>코스피 &amp; ETF</strong>
          </a>
          <a href="https://n.news.naver.com/article/newspaper/014/0000000001">
            <strong>코스피 &amp; ETF</strong>
          </a>
        </div>
        <div class="newspaper_brick_item">
          <a href="https://n.news.naver.com/article/newspaper/014/0000000002">
            <strong>2면 기사는 제외</strong>
          </a>
        </div>
        """

        articles = extract_naver_newspaper_front_page_articles(
            html_text,
            base_url="https://media.naver.com/press/014/newspaper",
        )

        self.assertEqual(
            articles,
            [
                {
                    "title": "코스피 & ETF",
                    "url": "https://n.news.naver.com/article/newspaper/014/0000000001",
                }
            ],
        )

    def test_parse_candidates_accepts_wrapped_json_and_deduplicates_keywords(self):
        output = "model log\n" + json.dumps(
            [
                {
                    "keyword": "코스피",
                    "source_url": " https://example.com/a ",
                    "reason": "  reason one  ",
                },
                {
                    "keyword": "코스피",
                    "source_url": "https://example.com/b",
                    "reason": "duplicate",
                },
            ],
            ensure_ascii=True,
        )

        candidates = parse_news_keyword_candidates(output)

        self.assertEqual(
            candidates,
            [
                {
                    "keyword": "코스피",
                    "source_url": "https://example.com/a",
                    "reason": "reason one",
                }
            ],
        )

    def test_parse_candidates_preserves_keyword_description_and_quiz(self):
        output = json.dumps(
            [
                {
                    "keyword": "ETF",
                    "source_url": "https://example.com/a",
                    "reason": "model reason",
                    "keyword_description": "ETF desc",
                    "quiz": {
                        "question": "ETF question",
                    "option_a": "right",
                    "option_b": "wrong",
                    "answer": "b",
                    "explanation": "right reason",
                },
            }
            ],
            ensure_ascii=True,
        )

        candidates = parse_news_keyword_candidates(output)

        self.assertEqual(candidates[0]["keyword_description"], "ETF desc")
        self.assertEqual(
            candidates[0]["quiz"],
            {
                "question": "ETF question",
                "option_a": "right",
                "option_b": "wrong",
                "answer": "B",
                "explanation": "right reason",
            },
        )
        self.assertTrue(has_complete_candidate_learning_content(candidates[0]))

    def test_filter_candidates_requires_source_url_keyword_length_and_title_support(self):
        articles = [
            {"title": "코스피 8000선 돌파", "url": "https://example.com/a"},
            {"title": "전기요금 인상", "url": "https://example.com/b"},
        ]
        candidates = [
            {"keyword": "코스피", "source_url": "https://example.com/a", "reason": "model"},
            {"keyword": "가", "source_url": "https://example.com/a", "reason": "too short"},
            {"keyword": "반도체", "source_url": "https://example.com/b", "reason": "unsupported"},
            {"keyword": "ETF", "source_url": "https://example.com/missing", "reason": "missing"},
        ]

        filtered = filter_news_keyword_candidates(candidates, articles)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["keyword"], "코스피")
        self.assertEqual(filtered[0]["source_title"], "코스피 8000선 돌파")
        self.assertEqual(filtered[0]["reason"], "model")

    def test_filter_candidates_does_not_add_keyword_description_and_quiz(self):
        articles = [{"title": "ETF 신규 상장", "url": "https://example.com/a"}]
        candidates = [
            {"keyword": "ETF", "source_url": "https://example.com/a", "reason": "model"}
        ]

        filtered = filter_news_keyword_candidates(candidates, articles)

        self.assertEqual(len(filtered), 1)
        self.assertNotIn("keyword_description", filtered[0])
        self.assertNotIn("quiz", filtered[0])

    def test_filter_candidates_requires_learning_content_when_requested(self):
        articles = [
            {"title": "ETF 신규 상장", "url": "https://example.com/a"},
            {"title": "IPO 상장 흥행", "url": "https://example.com/b"},
        ]
        candidates = [
            {
                "keyword": "ETF",
                "source_url": "https://example.com/a",
                "reason": "model",
                "keyword_description": "ETF desc",
                "quiz": {
                    "question": "ETF question",
                    "option_a": "right",
                    "option_b": "wrong",
                    "answer": "A",
                    "explanation": "right reason",
                },
            },
            {
                "keyword": "IPO",
                "source_url": "https://example.com/b",
                "reason": "model",
            },
        ]

        filtered = filter_news_keyword_candidates(
            candidates,
            articles,
            keyword_count=2,
            require_learning_content=True,
        )

        self.assertEqual([candidate["keyword"] for candidate in filtered], ["ETF"])

    def test_build_prompt_uses_readable_korean_without_quiz_fields(self):
        prompt = build_news_keyword_prompt(
            [{"title": "코스피 8000선 돌파", "url": "https://example.com/a"}],
        )

        self.assertNotIn('"keyword_description"', prompt)
        self.assertNotIn('"quiz"', prompt)
        self.assertNotIn('"option_a"', prompt)
        self.assertNotIn('"option_b"', prompt)
        self.assertIn("코스피 8000선 돌파", prompt)
        self.assertIn("Keep Korean text as normal readable Korean", prompt)
        self.assertNotIn("Unicode escape sequences", prompt)
        self.assertNotIn("\\ucf54\\uc2a4\\ud53c", prompt)


    def test_build_prompt_can_request_eight_candidates_with_learning_content(self):
        prompt = build_news_keyword_prompt(
            [{"title": "ETF 신규 상장", "url": "https://example.com/a"}],
            keyword_count=NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT,
            include_learning_content=True,
        )
        schema = build_news_keyword_response_json_schema(
            keyword_count=NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT,
            include_learning_content=True,
        )

        self.assertIn("Select exactly 8", prompt)
        self.assertIn('"keyword_description"', prompt)
        self.assertIn('"quiz"', prompt)
        self.assertIn('"explanation"', prompt)
        self.assertIn("friendly teacher-like", prompt)
        self.assertIn("Korean high-school student", prompt)
        self.assertIn("roughly twice as long", prompt)
        self.assertIn("word counts", prompt)
        self.assertIn("both sound economically plausible", prompt)
        self.assertIn("tempting but incomplete interpretation", prompt)
        self.assertIn("confuse direct and indirect effects", prompt)
        self.assertIn("무관", prompt)
        self.assertIn("second-order effect or tradeoff", prompt)
        self.assertEqual(schema["minItems"], 8)
        self.assertIn("quiz", schema["items"]["required"])


if __name__ == "__main__":
    unittest.main()
