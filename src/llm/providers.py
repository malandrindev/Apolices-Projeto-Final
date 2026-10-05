"""Small provider contract; pipeline remains independent of the inference SDK."""
from __future__ import annotations
from typing import Any, Callable, Protocol
from src.config import Settings, ConfigurationError, get_settings
from src.llm.groq_client import GroqGateway
from src.llm.resilience import LLMClientError, classify_error, retry_after_seconds, emit_event, cached_tokens_from_usage, response_metadata
import time, os
from dataclasses import replace

class LLMProvider(Protocol):
    provider: str
    @property
    def usage(self) -> dict[str, int | None]: ...
    def complete(self, *, model: str, messages: list[dict[str, str]], agent: str, max_tokens: int | None = None, temperature: float | None = None, response_format: dict[str, Any] | None = None) -> str: ...

class GroqProvider(GroqGateway):
    provider = "groq"
    def __init__(self, settings: Settings, client: Any = None, event_callback: Callable | None = None):
        super().__init__(settings, client)
        self.events: list[dict[str, Any]] = []
        self.event_callback = event_callback
        self._stage = ""
        self._attempt = 0
        self._cached_tokens: int | None = 0
    @property
    def usage(self) -> dict[str, int | None]:
        return {**super().usage, "cached_tokens": self._cached_tokens}
    def complete(self, **kwargs: Any) -> str:
        self._stage = kwargs["agent"]
        self._attempt = 0
        return super().complete(**kwargs)
    def _request(self, **kwargs: Any) -> Any:
        self._attempt += 1
        started = time.perf_counter()
        try:
            response = super()._request(**kwargs)
        except Exception as error:
            self._cached_tokens = None
            kind = classify_error(error)
            delay = retry_after_seconds(error)
            emit_event(self, kwargs["model"], self._stage, started, self._attempt, "error", kind=kind)
            if kind in {"quota", "credit", "TPD", "context_length", "authentication", "permission"} or (delay is not None and delay > self._settings.max_retry_wait_seconds):
                raise LLMClientError(self.provider, kind, retry_after=delay) from None
            raise
        usage = getattr(response, "usage", None)
        cached_tokens = cached_tokens_from_usage(usage, "prompt_tokens_details")
        self._cached_tokens = None if cached_tokens is None or self._cached_tokens is None else self._cached_tokens + cached_tokens
        emit_event(self, kwargs["model"], self._stage, started, self._attempt, "completed", input_tokens=getattr(usage,"prompt_tokens",0) or 0, output_tokens=getattr(usage,"completion_tokens",0) or 0,cached_tokens=cached_tokens,**response_metadata(response))
        return response

def get_gateway(settings: Settings | None = None, *, client: Any = None,
                event_callback: Callable | None = None, routing_policy: Any = None,
                request_control: Any = None) -> LLMProvider:
    effective = settings or get_settings()
    explicit_routing = routing_policy is not None or request_control is not None
    enabled = os.getenv("MODEL_ROUTING_ENABLED", "false").strip().lower()
    if enabled not in {"true", "false", "1", "0", "yes", "no"}:
        raise ConfigurationError("MODEL_ROUTING_ENABLED must be a boolean.")
    if effective.llm_provider == "groq":
        if explicit_routing:
            raise ConfigurationError("This model routing profile requires the OpenAI provider.")
        return GroqProvider(effective, client=client, event_callback=event_callback)
    from src.llm.openai_client import OpenAIProvider
    if explicit_routing or enabled in {"true", "1", "yes"}:
        from src.llm.model_routing import ModelRoutingPolicy, RequestControl, RoutedGateway
        policy = routing_policy or ModelRoutingPolicy.from_env()
        control = request_control or RequestControl()
        bounded = replace(effective, max_retries=1, timeout_seconds=min(60, effective.timeout_seconds))
        raw = OpenAIProvider(bounded, client=client, event_callback=event_callback, local_json_repair=True)
        return RoutedGateway(raw, policy=policy, control=control)
    return OpenAIProvider(effective, client=client, event_callback=event_callback)
