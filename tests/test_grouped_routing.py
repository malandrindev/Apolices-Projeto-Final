
"""E2 governance/status regression tests. All providers are deterministic offline doubles."""
from __future__ import annotations

import hashlib
import json

import pytest

from src.agents.comparison import ComparisonAgent
from src.agents.extraction import Phase2Report
from src.agents.grouped_extraction import ExtractionBatch, GroupedExtractionAgent
from src.agents.ocr import PageText, ProcessedDocument
from src.agents.segmentation import SegmentationAgent
from src.config import ExtractionOptimizationSettings, ExtractionRouting
from src.llm.model_routing import ModelRoutingPolicy, RequestControl, RoutedGateway
from src.llm.resilience import LLMClientError
from src.retrieval.local import RetrievalCandidate
from src.schemas.clause import ClauseCategory, ClauseChunk, SourcePage
from src.schemas.comparison import DifferenceClass
from src.schemas.policy import NOT_FOUND, FieldEvidence, PolicyExtraction
from src.schemas.retrieval import FieldStatus, GroupExtractionResponse
from src.workspace import build_matrix, executive_metrics


def document(text):
    return ProcessedDocument(
        source_name="synthetic-e2.pdf", sha256=hashlib.sha256(text.encode()).hexdigest(),
        size_bytes=len(text.encode()), media_type="application/pdf",
        pages=[PageText(page_number=1, text=text, extraction_method="native")],
        processed_at="2026-10-04T00:00:00Z", cache_key="synthetic-e2",
    )


def response(payload, facts=None, ambiguous=()):
    rows = []
    for name in payload["field_names"]:
        evidence = FieldEvidence()
        status = "NOT_FOUND"
        if name in (facts or {}):
            value, quote, confidence = facts[name]
            for source in payload["sources"]:
                if name not in source["fields"]:
                    continue
                for page in source["pages"]:
                    if quote in page["text"]:
                        evidence = FieldEvidence(valor=value, trecho_origem=quote,
                                                 pagina=page["page_number"], confianca=confidence)
                        status = "FOUND"
        if name in ambiguous:
            status = "AMBIGUOUS"
        rows.append({"field_name": name, "evidence": evidence.model_dump(), "status": status})
    return json.dumps({"fields": rows})


class Provider:
    provider = "offline-e2"
    def __init__(self, handler=None):
        self.calls, self.events = [], []
        self.handler = handler or (lambda call, payload: response(payload))

    def complete(self, **call):
        self.calls.append(call)
        return self.handler(call, json.loads(call["messages"][1]["content"]))


def run(text, provider, tmp_path=None, budget=50, snapshots=None):
    gateway = RoutedGateway(provider, ModelRoutingPolicy(), RequestControl(budget))
    doc = document(text)
    chunks = SegmentationAgent(gateway=None).segment(doc)
    agent = GroupedExtractionAgent(
        gateway=gateway, routing=ExtractionRouting("unused-fast", "unused-medium", "unused-verifier"),
        optimization=ExtractionOptimizationSettings(strategy="optimized"),
        processed_dir=tmp_path, snapshot_callback=snapshots.append if snapshots is not None else None,
    )
    return agent.extract(doc, chunks), gateway


def test_technical_status_is_local_and_e1_response_schema_stays_four_states():
    assert GroupExtractionResponse.model_json_schema()["$defs"]["FieldStatus"]["enum"] == [
        "FOUND", "NOT_FOUND", "NOT_RETRIEVED", "AMBIGUOUS",
    ]
    with pytest.raises(ValueError):
        GroupExtractionResponse(fields=[{"field_name": "premio", "status": "TECHNICAL_UNAVAILABLE"}])
    report = Phase2Report(source_name="A", sha256="a", clause_count=0, policy=PolicyExtraction(),
                          field_status={"premio": FieldStatus.TECHNICAL_UNAVAILABLE})
    assert Phase2Report.model_validate_json(report.model_dump_json()).field_status["premio"] == FieldStatus.TECHNICAL_UNAVAILABLE


def test_valid_primary_objective_evidence_does_not_go_to_stronger_model(tmp_path):
    quote = "Premio total de R$ 1.000,00."
    provider = Provider(lambda call, payload: response(
        payload, {"premio": ("R$ 1.000,00", quote, .95)}))
    result, gateway = run(quote, provider, tmp_path)
    assert result.field_status["premio"] == FieldStatus.FOUND
    calls = [event for event in gateway.events if "premio" in event["fields"]]
    assert len(calls) == 1 and calls[0]["requested_model"] == gateway.policy.role("extraction").primary
    assert calls[0]["semantic_level"] == 0
    assert all("content" not in event and "messages" not in event for event in gateway.events)
    # Aggregate cache keeps zero inference on a fresh governed gateway.
    result2, gateway2 = run(quote, Provider(), tmp_path)
    assert result2.retrieval_diagnostics["aggregate_cache_hit"]
    assert gateway2.control.http_attempts == 0


def test_local_retrieval_precedes_hard_case_and_sol_is_not_duplicated():
    quote = "Side A cobre perdas nao indenizaveis."
    def handler(call, payload):
        ambiguous = ("side_a",) if call["model"] != "gpt-5.6-sol" else ()
        return response(payload, {"side_a": ("Perdas nao indenizaveis", quote, .95)}, ambiguous)
    result, gateway = run(quote, Provider(handler))
    promoted = [event for event in gateway.events if event["semantic_escalation"]]
    assert promoted and all(event["retrieval_stage"] == 4 for event in promoted)
    sol_a = [event for event in gateway.events
             if event["requested_model"] == "gpt-5.6-sol" and "side_a" in event["fields"]]
    assert len(sol_a) == 1
    assert result.field_status["side_a"] == FieldStatus.FOUND
    assert result.retrieval_diagnostics["semantic_escalations"]["side_a"] == 1


def test_budget_stop_preserves_valid_value_and_27_status_snapshots_without_complete_cache(tmp_path):
    snapshots = []
    text = "LMG: R$ 1.000.000,00\nSide A cobre perdas nao indenizaveis."
    result, gateway = run(text, Provider(), tmp_path, budget=1, snapshots=snapshots)
    assert gateway.control.http_attempts == 1
    assert result.field_status["limite_maximo_garantia"] == FieldStatus.FOUND
    assert result.policy.limite_maximo_garantia.valor == "R$ 1.000.000,00"
    assert result.field_status["side_a"] == FieldStatus.TECHNICAL_UNAVAILABLE
    assert result.retrieval_diagnostics["stop_reason"] == "budget_exhausted"
    assert snapshots and all(set(item.field_status) == set(PolicyExtraction.model_fields) for item in snapshots)
    assert not list((tmp_path / "grouped_results").glob("*.json"))


def test_authentication_aborts_after_safe_partial_snapshot(tmp_path):
    snapshots = []
    def fail(call, payload):
        raise LLMClientError("offline", "authentication")
    with pytest.raises(LLMClientError) as caught:
        run("LMG: R$ 100,00\nSide A cobre perdas.", Provider(fail), tmp_path, snapshots=snapshots)
    assert caught.value.kind == "authentication"
    assert snapshots[-1].field_status["limite_maximo_garantia"] == FieldStatus.FOUND
    assert FieldStatus.TECHNICAL_UNAVAILABLE in snapshots[-1].field_status.values()
    assert len(snapshots[-1].field_status) == 27
    assert not list((tmp_path / "grouped_results").glob("*.json"))


def test_schema_failure_uses_technical_fallback_with_validator_feedback():
    quote = "Premio total de R$ 100,00."
    def handler(call, payload):
        if call["model"] == "gpt-5.6-luna":
            return '{"fields": []}'
        return response(payload, {"premio": ("R$ 100,00", quote, .95)})
    result, gateway = run(quote, Provider(handler))
    events = [item for item in gateway.events if "premio" in item["fields"]]
    assert len(events) == 2
    assert events[0]["evidence_validation_status"] == "SCHEMA_INVALID"
    assert events[1]["fallback_reason"] == "schema_invalid"
    assert events[1]["semantic_level"] == 0
    assert result.field_status["premio"] == FieldStatus.FOUND


def test_evidence_failure_expands_before_semantic_not_technical_fallback():
    quote = "Side A cobre perdas nao indenizaveis."
    def handler(call, payload):
        raw = json.loads(response(payload, {"side_a": ("Perdas nao indenizaveis", quote, .95)}))
        if call["model"] != "gpt-5.6-sol":
            for row in raw["fields"]:
                if row["field_name"] == "side_a":
                    row["evidence"]["trecho_origem"] = "trecho inventado"
        return json.dumps(raw)
    result, gateway = run(quote, Provider(handler))
    a_events = [item for item in gateway.events if "side_a" in item["fields"]]
    assert a_events[0]["evidence_validation_status"] == "EVIDENCE_INVALID"
    assert all(item["fallback_level"] == 0 for item in a_events)
    assert a_events[-1]["retrieval_stage"] == 4
    assert result.field_status["side_a"] == FieldStatus.FOUND


def test_cg_shortcut_does_not_drop_lmg_retroactivity_or_premium_topics():
    text = ("CONDICOES GERAIS\n"
            "Limite maximo de garantia e a importancia fixada na Apolice.\n"
            "Data retroativa e a data estipulada nas especificacoes.\n"
            "O premio sera pago conforme as condicoes da Apolice.\n"
            "O tomador e a sociedade contratante.")
    provider = Provider()
    result, gateway = run(text, provider)
    requested = {name for event in gateway.events for name in event["fields"]}
    assert {"limite_maximo_garantia", "data_retroativa", "premio"} <= requested
    assert "tomador_segurado" not in requested
    assert result.field_status["tomador_segurado"] == FieldStatus.NOT_RETRIEVED
    assert result.policy.tomador_segurado.valor == NOT_FOUND


@pytest.mark.parametrize("field,value,quote", [
    ("limite_maximo_garantia", "R$ 1.000,00", "Sublimite de R$ 1.000,00 para custos de defesa."),
    ("premio", "Premio", "O Tomador tem direito a devolucao do Premio."),
    ("limite_maximo_garantia", "LMG", "O LMG e estabelecido na Especificacao."),
    ("sublimites", "Sublimites", "Sublimites previstos nas condicoes."),
    ("retencao_franquia", "Franquia", "Franquia indicada na especificacao."),
    ("limite_maximo_garantia", "R$ 1.000,00", "A franquia tem limite de R$ 1.000,00."),
    ("retencao_franquia", "Retencao R$ 1.000,00", "Franquia de R$ 1.000,00."),
    ("territorialidade", "Brasil", "Foro competente no Brasil."),
    ("jurisdicao_lei", "Mundial", "Territorialidade mundial."),
    ("side_c", "Cobertura incondicional", "Side C cobre valores mobiliarios desde que contratada."),
])
def test_generic_cross_topic_unit_and_conditional_guards(field, value, quote):
    doc = document(quote)
    chunk = ClauseChunk(clause_id="topic", title="Fonte", category=ClauseCategory.OTHER,
                        text=quote, page_start=1, page_end=1,
                        source_pages=[SourcePage(page_number=1, text=quote)])
    item = RetrievalCandidate("topic", chunk, 1.0, (), (), (field,))
    batch = ExtractionBatch("topic", (field,), (item,))
    parsed = GroupExtractionResponse(fields=[{
        "field_name": field, "status": "FOUND",
        "evidence": FieldEvidence(valor=value, pagina=1, trecho_origem=quote, confianca=.95),
    }])
    assert GroupedExtractionAgent.evidence_errors(parsed, batch, doc) == [field]


def test_technical_state_prevents_advantage_even_with_preserved_money_evidence():
    quote = "LMG: R$ 100,00"
    evidence = FieldEvidence(valor="R$ 100,00", pagina=1, trecho_origem=quote, confianca=.95)
    reference = Phase2Report(source_name="A", sha256="a", clause_count=1,
                            policy=PolicyExtraction(limite_maximo_garantia=evidence),
                            field_status={"limite_maximo_garantia": FieldStatus.TECHNICAL_UNAVAILABLE})
    candidate = reference.model_copy(deep=True, update={"source_name": "B", "sha256": "b",
                                                       "field_status": {}})
    comparison = ComparisonAgent(gateway=Provider(), model_strong="unused").compare(reference, candidate)
    difference = next(item for item in comparison.differences if item.field_name == "limite_maximo_garantia")
    assert difference.classification == DifferenceClass.NOT_COMPARABLE
    row = next(item for item in build_matrix(reference, [candidate], [comparison])
               if item["field_name"] == "limite_maximo_garantia")
    assert row["candidates"]["b"]["classification"] == DifferenceClass.NOT_COMPARABLE.value
    assert executive_metrics(comparison)["favorable_candidate"] == 0
    assert executive_metrics(comparison)["restrictive_candidate"] == 0

def test_exhausted_invalid_response_stays_technical_without_semantic_promotion():
    quote = "Side A cobre perdas nao indenizaveis."
    def handler(call, payload):
        if "side_a" in payload["field_names"]:
            raise LLMClientError("offline", "invalid_response")
        return response(payload)
    result, gateway = run(quote, Provider(handler))
    assert result.field_status["side_a"] == FieldStatus.TECHNICAL_UNAVAILABLE
    assert not any(event["semantic_escalation"] for event in gateway.events)
    assert not any(event["requested_model"] == gateway.policy.role("verifier").primary for event in gateway.events)

def test_split_hard_case_keeps_first_batch_ambiguity():
    quote = "Side A cobre perdas nao indenizaveis."
    texts = (quote + "\n" + "Contexto documental primeiro. " * 70,
             quote + "\n" + "Contexto documental segundo. " * 70)
    doc = document("\n".join(texts))
    chunks = [ClauseChunk(clause_id=f"split-{index}", title="Side A",
        category=ClauseCategory.COVERAGE, text=text, page_start=1, page_end=1,
        source_pages=[SourcePage(page_number=1, text=text)]) for index,text in enumerate(texts)]
    sol_batches = 0
    def handler(call,payload):
        nonlocal sol_batches
        ambiguous = ()
        if "side_a" in payload["field_names"]:
            if call["model"] == "gpt-5.6-sol":
                sol_batches += 1
                ambiguous = ("side_a",) if sol_batches == 1 else ()
            else:
                ambiguous = ("side_a",)
        return response(payload, {"side_a": ("Perdas nao indenizaveis",quote,.95)},ambiguous)
    gateway = RoutedGateway(Provider(handler))
    agent = GroupedExtractionAgent(gateway=gateway,
        routing=ExtractionRouting("unused-fast","unused-medium","unused-verifier"),
        optimization=ExtractionOptimizationSettings(strategy="optimized",batch_chars=4000))
    result = agent.extract(doc,chunks)
    assert sol_batches == 2
    assert result.field_status["side_a"] == FieldStatus.AMBIGUOUS
    assert result.policy.side_a.valor == "Perdas nao indenizaveis"
    assert result.retrieval_diagnostics["semantic_escalations"]["side_a"] == 1
