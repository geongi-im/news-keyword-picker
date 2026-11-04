import html
import http.client
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


DEFAULT_NEWSPAPER_SOURCES = (
    {"name": "파이낸셜뉴스", "url": "https://media.naver.com/press/014/newspaper"},
    {"name": "머니투데이", "url": "https://media.naver.com/press/008/newspaper"},
    {"name": "서울경제", "url": "https://media.naver.com/press/011/newspaper"},
    {"name": "한국경제", "url": "https://media.naver.com/press/015/newspaper"}
)
DEFAULT_NEWS_TITLE_LIMIT = 30
NEWS_KEYWORD_COUNT = 3
NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT = 5
DEFAULT_ARTICLE_BODY_CHAR_LIMIT = 900
DEFAULT_ARTICLE_BODY_WORKERS = 8
ARTICLE_BODY_ELEMENT_IDS = ("dic_area", "newsct_article", "articeBody", "articleBodyContents")
ARTICLE_BODY_FETCH_ERRORS = (RuntimeError, OSError, http.client.HTTPException)
DEFAULT_FETCH_TIMEOUT = 15
DEFAULT_FETCH_RETRY_COUNT = 2
DEFAULT_FETCH_RETRY_DELAY_SECONDS = 2
NETWORK_FETCH_ERRORS = (OSError, http.client.HTTPException)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def normalize_title(value):
    """HTML 엔티티, 태그, 중복 공백을 제거해 기사 제목 문자열을 정규화합니다.

    입력: value는 원본 제목 문자열 또는 HTML 조각입니다.
    출력: 정리된 제목 문자열을 반환합니다.
    """
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_naver_newspaper_front_page_articles(html_text, base_url):
    """네이버 신문보기 HTML에서 1면 영역의 기사 제목과 URL을 추출합니다.

    입력: html_text는 신문보기 HTML 문자열, base_url은 상대 링크 보정 기준 URL입니다.
    출력: title과 url을 가진 1면 기사 딕셔너리 목록을 반환합니다.
    """
    match = re.search(
        r'<div class="newspaper_brick_item[^"]*_start_page[^"]*">(?P<block>[\s\S]*?)(?=\s*<div class="newspaper_brick_item|\Z)',
        html_text,
    )
    if not match:
        match = re.search(
            r'<div class="newspaper_brick_item[^"]*">(?P<block>[\s\S]*?<em>\s*1\s*</em>\s*면[\s\S]*?)(?=\s*<div class="newspaper_brick_item|\Z)',
            html_text,
        )
    if not match:
        return []

    articles = []
    seen = set()
    block = match.group("block")
    for article_match in re.finditer(
        r'<a\b[^>]*href="(?P<url>[^"]*/article/newspaper/[^"]*)"[^>]*>(?P<body>[\s\S]*?)</a>',
        block,
    ):
        title_match = re.search(r"<strong[^>]*>(?P<title>[\s\S]*?)</strong>", article_match.group("body"))
        if not title_match:
            continue
        title = normalize_title(title_match.group("title"))
        url = urllib.parse.urljoin(base_url, article_match.group("url"))
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        articles.append({"title": title, "url": url})
    return articles


def is_retryable_fetch_error(exc):
    """요청 실패를 재시도할 수 있는 일시적 오류인지 판단합니다.

    입력: exc는 요청 중 발생한 예외입니다.
    출력: 타임아웃/연결 오류/서버 오류면 True, 404 같은 응답 오류면 False를 반환합니다.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return True


def fetch_html(
    url,
    timeout=DEFAULT_FETCH_TIMEOUT,
    retry_count=DEFAULT_FETCH_RETRY_COUNT,
    retry_delay=DEFAULT_FETCH_RETRY_DELAY_SECONDS,
    logger=None,
):
    """지정한 URL의 HTML을 User-Agent 헤더와 함께 가져옵니다.

    입력: url은 요청 URL, timeout은 초 단위 네트워크 제한 시간, retry_count는 추가 재시도 횟수, retry_delay는 재시도 간 대기 초, logger는 선택적 로거입니다.
    출력: 디코딩된 HTML 문자열을 반환하고, 모든 시도가 실패하면 RuntimeError를 발생시킵니다.
    비고: SSL 핸드셰이크 타임아웃 같은 일시적 네트워크 오류는 대기 시간을 늘려가며 재시도합니다.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    attempt_limit = max(1, retry_count + 1)
    last_error = None
    for attempt in range(1, attempt_limit + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except NETWORK_FETCH_ERRORS as exc:
            last_error = exc
            if logger:
                logger.warning(
                    "news_page_fetch_failed url=%s attempt=%s/%s error=%s",
                    url,
                    attempt,
                    attempt_limit,
                    exc,
                )
            if attempt >= attempt_limit or not is_retryable_fetch_error(exc):
                break
            time.sleep(retry_delay * attempt)
    raise RuntimeError(f"Failed to fetch news page: {url}: {last_error}") from last_error


def fetch_naver_newspaper_front_page_articles(
    sources=DEFAULT_NEWSPAPER_SOURCES,
    limit=DEFAULT_NEWS_TITLE_LIMIT,
    timeout=DEFAULT_FETCH_TIMEOUT,
    logger=None,
):
    """여러 네이버 신문보기 출처에서 1면 기사 목록을 수집합니다.

    입력: sources는 name/url 딕셔너리 목록, limit은 전체 최대 기사 수, timeout은 요청 제한 시간, logger는 선택적 로거입니다.
    출력: source_name, title, url을 가진 기사 딕셔너리 목록을 반환합니다.
    비고: 일부 출처 요청이 실패해도 나머지 출처로 계속 진행하고, 모든 출처가 실패했을 때만 ValueError를 발생시킵니다.
    """
    articles = []
    failures = []
    for source in sources:
        try:
            html_text = fetch_html(source["url"], timeout=timeout, logger=logger)
        except RuntimeError as exc:
            failures.append(f"{source['name']}: {exc}")
            if logger:
                logger.warning(
                    "news_source_fetch_skipped source=%s url=%s error=%s",
                    source["name"],
                    source["url"],
                    exc,
                )
            continue
        source_articles = extract_naver_newspaper_front_page_articles(
            html_text,
            base_url=source["url"],
        )
        for article in source_articles:
            articles.append(
                {
                    "source_name": source["name"],
                    "title": article["title"],
                    "url": article["url"],
                }
            )

    if limit:
        articles = articles[:limit]
    if not articles:
        source_urls = ", ".join(source["url"] for source in sources)
        message = f"No Naver newspaper front-page articles found from: {source_urls}"
        if failures:
            message = f"{message} (fetch failures: {' | '.join(failures)})"
        raise ValueError(message)
    return articles


def extract_html_element_by_id(html_text, element_id):
    """HTML에서 지정한 id를 가진 article/div/section 요소의 내부 HTML을 잘라냅니다.

    입력: html_text는 기사 페이지 HTML 문자열, element_id는 찾을 요소의 id 값입니다.
    출력: 여는 태그와 짝이 맞는 닫는 태그 사이의 HTML 조각을 반환하고, 없으면 빈 문자열을 반환합니다.
    """
    open_tag_match = re.search(
        r'<(?P<tag>article|div|section)\b[^>]*id="%s"[^>]*>' % re.escape(element_id),
        html_text,
    )
    if not open_tag_match:
        return ""

    tag = open_tag_match.group("tag")
    start = open_tag_match.end()
    depth = 1
    for tag_match in re.finditer(r"</?%s\b" % tag, html_text[start:]):
        if tag_match.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return html_text[start : start + tag_match.start()]
        else:
            depth += 1
    return html_text[start:]


def normalize_article_body(value, char_limit=DEFAULT_ARTICLE_BODY_CHAR_LIMIT):
    """기사 본문 HTML 조각을 한 줄 평문 텍스트로 정리하고 길이를 제한합니다.

    입력: value는 본문 HTML 조각, char_limit은 프롬프트에 담을 최대 글자 수입니다.
    출력: 태그와 스크립트를 제거하고 공백을 정리한 본문 문자열을 반환합니다.
    """
    fragment = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1>", " ", str(value))
    fragment = re.sub(r"<!--[\s\S]*?-->", " ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    body = html.unescape(fragment)
    body = re.sub(r"\s+", " ", body).strip()
    if char_limit and len(body) > char_limit:
        truncated = body[:char_limit]
        last_space = truncated.rfind(" ")
        if last_space > char_limit // 2:
            truncated = truncated[:last_space]
        body = truncated.strip()
    return body


def extract_naver_article_body(html_text, char_limit=DEFAULT_ARTICLE_BODY_CHAR_LIMIT):
    """네이버 기사 페이지 HTML에서 본문 텍스트를 추출합니다.

    입력: html_text는 기사 페이지 HTML 문자열, char_limit은 최대 본문 글자 수입니다.
    출력: 정리된 본문 텍스트를 반환하고, 본문 영역을 찾지 못하면 빈 문자열을 반환합니다.
    """
    for element_id in ARTICLE_BODY_ELEMENT_IDS:
        fragment = extract_html_element_by_id(html_text, element_id)
        if not fragment:
            continue
        body = normalize_article_body(fragment, char_limit=char_limit)
        if body:
            return body
    return ""


def fetch_naver_article_body(url, timeout=DEFAULT_FETCH_TIMEOUT, char_limit=DEFAULT_ARTICLE_BODY_CHAR_LIMIT):
    """기사 URL을 요청해 본문 텍스트를 가져옵니다.

    입력: url은 기사 URL, timeout은 초 단위 요청 제한 시간, char_limit은 최대 본문 글자 수입니다.
    출력: 정리된 본문 텍스트를 반환합니다.
    """
    html_text = fetch_html(url, timeout=timeout)
    return extract_naver_article_body(html_text, char_limit=char_limit)


def attach_naver_article_bodies(
    articles,
    char_limit=DEFAULT_ARTICLE_BODY_CHAR_LIMIT,
    timeout=DEFAULT_FETCH_TIMEOUT,
    max_workers=DEFAULT_ARTICLE_BODY_WORKERS,
    logger=None,
):
    """기사 목록의 각 URL 본문을 병렬로 수집해 body 값을 채웁니다.

    입력: articles는 title/url을 가진 기사 목록, char_limit은 최대 본문 글자 수, timeout은 요청 제한 시간, max_workers는 동시 요청 수, logger는 선택적 로거입니다.
    출력: 각 기사에 body 값을 추가한 새 기사 목록을 반환합니다.
    비고: 개별 기사 요청이 실패해도 전체 실행을 중단하지 않고 해당 기사의 body만 빈 문자열로 둡니다.
    """
    article_list = [dict(article) for article in articles]
    if not article_list or char_limit < 1:
        return article_list

    workers = max(1, min(max_workers, len(article_list)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                fetch_naver_article_body,
                article["url"],
                timeout,
                char_limit,
            ): index
            for index, article in enumerate(article_list)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                article_list[index]["body"] = future.result()
            except ARTICLE_BODY_FETCH_ERRORS as exc:
                article_list[index]["body"] = ""
                if logger:
                    logger.warning(
                        "article_body_fetch_failed url=%s error=%s",
                        article_list[index]["url"],
                        exc,
                    )
    return article_list


def count_articles_with_body(articles):
    """본문 수집에 성공한 기사 개수를 셉니다.

    입력: articles는 body 값을 가질 수 있는 기사 목록입니다.
    출력: 본문 텍스트가 채워진 기사 개수를 반환합니다.
    """
    return sum(1 for article in articles if (article.get("body") or "").strip())


def build_news_keyword_response_json_schema(
    keyword_count=NEWS_KEYWORD_COUNT,
    include_learning_content=False,
):
    """뉴스 키워드 후보 추출용 JSON 응답 스키마를 만듭니다."""
    properties = {
        "keyword": {"type": "string"},
        "source_url": {"type": "string"},
        "reason": {"type": "string"},
    }
    required = ["keyword", "source_url", "reason"]
    if include_learning_content:
        properties["keyword_description"] = {"type": "string"}
        properties["quiz"] = {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "option_a": {"type": "string"},
                "option_b": {"type": "string"},
                "answer": {"type": "string"},
                "explanation": {"type": "string"},
            },
            "required": ["question", "option_a", "option_b", "answer", "explanation"],
        }
        required.extend(["keyword_description", "quiz"])

    return {
        "type": "array",
        "minItems": keyword_count,
        "maxItems": keyword_count,
        "items": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def build_news_keyword_prompt_examples(
    keyword_count=NEWS_KEYWORD_COUNT,
    include_learning_content=False,
):
    """Build example keyword candidate objects for the extraction prompt.

    비고: 예시는 서로 다른 유형의 한국어 키워드로 구성하고, 정답 위치(A/B)를 섞어 모델이 틀을 베끼지 않게 합니다.
    """
    examples = [
        {
            "keyword": "코스피",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000001",
            "reason": "외국인 순매수가 이어지면서 지수가 연중 최고치를 경신한 시장 움직임을 다룬다.",
            "keyword_description": (
                "코스피는 국내 상장 대형주의 가격 흐름을 한 숫자로 보여 줘서 "
                "외국인 자금이 얼마나 들어오는지 가늠하는 잣대가 돼요."
            ),
            "quiz": {
                "question": "외국인 순매수로 코스피가 오를 때 더 타당한 해석은 무엇일까요?",
                "option_a": "외국인 매수 자체가 지수를 밀어 올리므로 수급만 보면 이후 흐름까지 충분히 설명된다고 보는 해석",
                "option_b": "수급이 지수를 밀어 올리더라도 기업 실적과 환율 전망이 뒷받침돼야 상승이 이어진다고 보는 해석",
                "answer": "B",
                "explanation": "수급만 보고 실적과 환율이라는 지속 조건을 놓친 해석보다 두 조건까지 함께 보는 해석이 더 정확해요.",
            },
        },
        {
            "keyword": "ETF",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000002",
            "reason": "신규 ETF 상장 직후 자금이 빠르게 유입되며 특정 테마 투자 수요가 확인됐다.",
            "keyword_description": (
                "ETF는 여러 종목을 묶어 주식처럼 사고파는 상품이라 "
                "이번 상장은 개인 자금이 어디로 몰리는지 보여 주는 단서예요."
            ),
            "quiz": {
                "question": "신규 ETF에 자금이 빠르게 몰릴 때 더 타당한 해석은 무엇일까요?",
                "option_a": "특정 테마의 수요가 커졌다는 신호지만 운용 보수와 쏠림 위험까지 따져야 투자 부담을 판단할 수 있다고 보는 해석",
                "option_b": "자금이 몰린 만큼 해당 테마 기업의 이익이 이미 늘었다는 근거로 볼 수 있어 추가 확인이 필요 없다고 보는 해석",
                "answer": "A",
                "explanation": "자금 유입을 기업 이익의 증거로 착각한 해석보다 수요 신호와 비용, 쏠림 위험을 함께 보는 해석이 더 적절해요.",
            },
        },
        {
            "keyword": "전기요금",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000003",
            "reason": "전기요금 인상으로 한국전력의 적자 부담과 가계, 기업의 비용 증가가 함께 거론된다.",
            "keyword_description": (
                "전기요금은 공장과 가정이 매달 내는 에너지 비용이라 "
                "인상되면 기업의 생산 원가와 물가 부담이 함께 움직여요."
            ),
            "quiz": {
                "question": "전기요금이 오를 때 더 타당한 해석은 무엇일까요?",
                "option_a": "한국전력의 적자가 줄어드는 만큼 요금 인상의 효과는 공기업 재무 개선에 한정된다고 보는 해석",
                "option_b": "한국전력의 적자는 줄 수 있지만 제조업 원가가 올라 제품 가격과 물가로 번질 수 있다고 보는 해석",
                "answer": "B",
                "explanation": "공기업 재무만 본 해석보다 원가와 물가로 번지는 간접 효과까지 함께 보는 해석이 더 적절해요.",
            },
        },
        {
            "keyword": "HBM",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000004",
            "reason": "AI 서버 수요가 늘면서 HBM 공급 계약이 반도체 기업의 실적 전망을 끌어올리고 있다.",
            "keyword_description": (
                "HBM은 AI 반도체 옆에 붙는 고성능 메모리로, "
                "이 제품 수요가 메모리 기업의 이익을 좌우하는 핵심 변수가 돼요."
            ),
            "quiz": {
                "question": "HBM 수요가 급증할 때 메모리 기업에 대한 더 타당한 해석은 무엇일까요?",
                "option_a": "고부가 제품 비중이 늘어 이익률이 개선될 수 있지만 증설 부담과 수요 둔화 시 재고 위험도 함께 커진다고 보는 해석",
                "option_b": "수요가 늘었으니 증설 부담 없이 기존 설비만으로 가격 상승분이 그대로 이익으로 이어진다고 보는 해석",
                "answer": "A",
                "explanation": "수요 증가만 보고 증설 비용과 재고 위험을 놓친 해석보다 반대편 효과까지 함께 보는 해석이 더 정확해요.",
            },
        },
        {
            "keyword": "주택공급",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000005",
            "reason": "정부가 수도권 주택 공급 확대 방안을 내놓으며 집값 안정 효과를 두고 논의가 이어진다.",
            "keyword_description": (
                "주택공급은 새로 지어 시장에 내놓는 집의 양이라 "
                "늘어나는 속도에 따라 집값과 전세 부담이 달라질 수 있어요."
            ),
            "quiz": {
                "question": "주택 공급 확대 계획이 발표될 때 더 타당한 해석은 무엇일까요?",
                "option_a": "발표 직후 공급이 곧바로 늘어 집값이 내려가므로 단기 시장 반응이 가장 중요하다고 보는 해석",
                "option_b": "입주까지 시간이 걸려 단기에는 기대 심리가 먼저 움직이고 실제 가격 효과는 공급 속도에 달렸다고 보는 해석",
                "answer": "B",
                "explanation": "발표와 실제 입주 시점의 차이를 놓친 해석보다 기대 심리와 공급 속도를 구분하는 해석이 더 적절해요.",
            },
        },
    ][:keyword_count]

    if include_learning_content:
        return examples

    return [
        {key: example[key] for key in ("keyword", "source_url", "reason")}
        for example in examples
    ]


def build_news_keyword_prompt(
    articles,
    keyword_count=NEWS_KEYWORD_COUNT,
    include_learning_content=False,
):
    """기사 목록을 기반으로 뉴스 키워드 후보 추출용 LLM 프롬프트를 생성합니다."""
    articles_json = json.dumps(list(articles), ensure_ascii=False, indent=2)
    example_output = json.dumps(
        build_news_keyword_prompt_examples(
            keyword_count=keyword_count,
            include_learning_content=include_learning_content,
        ),
        ensure_ascii=False,
        indent=2,
    )
    field_rule = '"keyword", "source_url", and "reason" string fields'
    learning_rules = ""
    if include_learning_content:
        field_rule = (
            '"keyword", "source_url", "reason", "keyword_description", '
            'and "quiz" fields'
        )
        learning_rules = """
- All Korean user-facing text in keyword_description, quiz.question, quiz.option_a, quiz.option_b, and quiz.explanation must use a friendly teacher-like 해요체 tone.
- keyword_description must be one single-line Korean sentence or sentence-like line explaining the keyword in this news context.
- keyword_description should be roughly twice as long as a very short one-line definition, about 45-75 Korean characters excluding spaces, with no newline.
- quiz must be an object with "question", "option_a", "option_b", "answer", and "explanation" fields.
- quiz.question must test the economic meaning or likely market/industry impact of the keyword, not just ask a direct dictionary definition.
- quiz should target a Korean high-school student who read the title and can reason about basic macroeconomics, markets, firms, consumers, and policy effects.
- option_a and option_b must both sound economically plausible at first glance; users should feel mildly unsure until they compare the mechanisms.
- option_a and option_b must be same-category economic interpretations with similar length, similar specificity, and similar confidence level.
- The correct option should be the more complete causal chain, such as policy -> costs -> prices, demand -> revenue -> investment, rates -> financing cost -> consumption, supply -> price pressure -> margins, or expectations -> asset prices -> behavior.
- The wrong option should be a tempting but incomplete interpretation: it may focus only on short-term sentiment, confuse direct and indirect effects, confuse level and rate of change, overemphasize one side of supply/demand, or ignore who bears the cost.
- Do not make the wrong option obviously false by using extreme words like "전혀", "무관", "반드시", "항상", "아예", "전부", "무조건", "사라진다", or "보장된다".
- Do not make either option about word counts, name changes, color changes, product weight, weather, random facts, jokes, salary statements, or unrelated objects.
- Avoid options where one choice says "has economic impact" and the other says "has no economic impact"; instead, make both choices describe different plausible economic impacts and let only one be more accurate.
- At least one option should include a second-order effect or tradeoff, such as cost burden, margin pressure, investment timing, consumer demand, regional employment, inflation expectations, exchange-rate sensitivity, or supply-chain risk.
- answer must be exactly "A" or "B".
- explanation must be one Korean sentence in friendly teacher-like 해요체 explaining why the answer is correct and why the other option is less appropriate.
- explanation must not refer to the options by the labels A or B, because the option order is shuffled after generation; describe the options by their content instead.
- keyword_description and quiz must stay grounded in the provided title and body: do not add company names, numbers, dates, or causal claims that they do not support. General economic mechanisms are allowed, but they must not contradict the article.
"""

    return f"""You extract image-generation source keywords from Korean economy newspaper front-page articles.

Select exactly {keyword_count} main keyword candidates from the article list.
The article list is JSON and Korean text is provided as-is.
Each article object has a "title" field and a "body" field that holds the beginning of the crawled article text.
The body may be truncated, and it is an empty string when the crawl failed. Read both fields to judge what the article is really about.

Rules:
- Return only a JSON array of exactly {keyword_count} objects.
- Each object must have {field_rule}.
- All keyword values must be unique.
- source_url must be copied exactly from the input article URL that most directly supports the keyword.
- reason must be one concrete Korean sentence explaining the economic event, metric, company action, or market movement behind the keyword.
- reason must stay grounded in the provided title and body, and must not add company names, numbers, causes, or claims that they do not support.
- Do not use meta phrases in reason such as "headline", "title", "article", or "news".
- A good reason names the actual substance, such as price movement, fund sales, product launch, policy change, supply contract, labor dispute, or earnings impact.
- Each keyword must appear in the selected article's title or body text, and it must be one of the core topics of that article.
- A keyword that is absent from the title is allowed when the body covers it as core content. Never use a term that appears only as minor background detail, and never use a term that is in neither the title nor the body.
- Do not select personal names, politician names, or executive names as keywords. Choose the economic term, event, product, policy, or market indicator instead.
- Do not output typo-like variants. If a title implies "KOSPI", never write a misspelled variant.
- Exclude security, diplomacy, and politics topics such as nuclear submarines, uranium, Iran, Trump, presidential remarks, and executive apologies unless the keyword itself is a direct economic market term.
- Prefer candidates like stock indexes, ETFs, IPOs, housing supply rules, virtual-asset accounts, tax revenue, nominal growth, and semiconductor market movement.
- Keep Korean text as normal readable Korean. Do not escape Korean characters as \\uXXXX.
- Do not add numbering, bullets, explanations, quotes, or markdown.
- Each keyword must be a concise economic term written exactly as it appears in the article text, either a Korean term or an acronym such as ETF, IPO, or HBM. Do not translate Korean terms into English.
- Prefer topics that can become educational economy image content.
- Avoid generic words like economy, market, company, government, issue, news.
{learning_rules}
Example output:
{example_output}

<ARTICLES_JSON>
{articles_json}
</ARTICLES_JSON>
"""


def parse_news_keyword_candidates(output_text, keyword_count=NEWS_KEYWORD_COUNT):
    """LLM 원문 응답에서 키워드 후보를 파싱하고 중복을 제거합니다.

    입력: output_text는 LLM 응답 문자열, keyword_count는 최대 후보 개수입니다.
    출력: keyword, source_url, reason을 가진 후보 딕셔너리 목록을 반환합니다.
    """
    candidates = parse_json_candidates(output_text)
    if not candidates:
        candidates = parse_line_candidates(output_text)

    unique_candidates = []
    seen = set()
    for candidate in candidates:
        normalized = normalize_candidate(candidate)
        keyword = normalized["keyword"]
        source_url = normalized["source_url"]
        if not keyword or not source_url:
            continue
        if keyword in seen:
            continue
        seen.add(keyword)
        unique_candidates.append(normalized)
        if len(unique_candidates) == keyword_count:
            break

    return unique_candidates


QUIZ_OPTION_LABEL_PATTERN = re.compile(r"(?<![A-Za-z0-9])[AB](?![A-Za-z0-9])")


def maybe_swap_quiz_options(quiz, rng=None):
    """퀴즈 보기 A/B를 절반 확률로 맞바꾸고 정답 표기를 함께 뒤집습니다.

    입력: quiz는 정규화된 퀴즈 딕셔너리, rng는 random() 메서드를 가진 난수 객체(기본은 random 모듈)입니다.
    출력: 새 퀴즈 딕셔너리를 반환합니다. 원본은 바꾸지 않습니다.
    비고: LLM이 정답을 A로 몰아서 내는 편향을 코드로 상쇄합니다.
    해설이 A/B 라벨을 직접 언급하면 순서를 바꿀 때 해설이 틀어지므로 그대로 둡니다.
    """
    rng = rng or random
    if rng.random() >= 0.5:
        return quiz
    if QUIZ_OPTION_LABEL_PATTERN.search(quiz.get("explanation", "")):
        return quiz
    swapped = dict(quiz)
    swapped["option_a"] = quiz["option_b"]
    swapped["option_b"] = quiz["option_a"]
    swapped["answer"] = "B" if quiz["answer"] == "A" else "A"
    return swapped


def filter_news_keyword_candidates(
    candidates,
    articles,
    keyword_count=NEWS_KEYWORD_COUNT,
    require_learning_content=False,
    rng=None,
):
    """후보가 실제 수집된 기사 URL과 기사 텍스트로 뒷받침되는지 검증하고 필터를 적용합니다.

    입력: candidates는 LLM 후보 목록, articles는 title/body를 가진 원본 기사 목록, keyword_count는 최대 후보 개수입니다.
    require_learning_content가 True이면 완성된 설명/퀴즈가 있는 후보만 남기고 퀴즈 보기 순서를 무작위로 섞습니다. rng는 테스트용 난수 객체입니다.
    출력: source_title과 정제된 reason을 포함한 후보 딕셔너리 목록을 반환합니다.
    비고: 키워드는 제목에 없어도 수집한 본문에 등장하면 허용합니다.
    """
    articles_by_url = {article["url"]: article for article in articles}
    filtered = []
    seen = set()

    for candidate in candidates:
        keyword = candidate["keyword"]
        article = articles_by_url.get(candidate["source_url"])
        if not article:
            continue
        if keyword in seen:
            continue
        if not is_allowed_news_keyword(keyword):
            continue
        if not is_keyword_supported_by_article(keyword, article["title"], article.get("body", "")):
            continue
        if require_learning_content and not has_complete_candidate_learning_content(candidate):
            continue

        filtered_candidate = dict(candidate)
        if require_learning_content:
            quiz = normalize_candidate_learning_content(candidate)["quiz"]
            filtered_candidate["quiz"] = maybe_swap_quiz_options(quiz, rng)
        filtered_candidate["source_title"] = article["title"]
        if article.get("source_name"):
            filtered_candidate["source_name"] = article["source_name"]
        seen.add(keyword)
        filtered.append(filtered_candidate)
        if len(filtered) == keyword_count:
            break

    return filtered


def is_allowed_news_keyword(keyword):
    """키워드가 길이와 최소 문자 조건을 통과하는지 확인합니다.

    입력: keyword는 검사할 키워드 문자열입니다.
    출력: 허용 가능하면 True, 제외해야 하면 False를 반환합니다.
    """
    if not keyword:
        return False
    normalized = normalize_keyword_match_text(keyword)
    if len(normalized) < 2:
        return False
    if len(keyword) > 20:
        return False
    return True


def is_keyword_supported_by_article(keyword, title, body=""):
    """키워드가 기사 제목 또는 수집한 본문에 등장하는지 확인합니다.

    입력: keyword는 후보 키워드, title은 기사 제목, body는 수집한 기사 본문 텍스트입니다.
    출력: 제목이나 본문에 등장하면 True, 둘 다에 없으면 False를 반환합니다.
    비고: 본문 수집에 실패해 body가 비어 있으면 검증 근거가 없으므로 제목 검사만 적용하고 통과시킵니다.
    """
    normalized_keyword = normalize_keyword_match_text(keyword)
    if not normalized_keyword:
        return False
    if normalized_keyword in normalize_keyword_match_text(title):
        return True

    normalized_body = normalize_keyword_match_text(body)
    if not normalized_body:
        return True
    return normalized_keyword in normalized_body


def normalize_keyword_match_text(value):
    """키워드 비교를 위해 한글, 영문, 숫자를 제외한 문자를 제거하고 소문자로 바꿉니다.

    입력: value는 비교 대상 문자열입니다.
    출력: 비교용으로 정규화된 문자열을 반환합니다.
    """
    return "".join(character for character in str(value).lower() if character.isalnum())


def parse_json_candidates(output_text):
    """LLM 응답에서 JSON 배열 또는 후보 목록 필드를 찾아 파싱합니다.

    입력: output_text는 LLM 응답 문자열입니다.
    출력: 파싱된 후보 원본 목록을 반환하고, 실패하면 빈 목록을 반환합니다.
    """
    text = output_text.strip()
    json_texts = [text]
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and start < end:
        json_texts.append(text[start : end + 1])

    value = None
    for json_text in json_texts:
        try:
            value = json.loads(json_text)
            break
        except json.JSONDecodeError:
            continue
    if value is None:
        return []

    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("keywords", "candidates", "keyword_candidates"):
            items = value.get(key)
            if isinstance(items, list):
                return items
    return []


def parse_line_candidates(output_text):
    """JSON 파싱에 실패한 응답을 줄 단위 후보 문자열 목록으로 해석합니다.

    입력: output_text는 LLM 응답 문자열입니다.
    출력: 비어 있지 않은 줄의 후보 문자열 목록을 반환합니다.
    """
    candidates = []
    for line in output_text.splitlines():
        line = re.sub(r"^\s*(?:[-*]|\d+[\).:-])\s*", "", line)
        line = line.strip().strip("\"'` ")
        if line:
            candidates.append(line)
    return candidates


def normalize_optional_candidate_text(value):
    """선택 필드의 문자열 값을 정리합니다."""
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"\s+", " ", text)
    return text.strip().strip("\"'` ")


def normalize_quiz_answer(value):
    """LLM이 반환한 정답 표기를 A 또는 B로 정규화합니다."""
    text = normalize_optional_candidate_text(value).upper().strip(".:)")
    if text in {"A", "OPTION_A", "OPTION A", "QUIZ_OPTION_A"}:
        return "A"
    if text in {"B", "OPTION_B", "OPTION B", "QUIZ_OPTION_B"}:
        return "B"
    return ""


def first_candidate_text(*values):
    """여러 후보 값 중 첫 번째로 비어 있지 않은 문자열을 반환합니다."""
    for value in values:
        text = normalize_optional_candidate_text(value)
        if text:
            return text
    return ""


def normalize_candidate_learning_content(candidate):
    """키워드 설명과 A/B 퀴즈 필드를 표준 구조로 정규화합니다."""
    if not isinstance(candidate, dict):
        return {}

    quiz = candidate.get("quiz")
    if not isinstance(quiz, dict):
        quiz = {}

    description = first_candidate_text(
        candidate.get("keyword_description"),
        candidate.get("one_line_description"),
        candidate.get("description"),
    )
    question = first_candidate_text(
        quiz.get("question"),
        candidate.get("quiz_question"),
        candidate.get("question"),
    )
    option_a = first_candidate_text(
        quiz.get("option_a"),
        quiz.get("A"),
        quiz.get("a"),
        candidate.get("quiz_option_a"),
        candidate.get("option_a"),
    )
    option_b = first_candidate_text(
        quiz.get("option_b"),
        quiz.get("B"),
        quiz.get("b"),
        candidate.get("quiz_option_b"),
        candidate.get("option_b"),
    )
    answer = normalize_quiz_answer(
        quiz.get("answer")
        or candidate.get("quiz_answer")
        or candidate.get("answer")
    )
    explanation = first_candidate_text(
        quiz.get("explanation"),
        candidate.get("quiz_explanation"),
        candidate.get("explanation"),
    )

    learning_content = {}
    if description:
        learning_content["keyword_description"] = description

    normalized_quiz = {}
    if question:
        normalized_quiz["question"] = question
    if option_a:
        normalized_quiz["option_a"] = option_a
    if option_b:
        normalized_quiz["option_b"] = option_b
    if answer:
        normalized_quiz["answer"] = answer
    if explanation:
        normalized_quiz["explanation"] = explanation
    if normalized_quiz:
        learning_content["quiz"] = normalized_quiz
    return learning_content


def has_complete_candidate_learning_content(candidate):
    """후보에 표준 설명과 완성된 A/B 퀴즈가 있는지 확인합니다."""
    learning_content = normalize_candidate_learning_content(candidate)
    quiz = learning_content.get("quiz")
    return (
        bool(learning_content.get("keyword_description"))
        and isinstance(quiz, dict)
        and bool(quiz.get("question"))
        and bool(quiz.get("option_a"))
        and bool(quiz.get("option_b"))
        and quiz.get("answer") in {"A", "B"}
        and bool(quiz.get("explanation"))
    )


def normalize_candidate(candidate):
    """딕셔너리 또는 문자열 후보를 표준 후보 딕셔너리 형태로 정규화합니다.

    입력: candidate는 keyword/source_url/reason 딕셔너리 또는 구분자를 포함한 문자열입니다.
    출력: keyword, source_url, reason 키를 가진 정규화된 딕셔너리를 반환합니다.
    """
    if isinstance(candidate, dict):
        keyword = candidate.get("keyword", "")
        source_url = candidate.get("source_url", "")
        reason = candidate.get("reason", "")
    else:
        text = str(candidate)
        parts = [part.strip() for part in re.split(r"\s+\|\s+", text, maxsplit=2)]
        if len(parts) == 3:
            keyword, source_url, reason = parts
        else:
            match = re.match(r"(.+?)\s+(?:-|->)\s+(https?://\S+)\s+(?:-|->)\s+(.+)", text)
            if match:
                keyword = match.group(1)
                source_url = match.group(2)
                reason = match.group(3)
            else:
                keyword = text
                source_url = ""
                reason = ""

    keyword = html.unescape(str(keyword))
    keyword = re.sub(r"\s+", " ", keyword)
    source_url = html.unescape(str(source_url))
    source_url = re.sub(r"\s+", "", source_url)
    reason = html.unescape(str(reason))
    reason = re.sub(r"\s+", " ", reason)
    normalized = {
        "keyword": keyword.strip().strip("\"'` "),
        "source_url": source_url.strip().strip("\"'` "),
        "reason": reason.strip().strip("\"'` "),
    }
    normalized.update(normalize_candidate_learning_content(candidate))
    return normalized
