"""Porta única de acesso à API Groq, com timeout, retry e logs sem conteúdo."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import partial
from typing import Any

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    Groq,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
)
from tenacity.wait import wait_base, wait_exponential

from src.config import ConfigurationError, Settings, get_settings

logger = logging.getLogger(__name__)
RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


class GroqClientError(RuntimeError):
    """Erro de API com mensagem segura para exibição na interface."""


class _WaitRetryAfter(wait_base):
    """Prioriza Retry-After; sem o cabeçalho, usa espera exponencial."""

    def __init__(self) -> None:
        self._fallback = wait_exponential(multiplier=1, min=1, max=30)

    def __call__(self, retry_state: RetryCallState) -> float:
        error = retry_state.outcome.exception() if retry_state.outcome else None
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", {})
        retry_after = headers.get("retry-after") if headers else None
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        return self._fallback(retry_state)


def _log_retry(
    retry_state: RetryCallState,
    *,
    call_id: int,
    max_attempts: int,
) -> None:
    error = retry_state.outcome.exception() if retry_state.outcome else None
    sleep_seconds = retry_state.next_action.sleep if retry_state.next_action else 0
    logger.warning(
        "Retry Groq; chamada=%d; tentativa=%d/%d; próxima=%d/%d; espera=%.1fs; erro=%s",
        call_id,
        retry_state.attempt_number,
        max_attempts,
        retry_state.attempt_number + 1,
        max_attempts,
        sleep_seconds,
        type(error).__name__ if error else "desconhecido",
    )


class GroqGateway:
    """Cliente compartilhado pela aplicação para chamadas de chat da Groq."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        # Instanciar o SDK fica restrito a este módulo.
        self._client = client or Groq(
            api_key=settings.groq_api_key,
            timeout=settings.timeout_seconds,
            max_retries=0,
        )
        self._calls = 0
        self._logical_calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0

    @property
    def usage(self) -> dict[str, int]:
        """Retorna contadores agregados apropriados para a interface."""
        return {
            "calls": self._calls,
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
        }

    def _request(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        response_format: dict[str, str] | None,
    ) -> Any:
        self._calls += 1
        parameters: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format is not None:
            parameters["response_format"] = response_format
        return self._client.chat.completions.create(**parameters)

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        agent: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
    ) -> str:
        """Executa uma chamada e retorna somente conteúdo não vazio do assistente."""
        started_at = time.perf_counter()
        self._logical_calls += 1
        call_id = self._logical_calls
        attempts_before_call = self._calls
        retrying = Retrying(
            retry=retry_if_exception_type(RETRYABLE_ERRORS),
            stop=stop_after_attempt(self._settings.max_retries),
            wait=_WaitRetryAfter(),
            before_sleep=partial(
                _log_retry,
                call_id=call_id,
                max_attempts=self._settings.max_retries,
            ),
            reraise=True,
        )
        try:
            response = retrying(
                self._request,
                model=model,
                messages=messages,
                max_tokens=(
                    self._settings.max_tokens if max_tokens is None else max_tokens
                ),
                temperature=self._settings.temperature if temperature is None else temperature,
                response_format=response_format,
            )
        except AuthenticationError:
            raise GroqClientError(
                "A Groq recusou a autenticação. Verifique a configuração local da API."
            ) from None
        except RateLimitError:
            logger.error(
                "Groq rate limit persistiu; chamada=%d; tentativas=%d",
                call_id,
                self._settings.max_retries,
            )
            raise GroqClientError(
                "Limite de uso da Groq atingido. Aguarde alguns instantes e tente novamente."
            ) from None
        except (APITimeoutError, APIConnectionError):
            raise GroqClientError(
                "Não foi possível obter resposta da Groq por timeout ou falha de conexão."
            ) from None
        except InternalServerError:
            raise GroqClientError(
                "A Groq está temporariamente indisponível. Tente novamente mais tarde."
            ) from None
        except APIStatusError as error:
            raise GroqClientError(
                f"A Groq retornou um erro de serviço (HTTP {error.status_code})."
            ) from None

        choices = getattr(response, "choices", None) or []
        content = choices[0].message.content if choices else None
        if not isinstance(content, str) or not content.strip():
            raise GroqClientError("A Groq retornou uma resposta vazia. Tente novamente.")

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        logger.info(
            "Chamada Groq concluída; chamada=%d; tentativas=%d; agente=%s; modelo=%s; "
            "prompt_tokens=%s; completion_tokens=%s; duracao_ms=%d",
            call_id,
            self._calls - attempts_before_call,
            agent,
            model,
            prompt_tokens,
            completion_tokens,
            int((time.perf_counter() - started_at) * 1000),
        )
        return content.strip()


def ping() -> str:
    """Testa conectividade com uma mensagem neutra e curta."""
    settings = get_settings(provider="groq")
    gateway = GroqGateway(settings)
    return gateway.complete(
        model=settings.model_fast,
        messages=[
            {"role": "system", "content": "Responda apenas com a palavra OK."},
            {"role": "user", "content": "Teste de conectividade."},
        ],
        agent="connection_test",
        max_tokens=64,
        temperature=0,
    )


def get_gateway(settings: Settings | None = None) -> GroqGateway:
    """Obtém o gateway central, sem permitir inicialização do SDK em outros módulos."""
    return GroqGateway(settings or get_settings(provider="groq"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Cliente central da API Groq")
    parser.add_argument("--ping", action="store_true", help="testa a conexão com a Groq")
    arguments = parser.parse_args()
    if not arguments.ping:
        parser.print_help()
        return 0

    try:
        answer = ping()
    except (ConfigurationError, GroqClientError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1
    print(f"Conexão Groq OK: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
