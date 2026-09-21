import json
import unittest
import urllib.error
from unittest.mock import patch

from news_keyword import (
    NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT,
    attach_naver_article_bodies,
    fetch_html,
    fetch_naver_newspaper_front_page_articles,
    build_news_keyword_prompt,
    build_news_keyword_response_json_schema,
    count_articles_with_body,
    extract_naver_article_body,
    extract_naver_newspaper_front_page_articles,
    filter_news_keyword_candidates,
    has_complete_candidate_learning_content,
    is_keyword_supported_by_article,
    parse_news_keyword_candidates,
)


ARTICLE_PAGE_HTML = """
<html><body>
  <div id="newsct_article" class="newsct_article _article_body">
    <article id="dic_area" class="go_trans _article_content">
      <script>var ad = 1;</script>
      <strong>전기요금 인상</strong> 결정으로 발전단가 부담이 커졌다.
      <div class="end_photo_org"><em>그래픽=기자</em></div>
      한국전력은 &quot;원가 회수율&quot;을 근거로 제시했다.
    </article>
  </div>
  <div id="comment_area">댓글 영역은 본문이 아닙니다.</div>
</body></html>
"""


FRONT_PAGE_HTML = """
<div class="newspaper_brick_item _start_page">
  <a href="https://n.news.naver.com/article/newspaper/014/0000000001">
    <strong>전기요금 인상</strong>
  </a>
</div>
"""


class NewsPageFetchTest(unittest.TestCase):
    def test_fetch_html_retries_transient_network_error(self):
        attempts = []

        def fake_urlopen(request, timeout=None):
            attempts.append(timeout)
            if len(attempts) < 3:
                raise urllib.error.URLError(TimeoutError("handshake operation timed out"))
            return FakeResponse(FRONT_PAGE_HTML)

        with (
            patch("news_keyword.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("news_keyword.time.sleep"),
        ):
            html_text = fetch_html("https://example.com/page", retry_delay=0)

        self.assertEqual(len(attempts), 3)
        self.assertIn("전기요금 인상", html_text)

    def test_fetch_html_raises_after_retry_limit(self):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError(TimeoutError("handshake operation timed out"))

        with (
            patch("news_keyword.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("news_keyword.time.sleep"),
        ):
            with self.assertRaises(RuntimeError) as error:
                fetch_html("https://example.com/page", retry_count=1, retry_delay=0)

        self.assertIn("Failed to fetch news page", str(error.exception))

    def test_fetch_html_does_not_retry_not_found_response(self):
        attempts = []
        not_found_error = urllib.error.HTTPError(
            "https://example.com/page", 404, "Not Found", {}, None
        )
        self.addCleanup(not_found_error.close)

        def fake_urlopen(request, timeout=None):
            attempts.append(timeout)
            raise not_found_error

        with (
            patch("news_keyword.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("news_keyword.time.sleep"),
        ):
            with self.assertRaises(RuntimeError):
                fetch_html("https://example.com/page", retry_delay=0)

        self.assertEqual(len(attempts), 1)

    def test_front_page_fetch_skips_failed_source(self):
        sources = (
            {"name": "실패신문", "url": "https://example.com/fail"},
            {"name": "정상신문", "url": "https://example.com/ok"},
        )

        def fake_fetch_html(url, timeout=None, logger=None):
            if url.endswith("/fail"):
                raise RuntimeError("Failed to fetch news page")
            return FRONT_PAGE_HTML

        with patch("news_keyword.fetch_html", side_effect=fake_fetch_html):
            articles = fetch_naver_newspaper_front_page_articles(sources=sources)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["source_name"], "정상신문")

    def test_front_page_fetch_raises_when_every_source_fails(self):
        sources = ({"name": "실패신문", "url": "https://example.com/fail"},)

        def fake_fetch_html(url, timeout=None, logger=None):
            raise RuntimeError("Failed to fetch news page")

        with patch("news_keyword.fetch_html", side_effect=fake_fetch_html):
            with self.assertRaises(ValueError) as error:
                fetch_naver_newspaper_front_page_articles(sources=sources)

        self.assertIn("fetch failures", str(error.exception))


class FakeResponse:
    """urlopen 응답을 대신하는 테스트용 컨텍스트 매니저입니다."""

    def __init__(self, body):
        self._body = body
        self.headers = FakeHeaders()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self._body.encode("utf-8")


class FakeHeaders:
    """응답 헤더의 charset 조회만 흉내내는 테스트용 객체입니다."""

    def get_content_charset(self):
        return "utf-8"


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

    def test_filter_candidates_requires_source_url_and_keyword_length(self):
        articles = [
            {"title": "코스피 8000선 돌파", "url": "https://example.com/a"},
            {"title": "전기요금 인상", "url": "https://example.com/b"},
        ]
        candidates = [
            {"keyword": "코스피", "source_url": "https://example.com/a", "reason": "model"},
            {"keyword": "가", "source_url": "https://example.com/a", "reason": "too short"},
            {"keyword": "ETF", "source_url": "https://example.com/missing", "reason": "missing"},
        ]

        filtered = filter_news_keyword_candidates(candidates, articles)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["keyword"], "코스피")
        self.assertEqual(filtered[0]["source_title"], "코스피 8000선 돌파")
        self.assertEqual(filtered[0]["reason"], "model")

    def test_filter_candidates_keeps_keyword_found_in_article_body(self):
        articles = [
            {
                "title": "전기요금 인상",
                "url": "https://example.com/b",
                "body": "한국전력은 발전단가 상승을 이유로 요금 인상을 결정했다.",
            }
        ]
        candidates = [
            {"keyword": "발전단가", "source_url": "https://example.com/b", "reason": "본문 핵심"}
        ]

        filtered = filter_news_keyword_candidates(candidates, articles)

        self.assertEqual([candidate["keyword"] for candidate in filtered], ["발전단가"])
        self.assertEqual(filtered[0]["source_title"], "전기요금 인상")

    def test_filter_candidates_drops_keyword_missing_from_title_and_body(self):
        articles = [
            {
                "title": "전기요금 인상",
                "url": "https://example.com/b",
                "body": "한국전력은 발전단가 상승을 이유로 요금 인상을 결정했다.",
            }
        ]
        candidates = [
            {"keyword": "가상자산", "source_url": "https://example.com/b", "reason": "근거 없음"}
        ]

        self.assertEqual(filter_news_keyword_candidates(candidates, articles), [])

    def test_filter_candidates_keeps_keyword_when_body_is_unavailable(self):
        articles = [{"title": "전기요금 인상", "url": "https://example.com/b", "body": ""}]
        candidates = [
            {"keyword": "발전단가", "source_url": "https://example.com/b", "reason": "본문 수집 실패"}
        ]

        filtered = filter_news_keyword_candidates(candidates, articles)

        self.assertEqual([candidate["keyword"] for candidate in filtered], ["발전단가"])

    def test_is_keyword_supported_by_article_matches_title_or_body(self):
        self.assertTrue(is_keyword_supported_by_article("전기요금", "전기요금 인상", ""))
        self.assertTrue(is_keyword_supported_by_article("발전단가", "전기요금 인상", "발전단가 상승"))
        self.assertFalse(is_keyword_supported_by_article("가상자산", "전기요금 인상", "발전단가 상승"))
        self.assertFalse(is_keyword_supported_by_article("", "전기요금 인상", "발전단가 상승"))

    def test_extract_naver_article_body_reads_article_content_only(self):
        body = extract_naver_article_body(ARTICLE_PAGE_HTML)

        self.assertIn("전기요금 인상", body)
        self.assertIn("발전단가", body)
        self.assertIn('"원가 회수율"', body)
        self.assertNotIn("var ad", body)
        self.assertNotIn("댓글 영역", body)
        self.assertNotIn("<", body)

    def test_extract_naver_article_body_applies_char_limit(self):
        body = extract_naver_article_body(ARTICLE_PAGE_HTML, char_limit=20)

        self.assertLessEqual(len(body), 20)
        self.assertTrue(body)

    def test_extract_naver_article_body_returns_empty_without_body_area(self):
        self.assertEqual(extract_naver_article_body("<html><body>no body</body></html>"), "")

    def test_attach_naver_article_bodies_fills_body_and_survives_failures(self):
        articles = [
            {"title": "전기요금 인상", "url": "https://example.com/ok"},
            {"title": "코스피 8000선 돌파", "url": "https://example.com/fail"},
        ]

        def fake_fetch_html(url, timeout=10):
            if url.endswith("/fail"):
                raise RuntimeError("Failed to fetch news page")
            return ARTICLE_PAGE_HTML

        with patch("news_keyword.fetch_html", side_effect=fake_fetch_html):
            attached = attach_naver_article_bodies(articles, char_limit=200)

        self.assertIn("발전단가", attached[0]["body"])
        self.assertEqual(attached[1]["body"], "")
        self.assertEqual(count_articles_with_body(attached), 1)
        self.assertNotIn("body", articles[0])

    def test_attach_naver_article_bodies_skips_crawling_when_limit_is_zero(self):
        articles = [{"title": "전기요금 인상", "url": "https://example.com/ok"}]

        with patch("news_keyword.fetch_html", side_effect=AssertionError("must not fetch")):
            attached = attach_naver_article_bodies(articles, char_limit=0)

        self.assertEqual(attached, articles)

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
        self.assertIn("appear in the selected article's title or body text", prompt)
        self.assertIn('"body" field that holds the beginning of the crawled article text', prompt)
        self.assertNotIn("Unicode escape sequences", prompt)
        self.assertNotIn("\\ucf54\\uc2a4\\ud53c", prompt)


    def test_build_prompt_can_request_learning_candidates_with_learning_content(self):
        prompt = build_news_keyword_prompt(
            [{"title": "ETF 신규 상장", "url": "https://example.com/a"}],
            keyword_count=NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT,
            include_learning_content=True,
        )
        schema = build_news_keyword_response_json_schema(
            keyword_count=NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT,
            include_learning_content=True,
        )

        self.assertIn(
            f"Select exactly {NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT}",
            prompt,
        )
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
        self.assertEqual(schema["minItems"], NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT)
        self.assertIn("quiz", schema["items"]["required"])


if __name__ == "__main__":
    unittest.main()
