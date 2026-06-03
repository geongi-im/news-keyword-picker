import argparse
import html
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymysql import MySQLError

from llm_provider import (
    DEFAULT_REASONING_EFFORT_ATTEMPTS,
    LLM_PROVIDER_ENV,
    SUPPORTED_LLM_PROVIDERS,
    create_llm_client,
)
from keyword_selection import run_news_keyword_selection_insert_process
from news_keyword import (
    DEFAULT_NEWSPAPER_SOURCES,
    DEFAULT_NEWS_TITLE_LIMIT,
    NEWS_KEYWORD_COUNT,
    build_news_keyword_prompt,
    fetch_naver_newspaper_front_page_articles,
    filter_news_keyword_candidates,
    parse_news_keyword_candidates,
)
from telegram_util import TelegramUtil


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
        help="LLM model used to extract Naver news keyword candidates. Defaults depend on the selected provider.",
    )
    parser.add_argument(
        "--llm-provider",
        choices=SUPPORTED_LLM_PROVIDERS,
        help=(
            f"Override {LLM_PROVIDER_ENV} for extracting Naver news keyword candidates. "
            f"Supported: {', '.join(SUPPORTED_LLM_PROVIDERS)}."
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
        help="Send extracted news keyword candidates to Telegram.",
    )
    parser.add_argument(
        "--telegram-test",
        action="store_true",
        help="Send Telegram output to TELEGRAM_CHAT_TEST_ID instead of TELEGRAM_CHAT_ID.",
    )
    parser.add_argument(
        "--select-keyword-and-insert",
        action="store_true",
        help="Check keyword duplicates in MySQL, select one final keyword, and run the insert SQL.",
    )
    return parser.parse_args(argv)


def validate_args(args):
    """설명: CLI 옵션 값이 실행 가능한 범위인지 검증합니다.
    입력: args는 parse_args가 반환한 옵션 객체입니다.
    출력: 유효하면 None을 반환하고, 잘못된 값이면 ValueError를 발생시킵니다.
    """
    if args.news_title_limit < 1:
        raise ValueError("--news-title-limit must be greater than 0.")


def suggest_news_keyword_candidates(args, output_dir, llm_client=None):
    """설명: 네이버 신문보기 1면 기사를 수집하고 LLM으로 키워드 후보를 추출합니다.
    입력: args는 CLI 옵션, output_dir은 임시 출력 디렉터리, llm_client는 선택적으로 주입하는 LLM 클라이언트입니다.
    출력: 품질 필터를 통과한 키워드 후보 목록을 반환합니다.
    """
    sources = resolve_news_keyword_sources(args)
    articles = fetch_naver_newspaper_front_page_articles(
        sources=sources,
        limit=args.news_title_limit,
    )
    print(f"news_titles_fetched: {len(articles)}")

    prompt = build_news_keyword_prompt(articles)
    if llm_client is None:
        llm_client = create_llm_client(args.llm_provider)
    candidates, output = extract_news_keyword_candidates(args, output_dir, articles, prompt, llm_client)
    if len(candidates) == NEWS_KEYWORD_COUNT:
        return candidates

    raise RuntimeError(
        f"Expected {NEWS_KEYWORD_COUNT} news keyword candidates after quality filtering, "
        f"but got {len(candidates)}. output={output!r}"
    )


def extract_news_keyword_candidates(args, output_dir, articles, prompt, llm_client):
    """설명: LLM 응답을 파싱하고 필터링하며, 후보 수가 부족하면 한 번 재시도합니다.
    입력: args는 CLI 옵션, output_dir은 출력 디렉터리, articles는 원본 기사 목록, prompt는 LLM 프롬프트, llm_client는 LLM 호출 구현체입니다.
    출력: 후보 목록과 마지막 LLM 원문 응답 문자열의 튜플을 반환합니다.
    """
    attempts = DEFAULT_REASONING_EFFORT_ATTEMPTS
    last_output = ""
    candidates = []

    for index, reasoning_effort in enumerate(attempts, start=1):
        output = llm_client.generate_text(
            prompt=prompt,
            output_dir=output_dir,
            model=args.news_keyword_model or llm_client.default_model,
            reasoning_effort=reasoning_effort,
        )
        last_output = output

        parsed_candidates = parse_news_keyword_candidates(output)
        candidates = filter_news_keyword_candidates(parsed_candidates, articles)
        if len(candidates) == NEWS_KEYWORD_COUNT:
            return candidates, output
        if index < len(attempts):
            print(
                "news_keyword_retry: "
                f"accepted={len(candidates)} "
                f"reasoning_effort={attempts[index]}"
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


def print_news_keyword_selection_insert_result(result):
    """설명: DB 중복 상태, 최종 선택 키워드, insert 결과를 콘솔에 출력합니다.
    입력: result는 키워드 선택/insert 처리 결과 딕셔너리입니다.
    출력: 콘솔에 내용을 출력하고 None을 반환합니다.
    """
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
    print("insert_result:")
    for item in insert_result:
        print(
            f"- category={item['category']} "
            f"affected_rows={item['affected_rows']} "
            f"lastrowid={item['lastrowid']}"
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
    lines = ["<b>경제지 1면 키워드 후보</b>"]
    for index, candidate in enumerate(candidates, start=1):
        keyword = html.escape(candidate["keyword"])
        source_title = html.escape(candidate.get("source_title", ""))
        source_url = html.escape(candidate["source_url"], quote=True)
        reason = html.escape(candidate["reason"])
        lines.append(
            f"{index}. <b>{keyword}</b>\n"
            f"원본 제목: {source_title}\n"
            f"근거: {reason}\n"
            f"출처: <a href=\"{source_url}\">원문 보기</a>"
        )
    return "\n\n".join(lines)


def format_selected_news_keyword_message(selected_candidate):
    """설명: 최종 선정된 뉴스 키워드를 Telegram HTML 메시지 문자열로 변환합니다.
    입력: selected_candidate는 keyword, source_title, source_url, selection_reason 값을 가진 후보 딕셔너리입니다.
    출력: Telegram parse_mode=html에 사용할 최종 선정 키워드 메시지 문자열을 반환합니다.
    """
    keyword = html.escape(selected_candidate["keyword"])
    source_title = html.escape(selected_candidate.get("source_title", ""))
    selection_reason = html.escape(
        selected_candidate.get("selection_reason")
        or selected_candidate.get("reason", "")
    )
    source_url = html.escape(selected_candidate["source_url"], quote=True)
    return "\n".join(
        [
            "<b>최종 경제 키워드</b>",
            f"키워드: <b>{keyword}</b>",
            f"원본 제목: {source_title}",
            f"선정 사유: {selection_reason}",
            f"원본 링크: <a href=\"{source_url}\">원문 보기</a>",
        ]
    )


def main(argv=None):
    """설명: CLI 진입점으로 환경 로드, 키워드 추출, 출력, 선택적 텔레그램 전송을 수행합니다.
    입력: argv는 선택 CLI 인자 리스트이며, None이면 sys.argv 값을 사용합니다.
    출력: 프로세스 종료 코드로 사용할 정수를 반환합니다.
    """
    args = parse_args(argv or sys.argv[1:])
    root_dir = Path(__file__).resolve().parent.parent

    try:
        validate_args(args)
        load_dotenv(root_dir / ".env")
        output_dir = resolve_output_dir(root_dir, args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        llm_client = create_llm_client(args.llm_provider)
        candidates = suggest_news_keyword_candidates(args, output_dir, llm_client)
        print_news_keyword_candidates(candidates)
        selection_result = None
        if args.select_keyword_and_insert:
            selection_result = run_news_keyword_selection_insert_process(
                candidates=candidates,
                args=args,
                root_dir=root_dir,
                output_dir=output_dir,
                llm_client=llm_client,
            )
            print_news_keyword_selection_insert_result(selection_result)
        if args.send_telegram:
            if selection_result is None:
                send_news_keyword_candidates_to_telegram(
                    candidates,
                    use_test_chat=args.telegram_test,
                )
            else:
                send_selected_news_keyword_to_telegram(
                    selection_result["selected_candidate"],
                    use_test_chat=args.telegram_test,
                )
            print("telegram_sent: true")
        return 0
    except (FileNotFoundError, ValueError, RuntimeError, MySQLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
