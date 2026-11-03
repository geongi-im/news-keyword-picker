import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymysql import MySQLError

from db_connector import mysql_connect_kwargs
from gemini_client import GEMINI_API_KEY_ENV, GeminiLLMClient
from script_generation import run_script_generation, template_keys
from utils.common_util import validate_required_environment
from utils.logger_util import LoggerUtil


REQUIRED_ENV_NAMES = [
    "LLM_MODEL",
    GEMINI_API_KEY_ENV,
]

SCRIPT_RESPONSE_MIME_TYPE = "text/plain"


def parse_args(argv):
    """설명: 명령줄 인자를 파싱해 실행 옵션 객체를 만듭니다.
    입력: argv는 프로그램 이름을 제외한 CLI 인자 리스트입니다.
    출력: argparse.Namespace 형태의 실행 옵션을 반환합니다.
    """
    parser = argparse.ArgumentParser(
        description="Generate a script for a keyword row and save it to n8n_publish_content.content."
    )
    parser.add_argument(
        "--template",
        default="explain_child",
        choices=template_keys(),
        help="Script template to use.",
    )
    parser.add_argument(
        "--date",
        help="Target date of the keyword row in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--keyword",
        help="Keyword to use directly. If the row for --date does not exist, it is inserted.",
    )
    parser.add_argument(
        "--model",
        help="Gemini model override. Defaults to the LLM_MODEL environment variable.",
    )
    return parser.parse_args(argv)


def create_script_llm_client(env=None):
    """설명: 스크립트 생성용 Gemini 클라이언트를 만듭니다. 응답은 JSON이 아니라 일반 텍스트입니다.
    입력: env는 환경변수 매핑이며 생략 시 os.environ을 사용합니다.
    출력: text/plain 응답을 요청하는 GeminiLLMClient를 반환합니다.
    """
    env = env if env is not None else os.environ
    return GeminiLLMClient(
        api_key=env.get(GEMINI_API_KEY_ENV),
        default_model=env.get("LLM_MODEL") or None,
        response_mime_type=SCRIPT_RESPONSE_MIME_TYPE,
    )


def main(argv=None):
    """설명: CLI 진입점으로 키워드 행에 대한 스크립트를 생성해 DB content에 저장합니다.
    입력: argv는 선택 CLI 인자 리스트이며, None이면 sys.argv 값을 사용합니다.
    출력: 프로세스 종료 코드를 반환합니다. 성공 0, 처리 실패 2, 사용자 중단 130입니다.
    """
    cli_args = argv if argv is not None else sys.argv[1:]
    args = parse_args(cli_args)
    root_dir = Path(__file__).resolve().parent.parent
    logger = LoggerUtil(log_dir=root_dir / "logs").get_logger()
    logger.info("script_program_started argv=%s", list(cli_args))

    try:
        load_dotenv(root_dir / ".env")
        validate_required_environment(REQUIRED_ENV_NAMES)
        db_config = mysql_connect_kwargs()
        llm_client = create_script_llm_client()
        result = run_script_generation(
            db_config=db_config,
            llm_client=llm_client,
            root_dir=root_dir,
            template_key=args.template,
            date_text=args.date,
            keyword=args.keyword,
            model=args.model,
        )
        print(f"script ({len(result['script'])} chars):")
        print(result["script"])
        if result["db_content_updated"] is None:
            print("db_content_updated: skipped (already set)")
        else:
            print(f"db_content_updated: {result['db_content_updated']}")
        logger.info(
            "script_program_completed idx=%s db_content_updated=%s",
            result["idx"],
            result["db_content_updated"],
        )
        return 0
    except (FileNotFoundError, ValueError, RuntimeError, MySQLError) as exc:
        logger.exception("script_program_failed exit_code=2")
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        logger.warning("script_program_interrupted exit_code=130")
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
