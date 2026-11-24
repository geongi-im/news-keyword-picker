import re
from datetime import datetime
from pathlib import Path

from db_connector import execute_write, fetch_one


PROMPT_DIR_NAME = "prompt"
MAX_CONTENT_CHARS = 500
NEWS_URL_PATTERN = re.compile(r"https?://\S+")
CODE_FENCE_PATTERN = re.compile(r"^```[^\n]*\n(.*?)\n?```$", re.DOTALL)

SCRIPT_PROMPT_FILES = {
    "explain_child": "SCRIPT_EXPLAN_CHILD.md",
    "3s_quiz": "SCRIPT_3S_QUIZ.md",
    "news_explain_child": "SCRIPT_NEWS_EXPLAIN_CHILD.md",
    "news_3s_quiz": "SCRIPT_NEWS_3S_QUIZ.md",
}

NEWS_CONTEXT_TEMPLATES = frozenset({"news_explain_child", "news_3s_quiz"})

# 정답/해설을 본문에서 떼어 comment(Threads 댓글)로 옮기는 퀴즈 템플릿입니다.
ANSWER_IN_COMMENT_TEMPLATES = frozenset({"3s_quiz", "news_3s_quiz"})

TEMPLATE_CATEGORIES = {
    "3s_quiz": "3초퀴즈",
    "explain_child": "자녀에게설명하기",
    "news_3s_quiz": "3초퀴즈",
    "news_explain_child": "자녀에게설명하기",
}

PUBLISH_CONTENT_SELECT_COLUMNS = """
    idx,
    category,
    keyword,
    content,
    comment,
    target_date
"""

PUBLISH_CONTENT_BY_DATE_QUERY = f"""
SELECT
{PUBLISH_CONTENT_SELECT_COLUMNS}
FROM n8n_publish_content
WHERE target_date = %(target_date)s
  AND category = %(category)s
  AND keyword IS NOT NULL
  AND TRIM(keyword) <> ''
ORDER BY idx DESC
LIMIT 1
"""

PUBLISH_CONTENT_BY_KEYWORD_DATE_QUERY = f"""
SELECT
{PUBLISH_CONTENT_SELECT_COLUMNS}
FROM n8n_publish_content
WHERE target_date = %(target_date)s
  AND keyword = %(keyword)s
  AND category = %(category)s
ORDER BY idx DESC
LIMIT 1
"""

PUBLISH_CONTENT_BY_IDX_QUERY = f"""
SELECT
{PUBLISH_CONTENT_SELECT_COLUMNS}
FROM n8n_publish_content
WHERE idx = %(idx)s
LIMIT 1
"""

INSERT_PUBLISH_CONTENT_QUERY = """
INSERT INTO n8n_publish_content (category, keyword, target_date)
VALUES (%(category)s, %(keyword)s, %(target_date)s)
"""

UPDATE_CONTENT_BY_IDX_QUERY = """
UPDATE n8n_publish_content
SET content = %(content)s
WHERE idx = %(idx)s
"""

UPDATE_CONTENT_AND_COMMENT_BY_IDX_QUERY = """
UPDATE n8n_publish_content
SET content = %(content)s,
    `comment` = %(comment)s
WHERE idx = %(idx)s
"""

NEWS_QUIZ_BY_SOURCE_URL_QUERY = """
SELECT
    mq_source_url,
    mq_title,
    mq_keyword,
    mq_keyword_description,
    mq_selection_reason
FROM mq_news_quiz
WHERE mq_source_url = %(source_url)s
LIMIT 1
"""


def template_keys():
    """설명: 사용할 수 있는 스크립트 템플릿 키 목록을 반환합니다.
    입력: 별도 입력이 없습니다.
    출력: 정렬된 템플릿 키 문자열 리스트를 반환합니다.
    """
    return sorted(SCRIPT_PROMPT_FILES)


def uses_news_context(template_key):
    """설명: 템플릿이 뉴스 컨텍스트를 함께 사용하는지 확인합니다.
    입력: template_key는 템플릿 키 문자열입니다.
    출력: 뉴스 기반 템플릿이면 True를 반환합니다.
    """
    return template_key in NEWS_CONTEXT_TEMPLATES


def resolve_script_prompt_path(root_dir, template_key):
    """설명: 템플릿 키에 대응하는 스크립트 프롬프트 파일 경로를 반환합니다.
    입력: root_dir은 프로젝트 루트 Path, template_key는 템플릿 키입니다.
    출력: prompt/SCRIPT_*.md 파일의 절대 Path를 반환하고, 알 수 없는 키면 ValueError를 발생시킵니다.
    """
    try:
        file_name = SCRIPT_PROMPT_FILES[template_key]
    except KeyError:
        allowed = ", ".join(template_keys())
        raise ValueError(f"Unknown template {template_key!r}. Use one of: {allowed}") from None
    return Path(root_dir) / PROMPT_DIR_NAME / file_name


def load_prompt(path):
    """설명: 프롬프트 파일을 UTF-8 텍스트로 읽습니다.
    입력: path는 읽을 프롬프트 파일 Path입니다.
    출력: 프롬프트 본문 문자열을 반환하고, 파일이 없으면 FileNotFoundError를 발생시킵니다.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def resolve_template_category(template_key):
    """설명: 템플릿 키를 n8n_publish_content.category 값으로 변환합니다.
    입력: template_key는 CLI로 받은 템플릿 키입니다.
    출력: 저장용 한글 카테고리명을 반환하고, 알 수 없는 키면 ValueError를 발생시킵니다.
    """
    try:
        return TEMPLATE_CATEGORIES[template_key]
    except KeyError:
        allowed = ", ".join(sorted(TEMPLATE_CATEGORIES))
        raise ValueError(f"Unknown template category {template_key!r}. Use one of: {allowed}") from None


def resolve_target_date(date_text):
    """설명: 날짜 문자열을 DB 조회용 YYYY-MM-DD 형식으로 변환합니다.
    입력: date_text는 YYYY-MM-DD 문자열이며, 비어 있으면 오늘 날짜를 사용합니다.
    출력: YYYY-MM-DD 문자열을 반환하고, 형식이 틀리면 ValueError를 발생시킵니다.
    """
    if not date_text:
        return datetime.now().date().isoformat()

    try:
        return datetime.strptime(date_text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("--date must use YYYY-MM-DD format.") from exc


def build_script_prompt(prompt_body, keyword):
    """설명: 키워드 기반 스크립트 생성용 최종 프롬프트를 만듭니다.
    입력: prompt_body는 스크립트 작성 규칙 본문, keyword는 스크립트 대상 키워드입니다.
    출력: LLM에 전달할 프롬프트 문자열을 반환합니다.
    """
    return f"""Use SCRIPT_TEMPLATE as the only writing rules document.

<SCRIPT_TEMPLATE>
{prompt_body}
</SCRIPT_TEMPLATE>

<KEYWORD>
{keyword}
</KEYWORD>

Generate the final script from KEYWORD by following SCRIPT_TEMPLATE.
The entire generated script must be fewer than 500 characters total, including spaces and line breaks.
This 500-character limit is mandatory even if SCRIPT_TEMPLATE describes per-post limits.
Respond with only the script text. Do not add explanations, labels, or code blocks.
"""


def build_news_script_prompt(prompt_body, keyword, news_context):
    """설명: 뉴스 컨텍스트를 포함한 스크립트 생성용 최종 프롬프트를 만듭니다.
    입력: prompt_body는 뉴스 기반 작성 규칙 본문, keyword는 대상 키워드, news_context는 기사 제목/설명/선별 이유 문자열입니다.
    출력: LLM에 전달할 프롬프트 문자열을 반환합니다.
    """
    return f"""Use SCRIPT_TEMPLATE as the only writing rules document.

<SCRIPT_TEMPLATE>
{prompt_body}
</SCRIPT_TEMPLATE>

<KEYWORD>
{keyword}
</KEYWORD>

<NEWS_CONTEXT>
{news_context}
</NEWS_CONTEXT>

Generate the final script from KEYWORD and NEWS_CONTEXT by following SCRIPT_TEMPLATE.
Do not browse the web or infer article facts that are not present in NEWS_CONTEXT.
The entire generated script must be fewer than 500 characters total, including spaces and line breaks.
This 500-character limit is mandatory even if SCRIPT_TEMPLATE describes per-post limits.
Respond with only the script text. Do not add explanations, labels, or code blocks.
"""


def clean_script_text(response_text):
    """설명: LLM 응답에서 앞뒤 공백과 코드블록 감싸기를 제거합니다.
    입력: response_text는 LLM이 반환한 응답 문자열입니다.
    출력: 정리된 스크립트 문자열을 반환하고, 비어 있으면 ValueError를 발생시킵니다.
    """
    text = (response_text or "").strip()
    fence_match = CODE_FENCE_PATTERN.match(text)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text:
        raise ValueError("LLM returned an empty script.")
    return text


def validate_content_length(content):
    """설명: DB content에 저장할 스크립트 길이를 검증합니다.
    입력: content는 생성된 스크립트 문자열입니다.
    출력: 500자 이상이면 ValueError를 발생시키고, 아니면 None을 반환합니다.
    """
    length = len(content)
    if length >= MAX_CONTENT_CHARS:
        raise ValueError(f"Generated content must be fewer than 500 characters. actual={length}")


TITLE_ICON_BY_KIND = {"explain": chr(0x1F30F), "news_explain": chr(0x1F4F0)}
PARENT_ICON = chr(0x1F469)
CHILD_ICON = chr(0x1F466)
MAX_DIALOGUE_LINES = 10
MAX_QUESTION_CHARS = 80
FORBIDDEN_SPEECH = ("란다", "군요", "합니다", "입니다", "답니다", "맞어")
CTA_PHRASES = (
    "헷갈렸다면 저장",
    "뉴스 볼 때 이 단어 나오면 체크해보세요",
    "맞췄어요?",
    "이건 알아두면 쓸모있어요",
)
OPTION_LINE_PATTERN = re.compile(r"^([A-D])\)\s*(.+)$", re.MULTILINE)
ANSWER_LINE_PATTERN = re.compile(r"^정답:\s*([A-D])\)\s*(.+)$", re.MULTILINE)
QUESTION_LINE_PATTERN = re.compile(r"^Q\)\s*(.+)$", re.MULTILINE)


def find_script_problems(template_key, script_text, keyword):
    """설명: 생성된 스크립트가 템플릿 형식과 말투 규칙을 지키는지 검사합니다.
    입력: template_key는 템플릿 키, script_text는 생성된 스크립트, keyword는 스크립트 중심 키워드입니다.
    출력: 발견된 문제 설명 문자열 리스트를 반환합니다. 문제가 없으면 빈 리스트입니다.
    """
    problems = []
    length = len(script_text)
    if length >= MAX_CONTENT_CHARS:
        problems.append(f"Script must be fewer than {MAX_CONTENT_CHARS} characters. actual={length}")

    for word in FORBIDDEN_SPEECH:
        if word in script_text:
            problems.append(f"Forbidden speech style found: {word!r}")

    if template_key in ("explain_child", "news_explain_child"):
        problems.extend(find_explain_problems(template_key, script_text, keyword))
    else:
        problems.extend(find_quiz_problems(script_text, keyword))
    return problems


def find_explain_problems(template_key, script_text, keyword):
    """설명: 대화형 스크립트의 제목 줄, 대화 줄 수, 첫 대사를 검사합니다.
    입력: template_key는 explain_child 또는 news_explain_child, script_text는 스크립트, keyword는 중심 키워드입니다.
    출력: 문제 설명 문자열 리스트를 반환합니다.
    """
    problems = []
    lines = [line.strip() for line in script_text.splitlines() if line.strip()]
    if template_key == "news_explain_child":
        expected_prefix = f"{TITLE_ICON_BY_KIND['news_explain']} [{keyword}] 뉴스로"
    else:
        expected_prefix = f"{TITLE_ICON_BY_KIND['explain']} [{keyword}]"
    if not lines or not lines[0].startswith(expected_prefix):
        problems.append(f"First line must start with {expected_prefix!r}")

    dialogue = [line for line in lines[1:] if line.startswith((PARENT_ICON, CHILD_ICON))]
    if len(dialogue) > MAX_DIALOGUE_LINES:
        problems.append(f"Dialogue must be {MAX_DIALOGUE_LINES} lines or fewer. actual={len(dialogue)}")
    if dialogue and not dialogue[0].startswith(CHILD_ICON):
        problems.append("The first dialogue line must be the child asking a question.")
    return problems


def find_quiz_problems(script_text, keyword):
    """설명: 4지선다 퀴즈의 질문 길이, 보기 구성, 정답, CTA 개수를 검사합니다.
    입력: script_text는 퀴즈 스크립트, keyword는 정답이어야 하는 키워드입니다.
    출력: 문제 설명 문자열 리스트를 반환합니다.
    """
    problems = []
    question = QUESTION_LINE_PATTERN.search(script_text)
    if not question:
        problems.append("Question line starting with 'Q)' is missing.")
    elif len(question.group(1).strip()) > MAX_QUESTION_CHARS:
        problems.append(
            f"Question must be {MAX_QUESTION_CHARS} characters or fewer. actual={len(question.group(1).strip())}"
        )

    options = {letter: text.strip() for letter, text in OPTION_LINE_PATTERN.findall(script_text)}
    if sorted(options) != ["A", "B", "C", "D"]:
        problems.append("Exactly four options A) B) C) D) are required.")
    else:
        names = list(options.values())
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                if first in second or second in first:
                    problems.append(f"Options overlap: {first!r} and {second!r}")
                elif first.split()[0] == second.split()[0] and len(first.split()) > 1:
                    problems.append(f"Options share the same leading word: {first!r} and {second!r}")

    answer = ANSWER_LINE_PATTERN.search(script_text)
    if not answer:
        problems.append("Answer line starting with '정답:' is missing.")
    else:
        if options.get(answer.group(1)) != answer.group(2).strip():
            problems.append("The answer line does not match the option text.")
        if answer.group(2).strip() != keyword:
            problems.append(f"The correct answer must be the keyword {keyword!r}.")

    cta_count = sum(script_text.count(phrase) for phrase in CTA_PHRASES)
    if cta_count != 1:
        problems.append(f"Exactly one CTA line is required. found={cta_count}")
    return problems


def has_field_value(value):
    """설명: DB 필드에 실제 값이 있는지 확인합니다.
    입력: value는 DB에서 조회한 필드 값입니다.
    출력: None이 아니고 공백 제거 후 비어 있지 않으면 True를 반환합니다.
    """
    return value is not None and bool(str(value).strip())


def extract_news_source_url(comment):
    """설명: n8n_publish_content.comment 값에서 뉴스 URL을 추출합니다.
    입력: comment는 URL이 들어 있는 comment 필드 값입니다.
    출력: mq_news_quiz 조회에 쓸 URL 문자열을 반환하고, URL이 없으면 ValueError를 발생시킵니다.
    """
    text = str(comment or "").strip().replace("\\/", "/")
    match = NEWS_URL_PATTERN.search(text)
    if match:
        return match.group(0).rstrip(".,;:)]}'\"<>")

    if text.startswith("www."):
        trimmed = text.rstrip(".,;:)]}'\"<>")
        return f"https://{trimmed}"

    raise ValueError("No news URL found in n8n_publish_content.comment.")


def fetch_news_quiz_by_source_url(db_config, source_url):
    """설명: 뉴스 URL과 일치하는 mq_news_quiz row를 조회합니다.
    입력: db_config는 MySQL 접속 설정, source_url은 뉴스 URL입니다.
    출력: 기사 제목, 뉴스 키워드, 설명, 선별 이유를 담은 row 딕셔너리를 반환하고, 없으면 ValueError를 발생시킵니다.
    """
    row = fetch_one(db_config, NEWS_QUIZ_BY_SOURCE_URL_QUERY, {"source_url": source_url})
    if not row:
        raise ValueError(f"No mq_news_quiz row returned for mq_source_url={source_url}.")
    return row


def build_news_context_text(news_quiz_row):
    """설명: mq_news_quiz row를 프롬프트에 넣을 뉴스 컨텍스트 문자열로 변환합니다.
    입력: news_quiz_row는 mq_news_quiz에서 조회한 row 딕셔너리입니다.
    출력: 라벨별로 줄바꿈된 컨텍스트 문자열을 반환합니다.
    """
    fields = (
        ("뉴스 URL", news_quiz_row.get("mq_source_url")),
        ("뉴스기사제목", news_quiz_row.get("mq_title")),
        ("뉴스 추출 키워드", news_quiz_row.get("mq_keyword")),
        ("키워드 한줄 설명", news_quiz_row.get("mq_keyword_description")),
        ("뉴스 선별 이유", news_quiz_row.get("mq_selection_reason")),
    )
    return "\n".join(f"{label}: {str(value or '').strip()}" for label, value in fields)


def resolve_news_script_keyword(keyword, news_quiz_row):
    """설명: 뉴스 기반 스크립트의 중심 키워드를 결정합니다.
    입력: keyword는 n8n_publish_content의 키워드, news_quiz_row는 mq_news_quiz row입니다.
    출력: 뉴스 추출 키워드가 있으면 그 값을, 없으면 keyword를 반환합니다.
    """
    news_keyword = news_quiz_row.get("mq_keyword")
    if news_keyword and str(news_keyword).strip():
        return str(news_keyword).strip()
    return keyword


def normalize_publish_content_row(row):
    """설명: 조회된 n8n_publish_content row의 키워드를 검증하고 정리합니다.
    입력: row는 DB에서 조회한 row 딕셔너리입니다.
    출력: keyword가 정리된 row를 반환하고, 키워드가 비어 있으면 ValueError를 발생시킵니다.
    """
    keyword = row.get("keyword")
    if keyword is None or not str(keyword).strip():
        raise ValueError("Keyword text from MySQL is empty.")

    row["keyword"] = str(keyword).strip()
    return row


def fetch_publish_content_by_date(db_config, target_date, category):
    """설명: 지정 날짜와 카테고리의 최신 n8n_publish_content row를 조회합니다.
    입력: db_config는 MySQL 접속 설정, target_date는 YYYY-MM-DD, category는 카테고리 문자열입니다.
    출력: row 딕셔너리를 반환하고, 없으면 ValueError를 발생시킵니다.
    """
    row = fetch_one(
        db_config,
        PUBLISH_CONTENT_BY_DATE_QUERY,
        {"category": category, "target_date": target_date},
    )
    if not row:
        raise ValueError(
            "No n8n_publish_content row returned for "
            f"target_date={target_date}, category={category}."
        )
    return normalize_publish_content_row(row)


def fetch_publish_content_by_keyword_date(db_config, keyword, target_date, category):
    """설명: 지정 키워드/날짜/카테고리의 최신 n8n_publish_content row를 조회합니다.
    입력: db_config는 MySQL 접속 설정, keyword, target_date, category는 조회 조건입니다.
    출력: row 딕셔너리를 반환하고, 없으면 None을 반환합니다.
    """
    row = fetch_one(
        db_config,
        PUBLISH_CONTENT_BY_KEYWORD_DATE_QUERY,
        {"category": category, "keyword": keyword, "target_date": target_date},
    )
    if not row:
        return None
    return normalize_publish_content_row(row)


def fetch_publish_content_by_idx(db_config, idx):
    """설명: idx가 일치하는 n8n_publish_content row를 조회합니다.
    입력: db_config는 MySQL 접속 설정, idx는 기본키입니다.
    출력: row 딕셔너리를 반환하고, 없으면 ValueError를 발생시킵니다.
    """
    row = fetch_one(db_config, PUBLISH_CONTENT_BY_IDX_QUERY, {"idx": idx})
    if not row:
        raise ValueError(f"No n8n_publish_content row returned for idx={idx}.")
    return normalize_publish_content_row(row)


def insert_publish_content_keyword_date(db_config, keyword, target_date, category):
    """설명: 키워드와 날짜만 가진 신규 n8n_publish_content row를 만듭니다.
    입력: db_config는 MySQL 접속 설정, keyword, target_date, category는 저장 값입니다.
    출력: 생성된 row 딕셔너리를 반환합니다.
    """
    result = execute_write(
        db_config,
        INSERT_PUBLISH_CONTENT_QUERY,
        {"category": category, "keyword": keyword, "target_date": target_date},
    )
    return fetch_publish_content_by_idx(db_config, result["lastrowid"])


def update_content_by_idx(db_config, idx, content):
    """설명: idx가 일치하는 행의 content 필드를 갱신합니다.
    입력: db_config는 MySQL 접속 설정, idx는 대상 기본키, content는 저장할 스크립트입니다.
    출력: 갱신된 행 수를 반환합니다.
    """
    result = execute_write(
        db_config,
        UPDATE_CONTENT_BY_IDX_QUERY,
        {"idx": idx, "content": content},
    )
    return result["affected_rows"]


def update_content_and_comment_by_idx(db_config, idx, content, comment):
    """설명: idx가 일치하는 행의 content와 comment 필드를 한 번에 갱신합니다.
    입력: db_config는 MySQL 접속 설정, idx는 대상 기본키, content는 본문, comment는 댓글 텍스트입니다.
    출력: 갱신된 행 수를 반환합니다.
    """
    result = execute_write(
        db_config,
        UPDATE_CONTENT_AND_COMMENT_BY_IDX_QUERY,
        {"idx": idx, "content": content, "comment": comment},
    )
    return result["affected_rows"]


def moves_answer_to_comment(template_key):
    """설명: 템플릿이 정답/해설을 comment로 옮기는 퀴즈 템플릿인지 확인합니다.
    입력: template_key는 템플릿 키 문자열입니다.
    출력: 3초퀴즈 계열 템플릿이면 True를 반환합니다.
    """
    return template_key in ANSWER_IN_COMMENT_TEMPLATES


def split_quiz_answer(script_text):
    """설명: 퀴즈 스크립트를 본문과 정답 영역으로 나눕니다.
    정답 영역은 '정답:' 줄부터 CTA 줄 직전까지(정답 + 해설)이고, CTA 줄부터는 본문에 남깁니다.
    입력: script_text는 검증을 통과한 퀴즈 스크립트입니다.
    출력: (본문, 정답 영역) 튜플을 반환하고, 정답 줄이 없으면 ValueError를 발생시킵니다.
    """
    lines = script_text.splitlines()
    answer_index = next(
        (index for index, line in enumerate(lines) if ANSWER_LINE_PATTERN.match(line.strip())),
        None,
    )
    if answer_index is None:
        raise ValueError("Answer line starting with '정답:' is missing.")

    cta_index = next(
        (
            index
            for index in range(answer_index + 1, len(lines))
            if any(phrase in lines[index] for phrase in CTA_PHRASES)
        ),
        len(lines),
    )
    head = "\n".join(lines[:answer_index]).strip()
    answer = "\n".join(lines[answer_index:cta_index]).strip()
    tail = "\n".join(lines[cta_index:]).strip()
    content = "\n\n".join(part for part in (head, tail) if part)
    return content, answer


def strip_previous_quiz_answer(comment):
    """설명: 이전 생성에서 comment 앞에 붙인 정답/해설 블록을 떼어내고 원래 comment만 남깁니다.
    comment의 첫 줄이 '정답:' 줄이면 이전 생성 결과로 보고, 그 뒤 처음 나오는 URL 줄부터를 원래 값으로 봅니다.
    URL 줄이 없으면(뉴스 URL 없이 만든 행) 전체를 이전 정답 블록으로 보고 빈 문자열을 반환합니다.
    입력: comment는 행의 기존 comment 값입니다.
    출력: 정답 블록을 뺀 원래 comment 문자열을 반환합니다.
    """
    lines = str(comment or "").strip().splitlines()
    if not lines or not ANSWER_LINE_PATTERN.match(lines[0].strip()):
        return "\n".join(lines).strip()

    source_index = next(
        (
            index
            for index in range(1, len(lines))
            if NEWS_URL_PATTERN.search(lines[index]) or lines[index].strip().startswith("www.")
        ),
        len(lines),
    )
    return "\n".join(lines[source_index:]).strip()


def build_quiz_comment(answer_text, existing_comment):
    """설명: 정답 영역 뒤에 원래 comment(뉴스 URL 등)를 붙여 댓글 텍스트를 만듭니다.
    재생성이라 기존 comment에 이전 정답 블록이 남아 있으면 떼어내고 새 정답으로 바꿉니다.
    입력: answer_text는 정답/해설 텍스트, existing_comment는 행의 기존 comment 값입니다.
    출력: 정답, 해설, 원래 comment 순서의 댓글 문자열을 반환합니다.
    """
    source = strip_previous_quiz_answer(existing_comment)
    if not source:
        return answer_text
    return f"{answer_text}\n\n{source}"


def build_publish_texts(template_key, script_text, existing_comment):
    """설명: 생성된 스크립트를 DB에 저장할 content와 comment로 변환합니다.
    입력: template_key는 템플릿 키, script_text는 검증된 스크립트, existing_comment는 행의 기존 comment입니다.
    출력: (content, comment) 튜플을 반환합니다. comment를 바꾸지 않는 템플릿이면 comment는 None입니다.
    """
    if not moves_answer_to_comment(template_key):
        return script_text, None
    content, answer_text = split_quiz_answer(script_text)
    return content, build_quiz_comment(answer_text, existing_comment)


def print_target_row_status(target_row):
    """설명: 대상 DB row의 작업 상태를 콘솔에 출력합니다.
    입력: target_row는 n8n_publish_content row 딕셔너리입니다.
    출력: idx, keyword, content 설정 여부를 stdout에 출력합니다.
    """
    print(
        "db_row: "
        f"idx={target_row.get('idx')} "
        f"keyword={target_row.get('keyword')} "
        f"content_set={has_field_value(target_row.get('content'))}"
    )


def load_target_content_row(db_config, template_key, date_text):
    """설명: 키워드가 직접 주어지지 않았을 때 작업 대상 row를 조회합니다.
    입력: db_config는 MySQL 접속 설정, template_key는 템플릿 키, date_text는 날짜 문자열입니다.
    출력: 지정 날짜와 템플릿 카테고리의 최신 row 딕셔너리를 반환합니다.
    """
    target_date = resolve_target_date(date_text)
    category = resolve_template_category(template_key)
    return fetch_publish_content_by_date(db_config, target_date, category)


def load_or_insert_direct_keyword_row(db_config, template_key, date_text, keyword):
    """설명: 직접 입력한 키워드와 날짜의 row를 조회하고, 없으면 새로 만듭니다.
    입력: db_config는 MySQL 접속 설정, template_key, date_text, keyword는 CLI 입력입니다.
    출력: 조회 또는 생성된 row 딕셔너리를 반환합니다.
    """
    target_date = resolve_target_date(date_text)
    category = resolve_template_category(template_key)
    target_row = fetch_publish_content_by_keyword_date(db_config, keyword, target_date, category)
    if target_row:
        print_target_row_status(target_row)
        return target_row

    target_row = insert_publish_content_keyword_date(db_config, keyword, target_date, category)
    print(
        "db_row_inserted: "
        f"idx={target_row.get('idx')} "
        f"category={target_row.get('category')} "
        f"keyword={target_row.get('keyword')} "
        f"target_date={target_row.get('target_date')}"
    )
    return target_row


def build_retry_prompt(prompt, previous_script, problems):
    """설명: 검증에 실패한 이전 결과와 문제점을 덧붙인 재생성 프롬프트를 만듭니다.
    입력: prompt는 원래 프롬프트, previous_script는 실패한 스크립트, problems는 문제 설명 리스트입니다.
    출력: 재생성용 프롬프트 문자열을 반환합니다.
    """
    problem_lines = "\n".join(f"- {problem}" for problem in problems)
    return f"""{prompt}
<PREVIOUS_ATTEMPT>
{previous_script}
</PREVIOUS_ATTEMPT>

<PROBLEMS>
{problem_lines}
</PROBLEMS>

PREVIOUS_ATTEMPT broke the rules listed in PROBLEMS.
Write the script again from scratch and fix every problem. Follow all original rules.
Respond with only the script text.
"""


def generate_script_text(llm_client, prompt, model=None, check=None, max_attempts=2):
    """설명: LLM으로 스크립트를 생성하고 정리와 검증을 마칩니다. 검증에 실패하면 문제점을 알려 재생성합니다.
    입력: llm_client는 generate_text를 가진 LLM 클라이언트, prompt는 최종 프롬프트, model은 모델명 override, check는 스크립트를 받아 문제 리스트를 반환하는 함수, max_attempts는 최대 생성 횟수입니다.
    출력: 검증된 스크립트 문자열을 반환하고, 끝내 통과하지 못하면 ValueError를 발생시킵니다.
    """
    current_prompt = prompt
    problems = []
    for _attempt in range(max_attempts):
        script_text = clean_script_text(llm_client.generate_text(prompt=current_prompt, model=model))
        problems = [] if check is None else check(script_text)
        if not problems:
            validate_content_length(script_text)
            return script_text
        current_prompt = build_retry_prompt(prompt, script_text, problems)

    raise ValueError("Generated script failed validation: " + "; ".join(problems))


def generate_script_for_row(db_config, llm_client, root_dir, template_key, target_row, model=None):
    """설명: 대상 row의 키워드로 템플릿에 맞는 스크립트를 생성합니다. DB에는 쓰지 않습니다.
    입력: db_config는 MySQL 접속 설정, llm_client는 LLM 클라이언트, root_dir은 프로젝트 루트, template_key는 템플릿 키, target_row는 대상 row, model은 모델명 override입니다.
    출력: 검증된 스크립트 문자열을 반환합니다.
    """
    prompt_body = load_prompt(resolve_script_prompt_path(root_dir, template_key))
    keyword = target_row["keyword"]

    if uses_news_context(template_key):
        news_url = extract_news_source_url(target_row.get("comment"))
        news_quiz_row = fetch_news_quiz_by_source_url(db_config, news_url)
        keyword = resolve_news_script_keyword(keyword, news_quiz_row)
        prompt = build_news_script_prompt(
            prompt_body,
            keyword,
            build_news_context_text(news_quiz_row),
        )
    else:
        prompt = build_script_prompt(prompt_body, keyword)

    return generate_script_text(
        llm_client,
        prompt,
        model=model,
        check=lambda script_text: find_script_problems(template_key, script_text, keyword),
    )


def update_generated_script_content(db_config, script_text, target_row, comment=None):
    """설명: 생성된 스크립트를 검증한 뒤 DB content 필드에 저장합니다. comment가 있으면 함께 저장합니다.
    입력: db_config는 MySQL 접속 설정, script_text는 본문, target_row는 idx를 가진 대상 row, comment는 함께 저장할 댓글 텍스트입니다.
    출력: 갱신된 행 수를 반환합니다.
    """
    text = (script_text or "").strip()
    if not text:
        raise ValueError("Script text is empty.")
    validate_content_length(text)
    if comment is None:
        return update_content_by_idx(db_config, target_row["idx"], text)
    return update_content_and_comment_by_idx(db_config, target_row["idx"], text, comment)


def run_script_generation(
    db_config,
    llm_client,
    root_dir,
    template_key,
    date_text=None,
    keyword=None,
    model=None,
):
    """설명: 키워드 행을 찾아 스크립트를 만들고 DB content에 저장하는 전체 흐름입니다.
    입력: db_config는 MySQL 접속 설정, llm_client는 LLM 클라이언트, root_dir은 프로젝트 루트, template_key는 템플릿 키, date_text/keyword는 대상 선택 조건, model은 모델명 override입니다.
    출력: idx, script(content에 저장한 본문), comment(새로 저장한 댓글 또는 None), db_content_updated(갱신 행 수 또는 None)를 담은 딕셔너리를 반환합니다.
    content가 이미 있으면 생성하지 않고 기존 값을 반환합니다.
    """
    keyword = (keyword or "").strip() or None
    if keyword:
        target_row = load_or_insert_direct_keyword_row(db_config, template_key, date_text, keyword)
    else:
        target_row = load_target_content_row(db_config, template_key, date_text)
        print_target_row_status(target_row)

    if has_field_value(target_row.get("content")):
        return {
            "idx": target_row["idx"],
            "script": str(target_row["content"]).strip(),
            "comment": None,
            "db_content_updated": None,
        }

    script_text = generate_script_for_row(
        db_config, llm_client, root_dir, template_key, target_row, model=model
    )
    content_text, comment_text = build_publish_texts(
        template_key, script_text, target_row.get("comment")
    )
    updated_rows = update_generated_script_content(
        db_config, content_text, target_row, comment=comment_text
    )
    return {
        "idx": target_row["idx"],
        "script": content_text,
        "comment": comment_text,
        "db_content_updated": updated_rows,
    }
