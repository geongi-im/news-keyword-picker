import os
from dataclasses import dataclass
from typing import Mapping, Protocol

from codex_client import CODEX_LLM_PROVIDER, CodexLLMClient, DEFAULT_CODEX_MODEL
from gemini_client import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_API_KEY_ENV,
    GEMINI_LLM_PROVIDER,
    GeminiLLMClient,
)


DEFAULT_LLM_PROVIDER = CODEX_LLM_PROVIDER
LLM_PROVIDER_ENV = "LLM_PROVIDER"
SUPPORTED_LLM_PROVIDERS = (CODEX_LLM_PROVIDER, GEMINI_LLM_PROVIDER)
DEFAULT_REASONING_EFFORT_ATTEMPTS = ("low", "medium")
NEWS_KEYWORD_RESPONSE_JSON_SCHEMA = {
    "type": "array",
    "minItems": 5,
    "maxItems": 5,
    "items": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "source_url": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["keyword", "source_url", "reason"],
    },
}


class LLMProviderClient(Protocol):
    """설명: provider별 LLM 클라이언트가 따라야 하는 호출 규약입니다."""

    provider: str
    default_model: str

    def generate_text(
        self,
        prompt,
        output_dir,
        model=None,
        reasoning_effort=None,
        response_json_schema=None,
        response_mime_type=None,
    ):
        """설명: provider별 LLM에 프롬프트를 전달하고 텍스트 응답을 생성합니다.
        입력: prompt는 LLM 요청 문자열, output_dir은 임시 출력 디렉터리, model은 사용할 모델명, reasoning_effort는 추론 강도입니다.
        출력: provider가 생성한 응답 문자열을 반환합니다.
        """
        ...


@dataclass(frozen=True)
class LLMClient:
    """설명: 선택된 provider 클라이언트로 LLM 호출을 위임하는 메인 클라이언트입니다."""

    provider: str
    clients: Mapping[str, LLMProviderClient]

    @property
    def selected_client(self):
        """설명: 현재 선택된 provider의 실제 클라이언트를 조회합니다.
        입력: 별도 입력 없이 self.provider와 self.clients를 사용합니다.
        출력: provider 이름에 대응하는 LLM provider 클라이언트 객체를 반환합니다.
        """
        return self.clients[self.provider]

    @property
    def default_model(self):
        """설명: 현재 선택된 provider의 기본 모델명을 조회합니다.
        입력: 별도 입력 없이 selected_client의 설정을 사용합니다.
        출력: 기본 모델명 문자열을 반환합니다.
        """
        return self.selected_client.default_model

    def generate_text(
        self,
        prompt,
        output_dir,
        model=None,
        reasoning_effort=None,
        response_json_schema=None,
        response_mime_type=None,
    ):
        """설명: 메인 LLM 클라이언트 호출을 현재 선택된 provider 클라이언트로 전달합니다.
        입력: prompt는 LLM 요청 문자열, output_dir은 임시 출력 디렉터리, model은 사용할 모델명, reasoning_effort는 추론 강도입니다.
        출력: 선택된 provider가 생성한 응답 문자열을 반환합니다.
        """
        kwargs = {
            "prompt": prompt,
            "output_dir": output_dir,
            "model": model,
            "reasoning_effort": reasoning_effort,
        }
        if response_json_schema is not None:
            kwargs["response_json_schema"] = response_json_schema
        if response_mime_type is not None:
            kwargs["response_mime_type"] = response_mime_type
        return self.selected_client.generate_text(**kwargs)


def normalize_llm_provider(provider):
    """설명: provider 이름을 정규화하고 지원 여부를 검증합니다.
    입력: provider는 사용자 입력 또는 환경변수에서 읽은 provider 이름입니다.
    출력: 정규화된 provider 이름을 반환하고, 미지원 값이면 ValueError를 발생시킵니다.
    """
    normalized_provider = (provider or DEFAULT_LLM_PROVIDER).strip().lower()
    if normalized_provider in SUPPORTED_LLM_PROVIDERS:
        return normalized_provider

    supported = ", ".join(SUPPORTED_LLM_PROVIDERS)
    raise ValueError(f"Unsupported LLM provider: {provider}. supported={supported}")


def resolve_llm_provider(provider=None, env=None):
    """설명: CLI 인자, 환경변수, 기본값 순서로 사용할 LLM provider를 결정합니다.
    입력: provider는 명시적으로 전달된 provider 이름, env는 환경변수 매핑이며 생략 시 os.environ을 사용합니다.
    출력: 정규화된 provider 이름을 반환합니다.
    """
    env = env if env is not None else os.environ
    return normalize_llm_provider(provider or env.get(LLM_PROVIDER_ENV) or DEFAULT_LLM_PROVIDER)


def create_llm_clients(env=None):
    """설명: 지원하는 모든 하위 LLM provider 클라이언트를 생성합니다.
    입력: env는 환경변수 매핑이며 Gemini API 키 등을 읽는 데 사용하고, 생략 시 os.environ을 사용합니다.
    출력: provider 이름을 키로 갖는 LLM provider 클라이언트 딕셔너리를 반환합니다.
    """
    env = env if env is not None else os.environ
    return {
        CODEX_LLM_PROVIDER: CodexLLMClient(),
        GEMINI_LLM_PROVIDER: GeminiLLMClient(
            api_key=env.get(GEMINI_API_KEY_ENV),
            response_json_schema=NEWS_KEYWORD_RESPONSE_JSON_SCHEMA,
        ),
    }


def create_llm_client(provider=None, env=None):
    """설명: 환경변수 또는 인자로 선택된 provider를 사용하는 메인 LLMClient를 생성합니다.
    입력: provider는 환경변수를 덮어쓸 provider 이름, env는 환경변수 매핑이며 생략 시 os.environ을 사용합니다.
    출력: 선택된 provider로 라우팅하는 LLMClient 객체를 반환합니다.
    """
    env = env if env is not None else os.environ
    selected_provider = resolve_llm_provider(provider=provider, env=env)
    return LLMClient(provider=selected_provider, clients=create_llm_clients(env=env))
