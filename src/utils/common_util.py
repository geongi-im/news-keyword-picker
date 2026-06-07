import os


def validate_required_environment(required_env_names, env=None):
    """설명: 전달받은 환경변수 이름 목록만 필수 값으로 검증합니다.
    입력: required_env_names는 필수 환경변수 이름 목록, env는 환경변수 매핑이며 생략 시 os.environ을 사용합니다.
    출력: 모든 필수 값이 있으면 None을 반환하고, 누락 값이 있으면 ValueError를 발생시킵니다.
    """
    env = env if env is not None else os.environ
    missing_names = [
        name
        for name in required_env_names
        if not (env.get(name) or "").strip()
    ]
    if missing_names:
        raise ValueError(
            "Missing required environment variable(s): "
            + ", ".join(missing_names)
        )
