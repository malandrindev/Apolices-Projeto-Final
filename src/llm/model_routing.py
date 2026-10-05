"""Opt-in model governance over the existing provider; never constructs an SDK.

A persisted reservation precedes every provider dispatch. Providers must perform
one HTTP attempt per dispatch (SDK retries disabled). Semantic promotion is an
explicit caller decision after local retrieval, distinct from technical fallback.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import statistics
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Mapping

from src.config import ConfigurationError
from src.llm.resilience import (
    LLMClientError, classify_error, repair_json_locally, response_metadata,
)
from src.schemas.policy import PolicyExtraction

ROUTING_VERSION = "model-routing-v2-provider-breaker"
MAX_SAME_MODEL_RETRIES = 0
MAX_MODEL_ATTEMPTS_PER_LOGICAL_STEP = 2
MAX_SEMANTIC_ESCALATIONS_PER_FIELD = 2
MAX_FALLBACK_DEPTH = 2
TECHNICAL_FALLBACK_KINDS = frozenset({
    "timeout", "connection", "RPM", "TPM", "rate_limit", "provider_error", "response_incomplete",
    "response_empty", "invalid_response", "schema_invalid", "model_unavailable",
})
PROVIDER_TRANSPORT_KINDS = frozenset({
    "timeout", "connection", "RPM", "TPM", "rate_limit", "provider_error", "model_unavailable", "TPD",
})
MODEL_OUTPUT_CONTRACT_KINDS = frozenset({
    "response_incomplete", "response_empty", "invalid_response", "schema_invalid",
})
SEMANTIC_FAILURE_STATUSES = frozenset({"EVIDENCE_INVALID", "AMBIGUOUS", "SEMANTIC_INCOMPLETE", "SEMANTIC_ESCALATION_REQUIRED", "SEMANTIC_QUALITY_FAILURE"})

def failure_category(kind: str | None = None, validation_status: str | None = None) -> str | None:
    if kind in PROVIDER_TRANSPORT_KINDS:
        return "PROVIDER_TRANSPORT_FAILURE"
    if kind in MODEL_OUTPUT_CONTRACT_KINDS:
        return "MODEL_OUTPUT_CONTRACT_FAILURE"
    if validation_status in SEMANTIC_FAILURE_STATUSES:
        return "SEMANTIC_QUALITY_FAILURE"
    return None

FATAL_KINDS = frozenset({"authentication", "permission", "quota", "credit", "TPD"})
VALIDATION_STATUSES = frozenset({
    "NOT_CHECKED", "VALID", "SCHEMA_INVALID", "EVIDENCE_INVALID", "AMBIGUOUS",
    "NOT_RETRIEVED", "NOT_FOUND", "FOUND", "TECHNICAL_UNAVAILABLE",
    "SEMANTIC_INCOMPLETE", "SEMANTIC_ESCALATION_REQUIRED", "SEMANTIC_QUALITY_FAILURE",
})
FIELD_STATUSES = frozenset({"FOUND", "NOT_FOUND", "NOT_RETRIEVED", "AMBIGUOUS",
                            "TECHNICAL_UNAVAILABLE"})
_FIELDS = frozenset(PolicyExtraction.model_fields)
_ROLES = ("extraction", "interpretation", "comparison", "verifier", "auxiliary")


def _identifier(value: Any, *, maximum: int = 200) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) is None
            or "sk-" in value.lower() or "gsk_" in value.lower()):
        raise ConfigurationError("Routing identifiers must be bounded and must not contain secrets.")
    return value


def _field_names(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ConfigurationError("Routing fields must be known policy field identifiers.")
    if any(not isinstance(value, str) or value not in _FIELDS for value in values):
        raise ConfigurationError("Routing fields must be known policy field identifiers.")
    result = tuple(dict.fromkeys(values))
    return result


@dataclass(frozen=True)
class RoleRoute:
    primary: str
    technical_fallbacks: tuple[str, ...] = ()
    escalations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.primary)
        object.__setattr__(self, "technical_fallbacks", tuple(self.technical_fallbacks))
        object.__setattr__(self, "escalations", tuple(self.escalations))
        for value in self.technical_fallbacks + self.escalations:
            _identifier(value)
        if len(set((self.primary,) + self.technical_fallbacks)) != 1 + len(self.technical_fallbacks):
            raise ConfigurationError("Technical fallback routes must not repeat a model.")
        if len(set((self.primary,) + self.escalations)) != 1 + len(self.escalations):
            raise ConfigurationError("Semantic escalation routes must not repeat a model.")
        if len(self.technical_fallbacks) > MAX_FALLBACK_DEPTH or len(self.escalations) > 2:
            raise ConfigurationError("Routing depth exceeds the hard maximum.")

    @property
    def fallbacks(self) -> tuple[str, ...]:
        return self.technical_fallbacks


def _default_routes() -> dict[str, RoleRoute]:
    return {
        "extraction": RoleRoute("gpt-5.6-luna", ("gpt-5.4-mini-2026-03-17",), ("gpt-5.6-terra",)),
        "interpretation": RoleRoute("gpt-5.6-terra", ("gpt-5.4-2026-03-05",), ("gpt-5.6-sol",)),
        "comparison": RoleRoute("gpt-5.6-terra", ("gpt-5.4-2026-03-05",), ("gpt-5.6-sol",)),
        "verifier": RoleRoute("gpt-5.6-sol", ("gpt-5.6-terra", "gpt-5.2-2025-12-11")),
        "auxiliary": RoleRoute("gpt-5.4-mini-2026-03-17", ("gpt-4.1-mini-2025-04-14",)),
    }


class ModelRoutingPolicy:
    """Configuration owns model identifiers; business agents only name roles."""
    def __init__(self, routes: Mapping[str, RoleRoute] | None = None) -> None:
        configured = dict(_default_routes() if routes is None else routes)
        if set(configured) != set(_ROLES) or any(not isinstance(r, RoleRoute) for r in configured.values()):
            raise ConfigurationError("Routing policy must define all five roles.")
        self.routes = MappingProxyType(configured)

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> ModelRoutingPolicy:
        environment = os.environ if environment is None else environment
        routes = {}
        for role, default in _default_routes().items():
            prefix = "MODEL_ROUTING_" + role.upper() + "_"
            primary = environment.get(prefix + "PRIMARY", default.primary).strip() or default.primary
            def listed(suffix: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
                raw = environment.get(prefix + suffix)
                if raw is None:
                    return fallback
                return tuple(value.strip() for value in raw.split(",") if value.strip())
            routes[role] = RoleRoute(primary, listed("TECHNICAL_FALLBACKS", default.technical_fallbacks),
                                     listed("ESCALATIONS", default.escalations))
        return cls(routes)

    def role(self, role: str) -> RoleRoute:
        canonical = "verifier" if role in {"verification", "verifier"} else role
        if canonical not in self.routes:
            raise ConfigurationError("Unknown model routing role.")
        return self.routes[canonical]

    @property
    def routing_signature(self) -> dict[str, Any]:
        return {"version": ROUTING_VERSION,
                "limits": {"same_model_retries": 0, "model_attempts_per_step": 2,
                           "semantic_escalations_per_field": 2, "fallback_depth": 2},
                "roles": {role: {"primary": route.primary,
                                 "technical_fallbacks": list(route.technical_fallbacks),
                                 "escalations": list(route.escalations)}
                          for role, route in self.routes.items()}}


@dataclass(frozen=True)
class ValidationOutcome:
    status: str = "VALID"
    fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in VALIDATION_STATUSES:
            raise ConfigurationError("Unknown safe validation status.")
        object.__setattr__(self, "fields", _field_names(self.fields))


def _validation(value: Any, fields: tuple[str, ...]) -> ValidationOutcome:
    if isinstance(value, ValidationOutcome):
        return value
    if isinstance(value, bool):
        return ValidationOutcome("VALID" if value else "EVIDENCE_INVALID", fields)
    if isinstance(value, str):
        return ValidationOutcome(value, fields)
    if isinstance(value, dict):
        return ValidationOutcome(value.get("status", "EVIDENCE_INVALID"),
                                 value.get("fields", fields))
    raise ConfigurationError("Validator must return a safe validation outcome.")


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _context(context: Mapping[str, Any], role: str) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ConfigurationError("Routed calls require bounded document/step context.")
    result = {
        "document_id": _identifier(context.get("document_id"), maximum=128),
        "logical_step_id": _identifier(context.get("logical_step_id"), maximum=240),
        "field_group": _identifier(context.get("field_group", role), maximum=80),
        "fields": list(_field_names(context.get("fields", ()))),
    }
    stage = context.get("retrieval_stage", 0)
    if type(stage) is not int or not 0 <= stage <= 4:
        raise ConfigurationError("Retrieval stage must be between zero and four.")
    result["retrieval_stage"] = stage
    for name in ("candidate_count", "page_count_used"):
        count = _count(context.get(name, 0))
        if count is None:
            raise ConfigurationError("Routing counts must be nonnegative integers.")
        result[name] = count
    return result


class RequestControl:
    """Thread-safe durable budget and deterministic rolling circuit breaker."""
    def __init__(self, max_http_attempts: int = 50,
                 persist_callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        if type(max_http_attempts) is not int or not 1 <= max_http_attempts <= 50:
            raise ConfigurationError("HTTP request budget must be between one and fifty.")
        self.max_http_attempts = max_http_attempts
        self.persist_callback = persist_callback
        self.events: list[dict[str, Any]] = []
        self.http_attempts = 0
        self.blocked_reason: str | None = None
        self._lock = threading.RLock()
        self._history: deque[bool] = deque(maxlen=10)
        self._consecutive = self._technical_failures = 0
        self._step_counts: Counter[tuple[str, str]] = Counter()
        self._step_models: dict[tuple[str, str], set[str]] = {}
        self._fingerprints: set[str] = set()
        self._semantic_counts: Counter[tuple[str, str]] = Counter()
        self._semantic_promotions: set[tuple[str, str, str, int, str]] = set()
        self.limit_diagnostics: list[dict[str, Any]] = []

    @property
    def remaining(self) -> int:
        with self._lock:
            return self.max_http_attempts - self.http_attempts

    @property
    def budget_remaining(self) -> int:
        return self.remaining

    def _persist(self) -> None:
        if self.persist_callback is not None:
            try:
                self.persist_callback(self.snapshot())
            except Exception:
                self.blocked_reason = "persistence_error"
                raise LLMClientError("routing", "persistence_error") from None

    def _blocked(self) -> None:
        if self.blocked_reason is not None:
            kind = self.blocked_reason if self.blocked_reason in {
                "persistence_error", "circuit_open", "budget_exhausted",
                "authentication", "permission", "quota", "credit", "TPD",
            } else "routing_limit"
            raise LLMClientError("routing", kind, limit_reason=self.blocked_reason)

    def note_limit(self, context: Mapping[str, Any], reason: str) -> None:
        with self._lock:
            self.limit_diagnostics.append({
                "document_id": context["document_id"],
                "logical_step_id": context["logical_step_id"], "reason": reason})
            self._persist()

    def reserve(self, event: dict[str, Any], fingerprint: str, *,
                semantic_fields: tuple[str, ...] = ()) -> int:
        with self._lock:
            self._blocked()
            if self.http_attempts >= self.max_http_attempts:
                self.blocked_reason = "budget_exhausted"
                self._persist()
                raise LLMClientError("routing", "budget_exhausted")
            key = (event["document_id"], event["logical_step_id"])
            if self._step_counts[key] >= MAX_MODEL_ATTEMPTS_PER_LOGICAL_STEP:
                self.note_limit(event, "max_model_attempts_per_logical_step")
                raise LLMClientError("routing", "routing_limit",
                                     limit_reason="max_model_attempts_per_logical_step")
            if event["requested_model"] in self._step_models.get(key, set()):
                self.note_limit(event, "same_model_retry_disabled")
                raise LLMClientError("routing", "routing_limit", limit_reason="same_model_retry_disabled")
            if fingerprint in self._fingerprints:
                self.note_limit(event, "identical_same_model_request_disabled")
                raise LLMClientError("routing", "routing_limit",
                                     limit_reason="identical_same_model_request_disabled")
            promotions = {(event["document_id"], name, event["role"],
                           event["semantic_level"], event["requested_model"])
                          for name in semantic_fields} - self._semantic_promotions
            if any(self._semantic_counts[(document_id, name)] >= 2
                   for document_id, name, _, _, _ in promotions):
                self.note_limit(event, "max_semantic_escalations_per_field")
                raise LLMClientError("routing", "routing_limit",
                                     limit_reason="max_semantic_escalations_per_field")
            self.http_attempts += 1
            self._step_counts[key] += 1
            self._step_models.setdefault(key, set()).add(event["requested_model"])
            self._fingerprints.add(fingerprint)
            self._semantic_counts.update((document_id, name)
                                         for document_id, name, _, _, _ in promotions)
            self._semantic_promotions.update(promotions)
            event["semantic_promotion_new"] = bool(promotions)
            self.events.append(copy.deepcopy(event))
            # ATTEMPTED is persisted before entering the provider. An interrupted
            # in-flight request remains unknown and may never be repeated blindly.
            self._persist()
            return len(self.events) - 1

    def finish(self, index: int, update: dict[str, Any], *, technical_failure: bool,
               fatal_kind: str | None = None) -> None:
        with self._lock:
            self.events[index].update(update)
            # Never let a caller's broad technical-error boolean promote a model
            # contract or semantic failure into a provider outage.
            kind = self.events[index].get("error_kind")
            category = failure_category(kind, self.events[index].get("evidence_validation_status"))
            self.events[index]["failure_category"] = category
            technical_failure = kind in PROVIDER_TRANSPORT_KINDS
            self._history.append(technical_failure)
            self._technical_failures += int(technical_failure)
            self._consecutive = self._consecutive + 1 if technical_failure else 0
            if fatal_kind is not None:
                self.blocked_reason = fatal_kind
            elif self._consecutive >= 3 or (len(self._history) == 10 and sum(self._history) >= 2):
                self.blocked_reason = "circuit_open"
            self._persist()

    def record_validation(self, logical_step_id: str, status: str, *, fields: Any = (),
                          field_statuses: Mapping[str, Any] | None = None) -> None:
        _identifier(logical_step_id, maximum=240)
        outcome = ValidationOutcome(status, fields)
        statuses = {}
        for name, value in (field_statuses or {}).items():
            _field_names((name,))
            if value not in FIELD_STATUSES:
                raise ConfigurationError("Unknown policy field status in telemetry.")
            statuses[name] = str(value)
        with self._lock:
            for event in reversed(self.events):
                if event["logical_step_id"] != logical_step_id or event["result_status"] != "completed":
                    continue
                if outcome.fields and not set(outcome.fields) <= set(event["fields"]):
                    continue
                event["evidence_validation_status"] = outcome.status
                event["failure_category"] = failure_category(event.get("error_kind"), outcome.status)
                event["validation_fields"] = list(outcome.fields)
                event["field_statuses"] = statuses
                self._persist()
                return

    def summary(self) -> dict[str, Any]:
        with self._lock:
            by_model: dict[str, dict[str, Any]] = {}
            for event in self.events:
                model = event["requested_model"]
                aggregate = by_model.setdefault(model, {"requests": 0,
                    "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                    "known_input_tokens": 0, "known_cached_input_tokens": 0,
                    "known_output_tokens": 0, "known_total_tokens": 0,
                    "unknown_usage_requests": 0, "latency_ms": []})
                aggregate["requests"] += 1
                if event["latency_ms"] is not None:
                    aggregate["latency_ms"].append(event["latency_ms"])
                if any(event[key] is None for key in ("input_tokens", "output_tokens", "total_tokens")):
                    aggregate["unknown_usage_requests"] += 1
                for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
                    value = event[key]
                    if value is not None:
                        aggregate["known_" + key] += value
                    aggregate[key] = (aggregate[key] + value
                                      if aggregate[key] is not None and value is not None else None)
            latencies = [event["latency_ms"] for event in self.events if event["latency_ms"] is not None]
            fallback = sum(event["fallback_level"] > 0 for event in self.events)
            return {
                "http_attempts": self.http_attempts,
                "requests_by_model": {model: aggregate["requests"] for model, aggregate in by_model.items()},
                "tokens_by_requested_model": by_model,
                "fallback_count": fallback,
                "fallback_percent": fallback / self.http_attempts * 100 if self.http_attempts else 0,
                "semantic_escalation_count": sum(event.get("semantic_promotion_new", False)
                                                 for event in self.events),
                "semantic_escalation_http_attempts": sum(event["semantic_escalation"] for event in self.events),
                "sol_invocation_count": sum(event["requested_model"].startswith("gpt-5.6-sol")
                                           for event in self.events),
                "technical_failures": self._technical_failures,
                "provider_transport_failures": self._technical_failures,
                "model_output_contract_failures": sum(event.get("failure_category") == "MODEL_OUTPUT_CONTRACT_FAILURE" for event in self.events),
                "semantic_quality_failures": sum(event.get("failure_category") == "SEMANTIC_QUALITY_FAILURE" for event in self.events),
                "median_latency_ms": statistics.median(latencies) if latencies else None,
                "p95_latency_ms": sorted(latencies)[math.ceil(len(latencies) * .95) - 1]
                                   if latencies else None,
                "evidence_validation_failures": sum(event["evidence_validation_status"] == "EVIDENCE_INVALID"
                                                   for event in self.events),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "max_http_attempts": self.max_http_attempts, "http_attempts": self.http_attempts,
                "remaining": self.remaining, "budget_remaining": self.remaining,
                "blocked_reason": self.blocked_reason, "events": copy.deepcopy(self.events),
                "technical_failures": self._technical_failures,
                "provider_transport_failures": self._technical_failures,
                "model_output_contract_failures": sum(event.get("failure_category") == "MODEL_OUTPUT_CONTRACT_FAILURE" for event in self.events),
                "semantic_quality_failures": sum(event.get("failure_category") == "SEMANTIC_QUALITY_FAILURE" for event in self.events),
                "consecutive_technical_failures": self._consecutive,
                "last_10_technical_failures": list(self._history),
                "limit_diagnostics": copy.deepcopy(self.limit_diagnostics),
                "summary": self.summary(),
            }


class RoutedGateway:
    def __init__(self, gateway: Any, policy: ModelRoutingPolicy | None = None,
                 control: RequestControl | None = None) -> None:
        settings = getattr(gateway, "_settings", None)
        if settings is not None and (type(settings.max_retries) is not int or settings.max_retries != 1):
            raise ConfigurationError("Routed gateway requires provider max_retries=1 (one attempt).")
        timeout = getattr(settings, "timeout_seconds", 60)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ConfigurationError("Routed provider timeout must be finite and at most sixty seconds.")
        client = getattr(gateway, "_client", None)
        sdk_retries = getattr(client, "max_retries", None)
        if (type(sdk_retries) is int and sdk_retries != 0) or type(sdk_retries) is bool:
            raise ConfigurationError("Routed gateway requires SDK max_retries=0.")
        maximum_wait = getattr(settings, "max_retry_wait_seconds", 30)
        if (type(maximum_wait) not in (int, float) or not math.isfinite(maximum_wait)
                or maximum_wait < 0):
            raise ConfigurationError("Retry-After wait must be finite and nonnegative.")
        self.max_retry_wait_seconds = min(30, maximum_wait)
        self.gateway = gateway
        self.policy = policy or ModelRoutingPolicy.from_env()
        self.control = control or RequestControl()
        self._compatibility_steps = 0
        self._provider_lock = threading.RLock()

    @property
    def provider(self) -> str:
        return self.gateway.provider

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.control.events

    @property
    def usage(self) -> dict[str, int | None]:
        events = self.events
        def total(key: str) -> int | None:
            return sum(event[key] for event in events) if all(event[key] is not None for event in events) else None
        return {"calls": self.control.http_attempts, "prompt_tokens": total("input_tokens"),
                "completion_tokens": total("output_tokens"), "cached_tokens": total("cached_input_tokens")}

    def record_validation(self, logical_step_id: str, status: str, *, fields: Any = (),
                          field_statuses: Mapping[str, Any] | None = None) -> None:
        self.control.record_validation(logical_step_id, status, fields=fields, field_statuses=field_statuses)

    def complete(self, *, model: str, messages: list[dict[str, str]], agent: str,
                 max_tokens: int | None = None, temperature: float | None = None,
                 response_format: dict[str, Any] | None = None) -> str:
        # Legacy callers retain their interface. Explicit model remains the
        # primary; governance/budget still applies without inferred promotion.
        role = ("comparison" if "compar" in agent else
                "verifier" if "verif" in agent else
                "interpretation" if "interpret" in agent else
                "extraction" if "extract" in agent else "auxiliary")
        self._compatibility_steps += 1
        return self.complete_routed(
            role, messages, agent, {"document_id": "legacy",
                "logical_step_id": "legacy:" + str(self._compatibility_steps),
                "field_group": role, "retrieval_stage": 4, "fields": (),
                "candidate_count": 0, "page_count_used": 0},
            max_tokens=max_tokens, temperature=temperature, response_format=response_format,
            _primary_override=model)

    def complete_routed(self, role: str, messages: list[dict[str, str]], agent: str,
                        context: Mapping[str, Any], semantic_level: int = 0,
                        validator: Callable[[str], Any] | None = None,
                        max_tokens: int | None = None, temperature: float | None = None,
                        response_format: dict[str, Any] | None = None,
                        _primary_override: str | None = None) -> str:
        role = "verifier" if role == "verification" else role
        route = self.policy.role(role)
        safe_context = _context(context, role)
        _identifier(agent, maximum=160)
        if type(semantic_level) is not int or not 0 <= semantic_level <= 2:
            raise ConfigurationError("Semantic routing level must be between zero and two.")
        if (semantic_level or role == "verifier") and safe_context["retrieval_stage"] < 3:
            self.control.note_limit(safe_context, "retrieval_required_before_semantic_promotion")
            raise LLMClientError(self.provider, "routing_limit",
                                 limit_reason="retrieval_required_before_semantic_promotion")
        if semantic_level and not safe_context["fields"]:
            self.control.note_limit(safe_context, "semantic_fields_required")
            raise LLMClientError(self.provider, "routing_limit", limit_reason="semantic_fields_required")
        if semantic_level > len(route.escalations):
            self.control.note_limit(safe_context, "semantic_route_exhausted")
            raise LLMClientError(self.provider, "routing_limit", limit_reason="semantic_route_exhausted")
        selected = route.escalations[semantic_level - 1] if semantic_level else route.primary
        if _primary_override is not None:
            selected = _identifier(_primary_override)
        alternatives = tuple(model for model in route.technical_fallbacks if model != selected)
        models = (selected,) + alternatives[:1]
        last_kind = "provider_error"
        for fallback_level, model in enumerate(models):
            request = {"model": model, "messages": messages, "agent": agent,
                       "max_tokens": max_tokens, "temperature": temperature,
                       "response_format": response_format}
            fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True,
                ensure_ascii=False).encode("utf-8")).hexdigest()
            event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **safe_context, "role": role, "primary_model": route.primary,
                "requested_model": model, "actual_model": None,
                "response_id": None, "request_id": None,
                "fallback_level": fallback_level,
                "fallback_reason": last_kind if fallback_level else None,
                "semantic_escalation": bool(semantic_level or role == "verifier"),
                "semantic_level": semantic_level,
                "input_tokens": None, "cached_input_tokens": None,
                "output_tokens": None, "total_tokens": None, "latency_ms": None,
                "service_tier": None, "result_status": "ATTEMPTED",
                "evidence_validation_status": "NOT_CHECKED", "error_kind": None,
                "local_json_repair": False,
            }
            index = self.control.reserve(event, fingerprint,
                semantic_fields=tuple(safe_context["fields"])
                if (semantic_level or role == "verifier") and not fallback_level else ())
            started = time.perf_counter()
            self._provider_lock.acquire()
            raw_events = getattr(self.gateway, "events", [])
            previous_events = len(raw_events)
            outcome = ValidationOutcome("NOT_CHECKED", tuple(safe_context["fields"]))
            raw = None
            error = None
            try:
                raw = self.gateway.complete(**request)
                if not isinstance(raw, str) or not raw.strip():
                    raise LLMClientError(self.provider, "response_empty")
                if validator is not None:
                    try:
                        outcome = _validation(validator(raw), tuple(safe_context["fields"]))
                    except ValueError:
                        outcome = ValidationOutcome("SCHEMA_INVALID", tuple(safe_context["fields"]))
                    if outcome.status == "SCHEMA_INVALID":
                        repaired = repair_json_locally(raw)
                        if repaired is not None:
                            try:
                                repaired_outcome = _validation(validator(repaired), tuple(safe_context["fields"]))
                            except ValueError:
                                repaired_outcome = outcome
                            if repaired_outcome.status != "SCHEMA_INVALID":
                                raw, outcome = repaired, repaired_outcome
                                event["local_json_repair"] = True
                    if outcome.status == "SCHEMA_INVALID":
                        raise LLMClientError(self.provider, "schema_invalid")
            except LLMClientError as caught:
                known = TECHNICAL_FALLBACK_KINDS | FATAL_KINDS | {
                    "invalid_request", "context_length", "budget_exhausted", "circuit_open",
                    "routing_limit", "persistence_error"}
                error = caught if caught.kind in known else LLMClientError(self.provider, "invalid_request")
            except Exception as caught:
                # Classify by bounded status/type; no raw exception/body logging.
                error = LLMClientError(self.provider, classify_error(caught))
            finally:
                try:
                    reported = dict(raw_events[-1]) if len(raw_events) > previous_events else {}
                finally:
                    self._provider_lock.release()
            safe_metadata = response_metadata({
                "model": reported.get("actual_model"), "id": reported.get("response_id"),
                "request_id": reported.get("request_id"),
                "service_tier": reported.get("effective_service_tier")})
            update = {
                "actual_model": safe_metadata.get("actual_model"),
                "response_id": safe_metadata.get("response_id"), "request_id": safe_metadata.get("request_id"),
                "input_tokens": _count(reported.get("input_tokens")),
                "cached_input_tokens": _count(reported.get("cached_tokens")),
                "output_tokens": _count(reported.get("output_tokens")),
                "total_tokens": _count(reported.get("total_tokens")),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "service_tier": safe_metadata.get("effective_service_tier"),
                "result_status": "error" if error is not None else "completed",
                "evidence_validation_status": outcome.status,
                "validation_fields": list(outcome.fields),
                "local_json_repair": bool(event["local_json_repair"] or reported.get("local_json_repair")),
                "error_kind": error.kind if error is not None else None,
                "failure_category": failure_category(error.kind if error is not None else None, outcome.status),
            }
            technical = error is not None and error.kind in PROVIDER_TRANSPORT_KINDS
            self.control.finish(index, update, technical_failure=technical,
                                fatal_kind=error.kind if error is not None and error.kind in FATAL_KINDS | {"persistence_error"} else None)
            if error is None:
                return raw
            last_kind = error.kind
            if self.control.blocked_reason is not None:
                self.control._blocked()
            if error.kind not in TECHNICAL_FALLBACK_KINDS:
                raise error
            if (error.kind in {"RPM", "TPM", "rate_limit"}
                    and error.retry_after is not None and error.retry_after > 0):
                maximum_wait = self.max_retry_wait_seconds
                if not math.isfinite(error.retry_after) or error.retry_after > maximum_wait:
                    self.control.note_limit(safe_context, "provider_requested_wait_exceeds_limit")
                    raise LLMClientError(self.provider, error.kind, retry_after=error.retry_after,
                                         limit_reason="provider_requested_wait_exceeds_limit")
                if fallback_level < len(models) - 1:
                    time.sleep(error.retry_after)
            if fallback_level == len(models) - 1:
                reason = ("max_model_attempts_per_logical_step" if alternatives
                          else "technical_fallback_route_exhausted")
                self.control.note_limit(safe_context, reason)
                raise LLMClientError(self.provider, error.kind,
                                     retry_after=error.retry_after, limit_reason=reason)
        raise LLMClientError(self.provider, last_kind)
