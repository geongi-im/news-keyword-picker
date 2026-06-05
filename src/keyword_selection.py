import json
import re
from dataclasses import dataclass
from datetime import date

from db_connector import connect_mysql, fetch_one, mysql_connect_kwargs
from news_keyword import has_complete_candidate_learning_content, normalize_candidate


NEWS_KEYWORD_TABLE = "n8n_publish_content"
NEWS_KEYWORD_DEDUPE_COLUMN = "keyword"
NEWS_KEYWORD_INSERT_CATEGORIES = ("3초퀴즈", "자녀에게설명하기")
NEWS_KEYWORD_INSERT_QUERY = (
    "INSERT INTO n8n_publish_content(category, keyword, target_date) "
    "VALUES(%(category)s, %(keyword)s, %(target_date)s)"
)
NEWS_QUIZ_INSERT_QUERY = (
    "INSERT INTO mq_news_quiz("
    "mq_news_date, mq_company, mq_title, mq_source_url, mq_keyword, "
    "mq_keyword_description, mq_quiz_content, mq_selection_reason"
    ") VALUES("
    "%(mq_news_date)s, %(mq_company)s, %(mq_title)s, %(mq_source_url)s, %(mq_keyword)s, "
    "%(mq_keyword_description)s, %(mq_quiz_content)s, %(mq_selection_reason)s"
    ")"
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
    """?ㅻ챸: ?댁뒪 ?ㅼ썙??以묐났 寃?ъ? insert???꾩슂????μ냼 ?ㅼ젙?낅땲??
    ?낅젰: dedupe_table? 以묐났 寃???뚯씠釉? dedupe_column? ?ㅼ썙??而щ읆, target_date?????????좎쭨?낅땲??
    異쒕젰: ?곗씠?곕쿋?댁뒪 泥섎━ ?④퀎?먯꽌 ?ъ슜?섎뒗 遺덈? ?ㅼ젙 媛앹껜?낅땲??
    """

    dedupe_table: str
    dedupe_column: str
    target_date: str


def resolve_news_keyword_storage_config():
    """?ㅻ챸: ?댁뒪 ?ㅼ썙?????泥섎━???ъ슜??湲곕낯 DB ?ㅼ젙??援ъ꽦?⑸땲??
    ?낅젰: 蹂꾨룄 ?몄옄 ?놁씠 紐⑤뱢 ?곸닔? ?ㅻ뒛 ?좎쭨瑜??ъ슜?⑸땲??
    異쒕젰: NewsKeywordStorageConfig 媛앹껜瑜?諛섑솚?⑸땲??
    """
    return NewsKeywordStorageConfig(
        dedupe_table=NEWS_KEYWORD_TABLE,
        dedupe_column=NEWS_KEYWORD_DEDUPE_COLUMN,
        target_date=resolve_keyword_target_date(),
    )


def resolve_keyword_target_date():
    """?ㅻ챸: 理쒖쥌 ?ㅼ썙?쒕? ??ν븷 target_date 媛믪쓣 寃곗젙?⑸땲??
    ?낅젰: 蹂꾨룄 ?몄옄 ?놁씠 ?꾩옱 ?ㅽ뻾?쇱쓣 ?ъ슜?⑸땲??
    異쒕젰: yyyy-mm-dd ?뺤떇???좎쭨 臾몄옄?댁쓣 諛섑솚?⑸땲??
    """
    return date.today().isoformat()


def quote_mysql_identifier(identifier, label):
    """?ㅻ챸: MySQL ?뚯씠釉붾챸 ?먮뒗 而щ읆紐낆쓣 ?덉쟾??諛깊떛 ?앸퀎?먮줈 蹂?섑빀?덈떎.
    ?낅젰: identifier??寃?ы븷 ?앸퀎??臾몄옄?? label? ?ㅻ쪟 硫붿떆吏???ъ슜???앸퀎??醫낅쪟?낅땲??
    異쒕젰: 寃利앸맂 ?앸퀎?먮? 諛깊떛?쇰줈 媛먯떬 SQL 議곌컖?쇰줈 諛섑솚?⑸땲??
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
    """?ㅻ챸: ?ㅼ썙?쒓? DB???대? 議댁옱?섎뒗吏 ?뺤씤?섎뒗 SELECT 荑쇰━瑜?留뚮벊?덈떎.
    ?낅젰: table? 議고쉶 ????뚯씠釉붾챸, column? ?ㅼ썙??鍮꾧탳 而щ읆紐낆엯?덈떎.
    異쒕젰: %(keyword)s ?뚮씪誘명꽣瑜??ъ슜?섎뒗 MySQL SELECT 荑쇰━ 臾몄옄?댁쓣 諛섑솚?⑸땲??
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
    """?ㅻ챸: ?꾨낫 ?ㅼ썙?쒕쭏??DB 以묐났 ?щ?瑜?議고쉶???쒖떆?⑸땲??
    ?낅젰: candidates???꾨낫 紐⑸줉, db_config??MySQL ?묒냽 ?ㅼ젙, table/column? 以묐났 寃???꾩튂?낅땲??
    異쒕젰: 媛??꾨낫??exists_in_db 媛믪쓣 異붽??????꾨낫 紐⑸줉??諛섑솚?⑸땲??
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
    """?ㅻ챸: DB???대? 議댁옱?섏? ?딅뒗 ?댁뒪 ?ㅼ썙???꾨낫留??④퉩?덈떎.
    ?낅젰: candidates??exists_in_db 媛믪쓣 ?ы븿?????덈뒗 ?꾨낫 紐⑸줉?낅땲??
    異쒕젰: exists_in_db媛 李몄씠 ?꾨땶 ?꾨낫 紐⑸줉??諛섑솚?⑸땲??
    """
    return [candidate for candidate in candidates if not candidate.get("exists_in_db")]


def build_viral_keyword_selection_prompt(candidates):
    """?ㅻ챸: 以묐났???꾨땶 ?꾨낫 以?理쒖쥌 ?ㅼ썙??1媛쒕? 怨좊Ⅴ寃??섎뒗 LLM ?꾨＼?꾪듃瑜?留뚮벊?덈떎.
    ?낅젰: candidates??keyword, source_title, source_url, reason 媛믪쓣 媛吏??꾨낫 紐⑸줉?낅땲??
    異쒕젰: LLM???꾨떖???꾨＼?꾪듃 臾몄옄?댁쓣 諛섑솚?⑸땲??
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
    candidates_json = json.dumps(payload, ensure_ascii=False, indent=2)
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
    """?ㅻ챸: 以묐났???꾨땶 ?꾨낫 以?諛붿씠??媛?μ꽦???믪? 理쒖쥌 ?ㅼ썙??1媛쒕? ?좏깮?⑸땲??
    ?낅젰: candidates???꾨낫 紐⑸줉, output_dir? LLM ?꾩떆 異쒕젰 ?꾩튂, llm_client??LLM ?몄텧 援ы쁽泥? model? ?좏깮 紐⑤뜽紐낆엯?덈떎.
    異쒕젰: selection_reason???ы븿??理쒖쥌 ?꾨낫 ?뺤뀛?덈━瑜?諛섑솚?⑸땲??
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
    """?ㅻ챸: LLM??理쒖쥌 ?ㅼ썙???좏깮 ?묐떟???뚯떛?섍퀬 ?먮낯 ?꾨낫? 留ㅼ묶?⑸땲??
    ?낅젰: output_text??LLM ?묐떟 臾몄옄?? candidates???좏깮 媛?ν븳 ?먮낯 ?꾨낫 紐⑸줉?낅땲??
    異쒕젰: 留ㅼ묶???꾨낫??selection_reason??異붽???諛섑솚?섍퀬, ?ㅽ뙣?섎㈃ None??諛섑솚?⑸땲??
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
    """?ㅻ챸: LLM ?묐떟 臾몄옄?댁뿉??JSON 媛앹껜 ?먮뒗 諛곗뿴 媛믪쓣 異붿텧?⑸땲??
    ?낅젰: output_text??JSON留??덇굅???욌뮘 濡쒓렇媛 ?욎씤 ?묐떟 臾몄옄?댁엯?덈떎.
    異쒕젰: ?뚯떛??Python 媛?dict/list ????諛섑솚?섍퀬, ?ㅽ뙣?섎㈃ None??諛섑솚?⑸땲??
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
    """?ㅻ챸: ?좏깮??keyword? source_url????묓븯???먮낯 ?꾨낫瑜?李얠뒿?덈떎.
    ?낅젰: candidates???먮낯 ?꾨낫 紐⑸줉, keyword/source_url? LLM???좏깮??媛믪엯?덈떎.
    異쒕젰: ?뺥솗??留ㅼ묶?섎뒗 ?꾨낫瑜?諛섑솚?섍퀬, URL 留ㅼ묶??遺덈챸?뺥븯硫??ㅼ썙???⑥씪 留ㅼ묶 ?꾨낫瑜?諛섑솚?⑸땲??
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
    """?ㅻ챸: 理쒖쥌 ?좏깮 ?ㅼ썙?쒕? insert 荑쇰━???꾨떖???뚮씪誘명꽣濡?蹂?섑빀?덈떎.
    ?낅젰: selected_candidate??理쒖쥌 ?꾨낫, category?????移댄뀒怨좊━, target_date?????????좎쭨?낅땲??
    異쒕젰: MySQL execute???꾨떖???뚮씪誘명꽣 ?뺤뀛?덈━瑜?諛섑솚?⑸땲??
    """
    return {
        "category": category,
        "keyword": selected_candidate.get("keyword", ""),
        "target_date": target_date,
        "source_title": selected_candidate.get("source_title", ""),
        "source_url": selected_candidate.get("source_url", ""),
        "reason": selected_candidate.get("selection_reason")
        or selected_candidate.get("reason", ""),
        "candidate_reason": selected_candidate.get("candidate_reason")
        or selected_candidate.get("reason", ""),
        "selection_reason": selected_candidate.get("selection_reason", ""),
    }


def insert_selected_news_keyword(db_config, selected_candidate, target_date):
    """?ㅻ챸: 理쒖쥌 ?좏깮 ?ㅼ썙?쒕? 吏?뺣맂 移댄뀒怨좊━?ㅻ줈 MySQL????ν빀?덈떎.
    ?낅젰: db_config??MySQL ?묒냽 ?ㅼ젙, selected_candidate??理쒖쥌 ?꾨낫, target_date?????????좎쭨?낅땲??
    異쒕젰: 移댄뀒怨좊━蹂?affected_rows? lastrowid瑜?媛吏?insert 寃곌낵 紐⑸줉??諛섑솚?⑸땲??
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


def build_news_quiz_content(selected_candidate):
    quiz = selected_candidate.get("quiz")
    if not isinstance(quiz, dict):
        quiz = {}
    return {
        "question": quiz.get("question", ""),
        "option_a": quiz.get("option_a", ""),
        "option_b": quiz.get("option_b", ""),
        "answer": quiz.get("answer", ""),
        "explanation": quiz.get("explanation", ""),
    }


def build_selected_news_quiz_insert_params(selected_candidate, target_date):
    quiz_content = build_news_quiz_content(selected_candidate)
    return {
        "mq_news_date": target_date,
        "mq_company": selected_candidate.get("source_name", ""),
        "mq_title": selected_candidate.get("source_title", ""),
        "mq_source_url": selected_candidate.get("source_url", ""),
        "mq_keyword": selected_candidate.get("keyword", ""),
        "mq_keyword_description": selected_candidate.get("keyword_description", ""),
        "mq_quiz_content": json.dumps(quiz_content, ensure_ascii=False),
        "mq_selection_reason": selected_candidate.get("selection_reason")
        or selected_candidate.get("reason", ""),
    }


def insert_selected_news_quiz(db_config, selected_candidate, target_date):
    params = build_selected_news_quiz_insert_params(
        selected_candidate=selected_candidate,
        target_date=target_date,
    )
    with connect_mysql(db_config) as connection:
        try:
            with connection.cursor() as cursor:
                affected_rows = cursor.execute(NEWS_QUIZ_INSERT_QUERY, params)
                result = {
                    "table": "mq_news_quiz",
                    "affected_rows": int(affected_rows),
                    "lastrowid": cursor.lastrowid,
                }
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return result


def run_news_keyword_selection_process(
    candidates,
    args,
    root_dir,
    output_dir,
    llm_client,
    insert_publish_content=False,
    insert_news_quiz=False,
):
    """?ㅻ챸: ?꾨낫 以묒뿉??理쒖쥌 ?ㅼ썙??1媛쒕? ?좏깮?섍퀬, ?듭뀡???곕씪 MySQL insert瑜??ㅽ뻾?⑸땲??
    ?낅젰: candidates???ㅼ썙???꾨낫 紐⑸줉, args??CLI ?듭뀡, root_dir? ?꾨줈?앺듃 猷⑦듃, output_dir? LLM 異쒕젰 ?꾩튂, llm_client??LLM ?몄텧 援ы쁽泥댁엯?덈떎.
    異쒕젰: 以묐났 寃??寃곌낵, ?좏깮 媛???꾨낫, 理쒖쥌 ?꾨낫, target_date, insert 寃곌낵瑜??댁? ?뺤뀛?덈━瑜?諛섑솚?⑸땲??
    """
    storage_config = resolve_news_keyword_storage_config()
    db_config = None
    checked_candidates = [dict(candidate) for candidate in candidates]
    if insert_publish_content or insert_news_quiz:
        db_config = mysql_connect_kwargs()
    if insert_publish_content:
        checked_candidates = mark_news_keyword_duplicates(
            candidates,
            db_config=db_config,
            table=storage_config.dedupe_table,
            column=storage_config.dedupe_column,
        )
    eligible_candidates = (
        filter_non_duplicate_news_keyword_candidates(checked_candidates)
        if insert_publish_content
        else checked_candidates
    )
    selected_candidate = select_viral_news_keyword_candidate(
        eligible_candidates,
        output_dir=output_dir,
        llm_client=llm_client,
        model=getattr(args, "news_keyword_model", None) or llm_client.default_model,
    )
    if not has_complete_candidate_learning_content(selected_candidate):
        raise RuntimeError(
            "Selected news keyword is missing LLM-generated description or quiz content."
        )
    selected_candidate = dict(selected_candidate)
    selected_candidate.setdefault("candidate_reason", selected_candidate.get("reason", ""))
    selected_candidate.setdefault("selection_reason", selected_candidate.get("reason", ""))

    insert_result = []
    if insert_publish_content:
        insert_result = insert_selected_news_keyword(
            db_config=db_config,
            selected_candidate=selected_candidate,
            target_date=storage_config.target_date,
        )
    news_quiz_insert_result = None
    if insert_news_quiz:
        news_quiz_insert_result = insert_selected_news_quiz(
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
        "news_quiz_insert_result": news_quiz_insert_result,
    }



