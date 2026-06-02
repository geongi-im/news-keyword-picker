import json
import unittest

from news_keyword import (
    extract_naver_newspaper_front_page_articles,
    filter_news_keyword_candidates,
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

    def test_filter_candidates_requires_source_url_allowed_keyword_and_title_support(self):
        articles = [
            {"title": "코스피 8000선 돌파", "url": "https://example.com/a"},
            {"title": "전기요금 인상", "url": "https://example.com/b"},
        ]
        candidates = [
            {"keyword": "코스피", "source_url": "https://example.com/a", "reason": "model"},
            {"keyword": "경제", "source_url": "https://example.com/a", "reason": "generic"},
            {"keyword": "반도체", "source_url": "https://example.com/b", "reason": "unsupported"},
            {"keyword": "ETF", "source_url": "https://example.com/missing", "reason": "missing"},
        ]

        filtered = filter_news_keyword_candidates(candidates, articles)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["keyword"], "코스피")
        self.assertEqual(filtered[0]["source_title"], "코스피 8000선 돌파")
        self.assertIn("코스피 8000선 돌파", filtered[0]["reason"])


if __name__ == "__main__":
    unittest.main()
