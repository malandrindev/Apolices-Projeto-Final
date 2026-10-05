"""Offline pilot governance: durable one-shot claims and real controllers with fake HTTP."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import scripts.run_berkley_pilot as pilot
from src.agents.extraction import ExtractionResult
from src.agents.ocr import PageText, ProcessedDocument
from src.config import Settings
from src.schemas.clause import ClauseCategory, ClauseChunk, SourcePage
from src.schemas.retrieval import FieldStatus


def settings():
    return Settings(groq_api_key="", openai_api_key="", model_fast="gpt-5.6-luna",
                    model_strong="gpt-5.6-sol", model_intermediate="gpt-5.6-terra",
                    model_vision="gpt-5.6-luna", temperature=0, max_tokens=4500,
                    timeout_seconds=60, max_retries=1, llm_provider="openai")


def corpus(tmp_path: Path):
    text = ("Seguradora: Companhia Teste\nFRANQUIA\nRetencao: R$ 1000. "
            "Custos de defesa e Side A. Territorialidade Brasil. Exclusoes e definicoes.\n")
    pages = [PageText(page_number=number, text=text, extraction_method="native")
             for number in range(1, pilot.EXPECTED_PAGES + 1)]
    document = ProcessedDocument(source_name="berkley_do_202512.pdf",
        sha256=pilot.DOCUMENT_SHA256, size_bytes=10, media_type="application/pdf",
        processed_at="2026-10-04T00:00:00Z", cache_key="synthetic-test", pages=pages)
    clauses = []
    for number in range(pilot.EXPECTED_CHUNKS):
        page = pages[number % len(pages)]
        clauses.append(ClauseChunk(clause_id=f"chunk-{number}", title="FRANQUIA",
            category=ClauseCategory.LIMITS, page_start=page.page_number,
            page_end=page.page_number, text=page.text,
            source_pages=[SourcePage(page_number=page.page_number, text=page.text)]))
    source = tmp_path / "data/processed/public_validation_preflight/berkley_do_202512.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF synthetic unit-test source")
    references = {}
    for name, relative in (("golden_fixture", "tests/fixtures/public_retrieval_golden.json"),
                           ("manual_reference", "data/processed/berkley_pilot_manual_reference.json")):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        references[name] = {"path": relative, "sha256": pilot.file_hash(path),
                            "created_before_real_requests": True}
    references["golden_fixture"]["sha256"] = pilot.GOLDEN_SHA256
    ingested = SimpleNamespace(source_name=document.source_name, source_path=source,
                               sha256=pilot.DOCUMENT_SHA256)
    return pilot.PilotCorpus(ingested, document, clauses,
        {"id": pilot.DOCUMENT_ID, "origin": "synthetic_unit_test",
         "source_url": "https://www.berkley.com.br/official.pdf", "access_date": "2026-10-04"},
        [], references)


def policy():
    from src.llm.model_routing import ModelRoutingPolicy
    # Empty environment mapping is deterministic and does not load credentials.
    with patch.dict("os.environ", {}, clear=True):
        return ModelRoutingPolicy.from_env()


def valid_plan(corpus_value, policy_value):
    routing = pilot.policy_routing(policy_value)
    return {
        "document_id": pilot.DOCUMENT_ID, "document_sha256": pilot.DOCUMENT_SHA256,
        "pages": pilot.EXPECTED_PAGES, "full_local_chunks": pilot.EXPECTED_CHUNKS,
        "full_local_corpus_retained": True, "MAX_HTTP_ATTEMPTS": 50,
        "initial_candidates": 1, "planned_initial_calls": 1,
        "initial": {"batches": [{}]}, "shared_remaining_budget": 49,
        "comparison_calls": 0, "other_documents_processed": 0,
        "references": corpus_value.references,
        "routing_policy": policy_value.routing_signature,
        "routing": {"simple_model": routing.simple_model,
                    "interpretation_model": routing.interpretation_model,
                    "verification_model": routing.verification_model,
                    "comparison_model": routing.comparison_model},
        "optimization": {"strategy": "optimized", "batch_chars": 20000,
            "initial_top_n": 2, "expanded_top_n": 4, "confidence_threshold": .75,
            "auto_min_pages": 8, "auto_min_chunks": 20},
    }


@pytest.fixture
def authorized_fake_hash(monkeypatch):
    original = pilot.file_hash
    def hashes(path):
        if path.name == "berkley_do_202512.pdf":
            return pilot.DOCUMENT_SHA256
        if path.name == "public_retrieval_golden.json":
            return pilot.GOLDEN_SHA256
        return original(path)
    monkeypatch.setattr(pilot, "file_hash", hashes)


def test_default_main_never_constructs_provider_or_other_document(tmp_path, monkeypatch):
    value, routing_policy = corpus(tmp_path), policy()
    plan = valid_plan(value, routing_policy)
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    monkeypatch.setattr(pilot, "load_corpus", lambda root: value)
    monkeypatch.setattr(pilot, "prepare_plan", lambda *args, **kwargs: plan)
    with patch("src.llm.model_routing.ModelRoutingPolicy.from_env", return_value=routing_policy), \
         patch("src.config.get_extraction_optimization_settings"), \
         patch.object(pilot, "execute_pilot", side_effect=AssertionError("No execute")), \
         patch("src.llm.openai_client.OpenAI", side_effect=AssertionError("No provider")):
        assert pilot.main([]) == 0
    saved = json.loads((tmp_path / "data/processed/berkley_pilot_plan.json").read_text())
    assert saved["document_id"] == pilot.DOCUMENT_ID
    assert saved["other_documents_processed"] == saved["comparison_calls"] == 0
    assert not (tmp_path / "data/processed/berkley_pilot_claim.json").exists()


def test_local_plan_retains_full_corpus_and_does_not_include_review_oracle(tmp_path):
    value = corpus(tmp_path)
    with patch("src.llm.openai_client.OpenAI", side_effect=AssertionError("No API")):
        plan = pilot.prepare_plan(value, policy())
    pilot.validate_plan(plan)
    assert plan["pages"] == 83 and plan["full_local_chunks"] == 480
    assert plan["genai_calls"] == plan["providers_constructed"] == 0
    assert plan["planned_initial_calls"] == len(plan["initial"]["batches"])
    assert plan["shared_remaining_budget"] == 50 - plan["planned_initial_calls"]
    assert len({name for group in plan["groups"] for name in group["fields"]}) == 27
    # Persisted batches contain metadata only, never manual expected meanings.
    assert all("manual_reference" not in batch and "golden_items" not in batch
               for batch in plan["initial"]["batches"])


def test_initial_plan_over_50_stops_offline(tmp_path):
    value = corpus(tmp_path)
    from src.agents.grouped_extraction import ExtractionBatch
    fake_batches = [ExtractionBatch("IDENTIFICATION", ("seguradora",), (), batch_index=i)
                    for i in range(51)]
    with patch("src.agents.grouped_extraction.build_semantic_batches", return_value=fake_batches), \
         patch("src.llm.openai_client.OpenAI", side_effect=AssertionError("No API")):
        with pytest.raises(pilot.PilotError, match="hard 50"):
            pilot.prepare_plan(value, policy())


def test_claim_and_manifest_block_replay_even_if_interrupted(tmp_path):
    value, routing_policy = corpus(tmp_path), policy()
    store = pilot.PilotStore(tmp_path)
    store.start(valid_plan(value, routing_policy))
    reservation = {"max_http_attempts": 50, "http_attempts": 1, "budget_remaining": 49,
                   "blocked_reason": "", "events": [{"result_status": "ATTEMPTED"}]}
    store.persist_control(reservation)
    persisted = json.loads(store.manifest_path.read_text())
    assert persisted["request_control"]["http_attempts"] == 1
    assert store.claim_path.is_file() and persisted["references"] == value.references
    with pytest.raises(pilot.PilotError, match="blocks replay"):
        pilot.PilotStore(tmp_path).start(valid_plan(value, routing_policy))


@pytest.mark.parametrize("blocking_file", ["claim", "manifest", "result", "telemetry"])
def test_any_existing_runtime_artifact_blocks_replay(tmp_path, blocking_file):
    store = pilot.PilotStore(tmp_path)
    path = getattr(store, blocking_file + "_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    with pytest.raises(pilot.PilotError, match="blocks replay"):
        store.check_available()


def test_partial_extraction_is_persisted_and_never_marked_complete(tmp_path):
    value, routing_policy = corpus(tmp_path), policy()
    store = pilot.PilotStore(tmp_path)
    store.start(valid_plan(value, routing_policy))
    result = ExtractionResult(field_status={name: FieldStatus.NOT_RETRIEVED
                                           for name in pilot.PolicyExtraction.model_fields})
    store.persist_partial(result)
    persisted = json.loads(store.result_path.read_text())
    assert persisted["complete"] is False
    assert persisted["review_status"] == "PENDING_MANUAL_REVIEW"
    assert len(persisted["extraction"]["field_status"]) == 27


def test_unknown_usage_never_becomes_zero_or_effective_billing(tmp_path):
    events = [
        {"actual_model": "gpt-5.6-luna", "requested_model": "gpt-5.6-luna",
         "input_tokens": 12, "cached_input_tokens": 2, "output_tokens": 5,
         "total_tokens": 17, "latency_ms": 1000, "service_tier": "default"},
        {"actual_model": None, "requested_model": "gpt-5.6-terra",
         "input_tokens": None, "cached_input_tokens": None, "output_tokens": None,
         "total_tokens": None, "latency_ms": 3000, "fallback_level": 1,
         "error_kind": "timeout"},
    ]
    metrics = pilot.execution_metrics({"http_attempts": 2, "events": events}, wall_seconds=5)
    assert metrics["tokens"]["input_tokens"] == {"total": None, "known_sum": 12,
                                                     "unknown_requests": 1}
    assert metrics["performance"]["llm_runtime_seconds"] == 4
    assert metrics["performance"]["local_processing_runtime_seconds"] == 1
    assert metrics["performance"]["median_seconds"] == 2
    assert metrics["performance"]["p95_seconds"] == 3
    assert metrics["standard_rate_counterfactual"]["usd"] is None
    assert metrics["effective_billing"] == "PENDING_DASHBOARD_RECONCILIATION"
    assert metrics["by_actual_model"]["UNKNOWN"]["requests"] == 1


def test_counterfactual_uses_reported_cache_without_reserve_multiplier():
    event = {"actual_model": "gpt-5.6-luna", "input_tokens": 100,
             "cached_input_tokens": 40, "output_tokens": 20}
    prices = {"verified_on": "2026-10-04", "cache_write_input_multiplier": 1.25,
              "models": {"gpt-5.6-luna": {"input": 1, "cached_input": .1, "output": 2}}}
    cost = pilot.counterfactual_cost([event], prices)
    assert cost["usd"] == pytest.approx(.000104)
    assert cost["effective_billing"] == "PENDING_DASHBOARD_RECONCILIATION"


class FakeResponses:
    def __init__(self, store, *, interrupt_at=None):
        self.store, self.interrupt_at = store, interrupt_at
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        manifest = json.loads(self.store.manifest_path.read_text())
        # The fake HTTP endpoint refuses any request that was not durably reserved.
        assert manifest["request_control"]["http_attempts"] == self.calls
        assert self.calls <= 50 and self.store.claim_path.exists()
        if self.calls > 1:
            previous = manifest["provider_returns"][-1]
            assert previous["http_attempt"] == self.calls - 1
            assert previous["response_id"] == f"resp_fake_{self.calls - 1}"
            partial = manifest["partial_extraction"]
            assert len(partial["field_status"]) == 27
            assert partial["retrieval_diagnostics"]["completed_batches"] == self.calls - 1
        assert kwargs["store"] is False
        assert not {"tools", "service_tier"} & kwargs.keys()
        if self.calls == self.interrupt_at:
            raise KeyboardInterrupt()
        return SimpleNamespace(status="completed", output_text="{}", model=kwargs["model"],
            id=f"resp_fake_{self.calls}", _request_id=f"req_fake_{self.calls}", service_tier="default",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15,
                                  input_tokens_details=SimpleNamespace(cached_tokens=0)))


def fake_gateway_factory(record, *, interrupt_at=None):
    def factory(*, settings, policy, store):
        from src.llm.model_routing import RequestControl, RoutedGateway
        from src.llm.openai_client import OpenAIProvider
        responses = FakeResponses(store, interrupt_at=interrupt_at)
        record.append(responses)
        client = SimpleNamespace(responses=responses, max_retries=0)
        raw = OpenAIProvider(settings, client=client, local_json_repair=True,
                             response_callback=store.persist_provider_return)
        control = RequestControl(max_http_attempts=50, persist_callback=store.persist_control)
        return RoutedGateway(raw, policy=policy, control=control)
    return factory


class FakeExtraction:
    def __init__(self, *, gateway, snapshot_callback, **kwargs):
        self.gateway, self.snapshot_callback = gateway, snapshot_callback
        self.result = ExtractionResult(field_status={name: FieldStatus.NOT_RETRIEVED
            for name in pilot.PolicyExtraction.model_fields})

    def extract(self, document, clauses):
        from src.llm.resilience import LLMClientError
        for number in range(51):
            try:
                self.gateway.complete_routed(role="extraction", agent="optimized_extraction",
                    messages=[{"role": "user", "content": f"Return JSON step {number}."}],
                    context={"document_id": pilot.DOCUMENT_ID,
                        "logical_step_id": f"fake-step-{number}", "field_group": "IDENTIFICATION",
                        "retrieval_stage": 0, "fields": ["seguradora"],
                        "page_count_used": 1, "candidate_count": 1})
            except LLMClientError as error:
                self.result.retrieval_diagnostics["stop_reason"] = error.kind
                break
            self.result.retrieval_diagnostics["completed_batches"] = number + 1
            self.snapshot_callback(self.result)
        return self.result


def fake_sqlite_writer(corpus_value, result, root):
    return {"policy": result.policy.model_dump(mode="json"),
            "field_status": result.model_dump(mode="json")["field_status"],
            "retrieval_diagnostics": {"source_reference": {
                "document_id": pilot.DOCUMENT_ID, "document_sha256": pilot.DOCUMENT_SHA256,
                "source_url": corpus_value.metadata["source_url"]}}}


def test_runner_hard50_persisted_before_each_fake_http_and_no_request51(tmp_path, authorized_fake_hash):
    value, routing_policy, record = corpus(tmp_path), policy(), []
    result = pilot.execute_pilot(value, valid_plan(value, routing_policy), routing_policy,
        settings=settings(), root=tmp_path, gateway_factory=fake_gateway_factory(record),
        extractor_factory=FakeExtraction, sqlite_writer=fake_sqlite_writer)
    assert record[0].calls == 50
    assert result["metrics"]["http_attempts"] == 50
    assert result["complete"] is False and result["stop_reason"] == "budget_exhausted"
    manifest = json.loads((tmp_path / "data/processed/berkley_pilot_manifest.json").read_text())
    assert manifest["request_control"]["http_attempts"] == 50
    assert manifest["status"] == "PARTIAL_STOPPED"
    with pytest.raises(pilot.PilotError, match="blocks replay"):
        pilot.execute_pilot(value, valid_plan(value, routing_policy), routing_policy,
            settings=settings(), root=tmp_path, gateway_factory=fake_gateway_factory(record),
            extractor_factory=FakeExtraction, sqlite_writer=fake_sqlite_writer)
    assert len(record) == 1  # A blocked replay never constructs even a fake provider.


def test_interruption_preserves_reserved_attempt_and_previous_partial(tmp_path, authorized_fake_hash):
    value, routing_policy, record = corpus(tmp_path), policy(), []
    with pytest.raises(KeyboardInterrupt):
        pilot.execute_pilot(value, valid_plan(value, routing_policy), routing_policy,
            settings=settings(), root=tmp_path,
            gateway_factory=fake_gateway_factory(record, interrupt_at=3),
            extractor_factory=FakeExtraction, sqlite_writer=fake_sqlite_writer)
    manifest = json.loads((tmp_path / "data/processed/berkley_pilot_manifest.json").read_text())
    assert manifest["status"] == "INTERRUPTED"
    assert manifest["request_control"]["http_attempts"] == 3
    assert len(manifest["partial_extraction"]["field_status"]) == 27
    assert json.loads((tmp_path / "data/processed/berkley_pilot_result.json").read_text())["complete"] is False
    with pytest.raises(pilot.PilotError):
        pilot.PilotStore(tmp_path).check_available()


def test_persistence_failure_prevents_fake_http(tmp_path, authorized_fake_hash, monkeypatch):
    value, routing_policy, record = corpus(tmp_path), policy(), []
    real_atomic = pilot.atomic_json
    def failing_atomic(path, data):
        if path.name == "berkley_pilot_manifest.json" and data.get("request_control", {}).get("http_attempts") == 1:
            raise OSError("Synthetic disk failure")
        return real_atomic(path, data)
    monkeypatch.setattr(pilot, "atomic_json", failing_atomic)
    with pytest.raises(Exception):
        pilot.execute_pilot(value, valid_plan(value, routing_policy), routing_policy,
            settings=settings(), root=tmp_path, gateway_factory=fake_gateway_factory(record),
            extractor_factory=FakeExtraction, sqlite_writer=fake_sqlite_writer)
    assert record[0].calls == 0
    assert (tmp_path / "data/processed/berkley_pilot_claim.json").is_file()


def test_plan_reference_mutation_blocks_factory(tmp_path, authorized_fake_hash):
    value, routing_policy, record = corpus(tmp_path), policy(), []
    plan = valid_plan(value, routing_policy)
    (tmp_path / value.references["manual_reference"]["path"]).write_text("changed")
    with pytest.raises(pilot.PilotError, match="reference changed"):
        pilot.execute_pilot(value, plan, routing_policy, settings=settings(), root=tmp_path,
            gateway_factory=fake_gateway_factory(record), extractor_factory=FakeExtraction,
            sqlite_writer=fake_sqlite_writer)
    assert record == []
    assert not (tmp_path / "data/processed/berkley_pilot_claim.json").exists()


def test_local_quote_validation_does_not_claim_manual_accuracy(tmp_path):
    value = corpus(tmp_path)
    result = ExtractionResult(field_status={name: FieldStatus.NOT_RETRIEVED
                                           for name in pilot.PolicyExtraction.model_fields})
    result.policy.seguradora.valor = "Companhia Teste"
    result.policy.seguradora.pagina = 1
    result.policy.seguradora.trecho_origem = "Seguradora: Companhia Teste"
    result.policy.seguradora.confianca = .9
    result.field_status["seguradora"] = FieldStatus.FOUND
    review = pilot.local_evidence_review(value, result)
    assert review["valid_literal_citations"] == 1
    assert review["invalid_literal_citations"] == 0
    assert review["critical_correct"] is review["overall_correct"] is None
    assert review["unsupported_found_claims"] is review["hallucination_rate"] is None
    assert review["review_status"] == "PENDING_MANUAL_REVIEW"


def test_factory_pins_official_endpoint_and_disables_both_retry_layers(tmp_path):
    value, routing_policy = corpus(tmp_path), policy()
    store = pilot.PilotStore(tmp_path)
    store.start(valid_plan(value, routing_policy))
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: pytest.fail("No HTTP")),
                             max_retries=0)
    from dataclasses import replace
    with patch("openai.OpenAI", return_value=client) as constructor:
        gateway = pilot.build_gateway(settings=replace(settings(), max_retries=3, timeout_seconds=120),
                                      policy=routing_policy, store=store)
    assert constructor.call_args.kwargs["base_url"] == "https://api.openai.com/v1"
    assert constructor.call_args.kwargs["timeout"] == 60
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert gateway.gateway._settings.max_retries == 1
    assert gateway.gateway._local_json_repair is True
    assert gateway.gateway.response_callback == store.persist_provider_return
    assert gateway.control.http_attempts == 0


def test_unapproved_environment_model_cannot_construct_pilot_provider(tmp_path, authorized_fake_hash):
    from src.llm.model_routing import ModelRoutingPolicy
    configured = ModelRoutingPolicy.from_env({"MODEL_ROUTING_EXTRACTION_PRIMARY": "gpt-unapproved"})
    value, record = corpus(tmp_path), []
    with pytest.raises(pilot.PilotError, match="authorized pilot models"):
        pilot.execute_pilot(value, valid_plan(value, configured), configured,
            settings=settings(), root=tmp_path, gateway_factory=fake_gateway_factory(record),
            extractor_factory=FakeExtraction, sqlite_writer=fake_sqlite_writer)
    assert record == []
    assert not (tmp_path / "data/processed/berkley_pilot_claim.json").exists()


def test_forged_source_or_page_is_reported_without_semantic_accuracy_claim(tmp_path):
    value = corpus(tmp_path)
    result = ExtractionResult(field_status={name: FieldStatus.NOT_RETRIEVED
                                           for name in pilot.PolicyExtraction.model_fields})
    for name, page, quote in (("seguradora", 84, "Seguradora: Companhia Teste"),
                              ("side_a", 1, "Texto inexistente na fonte")):
        evidence = getattr(result.policy, name)
        evidence.valor, evidence.pagina, evidence.trecho_origem, evidence.confianca = "x", page, quote, .9
        result.field_status[name] = FieldStatus.FOUND
    review = pilot.local_evidence_review(value, result)
    assert review["invalid_page_references"] == 1
    assert review["invalid_literal_citations"] == 2
    assert review["valid_literal_citations"] == 0
    assert review["overall_correct"] is review["unsupported_found_claims"] is None


def forensic_store(tmp_path):
    value, routing_policy = corpus(tmp_path), policy()
    store = pilot.PilotStore(tmp_path)
    store.start(valid_plan(value, routing_policy))
    return store


def attempted_event(number=1, **changes):
    return {"logical_step_id": f"fake-step-{number}", "result_status": "ATTEMPTED",
            "response_id": None, "request_id": None, **changes}


def test_forensic_not_started_distinguishes_plan_from_actual_requests(tmp_path):
    pilot.atomic_json(pilot.output_path(tmp_path, "berkley_pilot_plan.json"),
                      {"planned_initial_calls": 7})
    report = pilot.forensic_recovery(tmp_path)
    assert report["status"] == "NOT_STARTED"
    assert report["planned_initial_calls"] == 7
    assert report["replay_blocked"] is False
    assert not any(report["runtime_files"].values())
    for name in ("reserved_attempts_found", "sent_attempts_found", "completed_attempts_found",
                 "response_ids_found", "results_persisted_found", "uncertain_attempts_found"):
        assert report[name] == 0
    assert report["remaining_budget"] == 50


def test_forensic_reserved_without_response_stays_uncertain_and_blocks_replay(tmp_path):
    store = forensic_store(tmp_path)
    store.persist_control({"max_http_attempts": 50, "http_attempts": 1, "remaining": 49,
                           "blocked_reason": None, "events": [attempted_event()]})
    report = pilot.forensic_recovery(tmp_path)
    assert report["reserved_attempts_found"] == 1
    assert report["sent_attempts_found"] == report["completed_attempts_found"] == 0
    assert report["response_ids_found"] == 0
    assert report["uncertain_attempts_found"] == 1
    assert report["uncertain_attempt_numbers"] == [1]
    assert report["uncertainty_status"] == "UNCERTAIN_PREVIOUS_ATTEMPT"
    assert report["remaining_budget"] == 49
    assert report["replay_blocked"] is True
    with pytest.raises(pilot.PilotError):
        pilot.PilotStore(tmp_path).check_available()


def test_forensic_response_received_before_merge_is_not_guessed_complete(tmp_path):
    store = forensic_store(tmp_path)
    store.persist_control({"max_http_attempts": 50, "http_attempts": 1, "remaining": 49,
                           "blocked_reason": None, "events": [attempted_event()]})
    store.persist_provider_return({"response_id": "resp_fake_1", "request_id": "req_fake_1",
                                   "status": "completed", "actual_model": "gpt-5.6-luna"})
    report = pilot.forensic_recovery(tmp_path)
    assert report["response_ids_found"] == report["sent_attempts_found"] == 1
    assert report["completed_attempts_found"] == report["results_persisted_found"] == 0
    assert report["returned_unmerged_attempts_found"] == 1
    assert report["uncertain_attempts_found"] == 0
    assert report["replay_blocked"] is True


@pytest.mark.parametrize("runtime_file", ["claim", "manifest", "result", "telemetry"])
def test_forensic_orphan_runtime_file_blocks_replay_without_inventing_counts(tmp_path, runtime_file):
    store = pilot.PilotStore(tmp_path)
    path = getattr(store, runtime_file + "_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    report = pilot.forensic_recovery(tmp_path)
    assert report["status"] == "INCONSISTENT_BLOCKED"
    assert report["replay_blocked"] is True
    assert report["reserved_attempts_found"] is report["completed_attempts_found"] is None


@pytest.mark.parametrize("corrupt_json", ["{", "[]", "null"])
def test_forensic_corrupt_or_wrong_shape_manifest_remains_unknown(tmp_path, corrupt_json):
    store = forensic_store(tmp_path)
    store.manifest_path.write_text(corrupt_json, encoding="utf-8")
    report = pilot.forensic_recovery(tmp_path)
    assert report["status"] == "INCONSISTENT_BLOCKED"
    assert report["replay_blocked"] is True
    assert report["reserved_attempts_found"] is report["sent_attempts_found"] is None
    assert report["remaining_budget"] is None


@pytest.mark.parametrize("changes", [
    {"http_attempts": 51, "events": [attempted_event()] * 51},
    {"http_attempts": 1, "events": []},
    {"http_attempts": True, "events": [attempted_event()]},
])
def test_forensic_inconsistent_attempt_ledger_never_looks_available(tmp_path, changes):
    store = forensic_store(tmp_path)
    state = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    state["request_control"].update(changes)
    pilot.atomic_json(store.manifest_path, state)
    report = pilot.forensic_recovery(tmp_path)
    assert report["status"] == "INCONSISTENT_BLOCKED" and report["replay_blocked"] is True
    assert report["reserved_attempts_found"] is None


def test_forensic_receipt_outside_reserved_ledger_is_inconsistent(tmp_path):
    store = forensic_store(tmp_path)
    store.persist_control({"max_http_attempts": 50, "http_attempts": 1, "remaining": 49,
                           "blocked_reason": None, "events": [attempted_event()]})
    state = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    state["provider_returns"] = [{"http_attempt": 2, "response_id": "resp_fake_2"}]
    pilot.atomic_json(store.manifest_path, state)
    report = pilot.forensic_recovery(tmp_path)
    assert report["status"] == "INCONSISTENT_BLOCKED" and report["replay_blocked"] is True
    assert report["response_ids_found"] is None


def test_response_id_is_durable_before_output_text_is_read(tmp_path, authorized_fake_hash):
    value, routing_policy, providers = corpus(tmp_path), policy(), []
    class ReceiptBeforeDecode:
        status, model, id, _request_id, service_tier = (
            "completed", "gpt-5.6-luna", "resp_immediate", "req_immediate", "default")
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15,
                                input_tokens_details=SimpleNamespace(cached_tokens=0))
        def __init__(self, store):
            self.store = store
        @property
        def output_text(self):
            state = json.loads(self.store.manifest_path.read_text(encoding="utf-8"))
            assert state["provider_returns"][-1]["response_id"] == "resp_immediate"
            assert state["request_control"]["http_attempts"] == 1
            raise KeyboardInterrupt()
    def gateway_factory(*, settings, policy, store):
        from src.llm.model_routing import RequestControl, RoutedGateway
        from src.llm.openai_client import OpenAIProvider
        client = SimpleNamespace(max_retries=0,
            responses=SimpleNamespace(create=lambda **kwargs: ReceiptBeforeDecode(store)))
        raw = OpenAIProvider(settings, client=client, response_callback=store.persist_provider_return)
        providers.append(raw)
        return RoutedGateway(raw, policy=policy,
            control=RequestControl(max_http_attempts=50, persist_callback=store.persist_control))
    with pytest.raises(KeyboardInterrupt):
        pilot.execute_pilot(value, valid_plan(value, routing_policy), routing_policy,
            settings=settings(), root=tmp_path, gateway_factory=gateway_factory,
            extractor_factory=FakeExtraction, sqlite_writer=fake_sqlite_writer)
    report = pilot.forensic_recovery(tmp_path)
    assert report["response_ids_found"] == report["returned_unmerged_attempts_found"] == 1
    assert report["completed_attempts_found"] == 0
    assert report["replay_blocked"] is True
    assert len(providers) == 1
