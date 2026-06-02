import unittest
from pathlib import Path
from unittest.mock import patch

from codex_client import CodexLLMClient


class CodexLLMClientTest(unittest.TestCase):
    def test_generate_text_delegates_to_codex_exec(self):
        with patch("codex_client.run_codex_exec_last_message", return_value=(0, "result")) as run:
            client = CodexLLMClient(sandbox="read-only", ignore_user_config=True)

            result = client.generate_text(
                prompt="prompt",
                output_dir=Path("output"),
                model="gpt-test",
                reasoning_effort="low",
            )

        self.assertEqual(result, "result")
        run.assert_called_once_with(
            prompt="prompt",
            output_dir=Path("output"),
            model="gpt-test",
            sandbox="read-only",
            reasoning_effort="low",
            ignore_user_config=True,
        )

    def test_generate_text_raises_when_codex_exec_fails(self):
        with patch("codex_client.run_codex_exec_last_message", return_value=(2, "failure")):
            client = CodexLLMClient()

            with self.assertRaisesRegex(RuntimeError, "exit_code=2"):
                client.generate_text(prompt="prompt", output_dir=Path("output"))


if __name__ == "__main__":
    unittest.main()
