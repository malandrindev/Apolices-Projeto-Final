"""Offline fast-track budgets, failure categories and durable stage handoffs."""
from __future__ import annotations
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from src.config import Settings
from src.llm.model_routing import RequestControl, ModelRoutingPolicy, RoutedGateway, RoleRoute
from src.llm.resilience import LLMClientError
from scripts.run_fasttrack_validation import SessionStore, build_gateway, LIMITS
from scripts.run_berkley_pilot import atomic_json, file_hash, execution_metrics

class FakeProvider:
    provider = "openai"
    def __init__(self, results):
        self.results, self.events, self.calls = list(results), [], []
        self._settings = SimpleNamespace(max_retries=1, timeout_seconds=60, max_retry_wait_seconds=0)
        self._client = SimpleNamespace(max_retries=0)
    def complete(self, **request):
        self.calls.append(request)
        result = self.results.pop(0)
        self.events.append({"actual_model": request["model"], "input_tokens": 10, "cached_tokens": 0, "output_tokens": 2, "total_tokens": 12, "response_id": "resp_test"})
        if isinstance(result, Exception):
            raise result
        return result

def dispatch(gateway, number, validator=None):
    return gateway.complete_routed("extraction", [{"role": "user", "content": "public synthetic " + str(number)}], "optimized_extraction", {"document_id": "doc", "logical_step_id": "step:"+str(number), "fields": ["side_a"], "retrieval_stage": 0}, validator=validator)

def test_contract_failures_do_not_open_provider_breaker_but_are_counted_and_fallback_bounded():
    raw = FakeProvider(["invalid", "OK"] * 6)
    gateway = RoutedGateway(raw)
    for number in range(6):
        assert dispatch(gateway, number, lambda text: "VALID" if text == "OK" else "SCHEMA_INVALID") == "OK"
    assert len(raw.calls) == 12
    assert gateway.control.blocked_reason is None
    summary = gateway.control.summary()
    assert summary["provider_transport_failures"] == 0
    assert summary["model_output_contract_failures"] == 6
    assert summary["fallback_count"] == 6
    assert all(event["requested_model"] == "gpt-5.4-mini-2026-03-17" for event in gateway.events[1::2])

def test_finish_cannot_turn_schema_failure_into_provider_failure_via_boolean():
    raw = FakeProvider(["OK"])
    gateway = RoutedGateway(raw)
    dispatch(gateway, 0)
    for _ in range(3):
        gateway.control.finish(0, {"error_kind": "schema_invalid", "evidence_validation_status": "SCHEMA_INVALID"}, technical_failure=True)
    assert gateway.control.blocked_reason is None
    assert gateway.control.summary()["provider_transport_failures"] == 0
    assert gateway.events[0]["failure_category"] == "MODEL_OUTPUT_CONTRACT_FAILURE"

def test_semantic_failure_is_separate_and_never_automatically_spends_fallback_budget():
    raw = FakeProvider(["valid json meaning incomplete"] * 10)
    gateway = RoutedGateway(raw)
    for number in range(10):
        dispatch(gateway, number, lambda _: "SEMANTIC_INCOMPLETE")
    assert len(raw.calls) == 10
    assert gateway.control.blocked_reason is None
    assert gateway.control.summary()["semantic_quality_failures"] == 10
    assert gateway.control.summary()["fallback_count"] == 0

class FakeResponses:
    def __init__(self, store):
        self.calls, self.store = [], store
    def create(self, **parameters):
        # Durable global and stage reservation and exact public payload precede HTTP.
        session = json.loads(self.store.session_path.read_text(encoding="utf-8"))
        assert session["global_http_attempts"] >= 1
        manifest = json.loads((self.store.run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["request_control"]["events"][-1]["result_status"] == "ATTEMPTED"
        assert manifest["payloads"][-1]["http_attempt"] == manifest["request_control"]["http_attempts"]
        self.calls.append(parameters)
        assert parameters["store"] is False
        assert not {"tools", "tool_choice", "service_tier"} & parameters.keys()
        number = len(self.calls)
        return SimpleNamespace(output_text="OK", status="completed", model=parameters["model"], id="resp_"+str(number), _request_id="req_"+str(number), service_tier="default", usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12, input_tokens_details=SimpleNamespace(cached_tokens=3)))

def settings():
    return Settings(groq_api_key="", model_fast="gpt-5.6-luna", model_strong="gpt-5.6-sol", model_vision="unused", temperature=0, max_tokens=4500, timeout_seconds=60, max_retries=1, llm_provider="openai", openai_api_key="not-a-real-credential", model_intermediate="gpt-5.6-terra")

def finish_review(store, gateway):
    metrics = execution_metrics(gateway.control.snapshot(), wall_seconds=.1)
    store.finish({"complete": True, "run_id": store.run["run_id"]}, metrics, "COMPLETED_PENDING_REVIEW")
    atomic_json(store.run_dir / "review.json", {"status": "PASS", "stage": store.run["stage"], "run_id": store.run["run_id"], "result_sha256": file_hash(store.run_dir / "result.json")})

def test_durable_three_stage_budgets_and_global70_block71_without_network(tmp_path):
    total = 0
    original_run_ids = []
    for stage in LIMITS:
        store = SessionStore(tmp_path)
        store.start(stage, {"stage": stage})
        original_run_ids.append(store.run["run_id"])
        responses = FakeResponses(store)
        with patch("openai.OpenAI", return_value=SimpleNamespace(responses=responses, max_retries=0)) as sdk:
            gateway = build_gateway(settings(), ModelRoutingPolicy(), store)
        assert sdk.call_args.kwargs["max_retries"] == 0
        assert sdk.call_args.kwargs["base_url"] == "https://api.openai.com/v1"
        for number in range(LIMITS[stage]):
            def check_response_saved(_):
                assert len(store.run["responses"]) == number + 1
                assert len(store.run["provider_returns"]) == number + 1
                return "VALID"
            dispatch(gateway, number, check_response_saved)
        with pytest.raises(LLMClientError) as error:
            dispatch(gateway, "over-budget")
        assert error.value.kind == "budget_exhausted"
        assert len(responses.calls) == LIMITS[stage]
        total += len(responses.calls)
        # A deliberate budget-check has stopped this fake run; for this persistence
        # handoff test terminal result is manually approved, without any live API.
        finish_review(store, gateway)
    assert total == 70
    state = SessionStore(tmp_path)
    assert state.state["global_http_attempts"] == 70
    assert len(set(original_run_ids)) == 3
    for stage in LIMITS:
        with pytest.raises(Exception, match="replay"):
            state.start(stage, {})
    aggregation = json.loads((state.base / "session_telemetry.json").read_text(encoding="utf-8"))
    assert aggregation["http_attempts"] == 70
    assert aggregation["tokens"]["input_tokens"]["total"] == 700
    assert aggregation["tokens"]["cached_input_tokens"]["total"] == 210
    assert aggregation["tokens"]["output_tokens"]["total"] == 140

@pytest.mark.parametrize("stage", ["axa", "comparison"])
def test_later_stages_require_earlier_independent_quality_review(tmp_path, stage):
    store = SessionStore(tmp_path)
    with pytest.raises(Exception):
        store.start(stage, {})
    assert not store.claim_path.exists()

def test_uncertain_reservation_survives_recovery_is_consumed_and_never_replayed(tmp_path):
    store = SessionStore(tmp_path)
    store.start("berkley", {})
    control = RequestControl(30, persist_callback=store.persist_control)
    event = {"document_id": "doc", "logical_step_id": "first", "requested_model": "gpt-5.6-luna", "fields": ["side_a"], "role": "extraction", "semantic_level": 0, "result_status": "ATTEMPTED", "input_tokens": None, "cached_input_tokens": None, "output_tokens": None, "total_tokens": None, "latency_ms": None, "fallback_level": 0, "semantic_escalation": False, "evidence_validation_status": "NOT_CHECKED"}
    control.reserve(event, "fp")
    recovered = SessionStore(tmp_path)
    report = recovered.recovery()
    assert report["global_reserved_attempts"] == 1
    assert report["global_remaining"] == 69
    assert report["stages"]["berkley"]["uncertain_attempts"] == 1
    with pytest.raises(Exception, match="replay"):
        recovered.start("berkley", {})

def test_review_is_bound_to_exact_result_hash_not_merely_a_pass_string(tmp_path):
    store = SessionStore(tmp_path)
    store.start("berkley", {})
    store.finish({"complete": True}, {}, "COMPLETED_PENDING_REVIEW")
    atomic_json(store.run_dir / "review.json", {"status": "PASS", "stage": "berkley", "run_id": store.run["run_id"], "result_sha256": "wrong"})
    with pytest.raises(Exception, match="independent PASS"):
        SessionStore(tmp_path).start("axa", {})


def test_global_authority_reservation_missing_from_run_manifest_stays_uncertain(tmp_path):
    store = SessionStore(tmp_path)
    store.start("berkley", {})
    # Power loss between durable global authority and secondary stage manifest.
    state = json.loads(store.session_path.read_text(encoding="utf-8"))
    state["stages"]["berkley"]["http_attempts"] = 1
    state["global_http_attempts"] = 1
    atomic_json(store.session_path, state)
    recovered = SessionStore(tmp_path).recovery()
    assert recovered["global_reserved_attempts"] == 1
    assert recovered["stages"]["berkley"]["uncertain_attempts"] == 1
    assert recovered["stages"]["berkley"]["unmerged_authority_reservations"] == 1


@pytest.mark.parametrize("environment", [{"OPENAI_CUSTOM_HEADERS": "opaque"}, {"OPENAI_BASE_URL": "https://unauthorized.invalid/v1"}])
def test_sdk_environment_cannot_change_authorized_endpoint_or_headers(environment, tmp_path):
    store = SessionStore(tmp_path)
    store.start("berkley", {})
    with patch.dict("os.environ", environment), patch("openai.OpenAI") as sdk:
        with pytest.raises(Exception):
            build_gateway(settings(), ModelRoutingPolicy(), store)
    sdk.assert_not_called()
    assert store.state["global_http_attempts"] == 0

def test_bound_conservative_projection_preserves_raw_result_and_rejects_fact_edits(tmp_path):
    from src.agents.extraction import Phase2Report
    from src.agents.semantic_completeness import apply_reviewed_conservative_statuses
    from src.schemas.policy import PolicyExtraction, FieldEvidence
    from src.schemas.retrieval import FieldStatus
    store = SessionStore(tmp_path)
    store.start("berkley", {})
    report = Phase2Report(source_name="generic.pdf", sha256="source", clause_count=1,
        policy=PolicyExtraction(definicoes_relevantes=FieldEvidence(
            valor="Definicao.", pagina=1, trecho_origem="Definicao.", confianca=.95)),
        field_status={"definicoes_relevantes": FieldStatus.FOUND}).model_dump(mode="json")
    store.finish({"run_id": store.run["run_id"], "report": report}, {}, "COMPLETED_PENDING_REVIEW")
    original_sha = file_hash(store.run_dir / "result.json")
    review = {"stage": "berkley", "run_id": store.run["run_id"], "status": "PASS",
        "result_sha256": original_sha, "conservative_field_overrides": {
            "definicoes_relevantes": {"status": "AMBIGUOUS", "reason": "Independently reviewed scope conflict."}}}
    projected = apply_reviewed_conservative_statuses(report, review)
    atomic_json(store.run_dir / "reviewed_report.json", projected)
    review["reviewed_report_sha256"] = file_hash(store.run_dir / "reviewed_report.json")
    atomic_json(store.run_dir / "review.json", review)
    assert store.require_review("berkley")["status"] == "PASS"
    assert file_hash(store.run_dir / "result.json") == original_sha
    projected["policy"]["definicoes_relevantes"]["valor"] = "Invented."
    atomic_json(store.run_dir / "reviewed_report.json", projected)
    review["reviewed_report_sha256"] = file_hash(store.run_dir / "reviewed_report.json")
    atomic_json(store.run_dir / "review.json", review)
    with pytest.raises(Exception, match="projection"):
        store.require_review("berkley")
