"""Testes locais do contador de tentativas Groq; nenhuma chamada de rede."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from groq import RateLimitError

from src.config import Settings
from src.llm.groq_client import GroqGateway


class FakeChatCompletions:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def create(self, **_: object) -> SimpleNamespace:
        self.calls += 1
        if self.calls <= self.failures:
            request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
            response = httpx.Response(429, request=request)
            raise RateLimitError("simulated rate limit", response=response, body={})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1),
        )


class FakeClient:
    def __init__(self, failures: int) -> None:
        self.completions = FakeChatCompletions(failures)
        self.chat = SimpleNamespace(completions=self.completions)


class GroqRetryTests(unittest.TestCase):
    def test_retry_log_counts_attempts_within_same_logical_call(self) -> None:
        client = FakeClient(failures=2)
        settings = Settings(
            groq_api_key="not-a-real-key",
            model_fast="fast-test",
            model_strong="strong-test",
            model_vision="vision-test",
            temperature=0,
            max_tokens=8,
            timeout_seconds=1,
            max_retries=3,
        )
        gateway = GroqGateway(settings, client=client)
        with (
            patch("src.llm.groq_client._WaitRetryAfter.__call__", return_value=0),
            self.assertLogs("src.llm.groq_client", level="WARNING") as logs,
        ):
            result = gateway.complete(
                model="fast-test",
                messages=[{"role": "user", "content": "ping"}],
                agent="test",
                max_tokens=8,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(client.completions.calls, 3)
        self.assertEqual(gateway.usage["calls"], 3)
        retry_lines = [line for line in logs.output if "Retry Groq" in line]
        self.assertEqual(len(retry_lines), 2)
        self.assertIn("chamada=1; tentativa=1/3; próxima=2/3", retry_lines[0])
        self.assertIn("chamada=1; tentativa=2/3; próxima=3/3", retry_lines[1])


if __name__ == "__main__":
    unittest.main()
