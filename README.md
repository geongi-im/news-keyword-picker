# news-keyword-picker

네이버 신문보기에서 지정 신문사들의 1면 기사 제목을 수집하고, 환경변수로 선택한 LLM provider(Codex CLI 또는 Gemini API)로 오늘의 경제뉴스 퀴즈용 최종 뉴스 1개를 선정하는 독립 프로젝트입니다.

## 실행

```powershell
python src/main.py
```

실행 전 `.env` 또는 환경변수에 `LLM_PROVIDER`, `LLM_MODEL`, `NEWS_MIN_ARTICLE_COUNT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 반드시 설정해야 합니다.

LLM provider는 `LLM_PROVIDER` 환경변수로 선택합니다. Gemini API를 사용하려면 provider를 `gemini`로 설정합니다.

```powershell
$env:LLM_PROVIDER="gemini"
$env:LLM_MODEL="gemini-3.5-flash"
python src/main.py
```

어떤 LLM provider를 사용하든 `LLM_MODEL` 환경변수가 필수입니다. `--news-keyword-model`을 함께 지정하면 실제 호출 모델은 CLI 값이 우선하지만, 실행 설정을 명시하기 위해 `LLM_MODEL`도 반드시 설정해야 합니다.

기본 실행은 한줄설명과 A/B 미니 퀴즈를 포함한 후보 5개를 LLM에 요청하고, 품질 필터를 통과한 후보 중 최종 뉴스 1개를 선정해 콘솔에만 출력합니다.
실행 로그는 프로젝트 루트의 `logs/` 하위에 `news_keyword_picker_YYYYMMDD.log` 파일로 저장됩니다.

텔레그램 전송:

```powershell
python src/main.py --send-telegram
python src/main.py --send-telegram --telegram-test
```

기존 `n8n_publish_content` 테이블 insert:

```powershell
python src/main.py --insert-publish-content
```

새 `mq_news_quiz` 테이블 insert:

```powershell
python src/main.py --insert-news-quiz
```

텔레그램 전송과 기존 테이블 insert를 함께 실행:

```powershell
python src/main.py --send-telegram --insert-publish-content
```

텔레그램 전송과 새 `mq_news_quiz` 테이블 insert를 함께 실행:

```powershell
python src/main.py --send-telegram --insert-news-quiz
```

`--insert-publish-content`를 켜면 후보를 `n8n_publish_content.keyword`에서 먼저 조회하고, 이미 존재하지 않는 후보만 남긴 뒤 경제적 이슈성/바이럴 가능성이 가장 높은 1개를 LLM으로 선별합니다. 마지막으로 `n8n_publish_content`에 `3초퀴즈`, `자녀에게설명하기` 카테고리로 2건을 insert합니다.

`--insert-news-quiz`를 켜면 최종 선정된 뉴스 1건의 제목, 링크, 키워드, 한줄설명, 퀴즈 JSON, 선정 사유를 `mq_news_quiz`에 1건 insert합니다.

테스트:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
```

또는 pytest로 전체 테스트를 실행할 수 있습니다.

```powershell
$env:PYTHONPATH="src"
python -m pytest
```

## 기본 신문보기 수집 대상

- 파이낸셜뉴스: `https://media.naver.com/press/014/newspaper`
- 머니투데이: `https://media.naver.com/press/008/newspaper`
- 서울경제: `https://media.naver.com/press/011/newspaper`
- 한국경제: `https://media.naver.com/press/015/newspaper`

## 옵션

```text
--news-keyword-model  키워드 후보 추출과 최종 선정에 사용할 LLM 모델입니다. 실제 호출 모델로 우선 사용됩니다.
--news-keyword-url    기본 신문보기 수집 대상 대신 단일 네이버 신문보기 URL을 수집합니다.
--news-title-limit    LLM에 전달할 기사 제목 개수 상한입니다. 기본값: 30
--send-telegram       최종 선정된 오늘의 경제뉴스 퀴즈를 텔레그램으로 전송합니다.
--telegram-test       TELEGRAM_CHAT_ID 대신 TELEGRAM_CHAT_TEST_ID로 전송합니다.
--output-dir          LLM 임시 출력 파일 디렉토리입니다. 기본값: output
--insert-publish-content
                      기존 n8n_publish_content 테이블에 최종 키워드를 insert합니다.
--insert-news-quiz   새 mq_news_quiz 테이블에 최종 뉴스 퀴즈를 insert합니다.
```

## 로그

프로그램 동작 로그는 소스 코드가 있는 `src/`가 아니라 프로젝트 루트의 `logs/`에 저장합니다. `src/`는 코드 패키지 영역이고, `logs/`는 실행 산출물이므로 루트 하위에 두는 것이 배포와 git 관리에 더 적합합니다.

로그 파일은 날짜별로 생성됩니다.

```text
logs/news_keyword_picker_YYYYMMDD.log
```

`logs/`는 `.gitignore`에 포함되어 있어 실제 실행 로그는 저장소에 커밋되지 않습니다.

## 환경 변수

`.env.example`을 참고해 `.env`를 구성합니다.

```text
# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
TELEGRAM_CHAT_TEST_ID=

# LLM
LLM_PROVIDER=codex
LLM_MODEL=gpt-5.5
GEMINI_API_KEY=

# News
NEWS_MIN_ARTICLE_COUNT=3

# MySQL
MYSQL_HOST=
MYSQL_PORT=3306
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_DATABASE=
MYSQL_CHARSET=utf8mb4
```

필수 환경변수는 `LLM_PROVIDER`, `LLM_MODEL`, `NEWS_MIN_ARTICLE_COUNT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`입니다. 값이 비어 있으면 프로그램 시작 시 실패합니다.

`LLM_PROVIDER`는 `codex` 또는 `gemini`를 지원합니다. provider는 `LLM_PROVIDER` 환경변수로만 제어합니다.

`LLM_MODEL`은 provider와 무관하게 필수입니다. 실제 호출 모델은 CLI `--news-keyword-model`, `LLM_MODEL` 환경변수 순서로 결정됩니다.

`NEWS_MIN_ARTICLE_COUNT`는 기본 또는 사용자 지정 네이버 신문보기 URL에서 최소 몇 개의 원본 기사를 수집해야 하는지 정하는 1 이상의 정수입니다. 수집 기사 수가 이 값보다 적으면 후보 생성 전에 실패합니다.

`TELEGRAM_CHAT_TEST_ID`는 `--telegram-test`를 사용할 때만 필요합니다.

최종 insert는 아래 쿼리와 같은 형태로 두 번 실행됩니다. `target_date`는 실행일 기준 오늘 날짜가 들어가고, `comment`에는 뉴스 원문 URL이 들어갑니다.

```sql
INSERT INTO n8n_publish_content(category, keyword, target_date, `comment`) VALUES("3초퀴즈", {선정된키워드}, {yyyy-mm-dd}, {뉴스원문URL});
INSERT INTO n8n_publish_content(category, keyword, target_date, `comment`) VALUES("자녀에게설명하기", {선정된키워드}, {yyyy-mm-dd}, {뉴스원문URL});
```

## LLM 구조

- `llm_provider.py`: `LLM_PROVIDER` 값을 해석하고 메인 `LLMClient`가 선택된 하위 provider로 호출을 위임합니다.
- `codex_client.py`: Codex CLI 기반 provider입니다. `codex exec`를 실행하고 마지막 응답 메시지를 읽습니다.
- `gemini_client.py`: Gemini API 기반 provider입니다. `google-genai` SDK로 `generate_content`를 호출합니다.

## 유틸 구조

- `utils/common_util.py`: 호출부가 전달한 특정 환경변수 이름 목록만 검증하는 공통 유틸입니다.
- `utils/telegram_util.py`: Telegram Bot API 메시지 전송 유틸입니다.
- `utils/logger_util.py`: 콘솔과 `logs/` 파일에 함께 기록하는 공용 로거입니다.

## 출력 항목

각 후보는 다음 정보를 포함합니다.

- 키워드
- 기사 원본 제목
- 원문 URL
- 추출 근거
- 해요체 키워드 한줄설명
- A/B 미니 퀴즈, 정답, 해설

최종 선정 키워드 메시지에는 후보 생성 단계에서 LLM이 생성한 해요체 키워드 한줄설명, A/B 미니 퀴즈 정답, 해설이 포함됩니다.

새 퀴즈 테이블 insert는 `mq_quiz_content`에 question, option_a, option_b, answer, explanation을 JSON 문자열로 저장합니다. 한글은 `ensure_ascii=False`로 직렬화해 읽을 수 있는 원문 그대로 저장합니다.
