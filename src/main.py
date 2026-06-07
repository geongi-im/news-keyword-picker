import argparse
import html
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymysql import MySQLError

from llm_provider import (
    DEFAULT_REASONING_EFFORT_ATTEMPTS,
    create_llm_client,
)
from keyword_selection import run_news_keyword_selection_process
from news_keyword import (
    DEFAULT_NEWSPAPER_SOURCES,
    DEFAULT_NEWS_TITLE_LIMIT,
    NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT,
    NEWS_KEYWORD_COUNT,
    build_news_keyword_prompt,
    build_news_keyword_response_json_schema,
    fetch_naver_newspaper_front_page_articles,
    filter_news_keyword_candidates,
    parse_news_keyword_candidates,
)
from utils.common_util import validate_required_environment
from utils.logger_util import LoggerUtil
from utils.telegram_util import TelegramUtil


REQUIRED_ENV_NAMES = [
    "LLM_PROVIDER",
    "LLM_MODEL",
    "NEWS_MIN_ARTICLE_COUNT",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]

NEWS_MIN_ARTICLE_COUNT_ENV = "NEWS_MIN_ARTICLE_COUNT"


def resolve_output_dir(root_dir, output_dir):
    """설명: CLI로 받은 출력 디렉터리 값을 프로젝트 기준 절대 경로로 변환합니다.
    입력: root_dir은 프로젝트 루트 Path, output_dir은 절대 또는 상대 경로 문자열입니다.
    출력: 실제 사용할 출력 디렉터리 Path를 반환합니다.
    """
    path = Path(output_dir)
    if path.is_absolute():
        return path
    return root_dir / path


def parse_args(argv):
    """설명: 명령줄 인자를 파싱해 실행 옵션 객체를 만듭니다.
    입력: argv는 프로그램 이름을 제외한 CLI 인자 리스트입니다.
    출력: argparse.Namespace 형태의 실행 옵션을 반환합니다.
    """
    parser = argparse.ArgumentParser(
        description="Suggest economy image keywords from Naver newspaper front-page titles."
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for temporary LLM output files. Relative paths are resolved from project root.",
    )
    parser.add_argument(
        "--news-keyword-model",
        help=(
            "LLM model used to extract and select Naver news keyword candidates. "
            "Overrides the provider default for this run."
        ),
    )
    parser.add_argument(
        "--news-keyword-url",
        help="Optional single Naver newspaper page URL to crawl instead of the default three economy newspapers.",
    )
    parser.add_argument(
        "--news-title-limit",
        type=int,
        default=DEFAULT_NEWS_TITLE_LIMIT,
        help="Maximum number of Naver news titles to send to the LLM.",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="Send the final selected news quiz to Telegram.",
    )
    parser.add_argument(
        "--telegram-test",
        action="store_true",
        help="Send Telegram output to TELEGRAM_CHAT_TEST_ID instead of TELEGRAM_CHAT_ID.",
    )
    parser.add_argument(
        "--insert-publish-content",
        action="store_true",
        help="Insert the final selected keyword into n8n_publish_content.",
    )
    parser.add_argument(
        "--insert-news-quiz",
        action="store_true",
        help="Insert the final selected news quiz into mq_news_quiz.",
    )
    return parser.parse_args(argv)


def validate_args(args):
    """설명: CLI 옵션 값이 실행 가능한 범위인지 검증합니다.
    입력: args는 parse_args가 반환한 옵션 객체입니다.
    출력: 유효하면 None을 반환하고, 잘못된 값이면 ValueError를 발생시킵니다.
    """
    if args.news_title_limit < 1:
        raise ValueError("--news-title-limit must be greater than 0.")


def resolve_news_keyword_candidate_count(args):
    """설명: 실행 옵션에 따라 LLM에서 받을 후보 개수를 결정합니다."""
    return NEWS_KEYWORD_LEARNING_CANDIDATE_COUNT


def resolve_min_news_keyword_candidate_count(args):
    """설명: 필터 후 실행을 계속할 최소 후보 개수를 결정합니다."""
    return NEWS_KEYWORD_COUNT


def resolve_min_news_article_count(env=None):
    """설명: 수집해야 하는 최소 기사 개수를 환경변수에서 읽습니다."""
    env = env if env is not None else os.environ
    raw_count = (env.get(NEWS_MIN_ARTICLE_COUNT_ENV) or "").strip()
    try:
        count = int(raw_count)
    except ValueError as exc:
        raise ValueError(
            f"{NEWS_MIN_ARTICLE_COUNT_ENV} must be a positive integer."
        ) from exc

    if count < 1:
        raise ValueError(f"{NEWS_MIN_ARTICLE_COUNT_ENV} must be greater than 0.")
    return count


def validate_news_article_count(articles, min_article_count):
    """설명: 수집된 기사 개수가 환경변수 기준을 만족하는지 확인합니다."""
    article_count = len(articles)
    if article_count < min_article_count:
        raise RuntimeError(
            f"Expected at least {min_article_count} news articles, but got {article_count}."
        )


def suggest_news_keyword_candidates(args, output_dir, llm_client=None, logger=None):
    """설명: 네이버 신문보기 1면 기사를 수집하고 LLM으로 키워드 후보를 추출합니다.
    입력: args는 CLI 옵션, output_dir은 임시 출력 디렉터리, llm_client는 선택적으로 주입하는 LLM 클라이언트입니다.
    출력: 품질 필터를 통과한 키워드 후보 목록을 반환합니다.
    """
    sources = resolve_news_keyword_sources(args)
    if logger:
        logger.info(
            "news_fetch_started sources=%s title_limit=%s",
            [source["name"] for source in sources],
            args.news_title_limit,
        )
    articles = fetch_naver_newspaper_front_page_articles(
        sources=sources,
        limit=args.news_title_limit,
    )
    print(f"news_titles_fetched: {len(articles)}")
    if logger:
        logger.info("news_fetch_completed article_count=%s", len(articles))

    min_article_count = resolve_min_news_article_count()
    validate_news_article_count(articles, min_article_count)
    if logger:
        logger.info(
            "news_article_count_validated article_count=%s min_required=%s",
            len(articles),
            min_article_count,
        )

    candidate_count = resolve_news_keyword_candidate_count(args)
    min_candidate_count = resolve_min_news_keyword_candidate_count(args)
    include_learning_content = True
    prompt = build_news_keyword_prompt(
        articles,
        keyword_count=candidate_count,
        include_learning_content=include_learning_content,
    )
    if llm_client is None:
        llm_client = create_llm_client()
    candidates, output = extract_news_keyword_candidates(
        args,
        output_dir,
        articles,
        prompt,
        llm_client,
        candidate_count=candidate_count,
        min_candidate_count=min_candidate_count,
        include_learning_content=include_learning_content,
        logger=logger,
    )
    if len(candidates) >= min_candidate_count:
        if logger:
            logger.info(
                "candidate_filter_completed accepted_count=%s min_required=%s",
                len(candidates),
                min_candidate_count,
            )
        return candidates

    if logger:
        logger.warning(
            "candidate_filter_failed accepted_count=%s min_required=%s",
            len(candidates),
            min_candidate_count,
        )
    raise RuntimeError(
        f"Expected at least {min_candidate_count} news keyword candidates after quality filtering, "
        f"but got {len(candidates)}. output={output!r}"
    )


def extract_news_keyword_candidates(
    args,
    output_dir,
    articles,
    prompt,
    llm_client,
    candidate_count=NEWS_KEYWORD_COUNT,
    min_candidate_count=NEWS_KEYWORD_COUNT,
    include_learning_content=False,
    logger=None,
):
    """설명: LLM 응답을 파싱하고 필터링하며, 후보 수가 부족하면 한 번 재시도합니다.
    입력: args는 CLI 옵션, output_dir은 출력 디렉터리, articles는 원본 기사 목록, prompt는 LLM 프롬프트, llm_client는 LLM 호출 구현체입니다.
    출력: 후보 목록과 마지막 LLM 원문 응답 문자열의 튜플을 반환합니다.
    """
    attempts = DEFAULT_REASONING_EFFORT_ATTEMPTS
    last_output = ""
    candidates = []

    for index, reasoning_effort in enumerate(attempts, start=1):
        model = args.news_keyword_model or llm_client.default_model
        if logger:
            logger.info(
                "llm_keyword_generation_started attempt=%s reasoning_effort=%s model=%s candidate_count=%s",
                index,
                reasoning_effort,
                model,
                candidate_count,
            )
        output = llm_client.generate_text(
            prompt=prompt,
            output_dir=output_dir,
            model=model,
            reasoning_effort=reasoning_effort,
            response_json_schema=build_news_keyword_response_json_schema(
                keyword_count=candidate_count,
                include_learning_content=include_learning_content,
            ),
            response_mime_type="application/json",
        )
        last_output = output

        parsed_candidates = parse_news_keyword_candidates(
            output,
            keyword_count=candidate_count,
        )
        candidates = filter_news_keyword_candidates(
            parsed_candidates,
            articles,
            keyword_count=candidate_count,
            require_learning_content=include_learning_content,
        )
        if logger:
            logger.info(
                "llm_keyword_generation_completed attempt=%s parsed_count=%s accepted_count=%s",
                index,
                len(parsed_candidates),
                len(candidates),
            )
        if len(candidates) >= min_candidate_count:
            return candidates, output
        if index < len(attempts):
            print(
                "news_keyword_retry: "
                f"accepted={len(candidates)} "
                f"reasoning_effort={attempts[index]}"
            )
            if logger:
                logger.warning(
                    "llm_keyword_generation_retry accepted_count=%s next_reasoning_effort=%s",
                    len(candidates),
                    attempts[index],
                )

    return candidates, last_output


def resolve_news_keyword_sources(args):
    """설명: 사용자 지정 URL 또는 기본 경제지 URL 목록을 수집 대상 목록으로 변환합니다.
    입력: args는 news_keyword_url 옵션을 포함한 CLI 옵션 객체입니다.
    출력: name과 url을 가진 수집 대상 딕셔너리 목록을 반환합니다.
    """
    if args.news_keyword_url:
        return [{"name": "custom", "url": args.news_keyword_url}]
    return list(DEFAULT_NEWSPAPER_SOURCES)


def print_news_keyword_candidates(candidates):
    """설명: 키워드 후보 목록을 콘솔에서 읽기 쉬운 텍스트로 출력합니다.
    입력: candidates는 keyword, source_url, reason 등을 가진 후보 딕셔너리 목록입니다.
    출력: 콘솔에 내용을 출력하고 None을 반환합니다.
    """
    print("news_keyword_candidates:")
    for index, candidate in enumerate(candidates, start=1):
        print(f"{index}. {candidate['keyword']}")
        print(f"   title: {candidate.get('source_title', '')}")
        print(f"   source_url: {candidate['source_url']}")
        print(f"   reason: {candidate['reason']}")
        if candidate.get("keyword_description"):
            print(f"   keyword_description: {candidate['keyword_description']}")
        quiz = candidate.get("quiz")
        if isinstance(quiz, dict) and quiz:
            print(f"   quiz: {quiz.get('question', '')}")
            print(f"      A. {quiz.get('option_a', '')}")
            print(f"      B. {quiz.get('option_b', '')}")
            print(f"      answer: {quiz.get('answer', '')}")
            print(f"      explanation: {quiz.get('explanation', '')}")


def print_news_keyword_selection_insert_result(result):
    """설명: DB 중복 상태, 최종 선택 키워드, 선택적 insert 결과를 콘솔에 출력합니다.
    입력: result는 키워드 선택/insert 처리 결과 딕셔너리입니다.
    출력: 콘솔에 내용을 출력하고 None을 반환합니다.
    """
    if result.get("insert_result"):
        print("news_keyword_db_status:")
        for index, candidate in enumerate(result["checked_candidates"], start=1):
            status = "duplicate" if candidate.get("exists_in_db") else "new"
            print(f"{index}. {candidate['keyword']}: {status}")

    selected = result["selected_candidate"]
    insert_result = result["insert_result"]
    print("selected_news_keyword:")
    print(f"keyword: {selected['keyword']}")
    print(f"source_url: {selected['source_url']}")
    print(f"reason: {selected.get('selection_reason', '')}")
    print(f"target_date: {result['target_date']}")
    if insert_result:
        print("insert_result:")
        for item in insert_result:
            print(
                f"- category={item['category']} "
                f"affected_rows={item['affected_rows']} "
                f"lastrowid={item['lastrowid']}"
            )
    news_quiz_insert_result = result.get("news_quiz_insert_result")
    if news_quiz_insert_result:
        print("news_quiz_insert_result:")
        print(
            f"- table={news_quiz_insert_result['table']} "
            f"affected_rows={news_quiz_insert_result['affected_rows']} "
            f"lastrowid={news_quiz_insert_result['lastrowid']}"
        )


def send_news_keyword_candidates_to_telegram(candidates, use_test_chat=False):
    """설명: 키워드 후보 목록을 텔레그램 메시지로 전송합니다.
    입력: candidates는 후보 목록, use_test_chat은 테스트 채팅방 사용 여부입니다.
    출력: 전송에 성공하면 None을 반환하고, 설정 또는 네트워크 오류가 있으면 예외를 전파합니다.
    """
    send_telegram_message(
        format_news_keyword_candidates_message(candidates),
        use_test_chat=use_test_chat,
    )


def send_selected_news_keyword_to_telegram(selected_candidate, use_test_chat=False):
    """설명: 최종 선정된 뉴스 키워드 1개를 텔레그램 메시지로 전송합니다.
    입력: selected_candidate는 keyword, source_title, source_url, selection_reason 값을 가진 최종 후보입니다.
    출력: 전송에 성공하면 None을 반환하고, 설정 또는 네트워크 오류가 있으면 예외를 전파합니다.
    """
    send_telegram_message(
        format_selected_news_keyword_message(selected_candidate),
        use_test_chat=use_test_chat,
    )


def send_telegram_message(message, use_test_chat=False):
    """설명: 완성된 Telegram HTML 메시지를 기본 또는 테스트 채팅방으로 전송합니다.
    입력: message는 Telegram HTML 메시지 문자열, use_test_chat은 테스트 채팅방 사용 여부입니다.
    출력: 전송에 성공하면 None을 반환하고, 설정 또는 네트워크 오류가 있으면 예외를 전파합니다.
    """
    telegram = TelegramUtil()
    if use_test_chat:
        telegram.send_test_message(message)
    else:
        telegram.send_message(message)


def format_news_keyword_candidates_message(candidates):
    """설명: 키워드 후보 목록을 Telegram HTML 메시지 문자열로 변환합니다.
    입력: candidates는 keyword, source_title, source_url, reason 값을 가진 후보 목록입니다.
    출력: Telegram parse_mode=html에 사용할 메시지 문자열을 반환합니다.
    """
    blocks = ["<b>경제지 1면 키워드 후보</b>"]
    for index, candidate in enumerate(candidates, start=1):
        keyword = html.escape(candidate["keyword"])
        source_title = html.escape(candidate.get("source_title", ""))
        source_url = html.escape(candidate["source_url"], quote=True)
        candidate_lines = [
            f"{index}. <b>{keyword}</b>",
            f"<b>원본 제목</b>\n{source_title}",
        ]
        if candidate.get("keyword_description"):
            keyword_description = html.escape(candidate["keyword_description"])
            candidate_lines.append(f"<b>한줄설명</b>\n{keyword_description}")
        quiz = candidate.get("quiz")
        if isinstance(quiz, dict) and quiz:
            candidate_lines.extend(format_quiz_message_lines(quiz))
        candidate_lines.append(f"<a href=\"{source_url}\">원문 보기</a>")
        blocks.append("\n".join(candidate_lines))
    return "\n\n".join(blocks)


def format_quiz_message_lines(quiz):
    """설명: 후보 퀴즈를 Telegram HTML 메시지 줄 목록으로 변환합니다."""
    question = html.escape(quiz.get("question", ""))
    option_a = html.escape(quiz.get("option_a", ""))
    option_b = html.escape(quiz.get("option_b", ""))
    answer = html.escape(quiz.get("answer", ""))
    lines = [
        "<b>미니 퀴즈</b>",
        f"Q. {question}",
        f"A. {option_a}",
        f"B. {option_b}",
        f"<b>정답: {answer}</b>",
    ]
    if quiz.get("explanation"):
        explanation = html.escape(quiz.get("explanation", ""))
        lines.append(f"<b>해설</b>\n{explanation}")
    return lines


def format_selected_news_keyword_message(selected_candidate):
    """설명: 최종 선정된 뉴스 키워드를 Telegram HTML 메시지 문자열로 변환합니다.
    입력: selected_candidate는 keyword, source_title, source_url, selection_reason 값을 가진 후보 딕셔너리입니다.
    출력: Telegram parse_mode=html에 사용할 최종 선정 키워드 메시지 문자열을 반환합니다.
    """
    keyword = html.escape(selected_candidate["keyword"])
    source_title = html.escape(selected_candidate.get("source_title", ""))
    source_url = html.escape(selected_candidate["source_url"], quote=True)
    sections = [
        "<b>오늘의 경제뉴스 퀴즈</b>",
        f"<b>1. 뉴스제목</b>\n{source_title}",
        f"<b>2. 뉴스링크</b>\n<a href=\"{source_url}\">원문 보기</a>",
        f"<b>3. 키워드</b>\n<b>{keyword}</b>",
    ]
    if selected_candidate.get("keyword_description"):
        keyword_description = html.escape(selected_candidate["keyword_description"])
        sections.append(f"<b>4. 한줄설명</b>\n{keyword_description}")
    quiz = selected_candidate.get("quiz")
    if isinstance(quiz, dict) and quiz:
        question = html.escape(quiz.get("question", ""))
        option_a = html.escape(quiz.get("option_a", ""))
        option_b = html.escape(quiz.get("option_b", ""))
        answer = html.escape(quiz.get("answer", ""))
        explanation = html.escape(quiz.get("explanation", ""))
        sections.append(
            "\n".join(
                [
                    "<b>5. 퀴즈</b>",
                    f"Q. {question}",
                    f"A. {option_a}",
                    f"B. {option_b}",
                ]
            )
        )
        explanation_lines = ["<b>6. 해설</b>"]
        if answer:
            explanation_lines.append(f"정답: <b>{answer}</b>")
        if explanation:
            explanation_lines.append(explanation)
        sections.append("\n".join(explanation_lines))
    return "\n\n".join(sections)


def main(argv=None):
    """설명: CLI 진입점으로 환경 로드, 키워드 추출, 출력, 선택적 텔레그램 전송을 수행합니다.
    입력: argv는 선택 CLI 인자 리스트이며, None이면 sys.argv 값을 사용합니다.
    출력: 프로세스 종료 코드로 사용할 정수를 반환합니다.
    """
    cli_args = argv if argv is not None else sys.argv[1:]
    args = parse_args(cli_args)
    root_dir = Path(__file__).resolve().parent.parent
    logger = LoggerUtil(log_dir=root_dir / "logs").get_logger()
    logger.info(
        "program_started argv=%s output_dir=%s send_telegram=%s telegram_test=%s insert_publish_content=%s insert_news_quiz=%s",
        list(cli_args),
        args.output_dir,
        args.send_telegram,
        args.telegram_test,
        args.insert_publish_content,
        args.insert_news_quiz,
    )

    try:
        validate_args(args)
        load_dotenv(root_dir / ".env")
        logger.info("environment_loaded env_path=%s", root_dir / ".env")
        validate_required_environment(REQUIRED_ENV_NAMES)
        logger.info("environment_validated required_env_names=%s", REQUIRED_ENV_NAMES)
        output_dir = resolve_output_dir(root_dir, args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("output_dir_ready path=%s", output_dir)
        llm_client = create_llm_client()
        logger.info(
            "llm_client_created provider=%s default_model=%s",
            getattr(llm_client, "provider", ""),
            getattr(llm_client, "default_model", ""),
        )
        candidates = suggest_news_keyword_candidates(
            args,
            output_dir,
            llm_client,
            logger=logger,
        )
        print_news_keyword_candidates(candidates)
        selection_result = run_news_keyword_selection_process(
            candidates=candidates,
            args=args,
            root_dir=root_dir,
            output_dir=output_dir,
            llm_client=llm_client,
            insert_publish_content=args.insert_publish_content,
            insert_news_quiz=args.insert_news_quiz,
        )
        logger.info(
            "selection_completed selected_keyword=%s target_date=%s insert_count=%s news_quiz_inserted=%s",
            selection_result["selected_candidate"].get("keyword", ""),
            selection_result["target_date"],
            len(selection_result["insert_result"]),
            bool(selection_result.get("news_quiz_insert_result")),
        )
        print_news_keyword_selection_insert_result(selection_result)
        if args.send_telegram:
            logger.info(
                "telegram_send_started test_chat=%s selected_keyword=%s",
                args.telegram_test,
                selection_result["selected_candidate"].get("keyword", ""),
            )
            send_selected_news_keyword_to_telegram(
                selection_result["selected_candidate"],
                use_test_chat=args.telegram_test,
            )
            print("telegram_sent: true")
            logger.info("telegram_send_completed")
        logger.info("program_completed exit_code=0")
        return 0
    except (FileNotFoundError, ValueError, RuntimeError, MySQLError) as exc:
        logger.exception("program_failed exit_code=2")
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        logger.warning("program_interrupted exit_code=130")
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
