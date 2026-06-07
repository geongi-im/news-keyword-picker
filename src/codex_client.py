from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import tempfile


CODEX_LLM_PROVIDER = "codex"
CODEX_BIN = "codex"
CODEX_KEYWORD_SANDBOX = "read-only"


@dataclass(frozen=True)
class CodexLLMClient:
    """설명: Codex CLI를 사용하는 LLM provider 클라이언트입니다."""

    sandbox: str = CODEX_KEYWORD_SANDBOX
    ignore_user_config: bool = True
    provider: str = CODEX_LLM_PROVIDER
    default_model: str | None = None

    def generate_text(
        self,
        prompt,
        output_dir,
        model=None,
        reasoning_effort=None,
        response_json_schema=None,
        response_mime_type=None,
    ):
        """설명: Codex CLI에 프롬프트를 전달하고 마지막 응답 텍스트를 반환합니다.
        입력: prompt는 LLM 요청 문자열, output_dir은 임시 출력 디렉터리, model은 사용할 모델명, reasoning_effort는 추론 강도입니다.
        출력: Codex가 생성한 응답 문자열을 반환하고, 실행 실패 시 RuntimeError를 발생시킵니다.
        """
        selected_model = model or self.default_model
        if not selected_model:
            raise ValueError("LLM model is required.")

        exit_code, output = run_codex_exec_last_message(
            prompt=prompt,
            output_dir=Path(output_dir),
            model=selected_model,
            sandbox=self.sandbox,
            reasoning_effort=reasoning_effort,
            ignore_user_config=self.ignore_user_config,
        )
        if exit_code != 0:
            raise RuntimeError(f"{self.provider} LLM call failed. exit_code={exit_code}")
        return output


def build_codex_exec_command(
    output_dir,
    model=None,
    output_last_message=None,
    sandbox=CODEX_KEYWORD_SANDBOX,
    reasoning_effort=None,
    ignore_user_config=False,
):
    """설명: codex exec 실행에 사용할 명령 인자 리스트를 구성합니다.
    입력: output_dir은 작업 디렉터리, model은 Codex 모델명, output_last_message는 마지막 응답 저장 파일, sandbox는 샌드박스 모드, reasoning_effort는 추론 강도, ignore_user_config는 사용자 설정 무시 여부입니다.
    출력: subprocess.run에 전달할 명령 리스트를 반환합니다.
    """
    cmd = [
        *resolve_codex_command(),
        "exec",
    ]
    if model:
        cmd.extend(["--model", model])
    if reasoning_effort:
        cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    if ignore_user_config:
        cmd.append("--ignore-user-config")
    cmd.extend(
        [
            "--sandbox",
            sandbox,
            "--cd",
            str(output_dir),
            "--skip-git-repo-check",
        ]
    )
    if output_last_message:
        cmd.extend(["--output-last-message", str(output_last_message)])
    cmd.append("-")
    return cmd


def run_codex_exec_last_message(
    prompt,
    output_dir,
    model=None,
    sandbox=CODEX_KEYWORD_SANDBOX,
    reasoning_effort=None,
    ignore_user_config=False,
):
    """설명: codex exec를 실행하고 --output-last-message 파일 내용을 읽습니다.
    입력: prompt는 표준 입력으로 보낼 요청, output_dir은 임시 파일 디렉터리, model/sandbox/reasoning_effort/ignore_user_config는 Codex 실행 옵션입니다.
    출력: Codex 종료 코드와 마지막 메시지 문자열의 튜플을 반환합니다.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, output_name = tempfile.mkstemp(prefix="codex_last_message_", suffix=".txt", dir=output_dir)
    os.close(fd)
    output_path = Path(output_name)

    try:
        cmd = build_codex_exec_command(
            output_dir,
            model=model,
            output_last_message=output_path,
            sandbox=sandbox,
            reasoning_effort=reasoning_effort,
            ignore_user_config=ignore_user_config,
        )
        completed = subprocess.run(
            cmd,
            check=False,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        message = ""
        if output_path.exists():
            message = output_path.read_text(encoding="utf-8").strip()
        if completed.returncode != 0 and not message:
            message = "\n".join(
                text.strip()
                for text in (completed.stdout, completed.stderr)
                if text and text.strip()
            )
        return completed.returncode, message
    finally:
        if output_path.exists():
            output_path.unlink()


def resolve_codex_command():
    """설명: 현재 운영체제에서 실행 가능한 Codex CLI 명령 경로를 찾습니다.
    입력: 별도 입력 없이 PATH와 Windows 실행 별칭을 확인합니다.
    출력: subprocess에 전달할 Codex 실행 명령 리스트를 반환합니다.
    """
    if os.name == "nt":
        cmd_path = shutil.which("codex.cmd")
        if cmd_path:
            base_dir = Path(cmd_path).resolve().parent
            script_path = base_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            node_path = base_dir / "node.exe"
            if script_path.exists():
                if node_path.exists():
                    return [str(node_path), str(script_path)]

                system_node = shutil.which("node.exe") or shutil.which("node")
                if system_node:
                    return [system_node, str(script_path)]

        exe_path = shutil.which("codex.exe")
        if exe_path:
            return [exe_path]

    return [shutil.which(CODEX_BIN) or CODEX_BIN]
