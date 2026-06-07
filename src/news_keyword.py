import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


NAVER_ECONOMY_NEWS_URL = "https://news.naver.com/section/101"
DEFAULT_NEWSPAPER_SOURCES = (
    {"name": "파이낸셜뉴스", "url": "https://media.naver.com/press/014/newspaper"},
    {"name": "머니투데이", "url": "https://media.naver.com/press/008/newspaper"},
    {"name": "서울경제", "url": "https://media.naver.com/press/011/newspaper"},
    {"name": "한국경제", "url": "https://media.naver.com/press/015/newspaper"}
)
DEFAULT_NEWS_TITLE_LIMIT = 30
NEWS_KEYWORD_COUNT = 3
NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT = 5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class NaverNewsTitleParser(HTMLParser):
    """네이버 경제 뉴스 HTML에서 기사 제목 링크를 수집하는 HTMLParser 구현체입니다.

    입력: feed 메서드로 HTML 문자열을 입력받습니다.
    출력: articles 속성에 title과 url을 가진 기사 딕셔너리 목록을 누적합니다.
    """

    def __init__(self):
        """파서 상태와 수집 결과 저장소를 초기화합니다.

        입력: 별도 인자를 받지 않습니다.
        출력: 빈 articles 목록과 캡처 상태를 가진 파서 인스턴스를 구성합니다.
        """
        super().__init__(convert_charrefs=True)
        self.articles = []
        self._capture_depth = 0
        self._current_url = ""
        self._parts = []

    def handle_starttag(self, tag, attrs):
        """제목 링크로 판단되는 a 태그를 만나면 텍스트 캡처를 시작합니다.

        입력: tag는 태그명, attrs는 HTML 속성 튜플 목록입니다.
        출력: 내부 캡처 상태를 갱신하고 None을 반환합니다.
        """
        if self._capture_depth:
            self._capture_depth += 1
            return

        if tag != "a":
            return

        attrs_by_name = dict(attrs)
        class_names = attrs_by_name.get("class", "").split()
        if "sa_text_title" in class_names:
            self._capture_depth = 1
            self._current_url = attrs_by_name.get("href", "")
            self._parts = []

    def handle_endtag(self, tag):
        """캡처 중인 태그가 끝나면 제목과 URL을 기사 목록에 추가합니다.

        입력: tag는 닫힘 HTML 태그명입니다.
        출력: articles 목록과 내부 상태를 갱신하고 None을 반환합니다.
        """
        if not self._capture_depth:
            return

        self._capture_depth -= 1
        if self._capture_depth:
            return

        title = normalize_title(" ".join(self._parts))
        if title:
            self.articles.append({"title": title, "url": self._current_url})
        self._current_url = ""
        self._parts = []

    def handle_data(self, data):
        """캡처 중인 제목 텍스트 조각을 임시 목록에 저장합니다.

        입력: data는 HTMLParser가 전달한 텍스트 조각입니다.
        출력: 내부 텍스트 조각 목록을 갱신하고 None을 반환합니다.
        """
        if self._capture_depth:
            self._parts.append(data)


def normalize_title(value):
    """HTML 엔티티, 태그, 중복 공백을 제거해 기사 제목 문자열을 정규화합니다.

    입력: value는 원본 제목 문자열 또는 HTML 조각입니다.
    출력: 정리된 제목 문자열을 반환합니다.
    """
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_naver_news_titles(html_text):
    """네이버 경제 뉴스 HTML에서 기사 제목만 추출합니다.

    입력: html_text는 네이버 뉴스 HTML 문자열입니다.
    출력: 중복 제거된 기사 제목 문자열 목록을 반환합니다.
    """
    return [article["title"] for article in extract_naver_news_articles(html_text)]


def extract_naver_news_articles(html_text, base_url=NAVER_ECONOMY_NEWS_URL):
    """네이버 경제 뉴스 HTML에서 제목과 URL을 가진 기사 목록을 추출합니다.

    입력: html_text는 HTML 문자열, base_url은 상대 링크를 절대 링크로 바꿀 기준 URL입니다.
    출력: title과 url을 가진 기사 딕셔너리 목록을 반환합니다.
    """
    parser = NaverNewsTitleParser()
    parser.feed(html_text)

    articles = []
    seen = set()
    for article in parser.articles:
        title = article["title"]
        if title in seen:
            continue
        seen.add(title)
        articles.append(
            {
                "title": title,
                "url": urllib.parse.urljoin(base_url, article["url"]),
            }
        )
    return articles


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


def fetch_html(url, timeout=10):
    """지정한 URL의 HTML을 User-Agent 헤더와 함께 가져옵니다.

    입력: url은 요청 URL, timeout은 초 단위 네트워크 제한 시간입니다.
    출력: 디코딩된 HTML 문자열을 반환하고, 요청 실패 시 RuntimeError를 발생시킵니다.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to fetch news page: {url}: {exc}") from exc


def fetch_naver_economy_articles(url=NAVER_ECONOMY_NEWS_URL, limit=DEFAULT_NEWS_TITLE_LIMIT, timeout=10):
    """네이버 경제 섹션에서 기사 목록을 가져옵니다.

    입력: url은 경제 섹션 URL, limit은 최대 기사 수, timeout은 요청 제한 시간입니다.
    출력: title과 url을 가진 기사 딕셔너리 목록을 반환합니다.
    """
    html_text = fetch_html(url, timeout=timeout)
    articles = extract_naver_news_articles(html_text, base_url=url)
    if limit:
        articles = articles[:limit]
    if not articles:
        raise ValueError(f"No Naver economy news titles found from {url}")
    return articles


def fetch_naver_newspaper_front_page_articles(sources=DEFAULT_NEWSPAPER_SOURCES, limit=DEFAULT_NEWS_TITLE_LIMIT, timeout=10):
    """여러 네이버 신문보기 출처에서 1면 기사 목록을 수집합니다.

    입력: sources는 name/url 딕셔너리 목록, limit은 전체 최대 기사 수, timeout은 요청 제한 시간입니다.
    출력: source_name, title, url을 가진 기사 딕셔너리 목록을 반환합니다.
    """
    articles = []
    for source in sources:
        html_text = fetch_html(source["url"], timeout=timeout)
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
        raise ValueError(f"No Naver newspaper front-page articles found from: {source_urls}")
    return articles


def fetch_naver_economy_titles(url=NAVER_ECONOMY_NEWS_URL, limit=DEFAULT_NEWS_TITLE_LIMIT, timeout=10):
    """네이버 경제 섹션 기사 목록에서 제목만 가져옵니다.

    입력: url은 경제 섹션 URL, limit은 최대 제목 수, timeout은 요청 제한 시간입니다.
    출력: 기사 제목 문자열 목록을 반환합니다.
    """
    return [
        article["title"]
        for article in fetch_naver_economy_articles(url=url, limit=limit, timeout=timeout)
    ]


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
    """Build example keyword candidate objects for the extraction prompt."""
    examples = [
        {
            "keyword": "KOSPI",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000001",
            "reason": "The index movement can explain investor sentiment and market direction.",
        },
        {
            "keyword": "ETF",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000002",
            "reason": "Fund listing and inflow show demand for investment products.",
        },
        {
            "keyword": "Bitcoin",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000003",
            "reason": "Price movement and ETF flow reveal virtual-asset investor sentiment.",
        },
        {
            "keyword": "HBM",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000004",
            "reason": "AI server demand links memory supply to semiconductor earnings.",
        },
        {
            "keyword": "Electricity tariff",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000005",
            "reason": "Tariff changes can affect household inflation and company costs.",
        },
        {
            "keyword": "IPO",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000006",
            "reason": "Listing plans connect company financing with stock-market interest.",
        },
        {
            "keyword": "Housing price",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000007",
            "reason": "Housing price changes explain real-estate supply and buyer burden.",
        },
        {
            "keyword": "Semiconductor",
            "source_url": "https://n.news.naver.com/mnews/article/001/0000000008",
            "reason": "Export and investment flows connect the sector to domestic industry cycles.",
        },
    ][:keyword_count]

    if not include_learning_content:
        return examples

    for example in examples:
        keyword = example["keyword"]
        example["keyword_description"] = (
            f"{keyword}은 이번 기사에서 시장 참여자들이 어떤 비용, 수요, 투자 흐름을 "
            "보고 판단해야 하는지 알려주는 핵심 단서예요."
        )
        example["quiz"] = {
            "question": f"{keyword} 이슈를 해석할 때 더 타당한 관점은 무엇일까요?",
            "option_a": "가격, 수요, 자금 흐름이 기업 실적이나 소비자 부담으로 이어지는지를 함께 보는 관점",
            "option_b": "단기 관심이 커져도 실제 비용 구조보다는 투자 심리 변화에만 의미가 있다고 보는 관점",
            "answer": "A",
            "explanation": "B도 일부 맞아 보이지만 비용과 수요까지 함께 봐야 흐름을 더 정확히 이해할 수 있어요.",
        }
    return examples

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
"""

    return f"""You extract image-generation source keywords from Korean economy newspaper front-page article titles.

Select exactly {keyword_count} main keyword candidates from the article list.
The article list is JSON and Korean text is provided as-is.

Rules:
- Return only a JSON array of exactly {keyword_count} objects.
- Each object must have {field_rule}.
- All keyword values must be unique.
- source_url must be copied exactly from the input article URL that most directly supports the keyword.
- reason must be one concrete Korean sentence explaining the economic event, metric, company action, or market movement behind the keyword.
- reason must use only facts visible in the input title and must not add company names, numbers, causes, or claims that are not present there.
- Do not use meta phrases in reason such as "headline", "title", "article", or "news".
- A good reason names the actual substance, such as price movement, fund sales, product launch, policy change, supply contract, labor dispute, or earnings impact.
- Each keyword must be copied from the selected title or be a standard economic term directly implied by it, such as "KOSPI" for an index-level title.
- Do not select personal names, politician names, or executive names as keywords. Choose the economic term, event, product, policy, or market indicator instead.
- Do not output typo-like variants. If a title implies "KOSPI", never write a misspelled variant.
- Exclude security, diplomacy, and politics topics such as nuclear submarines, uranium, Iran, Trump, presidential remarks, and executive apologies unless the keyword itself is a direct economic market term.
- Prefer candidates like stock indexes, ETFs, IPOs, housing supply rules, virtual-asset accounts, tax revenue, nominal growth, and semiconductor market movement.
- Keep Korean text as normal readable Korean. Do not escape Korean characters as \\uXXXX.
- Do not add numbering, bullets, explanations, quotes, or markdown.
- Each keyword must be a concise Korean or common English economic term.
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


def filter_news_keyword_candidates(
    candidates,
    articles,
    keyword_count=NEWS_KEYWORD_COUNT,
    require_learning_content=False,
):
    """후보가 실제 기사 URL과 제목에 의해 뒷받침되는지 검증하고 필터를 적용합니다.

    입력: candidates는 LLM 후보 목록, articles는 원본 기사 목록, keyword_count는 최대 후보 개수입니다.
    출력: source_title과 정제된 reason을 포함한 후보 딕셔너리 목록을 반환합니다.
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
        if not is_keyword_supported_by_title(keyword, article["title"]):
            continue
        if require_learning_content and not has_complete_candidate_learning_content(candidate):
            continue

        filtered_candidate = dict(candidate)
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


def is_keyword_supported_by_title(keyword, title):
    """키워드가 기사 제목에 포함되어 있는지 확인합니다.

    입력: keyword는 후보 키워드, title은 원본 기사 제목입니다.
    출력: 제목으로 뒷받침되면 True, 그렇지 않으면 False를 반환합니다.
    """
    normalized_title = normalize_keyword_match_text(title)
    if not normalized_title:
        return False

    normalized_keyword = normalize_keyword_match_text(keyword)
    return bool(normalized_keyword and normalized_keyword in normalized_title)


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

