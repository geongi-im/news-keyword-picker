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
)
DEFAULT_NEWS_KEYWORD_MODEL = "gpt-5.4-mini"
DEFAULT_NEWS_TITLE_LIMIT = 30
NEWS_KEYWORD_COUNT = 5
BANNED_NEWS_KEYWORDS = {
    "정용진",
    "트럼프",
    "머스크",
    "이재명",
    "李",
    "경제",
    "시장",
    "회사",
    "기업",
    "정부",
    "이슈",
    "뉴스",
    "기사",
    "제목",
    "투자",
    "정책",
    "핵잠",
    "자주국방",
    "우라늄",
    "이란",
}
KEYWORD_TITLE_ALIASES = {
    "코스피": ("코스피", "8000피", "8천피"),
    "반도체": ("삼전닉스", "삼전", "닉스"),
    "가상자산": ("코인", "법인계좌"),
    "IPO": ("IPO", "상장"),
    "ETF": ("ETF",),
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class NaverNewsTitleParser(HTMLParser):
    """설명: 네이버 경제 뉴스 HTML에서 기사 제목 링크를 수집하는 HTMLParser 구현체입니다.
    입력: feed 메서드로 HTML 문자열을 입력받습니다.
    출력: articles 속성에 title과 url을 가진 기사 딕셔너리 목록을 누적합니다.
    """

    def __init__(self):
        """설명: 파서 상태와 수집 결과 저장소를 초기화합니다.
        입력: 별도 인자를 받지 않습니다.
        출력: 빈 articles 목록과 캡처 상태를 가진 파서 인스턴스를 구성합니다.
        """
        super().__init__(convert_charrefs=True)
        self.articles = []
        self._capture_depth = 0
        self._current_url = ""
        self._parts = []

    def handle_starttag(self, tag, attrs):
        """설명: 제목 링크로 판단되는 a 태그를 만나면 텍스트 캡처를 시작합니다.
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
        """설명: 캡처 중인 태그가 끝나면 제목과 URL을 기사 목록에 추가합니다.
        입력: tag는 닫힌 HTML 태그명입니다.
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
        """설명: 캡처 중인 제목 텍스트 조각을 임시 목록에 저장합니다.
        입력: data는 HTMLParser가 전달한 텍스트 조각입니다.
        출력: 내부 텍스트 조각 목록을 갱신하고 None을 반환합니다.
        """
        if self._capture_depth:
            self._parts.append(data)


def normalize_title(value):
    """설명: HTML 엔티티, 태그, 중복 공백을 제거해 기사 제목 문자열을 정규화합니다.
    입력: value는 원본 제목 문자열 또는 HTML 조각입니다.
    출력: 정리된 제목 문자열을 반환합니다.
    """
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_naver_news_titles(html_text):
    """설명: 네이버 경제 뉴스 HTML에서 기사 제목만 추출합니다.
    입력: html_text는 네이버 뉴스 HTML 문자열입니다.
    출력: 중복 제거된 기사 제목 문자열 목록을 반환합니다.
    """
    return [article["title"] for article in extract_naver_news_articles(html_text)]


def extract_naver_news_articles(html_text, base_url=NAVER_ECONOMY_NEWS_URL):
    """설명: 네이버 경제 뉴스 HTML에서 제목과 URL을 가진 기사 목록을 추출합니다.
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
    """설명: 네이버 신문보기 HTML에서 1면 영역의 기사 제목과 URL을 추출합니다.
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
    """설명: 지정한 URL의 HTML을 User-Agent 헤더와 함께 가져옵니다.
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
    """설명: 네이버 경제 섹션에서 기사 목록을 가져옵니다.
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
    """설명: 여러 네이버 신문보기 출처에서 1면 기사 목록을 수집합니다.
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
    """설명: 네이버 경제 섹션 기사 목록에서 제목만 가져옵니다.
    입력: url은 경제 섹션 URL, limit은 최대 제목 수, timeout은 요청 제한 시간입니다.
    출력: 기사 제목 문자열 목록을 반환합니다.
    """
    return [
        article["title"]
        for article in fetch_naver_economy_articles(url=url, limit=limit, timeout=timeout)
    ]


def build_news_keyword_prompt(articles, keyword_count=NEWS_KEYWORD_COUNT):
    """설명: 기사 목록을 기반으로 키워드 후보 추출용 LLM 프롬프트를 생성합니다.
    입력: articles는 기사 딕셔너리 목록, keyword_count는 요청할 후보 개수입니다.
    출력: LLM에 전달할 프롬프트 문자열을 반환합니다.
    """
    articles_json = json.dumps(list(articles), ensure_ascii=True, indent=2)
    return f"""You extract image-generation source keywords from Korean economy newspaper front-page article titles.

Select exactly {keyword_count} main keyword candidates from the article list.
The article list is JSON whose Korean text is encoded with Unicode escape sequences.
Decode those strings before selecting keywords.

Rules:
- Return only a JSON array of exactly {keyword_count} objects.
- Each object must have "keyword", "source_url", and "reason" string fields.
- All keyword values must be unique.
- source_url must be copied exactly from the input article URL that most directly supports the keyword.
- reason must be one concrete Korean sentence explaining the economic event, metric, company action, or market movement behind the keyword.
- reason must use only facts visible in the input title and must not add company names, numbers, causes, or claims that are not present there.
- Do not use meta phrases in reason such as "헤드라인", "제목", "기사", or "뉴스".
- A good reason names the actual substance, such as price movement, fund sales, product launch, policy change, supply contract, labor dispute, or earnings impact.
- Each keyword must be copied from the selected title or be a standard economic term directly implied by it, such as "코스피" for "8000피".
- Do not select personal names, politician names, or executive names as keywords. Choose the economic term, event, product, policy, or market indicator instead.
- Do not output typo-like variants. If a title implies "코스피", never write "카스피".
- Exclude security, diplomacy, and politics topics such as nuclear submarines, uranium, Iran, Trump, presidential remarks, and executive apologies unless the keyword itself is a direct economic market term.
- Prefer candidates like stock indexes, ETFs, IPOs, housing supply rules, virtual-asset accounts, tax revenue, nominal growth, and semiconductor market movement.
- Use Unicode escape sequences for non-ASCII characters in the JSON output.
- Do not add numbering, bullets, explanations, quotes, or markdown.
- Each keyword must be a concise Korean or common English economic term.
- Prefer topics that can become educational economy image content.
- Avoid generic words like economy, market, company, government, issue, news.

Example output:
[{{"keyword":"\\ucf54\\uc2a4\\ud53c","source_url":"https://n.news.naver.com/mnews/article/001/0000000001","reason":"\\uc9c0\\uc218\\uac00 8000\\uc120\\uc744 \\ub118\\uc5c8\\ub2e4\\ub294 \\uc2dc\\uc7a5 \\ubcc0\\ud654\\ub97c \\uc124\\uba85\\ud558\\uae30 \\uc88b\\uc740 \\ud22c\\uc790 \\uc9c0\\ud45c \\uc18c\\uc7ac\\uc785\\ub2c8\\ub2e4."}},{{"keyword":"ETF","source_url":"https://n.news.naver.com/mnews/article/001/0000000002","reason":"\\ub300\\uaddc\\ubaa8 \\uc0c1\\uc7a5\\uacfc \\uc790\\uae08 \\uc720\\uc785\\uc774 \\ud22c\\uc790\\uc0c1\\ud488 \\uc218\\uc694 \\ud750\\ub984\\uc744 \\ubcf4\\uc5ec\\uc90d\\ub2c8\\ub2e4."}},{{"keyword":"\\ube44\\ud2b8\\ucf54\\uc778","source_url":"https://n.news.naver.com/mnews/article/001/0000000003","reason":"\\uac00\\uaca9 \\ud6a1\\ubcf4\\uc640 ETF \\uc21c\\uc720\\ucd9c\\uc774 \\uac00\\uc0c1\\uc790\\uc0b0 \\ud22c\\uc790\\uc2ec\\ub9ac\\ub97c \\ub4dc\\ub7ec\\ub0c5\\ub2c8\\ub2e4."}},{{"keyword":"HBM","source_url":"https://n.news.naver.com/mnews/article/001/0000000004","reason":"AI \\uc11c\\ubc84 \\uc218\\uc694\\uac00 \\uace0\\ub300\\uc5ed\\ud3ed \\uba54\\ubaa8\\ub9ac \\uacf5\\uae09\\uacfc \\ubc18\\ub3c4\\uccb4 \\uc2e4\\uc801\\uc5d0 \\uc5f0\\uacb0\\ub429\\ub2c8\\ub2e4."}},{{"keyword":"\\uc804\\uae30\\uc694\\uae08","source_url":"https://n.news.naver.com/mnews/article/001/0000000005","reason":"\\uc694\\uae08 \\uc778\\uc0c1\\uc740 \\uac00\\uacc4 \\ubb3c\\uac00\\uc640 \\uae30\\uc5c5 \\uc6d0\\uac00\\uc5d0 \\ub3d9\\uc2dc\\uc5d0 \\uc601\\ud5a5\\uc744 \\uc904 \\uc218 \\uc788\\uc2b5\\ub2c8\\ub2e4."}}]

<ARTICLES_JSON>
{articles_json}
</ARTICLES_JSON>
"""


def parse_news_keyword_candidates(output_text, keyword_count=NEWS_KEYWORD_COUNT):
    """설명: LLM 원문 응답에서 키워드 후보를 파싱하고 중복을 제거합니다.
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


def filter_news_keyword_candidates(candidates, articles, keyword_count=NEWS_KEYWORD_COUNT):
    """설명: 후보가 실제 기사 URL과 제목에 의해 뒷받침되는지 검증하고 품질 필터를 적용합니다.
    입력: candidates는 LLM 후보 목록, articles는 원본 기사 목록, keyword_count는 최대 후보 개수입니다.
    출력: source_title과 정제된 reason이 포함된 후보 딕셔너리 목록을 반환합니다.
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

        filtered_candidate = dict(candidate)
        filtered_candidate["source_title"] = article["title"]
        filtered_candidate["reason"] = build_news_keyword_reason(keyword, article["title"])
        seen.add(keyword)
        filtered.append(filtered_candidate)
        if len(filtered) == keyword_count:
            break

    return filtered


def is_allowed_news_keyword(keyword):
    """설명: 키워드가 금지어, 길이, 최소 문자 조건을 통과하는지 확인합니다.
    입력: keyword는 검사할 키워드 문자열입니다.
    출력: 허용 가능하면 True, 제외해야 하면 False를 반환합니다.
    """
    if not keyword:
        return False
    if keyword in BANNED_NEWS_KEYWORDS:
        return False
    normalized = normalize_keyword_match_text(keyword)
    if len(normalized) < 2:
        return False
    if len(keyword) > 20:
        return False
    return True


def is_keyword_supported_by_title(keyword, title):
    """설명: 키워드 또는 사전 정의된 별칭이 기사 제목에 포함되어 있는지 확인합니다.
    입력: keyword는 후보 키워드, title은 원본 기사 제목입니다.
    출력: 제목으로 뒷받침되면 True, 그렇지 않으면 False를 반환합니다.
    """
    normalized_title = normalize_keyword_match_text(title)
    if not normalized_title:
        return False

    terms = [keyword]
    terms.extend(KEYWORD_TITLE_ALIASES.get(keyword, ()))
    terms.extend(KEYWORD_TITLE_ALIASES.get(keyword.upper(), ()))
    for term in terms:
        normalized_term = normalize_keyword_match_text(term)
        if normalized_term and normalized_term in normalized_title:
            return True
    return False


def normalize_keyword_match_text(value):
    """설명: 키워드 비교를 위해 한글, 영문, 숫자를 제외한 문자를 제거하고 소문자로 바꿉니다.
    입력: value는 비교 대상 문자열입니다.
    출력: 비교용으로 정규화된 문자열을 반환합니다.
    """
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value)).lower()


def build_news_keyword_reason(keyword, title):
    """설명: 필터를 통과한 키워드와 기사 제목으로 사용자에게 보여줄 근거 문장을 만듭니다.
    입력: keyword는 최종 후보 키워드, title은 해당 키워드를 뒷받침한 기사 제목입니다.
    출력: 한국어 근거 문장 문자열을 반환합니다.
    """
    title_text = clean_title_for_reason(title)
    keyword_key = keyword.upper()

    if keyword == "코스피":
        return f"{title_text} 상황을 바탕으로 국내 주가지수와 대형주 주가 흐름을 함께 설명하기 좋은 소재입니다."
    if keyword_key == "ETF":
        return f"{title_text} 흐름을 바탕으로 ETF 자금 규모와 지수 움직임의 관계를 설명하기 좋은 소재입니다."
    if keyword == "반도체":
        return f"{title_text} 흐름을 바탕으로 반도체 대형주가 지수 상승에 미치는 영향을 설명하기 좋은 소재입니다."
    if keyword_key == "IPO":
        return f"{title_text} 이슈를 바탕으로 기업공개 규모와 성장기업 가치평가를 설명하기 좋은 소재입니다."
    if keyword in {"도시형생활주택", "건축규제"}:
        return f"{title_text} 변화가 있어 주택 공급 규제 완화와 부동산 정책을 설명하기 좋은 소재입니다."
    if any(term in keyword for term in ("코인", "가상자산", "법인계좌")):
        return f"{title_text} 상황을 바탕으로 가상자산 제도와 자금 이동을 설명하기 좋은 소재입니다."
    if keyword in {"세수", "명목성장률"}:
        return f"{title_text} 전망을 바탕으로 성장률과 세수 흐름의 연결을 설명하기 좋은 소재입니다."
    if keyword in {"핵잠", "방산", "자주국방"}:
        return f"{title_text} 계획을 바탕으로 방산 투자와 자주국방 산업 흐름을 설명하기 좋은 소재입니다."
    if keyword == "육아휴직":
        return f"{title_text} 변화를 바탕으로 공공부문 근로제도와 육아 지원 정책을 설명하기 좋은 소재입니다."

    return f"{title_text} 내용을 바탕으로 {keyword}의 경제적 맥락을 설명하기 좋은 소재입니다."


def clean_title_for_reason(title):
    """설명: 근거 문장에 넣기 좋도록 기사 제목의 괄호 태그와 불필요한 공백을 정리합니다.
    입력: title은 원본 기사 제목 문자열입니다.
    출력: 근거 문장용으로 정리된 제목 문자열을 반환합니다.
    """
    text = normalize_title(title)
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .")


def parse_json_candidates(output_text):
    """설명: LLM 응답에서 JSON 배열 또는 후보 목록 필드를 찾아 파싱합니다.
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
    """설명: JSON 파싱에 실패한 응답을 줄 단위 후보 문자열 목록으로 해석합니다.
    입력: output_text는 LLM 응답 문자열입니다.
    출력: 비어 있지 않은 줄 후보 문자열 목록을 반환합니다.
    """
    candidates = []
    for line in output_text.splitlines():
        line = re.sub(r"^\s*(?:[-*]|\d+[\).:-])\s*", "", line)
        line = line.strip().strip("\"'` ")
        if line:
            candidates.append(line)
    return candidates


def normalize_candidate(candidate):
    """설명: 딕셔너리 또는 문자열 후보를 표준 후보 딕셔너리 형태로 정규화합니다.
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
            match = re.match(r"(.+?)\s+(?:-|—)\s+(https?://\S+)\s+(?:-|—)\s+(.+)", text)
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
    return {
        "keyword": keyword.strip().strip("\"'` "),
        "source_url": source_url.strip().strip("\"'` "),
        "reason": reason.strip().strip("\"'` "),
    }
