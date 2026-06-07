from dataclasses import dataclass


GEMINI_LLM_PROVIDER = "gemini"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"


@dataclass(frozen=True)
class GeminiLLMClient:
    """설명: Gemini API를 사용하는 LLM provider 클라이언트입니다."""

    api_key: str | None = None
    response_json_schema: dict | None = None
    response_mime_type: str = "application/json"
    provider: str = GEMINI_LLM_PROVIDER
    default_model: str | None = None

    def generate_text(
        self,
        prompt,
        output_dir=None,
        model=None,
        reasoning_effort=None,
        response_json_schema=None,
        response_mime_type=None,
    ):
        """설명: Gemini API에 프롬프트를 전달하고 응답 텍스트를 반환합니다.
        입력: prompt는 LLM 요청 문자열, output_dir은 인터페이스 호환용 값, model은 사용할 모델명, reasoning_effort는 현재 Gemini 호출에서는 사용하지 않는 값입니다.
        출력: Gemini가 생성한 응답 문자열을 반환하고, 빈 응답이면 RuntimeError를 발생시킵니다.
        """
        selected_model = model or self.default_model
        if not selected_model:
            raise ValueError("LLM model is required.")

        output = run_gemini_generate_content(
            prompt=prompt,
            model=selected_model,
            api_key=self.api_key,
            response_json_schema=(
                response_json_schema
                if response_json_schema is not None
                else self.response_json_schema
            ),
            response_mime_type=(
                response_mime_type
                if response_mime_type is not None
                else self.response_mime_type
            ),
        )
        if not output:
            raise RuntimeError(f"{self.provider} LLM call returned an empty response.")
        return output


def build_gemini_generation_config(response_json_schema=None, response_mime_type="application/json"):
    """설명: google-genai generate_content 호출에 사용할 config 딕셔너리를 구성합니다.
    입력: response_json_schema는 JSON 응답 검증 스키마, response_mime_type은 요청할 응답 MIME 타입입니다.
    출력: generate_content에 전달할 config 딕셔너리를 반환합니다.
    """
    config = {}
    if response_mime_type:
        config["response_mime_type"] = response_mime_type
    if response_json_schema:
        config["response_json_schema"] = response_json_schema
    return config


def create_gemini_client(api_key=None):
    """설명: google-genai 클라이언트 객체를 생성합니다.
    입력: api_key는 선택 API 키이며, 없으면 SDK 기본 인증 설정을 사용합니다.
    출력: genai.Client 인스턴스를 반환하고, 패키지가 없으면 RuntimeError를 발생시킵니다.
    """
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "Gemini LLM provider requires the google-genai package. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from exc

    if api_key:
        return genai.Client(api_key=api_key)
    return genai.Client()


def run_gemini_generate_content(
    prompt,
    model,
    api_key=None,
    response_json_schema=None,
    response_mime_type="application/json",
):
    """설명: Gemini generate_content를 실행하고 응답 텍스트를 추출합니다.
    입력: prompt는 요청 문자열, model은 Gemini 모델명, api_key는 선택 API 키, response_json_schema는 JSON 응답 스키마, response_mime_type은 응답 MIME 타입입니다.
    출력: 앞뒤 공백이 제거된 Gemini 응답 문자열을 반환합니다.
    """
    client = create_gemini_client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=build_gemini_generation_config(
            response_json_schema=response_json_schema,
            response_mime_type=response_mime_type,
        ),
    )
    return getattr(response, "text", "").strip()
