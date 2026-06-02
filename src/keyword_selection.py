import json
import re
from dataclasses import dataclass
from datetime import date

from db_connector import connect_mysql, fetch_one, mysql_connect_kwargs
from news_keyword import normalize_candidate


NEWS_KEYWORD_TABLE = "n8n_publish_content"
NEWS_KEYWORD_DEDUPE_COLUMN = "keyword"
NEWS_KEYWORD_INSERT_CATEGORIES = ("3초퀴즈", "자녀에게설명하기")
NEWS_KEYWORD_INSERT_QUERY = (
    "INSERT INTO n8n_publish_content(category, keyword, target_date) "
    "VALUES(%(category)s, %(keyword)s, %(target_date)s)"
)
NEWS_KEYWORD_SELECTION_REASONING_EFFORT = "medium"
MYSQL_IDENTIFIER_PART_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

VIRAL_SELECTION_RESPONSE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string"},
        "source_url": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["keyword", "source_url", "reason"],
}


@dataclass(frozen=True)
class NewsKeywordStorageConfig:
    """설명: 뉴스 키워드 중복 검사와 insert에 필요한 저장소 설정입니다.
    입력: dedupe_table은 중복 검사 테이블, dedupe_column은 키워드 컬럼, target_date는 저장 대상 날짜입니다.
    출력: 데이터베이스 처리 단계에서 사용하는 불변 설정 객체입니다.
    """

    dedupe_table: str
    dedupe_column: str
    target_date: str


def resolve_news_keyword_storage_config():
    """설명: 뉴스 키워드 저장 처리에 사용할 기본 DB 설정을 구성합니다.
    입력: 별도 인자 없이 모듈 상수와 오늘 날짜를 사용합니다.
    출력: NewsKeywordStorageConfig 객체를 반환합니다.
    """
    return NewsKeywordStorageConfig(
        dedupe_table=NEWS_KEYWORD_TABLE,
        dedupe_column=NEWS_KEYWORD_DEDUPE_COLUMN,
        target_date=resolve_keyword_target_date(),
    )


def resolve_keyword_target_date():
    """설명: 최종 키워드를 저장할 target_date 값을 결정합니다.
    입력: 별도 인자 없이 현재 실행일을 사용합니다.
    출력: yyyy-mm-dd 형식의 날짜 문자열을 반환합니다.
    """
    return date.today().isoformat()


def quote_mysql_identifier(identifier, label):
    """설명: MySQL 테이블명 또는 컬럼명을 안전한 백틱 식별자로 변환합니다.
    입력: identifier는 검사할 식별자 문자열, label은 오류 메시지에 사용할 식별자 종류입니다.
    출력: 검증된 식별자를 백틱으로 감싼 SQL 조각으로 반환합니다.
    """
    text = (identifier or "").strip()
    parts = text.split(".")
    if not text or not parts:
        raise ValueError(f"Invalid MySQL {label}: {identifier!r}")
    if any(not MYSQL_IDENTIFIER_PART_RE.match(part) for part in parts):
        raise ValueError(
            f"Invalid MySQL {label}: {identifier!r}. "
            "Use letters, digits, underscores, and optional dot-qualified names."
        )
    return ".".join(f"`{part}`" for part in parts)


def build_keyword_exists_query(table, column):
    """설명: 키워드가 DB에 이미 존재하는지 확인하는 SELECT 쿼리를 만듭니다.
    입력: table은 조회 대상 테이블명, column은 키워드 비교 컬럼명입니다.
    출력: %(keyword)s 파라미터를 사용하는 MySQL SELECT 쿼리 문자열을 반환합니다.
    """
    table_sql = quote_mysql_identifier(table, "table")
    column_sql = quote_mysql_identifier(column, "column")
    return (
        f"SELECT 1 AS exists_flag "
        f"FROM {table_sql} "
        f"WHERE {column_sql} = %(keyword)s "
        f"LIMIT 1"
    )


def mark_news_keyword_duplicates(candidates, db_config, table, column):
    """설명: 후보 키워드마다 DB 중복 여부를 조회해 표시합니다.
    입력: candidates는 후보 목록, db_config는 MySQL 접속 설정, table/column은 중복 검사 위치입니다.
    출력: 각 후보에 exists_in_db 값을 추가한 새 후보 목록을 반환합니다.
    """
    query = build_keyword_exists_query(table, column)
    checked = []
    for candidate in candidates:
        row = fetch_one(db_config, query, {"keyword": candidate["keyword"]})
        checked_candidate = dict(candidate)
        checked_candidate["exists_in_db"] = row is not None
        checked.append(checked_candidate)
    return checked


def filter_non_duplicate_news_keyword_candidates(candidates):
    """설명: DB에 이미 존재하지 않는 뉴스 키워드 후보만 남깁니다.
    입력: candidates는 exists_in_db 값을 포함할 수 있는 후보 목록입니다.
    출력: exists_in_db가 참이 아닌 후보 목록을 반환합니다.
    """
    return [candidate for candidate in candidates if not candidate.get("exists_in_db")]


def build_viral_keyword_selection_prompt(candidates):
    """설명: 중복이 아닌 후보 중 최종 키워드 1개를 고르게 하는 LLM 프롬프트를 만듭니다.
    입력: candidates는 keyword, source_title, source_url, reason 값을 가진 후보 목록입니다.
    출력: LLM에 전달할 프롬프트 문자열을 반환합니다.
    """
    payload = [
        {
            "keyword": candidate.get("keyword", ""),
            "source_title": candidate.get("source_title", ""),
            "source_url": candidate.get("source_url", ""),
            "reason": candidate.get("reason", ""),
        }
        for candidate in candidates
    ]
    candidates_json = json.dumps(payload, ensure_ascii=True, indent=2)
    return f"""You select one final keyword from Korean economy news keyword candidates.

Choose exactly one candidate that is both economically important and likely to become viral.
Judge viral potential by timeliness, public impact, market sensitivity, consumer relevance, investor interest, and image-content suitability.

Rules:
- Select only from the input candidates.
- Copy keyword and source_url exactly from the selected input candidate.
- Return only one JSON object with "keyword", "source_url", and "reason" string fields.
- The reason must be one concrete Korean sentence.
- Use only facts visible in the input candidate fields. Do not invent numbers, companies, causes, or claims.

<CANDIDATES_JSON>
{candidates_json}
</CANDIDATES_JSON>
"""


def select_viral_news_keyword_candidate(candidates, output_dir, llm_client, model=None):
    """설명: 중복이 아닌 후보 중 바이럴 가능성이 높은 최종 키워드 1개를 선택합니다.
    입력: candidates는 후보 목록, output_dir은 LLM 임시 출력 위치, llm_client는 LLM 호출 구현체, model은 선택 모델명입니다.
    출력: selection_reason을 포함한 최종 후보 딕셔너리를 반환합니다.
    """
    if not candidates:
        raise RuntimeError("No non-duplicate news keyword candidates are available.")
    if len(candidates) == 1:
        selected = dict(candidates[0])
        selected["selection_reason"] = (
            selected.get("reason") or "Only one non-duplicate candidate remained."
        )
        return selected

    prompt = build_viral_keyword_selection_prompt(candidates)
    output = llm_client.generate_text(
        prompt=prompt,
        output_dir=output_dir,
        model=model or llm_client.default_model,
        reasoning_effort=NEWS_KEYWORD_SELECTION_REASONING_EFFORT,
        response_json_schema=VIRAL_SELECTION_RESPONSE_JSON_SCHEMA,
        response_mime_type="application/json",
    )
    selected = parse_selected_viral_keyword_candidate(output, candidates)
    if selected is None:
        raise RuntimeError(f"Failed to select a final news keyword. output={output!r}")
    return selected


def parse_selected_viral_keyword_candidate(output_text, candidates):
    """설명: LLM의 최종 키워드 선택 응답을 파싱하고 원본 후보와 매칭합니다.
    입력: output_text는 LLM 응답 문자열, candidates는 선택 가능한 원본 후보 목록입니다.
    출력: 매칭된 후보에 selection_reason을 추가해 반환하고, 실패하면 None을 반환합니다.
    """
    value = parse_json_value(output_text)
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        return None

    normalized = normalize_candidate(value)
    keyword = normalized["keyword"]
    source_url = normalized["source_url"]
    selected = find_candidate(candidates, keyword, source_url)
    if selected is None:
        return None

    result = dict(selected)
    result["selection_reason"] = normalized["reason"] or selected.get("reason", "")
    return result


def parse_json_value(output_text):
    """설명: LLM 응답 문자열에서 JSON 객체 또는 배열 값을 추출합니다.
    입력: output_text는 JSON만 있거나 앞뒤 로그가 섞인 응답 문자열입니다.
    출력: 파싱된 Python 값(dict/list 등)을 반환하고, 실패하면 None을 반환합니다.
    """
    text = (output_text or "").strip()
    json_texts = [text]
    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start != -1 and object_end != -1 and object_start < object_end:
        json_texts.append(text[object_start : object_end + 1])
    array_start = text.find("[")
    array_end = text.rfind("]")
    if array_start != -1 and array_end != -1 and array_start < array_end:
        json_texts.append(text[array_start : array_end + 1])

    for json_text in json_texts:
        try:
            return json.loads(json_text)
        except json.JSONDecodeError:
            continue
    return None


def find_candidate(candidates, keyword, source_url):
    """설명: 선택된 keyword와 source_url에 대응하는 원본 후보를 찾습니다.
    입력: candidates는 원본 후보 목록, keyword/source_url은 LLM이 선택한 값입니다.
    출력: 정확히 매칭되는 후보를 반환하고, URL 매칭이 불명확하면 키워드 단일 매칭 후보를 반환합니다.
    """
    for candidate in candidates:
        if candidate.get("keyword") == keyword and candidate.get("source_url") == source_url:
            return candidate

    keyword_matches = [
        candidate for candidate in candidates if candidate.get("keyword") == keyword
    ]
    if len(keyword_matches) == 1:
        return keyword_matches[0]
    return None


def build_selected_news_keyword_insert_params(selected_candidate, category, target_date):
    """설명: 최종 선택 키워드를 insert 쿼리에 전달할 파라미터로 변환합니다.
    입력: selected_candidate는 최종 후보, category는 저장 카테고리, target_date는 저장 대상 날짜입니다.
    출력: MySQL execute에 전달할 파라미터 딕셔너리를 반환합니다.
    """
    return {
        "category": category,
        "keyword": selected_candidate.get("keyword", ""),
        "target_date": target_date,
        "source_title": selected_candidate.get("source_title", ""),
        "source_url": selected_candidate.get("source_url", ""),
        "reason": selected_candidate.get("selection_reason")
        or selected_candidate.get("reason", ""),
        "candidate_reason": selected_candidate.get("reason", ""),
        "selection_reason": selected_candidate.get("selection_reason", ""),
    }


def insert_selected_news_keyword(db_config, selected_candidate, target_date):
    """설명: 최종 선택 키워드를 지정된 카테고리들로 MySQL에 저장합니다.
    입력: db_config는 MySQL 접속 설정, selected_candidate는 최종 후보, target_date는 저장 대상 날짜입니다.
    출력: 카테고리별 affected_rows와 lastrowid를 가진 insert 결과 목록을 반환합니다.
    """
    insert_results = []
    with connect_mysql(db_config) as connection:
        try:
            with connection.cursor() as cursor:
                for category in NEWS_KEYWORD_INSERT_CATEGORIES:
                    params = build_selected_news_keyword_insert_params(
                        selected_candidate,
                        category=category,
                        target_date=target_date,
                    )
                    affected_rows = cursor.execute(NEWS_KEYWORD_INSERT_QUERY, params)
                    insert_results.append(
                        {
                            "category": category,
                            "affected_rows": int(affected_rows),
                            "lastrowid": cursor.lastrowid,
                        }
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return insert_results


def run_news_keyword_selection_insert_process(
    candidates,
    args,
    root_dir,
    output_dir,
    llm_client,
):
    """설명: 후보 중복 검사, 최종 키워드 선택, MySQL insert까지 전체 후처리 흐름을 실행합니다.
    입력: candidates는 키워드 후보 목록, args는 CLI 옵션, root_dir은 프로젝트 루트, output_dir은 LLM 출력 위치, llm_client는 LLM 호출 구현체입니다.
    출력: 중복 검사 결과, 선택 가능 후보, 최종 후보, target_date, insert 결과를 담은 딕셔너리를 반환합니다.
    """
    storage_config = resolve_news_keyword_storage_config()
    db_config = mysql_connect_kwargs()
    checked_candidates = mark_news_keyword_duplicates(
        candidates,
        db_config=db_config,
        table=storage_config.dedupe_table,
        column=storage_config.dedupe_column,
    )
    eligible_candidates = filter_non_duplicate_news_keyword_candidates(checked_candidates)
    selected_candidate = select_viral_news_keyword_candidate(
        eligible_candidates,
        output_dir=output_dir,
        llm_client=llm_client,
        model=getattr(args, "news_keyword_model", None) or llm_client.default_model,
    )
    insert_result = insert_selected_news_keyword(
        db_config=db_config,
        selected_candidate=selected_candidate,
        target_date=storage_config.target_date,
    )
    return {
        "checked_candidates": checked_candidates,
        "eligible_candidates": eligible_candidates,
        "selected_candidate": selected_candidate,
        "target_date": storage_config.target_date,
        "insert_result": insert_result,
    }
