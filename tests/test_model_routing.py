"""Offline E2 model governance tests; no SDK/network/client construction."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from src.config import ConfigurationError, Settings
from src.llm.model_routing import (
    ModelRoutingPolicy, RequestControl, RoleRoute, RoutedGateway, ValidationOutcome,
)
from src.llm.openai_client import OpenAIProvider
from src.llm.resilience import (
    LLMClientError, classify_error, repair_json_locally, response_metadata,
)


class Provider:
    provider = "openai"

    def __init__(self, results=(), metadata=None, on_call=None):
        self._settings = SimpleNamespace(max_retries=1, timeout_seconds=60,
                                         max_retry_wait_seconds=30)
        self._client = SimpleNamespace(max_retries=0)
        self.results, self.calls, self.events = list(results), [], []
        self.metadata, self.on_call = metadata, on_call

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.on_call:
            self.on_call(kwargs)
        result = self.results.pop(0) if self.results else "OK"
        event = {"actual_model": kwargs["model"], "request_id": "req_safe",
                 "response_id": "resp_safe", "effective_service_tier": "default",
                 "input_tokens": 15, "cached_tokens": 0,
                 "output_tokens": 5, "total_tokens": 20}
        if isinstance(result, Exception):
            event.update(input_tokens=None, cached_tokens=None, output_tokens=None,
                         total_tokens=None, response_id=None)
        if self.metadata is not None:
            event.update(self.metadata)
        self.events.append(event)
        if isinstance(result, Exception):
            raise result
        return result


def context(step="step-0", *, stage=0, fields=("side_a",), document="doc"):
    return {"document_id": document, "logical_step_id": step,
            "field_group": "COVERAGES", "retrieval_stage": stage,
            "fields": list(fields), "candidate_count": 3, "page_count_used": 2}


def call(gateway, step="step-0", *, role="extraction", stage=0, semantic_level=0,
         fields=("side_a",), validator=None, content=None, document="doc", **kwargs):
    return gateway.complete_routed(
        role, [{"role": "user", "content": content or ("synthetic-" + step)}],
        "optimized_extraction", context(step, stage=stage, fields=fields, document=document),
        semantic_level=semantic_level, validator=validator, **kwargs)


def policy_without_fallbacks():
    default = ModelRoutingPolicy()
    return ModelRoutingPolicy({role: RoleRoute(route.primary, (), route.escalations)
                               for role, route in default.routes.items()})


def json_validator(raw):
    parsed = json.loads(raw)
    return {"status": "VALID" if parsed == {"value": "source,}"} else "SCHEMA_INVALID",
            "fields": ["side_a"]}


def test_default_routes_cover_exact_authorized_models():
    policy = ModelRoutingPolicy.from_env({})
    assert policy.role("extraction") == RoleRoute(
        "gpt-5.6-luna", ("gpt-5.4-mini-2026-03-17",), ("gpt-5.6-terra",))
    assert policy.role("interpretation") == policy.role("comparison")
    assert policy.role("verification") == policy.role("verifier")
    assert policy.role("auxiliary").technical_fallbacks == ("gpt-4.1-mini-2025-04-14",)
    assert policy.role("verifier").technical_fallbacks == (
        "gpt-5.6-terra", "gpt-5.2-2025-12-11")
    assert json.loads(json.dumps(policy.routing_signature)) == policy.routing_signature
    assert policy.routing_signature["limits"]["same_model_retries"] == 0
    with pytest.raises(ConfigurationError):
        policy.role("private prompt")
    with pytest.raises(TypeError):
        policy.routes["extraction"] = RoleRoute("gpt-other")


def test_configuration_overrides_models_and_empty_lists_without_business_hardcode():
    policy = ModelRoutingPolicy.from_env({
        "MODEL_ROUTING_EXTRACTION_PRIMARY": "gpt-configured-2030-01-01",
        "MODEL_ROUTING_EXTRACTION_TECHNICAL_FALLBACKS": "",
        "MODEL_ROUTING_EXTRACTION_ESCALATIONS": " gpt-configured-strong ",
    })
    assert policy.role("extraction") == RoleRoute(
        "gpt-configured-2030-01-01", (), ("gpt-configured-strong",))
    assert policy.routing_signature != ModelRoutingPolicy().routing_signature


@pytest.mark.parametrize("route", [
    lambda: RoleRoute("gpt-a", ("gpt-a",)),
    lambda: RoleRoute("gpt-a", (), ("gpt-a",)),
    lambda: RoleRoute("gpt-a", ("gpt-b", "gpt-c", "gpt-d")),
    lambda: RoleRoute("sk-private"),
    lambda: ModelRoutingPolicy({}),
])
def test_invalid_route_configurations_are_rejected(route):
    with pytest.raises(ConfigurationError):
        route()


@pytest.mark.parametrize("budget", [0, 51, -1, True, 2.5])
def test_budget_requires_bounded_integer(budget):
    with pytest.raises(ConfigurationError):
        RequestControl(budget)


@pytest.mark.parametrize("setting,value", [
    ("max_retries", 2), ("max_retries", True), ("timeout_seconds", True),
    ("timeout_seconds", 61), ("max_retry_wait_seconds", float("nan")),
    ("max_retry_wait_seconds", -1),
    ("timeout_seconds", float("nan")), ("timeout_seconds", float("inf")),
])
def test_provider_retries_and_timeout_cannot_escape_governance(setting, value):
    raw = Provider()
    setattr(raw._settings, setting, value)
    with pytest.raises(ConfigurationError):
        RoutedGateway(raw)
    assert not raw.calls


@pytest.mark.parametrize("retries", [1, True])
def test_sdk_retries_are_disabled_before_any_dispatch(retries):
    raw = Provider()
    raw._client.max_retries = retries
    with pytest.raises(ConfigurationError):
        RoutedGateway(raw)
    assert not raw.calls


def test_primary_succeeds_without_stronger_model_or_extra_parameters():
    raw = Provider()
    gateway = RoutedGateway(raw)
    assert call(gateway, validator=lambda _: ValidationOutcome("VALID", ("side_a",))) == "OK"
    assert [c["model"] for c in raw.calls] == ["gpt-5.6-luna"]
    assert gateway.provider == raw.provider
    assert gateway.events[0]["evidence_validation_status"] == "VALID"
    assert gateway.events[0]["cached_input_tokens"] == 0
    assert gateway.events[0]["total_tokens"] == 20
    assert gateway.events[0]["actual_model"] == "gpt-5.6-luna"
    assert not any(k in raw.calls[0] for k in ("tools", "service_tier", "tool_choice"))
    assert gateway.usage == {"calls": 1, "prompt_tokens": 15,
                             "completion_tokens": 5, "cached_tokens": 0}


@pytest.mark.parametrize("kind", ["timeout", "RPM", "TPM", "rate_limit", "provider_error",
                                 "model_unavailable", "response_incomplete", "response_empty"])
def test_technical_fallback_uses_different_model_once(kind):
    raw = Provider([LLMClientError("openai", kind), "OK"])
    gateway = RoutedGateway(raw)
    assert call(gateway) == "OK"
    assert [c["model"] for c in raw.calls] == [
        "gpt-5.6-luna", "gpt-5.4-mini-2026-03-17"]
    assert gateway.events[1]["fallback_reason"] == kind
    assert gateway.control.summary()["fallback_count"] == 1
    assert gateway.control.summary()["semantic_escalation_count"] == 0
    assert gateway.events[0]["input_tokens"] is None


def test_schema_repair_is_local_and_preserves_quoted_source_characters():
    raw = Provider(['```json\n{"value":"source,}",}\n```'])
    gateway = RoutedGateway(raw)
    assert json.loads(call(gateway, validator=json_validator)) == {"value": "source,}"}
    assert len(raw.calls) == 1
    assert gateway.events[0]["local_json_repair"] is True
    assert gateway.events[0]["evidence_validation_status"] == "VALID"


def test_irreparable_schema_falls_back_but_evidence_invalid_does_not():
    raw = Provider(["not JSON", '{"value":"source,}"}'])
    gateway = RoutedGateway(raw)
    assert json.loads(call(gateway, validator=json_validator))["value"] == "source,}"
    assert len(raw.calls) == 2
    assert gateway.events[0]["error_kind"] == "schema_invalid"
    semantic_raw = Provider(["not grounded"])
    semantic = RoutedGateway(semantic_raw)
    assert call(semantic, validator=lambda _: {"status": "EVIDENCE_INVALID"}) == "not grounded"
    assert len(semantic_raw.calls) == 1
    assert semantic.control.summary()["evidence_validation_failures"] == 1
    assert semantic.control.summary()["technical_failures"] == 0


@pytest.mark.parametrize("status", ["AMBIGUOUS", "NOT_RETRIEVED", "NOT_FOUND"])
def test_uncertainty_returns_to_retrieval_without_automatic_escalation(status):
    raw = Provider(["uncertain"] * 12)
    gateway = RoutedGateway(raw)
    for index in range(12):
        call(gateway, f"uncertain-{index}", validator=lambda _: status)
    assert len(raw.calls) == 12
    assert set(c["model"] for c in raw.calls) == {"gpt-5.6-luna"}
    assert gateway.control.blocked_reason is None
    assert gateway.control.summary()["technical_failures"] == 0


@pytest.mark.parametrize("role,level", [("extraction", 1), ("interpretation", 1),
                                      ("verifier", 0)])
def test_semantic_promotion_requires_local_retrieval_first(role, level):
    raw = Provider()
    gateway = RoutedGateway(raw)
    with pytest.raises(LLMClientError) as error:
        call(gateway, role=role, semantic_level=level, stage=2)
    assert error.value.limit_reason == "retrieval_required_before_semantic_promotion"
    assert not raw.calls


def test_explicit_semantic_route_preserves_role_and_is_not_technical_fallback():
    raw = Provider()
    gateway = RoutedGateway(raw)
    call(gateway, stage=4, semantic_level=1)
    assert raw.calls[0]["model"] == "gpt-5.6-terra"
    assert gateway.events[0]["semantic_escalation"] is True
    assert gateway.events[0]["fallback_level"] == 0
    assert gateway.control.summary()["semantic_escalation_count"] == 1
    with pytest.raises(LLMClientError) as error:
        call(gateway, "no-level2", stage=4, semantic_level=2)
    assert error.value.limit_reason == "semantic_route_exhausted"
    assert len(raw.calls) == 1


def test_split_batches_count_one_promotion_and_two_distinct_promotions_per_field():
    raw = Provider()
    gateway = RoutedGateway(raw)
    for number in range(4):
        call(gateway, f"split-{number}", stage=4, semantic_level=1)
    assert len(raw.calls) == 4
    assert gateway.control.summary()["semantic_escalation_count"] == 1
    call(gateway, "verifier", stage=4, role="verifier")
    assert gateway.control.summary()["semantic_escalation_count"] == 2
    # Third distinct promotion is blocked, even when it resolves to the same Sol model.
    with pytest.raises(LLMClientError) as error:
        call(gateway, "third-promotion", stage=4, role="interpretation", semantic_level=1)
    assert error.value.limit_reason == "max_semantic_escalations_per_field"
    assert len(raw.calls) == 5
    # Already admitted verifier promotion can continue with another evidence batch.
    call(gateway, "verifier-split", stage=4, role="verifier")
    # Another field has its own promotion count.
    call(gateway, "other-field", stage=4, role="interpretation", semantic_level=1,
         fields=("side_b",))
    assert len(raw.calls) == 7


def test_verifier_second_configured_alternative_never_escapes_two_attempt_cap():
    raw = Provider([LLMClientError("openai", "timeout"),
                    LLMClientError("openai", "provider_error"), "should not send"])
    gateway = RoutedGateway(raw)
    with pytest.raises(LLMClientError) as error:
        call(gateway, role="verifier", stage=4)
    assert [c["model"] for c in raw.calls] == ["gpt-5.6-sol", "gpt-5.6-terra"]
    assert error.value.limit_reason == "max_model_attempts_per_logical_step"
    assert gateway.control.summary()["semantic_escalation_count"] == 1
    assert gateway.control.summary()["semantic_escalation_http_attempts"] == 2


@pytest.mark.parametrize("kind", ["authentication", "permission", "quota", "credit", "TPD"])
def test_fatal_errors_stop_whole_control_without_model_switch(kind):
    raw = Provider([LLMClientError("openai", kind), "should not send"])
    gateway = RoutedGateway(raw)
    with pytest.raises(LLMClientError) as error:
        call(gateway)
    assert error.value.kind == kind
    assert gateway.control.blocked_reason == kind
    with pytest.raises(LLMClientError):
        call(gateway, "second")
    assert len(raw.calls) == 1
    assert gateway.events[0]["input_tokens"] is None


@pytest.mark.parametrize("delay,maximum,wait,attempts", [
    (2, 30, 2, 2), (0, 30, None, 2), (31, 60, None, 1), (6, 5, None, 1),
])
def test_retry_after_wait_is_bounded_and_never_repeats_same_model(delay, maximum, wait, attempts):
    raw = Provider([LLMClientError("openai", "TPM", retry_after=delay), "OK"])
    raw._settings.max_retry_wait_seconds = maximum
    gateway = RoutedGateway(raw)
    with patch("src.llm.model_routing.time.sleep") as sleep:
        if attempts == 1:
            with pytest.raises(LLMClientError) as error:
                call(gateway)
            assert error.value.limit_reason == "provider_requested_wait_exceeds_limit"
        else:
            assert call(gateway) == "OK"
    if wait is None:
        sleep.assert_not_called()
    else:
        sleep.assert_called_once_with(wait)
    assert len(raw.calls) == attempts
    assert len({c["model"] for c in raw.calls}) == attempts


def test_durable_attempt_is_persisted_before_provider_and_unknown_until_response():
    persisted = []
    control = RequestControl(persist_callback=lambda snapshot: persisted.append(copy.deepcopy(snapshot)))
    def before(_):
        snapshot = persisted[-1]
        assert snapshot["http_attempts"] == 1
        assert snapshot["events"][0]["result_status"] == "ATTEMPTED"
        assert snapshot["events"][0]["input_tokens"] is None
        assert snapshot["remaining"] == 49
    raw = Provider(on_call=before)
    gateway = RoutedGateway(raw, control=control)
    call(gateway)
    assert persisted[-1]["events"][0]["result_status"] == "completed"
    assert persisted[-1]["events"][0]["input_tokens"] == 15


def test_persistence_failure_prevents_network_and_closes_control():
    def broken(_):
        raise OSError("sk-secret document-body")
    raw = Provider()
    gateway = RoutedGateway(raw, control=RequestControl(persist_callback=broken))
    with pytest.raises(LLMClientError) as error:
        call(gateway)
    assert error.value.kind == "persistence_error"
    assert "sk-secret" not in str(error.value)
    assert not raw.calls
    assert gateway.control.events[0]["result_status"] == "ATTEMPTED"
    assert gateway.control.blocked_reason == "persistence_error"


def test_hard_budget_blocks_request_51_and_counts_fallback():
    raw = Provider()
    gateway = RoutedGateway(raw)
    for number in range(50):
        call(gateway, f"request-{number}")
    with pytest.raises(LLMClientError) as error:
        call(gateway, "request-51")
    assert error.value.kind == "budget_exhausted"
    assert gateway.control.http_attempts == len(raw.calls) == 50
    assert gateway.control.budget_remaining == 0
    short = Provider([LLMClientError("openai", "timeout"), "never dispatch fallback"])
    bounded = RoutedGateway(short, control=RequestControl(1))
    with pytest.raises(LLMClientError) as error:
        call(bounded)
    assert error.value.kind == "budget_exhausted"
    assert len(short.calls) == 1


def test_thread_reservations_cannot_exceed_total_budget():
    raw = Provider()
    gateway = RoutedGateway(raw, control=RequestControl(10))
    def dispatch(number):
        try:
            return call(gateway, f"thread-{number}")
        except LLMClientError as error:
            return error.kind
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(dispatch, range(25)))
    assert outcomes.count("OK") == 10
    assert len(raw.calls) == gateway.control.http_attempts == 10
    assert len(gateway.events) == 10
    assert all(event["actual_model"] == event["requested_model"] for event in gateway.events)


def test_three_consecutive_technical_errors_open_breaker_before_fourth_request():
    raw = Provider([LLMClientError("openai", "timeout")] * 5)
    gateway = RoutedGateway(raw)
    with pytest.raises(LLMClientError):
        call(gateway, "first")
    with pytest.raises(LLMClientError) as error:
        call(gateway, "second")
    assert error.value.kind == "circuit_open"
    assert len(raw.calls) == 3
    assert gateway.control.summary()["technical_failures"] == 3
    assert gateway.control.snapshot()["consecutive_technical_failures"] == 3


def test_two_nonconsecutive_errors_in_ten_open_twenty_percent_breaker():
    raw = Provider([LLMClientError("openai", "timeout"), "OK", "OK",
                    LLMClientError("openai", "timeout")] + ["OK"] * 8)
    gateway = RoutedGateway(raw, policy=policy_without_fallbacks())
    for number in range(10):
        try:
            call(gateway, f"rolling-{number}")
        except LLMClientError:
            pass
    assert len(raw.calls) == 10
    assert gateway.control.blocked_reason == "circuit_open"
    assert sum(gateway.control.snapshot()["last_10_technical_failures"]) == 2
    with pytest.raises(LLMClientError) as error:
        call(gateway, "rolling-11")
    assert error.value.kind == "circuit_open"
    assert len(raw.calls) == 10


def test_identical_same_model_request_and_same_logical_step_are_not_repeated():
    raw = Provider()
    gateway = RoutedGateway(raw)
    call(gateway, "one", content="identical")
    with pytest.raises(LLMClientError) as error:
        call(gateway, "two", content="identical")
    assert error.value.limit_reason == "identical_same_model_request_disabled"
    with pytest.raises(LLMClientError) as error:
        call(gateway, "one", content="different")
    assert error.value.limit_reason == "same_model_retry_disabled"
    assert len(raw.calls) == 1


def test_validation_updates_successful_fallback_only_with_known_fields():
    raw = Provider([LLMClientError("openai", "timeout"), "OK"])
    gateway = RoutedGateway(raw)
    call(gateway)
    gateway.record_validation("step-0", "EVIDENCE_INVALID", fields=["side_a"],
                              field_statuses={"side_a": "TECHNICAL_UNAVAILABLE"})
    assert gateway.events[0]["evidence_validation_status"] == "NOT_CHECKED"
    assert gateway.events[1]["evidence_validation_status"] == "EVIDENCE_INVALID"
    assert gateway.events[1]["field_statuses"] == {"side_a": "TECHNICAL_UNAVAILABLE"}
    with pytest.raises(ConfigurationError):
        gateway.record_validation("step-0", "VALID", fields=["raw secret prompt"])
    with pytest.raises(ConfigurationError):
        gateway.record_validation("step-0", "VALID", field_statuses={"side_a": "raw secret"})


@pytest.mark.parametrize("override", [
    {"logical_step_id": "sk-secret"},
    {"document_id": "contains private spaces"},
    {"fields": [["side_a"]]},
    {"fields": ["unknown_private"]},
    {"candidate_count": True},
    {"retrieval_stage": 5},
])
def test_context_rejects_unsafe_or_unbounded_identifiers_before_dispatch(override):
    raw = Provider()
    gateway = RoutedGateway(raw)
    ctx = context()
    ctx.update(override)
    with pytest.raises(ConfigurationError):
        gateway.complete_routed("extraction", [{"role": "user", "content": "unused"}],
                                "optimized_extraction", ctx)
    assert not raw.calls


def test_untrusted_metadata_and_context_text_never_enter_snapshot_or_logs(caplog):
    secret = "sk-secret-sentinel"
    raw = Provider(metadata={"actual_model": secret, "request_id": "req_" + secret,
                            "response_id": "resp_" + secret, "effective_service_tier": secret,
                            "input_tokens": True, "cached_tokens": -1, "output_tokens": "5",
                            "total_tokens": None, "prompt": "private-doc-sentinel"})
    gateway = RoutedGateway(raw)
    ctx = context()
    ctx["page_text"] = "private-doc-sentinel"
    gateway.complete_routed("extraction", [{"role": "user", "content": "private-doc-sentinel"}],
                            "optimized_extraction", ctx)
    serial = json.dumps(gateway.control.snapshot())
    assert secret not in serial and "private-doc-sentinel" not in serial
    assert secret not in caplog.text and "private-doc-sentinel" not in caplog.text
    event = gateway.events[0]
    assert event["input_tokens"] is event["cached_input_tokens"] is event["output_tokens"] is None
    assert event["request_id"] is event["response_id"] is event["actual_model"] is None
    assert gateway.usage["prompt_tokens"] is None


def test_usage_unknown_does_not_hide_known_subtotals_or_fabricate_zero():
    raw = Provider([LLMClientError("openai", "timeout"), "OK"])
    gateway = RoutedGateway(raw)
    call(gateway)
    totals = gateway.control.summary()["tokens_by_requested_model"]
    assert totals["gpt-5.6-luna"]["input_tokens"] is None
    assert totals["gpt-5.6-luna"]["unknown_usage_requests"] == 1
    assert totals["gpt-5.4-mini-2026-03-17"]["known_input_tokens"] == 15
    assert gateway.usage["prompt_tokens"] is gateway.usage["cached_tokens"] is None


@pytest.mark.parametrize("status,code,kind", [
    (404, "", "model_unavailable"), (400, "model_not_found", "model_unavailable"),
    (429, "credit_balance_exhausted", "credit"),
    (429, "project_spend_limit_exceeded", "credit"),
    (429, "organization_spend_limit_exceeded", "credit"),
    (429, "organization_usage_limit_exceeded", "quota"),
])
def test_http_classification_distinguishes_model_availability_and_financial_stops(status, code, kind):
    error = RuntimeError("sk-secret raw body")
    error.status_code = status
    error.body = {"error": {"code": code, "message": "private text"}}
    assert classify_error(error) == kind
    raw = Provider([error, "OK"])
    gateway = RoutedGateway(raw)
    if kind == "model_unavailable":
        assert call(gateway) == "OK"
        assert len(raw.calls) == 2
    else:
        with pytest.raises(LLMClientError) as caught:
            call(gateway)
        assert caught.value.kind == kind
        assert len(raw.calls) == 1
    assert "private text" not in json.dumps(gateway.control.snapshot())
    assert "sk-secret" not in json.dumps(gateway.control.snapshot())


@pytest.mark.parametrize("raw", ['{"missing":', '{"number":NaN,}', "invented prose",
                               '{"quote":"unterminated}', '["complete"]'])
def test_local_json_repair_cannot_synthesize_missing_values(raw):
    assert repair_json_locally(raw) is None


def test_local_json_repair_preserves_escaped_quotes_and_source_punctuation():
    raw = '\ufeff{"text":"source \\"quoted\\" [,] ,}", "items":[1,2,],}'
    repaired = repair_json_locally(raw)
    assert json.loads(repaired) == {"text": 'source "quoted" [,] ,}', "items": [1, 2]}


def test_safe_metadata_accepts_actual_new_snapshot_and_only_known_tiers():
    metadata = response_metadata({"model": "gpt-5.6-luna-2026-10-04",
                                  "id": "resp_new", "_request_id": "req_new",
                                  "service_tier": "ultrafast"})
    assert metadata == {"actual_model": "gpt-5.6-luna-2026-10-04",
                        "request_id": "req_new", "response_id": "resp_new",
                        "effective_service_tier": "ultrafast"}


class Responses:
    def __init__(self, results):
        self.results, self.calls = list(results), []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def adapter(results, *, repair=True):
    settings = Settings(groq_api_key="", model_fast="gpt-5.6-luna",
        model_strong="gpt-5.6-sol", model_intermediate="gpt-5.6-terra",
        model_vision="unused", temperature=0, max_tokens=4500,
        timeout_seconds=60, max_retries=1, llm_provider="openai", openai_api_key="")
    client = SimpleNamespace(responses=Responses(results), max_retries=0)
    return OpenAIProvider(settings, client=client, local_json_repair=repair), client


def test_raw_openai_local_repair_and_routing_share_single_sdk_attempt_without_added_parameters():
    response = SimpleNamespace(output_text='{"value":"source,}",}', status="completed",
        model="gpt-5.6-luna-2026-10-04", id="resp_safe", _request_id="req_safe",
        service_tier="default", usage=SimpleNamespace(input_tokens=15, output_tokens=5,
        total_tokens=20, input_tokens_details=SimpleNamespace(cached_tokens=0)))
    raw, client = adapter([response])
    gateway = RoutedGateway(raw)
    output = call(gateway, validator=json_validator, response_format={"type": "json_object"})
    assert json.loads(output) == {"value": "source,}"}
    assert len(client.responses.calls) == 1
    params = client.responses.calls[0]
    assert params["store"] is False and params["reasoning"] == {"effort": "none"}
    assert params["text"]["format"]["strict"] is True
    assert not any(k in params for k in ("tools", "tool_choice", "service_tier", "temperature"))
    assert gateway.events[0]["actual_model"] == "gpt-5.6-luna-2026-10-04"
    assert gateway.events[0]["local_json_repair"] is True


def test_raw_openai_unknown_usage_is_none_and_legacy_default_repair_is_unchanged():
    response = SimpleNamespace(output_text="OK", status="completed", usage=None)
    raw, client = adapter([response])
    gateway = RoutedGateway(raw)
    assert call(gateway) == "OK"
    assert gateway.events[0]["input_tokens"] is None
    assert gateway.events[0]["output_tokens"] is None
    assert gateway.events[0]["total_tokens"] is None
    # Legacy usage counters stay backwards compatible, while safe per-call records retain unknown.
    assert raw.usage["prompt_tokens"] == 0
    invalid = SimpleNamespace(output_text='{"value":1,}', status="completed", usage=None)
    old, _ = adapter([invalid], repair=False)
    with pytest.raises(LLMClientError) as error:
        old.complete(model="gpt-5.6-luna", messages=[], agent="extraction",
                     response_format={"type": "json_object"})
    assert error.value.kind == "invalid_response"

def test_logical_step_has_two_attempt_ceiling_even_across_roles():
    raw = Provider()
    gateway = RoutedGateway(raw)
    call(gateway, role="extraction", content="first primary")
    call(gateway, role="interpretation", content="second primary")
    with pytest.raises(LLMClientError) as error:
        call(gateway, role="auxiliary", content="third primary")
    assert error.value.limit_reason == "max_model_attempts_per_logical_step"
    assert len(raw.calls) == 2


def test_finished_result_persistence_failure_stops_before_next_http():
    checkpoints = []
    def persist(snapshot):
        checkpoints.append(copy.deepcopy(snapshot))
        if snapshot["events"][-1]["result_status"] == "completed":
            raise OSError("private response body sentinel")
    raw = Provider()
    gateway = RoutedGateway(raw, control=RequestControl(persist_callback=persist))
    with pytest.raises(LLMClientError) as error:
        call(gateway)
    assert error.value.kind == "persistence_error"
    assert len(raw.calls) == 1
    with pytest.raises(LLMClientError):
        call(gateway, "next")
    assert len(raw.calls) == 1
    assert checkpoints[0]["events"][0]["result_status"] == "ATTEMPTED"
    assert gateway.events[0]["response_id"] == "resp_safe"
    assert "private response body sentinel" not in json.dumps(gateway.control.snapshot())
