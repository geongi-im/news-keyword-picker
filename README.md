# news-keyword-picker

네이버 경제지 신문보기 1면 기사 제목을 수집하고, 환경변수로 선택한 LLM provider(Codex CLI 또는 Gemini API)로 오늘의 경제뉴스 퀴즈용 최종 뉴스 1개를 선정하는 독립 프로젝트입니다.

## 실행

```powershell
python src/main.py
```

LLM provider는 기본값이 `codex`입니다. Gemini API를 사용하려면 환경변수에서 provider를 바꿉니다.

```powershell
$env:LLM_PROVIDER="gemini"
python src/main.py
```

일회성으로만 provider를 바꿀 때는 CLI 옵션으로 환경변수를 덮어쓸 수 있습니다.

```powershell
python src/main.py --llm-provider gemini
```

기본 실행은 후보 여러 개를 수집한 뒤 최종 뉴스 1개를 선정하고 콘솔에만 출력합니다.

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

`--insert-publish-content`를 켜면 후보를 `n8n_publish_content.keyword`에서 먼저 조회하고, 이미 존재하지 않는 후보만 남긴 뒤 경제적 이슈성/바이럴 가능성이 가장 높은 1개를 LLM으로 선별합니다. 마지막으로 `n8n_publish_content`에 `3초퀴즈`, `자녀에게설명하기` 카테고리로 2건을 insert합니다.

`--insert-news-quiz`를 켜면 최종 선정된 뉴스 1건의 제목, 링크, 키워드, 한줄설명, 퀴즈 JSON, 선정 사유를 `mq_news_quiz`에 1건 insert합니다.

테스트:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
```

## 기본 수집 대상

- 파이낸셜뉴스: `https://media.naver.com/press/014/newspaper`
- 머니투데이: `https://media.naver.com/press/008/newspaper`
- 서울경제: `https://media.naver.com/press/011/newspaper`

## 옵션

```text
--llm-provider        LLM_PROVIDER 환경변수를 임시로 덮어씁니다. 지원: codex, gemini
--news-keyword-model  키워드 후보 추출에 사용할 LLM 모델입니다. 기본값: codex=gpt-5.4-mini, gemini=gemini-3.5-flash
--news-keyword-url    기본 3개 경제지 대신 단일 네이버 신문보기 URL을 수집합니다.
--news-title-limit    LLM에 전달할 기사 제목 개수 상한입니다. 기본값: 30
--send-telegram       최종 선정된 오늘의 경제뉴스 퀴즈를 텔레그램으로 전송합니다.
--telegram-test       TELEGRAM_CHAT_ID 대신 TELEGRAM_CHAT_TEST_ID로 전송합니다.
--output-dir          LLM 임시 출력 파일 디렉토리입니다. 기본값: output
--insert-publish-content
                      기존 n8n_publish_content 테이블에 최종 키워드를 insert합니다.
--insert-news-quiz   새 mq_news_quiz 테이블에 최종 뉴스 퀴즈를 insert합니다.
```

## 환경 변수

`.env.example`을 참고해 `.env`를 구성합니다.

```text
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_CHAT_TEST_ID=

# LLM
LLM_PROVIDER=codex
GEMINI_API_KEY=

# MySQL
MYSQL_HOST=
MYSQL_PORT=3306
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_DATABASE=
MYSQL_CHARSET=utf8mb4
```

`LLM_PROVIDER`는 `codex` 또는 `gemini`를 지원합니다. 설정 우선순위는 CLI `--llm-provider`, `LLM_PROVIDER` 환경변수, 기본값 `codex` 순서입니다.

최종 insert는 아래 쿼리와 같은 형태로 두 번 실행됩니다. `target_date`는 실행일 기준 오늘 날짜가 들어갑니다.

```sql
INSERT INTO n8n_publish_content(category, keyword, target_date) VALUES("3초퀴즈", {선정된키워드}, {yyyy-mm-dd});
INSERT INTO n8n_publish_content(category, keyword, target_date) VALUES("자녀에게설명하기", {선정된키워드}, {yyyy-mm-dd});
```

## LLM 구조

- `llm_provider.py`: `LLM_PROVIDER` 값을 해석하고 메인 `LLMClient`가 선택된 하위 provider로 호출을 위임합니다.
- `codex_client.py`: Codex CLI 기반 provider입니다. `codex exec`를 실행하고 마지막 응답 메시지를 읽습니다.
- `gemini_client.py`: Gemini API 기반 provider입니다. `google-genai` SDK로 `generate_content`를 호출합니다.

## 출력 항목

각 후보는 다음 정보를 포함합니다.

- 키워드
- 기사 원본 제목
- 원문 URL
- 추출 근거

최종 선정 키워드 메시지에는 후보 생성 단계에서 LLM이 생성한 해요체 키워드 한줄설명, A/B 미니 퀴즈 정답, 해설이 포함됩니다.

새 퀴즈 테이블 insert는 `mq_quiz_content`에 question, option_a, option_b, answer, explanation을 JSON 문자열로 저장합니다. 한글은 `ensure_ascii=False`로 직렬화해 읽을 수 있는 원문 그대로 저장합니다.
