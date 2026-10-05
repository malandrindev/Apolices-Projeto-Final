"""Offline E1 checks: batching, evidence, routing, ambiguity and cache/resume."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.agents.comparison import ComparisonAgent
from src.agents.extraction import Phase2Pipeline, Phase2Report
from src.agents.grouped_extraction import (
    ExtractionBatch, GroupedExtractionAgent, build_semantic_batches,
)
from src.agents.ocr import PageText, ProcessedDocument
from src.agents.segmentation import SegmentationAgent
from src.config import ExtractionOptimizationSettings, ExtractionRouting
from src.llm.resilience import LLMClientError
from src.retrieval.local import LocalRetrievalIndex, RetrievalCandidate
from src.schemas.clause import ClauseCategory, ClauseChunk, SourcePage
from src.schemas.comparison import DifferenceClass
from src.schemas.policy import NOT_FOUND, FieldEvidence, PolicyExtraction
from src.schemas.retrieval import FieldStatus, GroupExtractionResponse

ROUTING = ExtractionRouting("simple-local", "semantic-local", "verify-local")


def doc(*texts):
    raw = "\n".join(texts)
    return ProcessedDocument(
        source_name="synthetic-e1.pdf", sha256=hashlib.sha256(raw.encode()).hexdigest(),
        size_bytes=max(1, len(raw.encode())), media_type="application/pdf",
        pages=[PageText(page_number=i, text=text, extraction_method="native")
               for i, text in enumerate(texts, 1)],
        processed_at="2026-10-04T00:00:00Z", cache_key="synthetic-e1",
    )


def candidate(chunk_id, text, fields=("side_a",), page=1, title="COBERTURAS"):
    chunk = ClauseChunk(clause_id=chunk_id, title=title, category=ClauseCategory.COVERAGE,
                        page_start=page, page_end=page, text=text,
                        source_pages=[SourcePage(page_number=page, text=text)])
    return RetrievalCandidate(chunk_id, chunk, 1.0, ("keyword",), (), tuple(fields))


def response_for(payload, facts=None, *, ambiguous=()):
    facts = facts or {}
    rows = []
    for name in payload["field_names"]:
        evidence = FieldEvidence()
        status = "AMBIGUOUS" if name in ambiguous else "NOT_FOUND"
        if name in facts:
            value, quote, confidence = facts[name]
            pages = [page for source in payload["sources"] if name in source["fields"]
                     for page in source["pages"] if quote in page["text"]]
            if pages:
                evidence = FieldEvidence(valor=value, pagina=pages[0]["page_number"],
                                         trecho_origem=quote, confianca=confidence)
                status = "AMBIGUOUS" if name in ambiguous else "FOUND"
        rows.append({"field_name": name, "evidence": evidence.model_dump(), "status": status})
    return json.dumps({"fields": rows})


class Gateway:
    provider = "offline-e1"
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or (lambda call, payload: response_for(payload))

    def complete(self, **call):
        self.calls.append(call)
        return self.handler(call, json.loads(call["messages"][1]["content"]))


def run(document, gateway, tmp_path=None, **options):
    optimization = ExtractionOptimizationSettings(strategy="optimized", **options)
    clauses = SegmentationAgent(gateway=None).segment(document)
    return GroupedExtractionAgent(
        gateway=gateway, optimization=optimization, routing=ROUTING,
        processed_dir=tmp_path,
    ).extract(document, clauses)


def test_batches_fill_multiple_fields_dedup_and_retain_whole_sources():
    shared = candidate("same", "Side A e Side B concedidas.", ("side_a",))
    shared_b = replace(shared, fields=("side_b",))
    other = candidate("other", "Custos de defesa. " + "texto " * 120, ("custos_defesa",))
    plan = SimpleNamespace(group_id="CORE_COVERAGES", fields=("side_a", "side_b", "custos_defesa"),
                           candidates=(shared, shared_b, other), stage=0)
    batches = build_semantic_batches(plan, max_chars=1100)
    assert {c.chunk_id for batch in batches for c in batch.candidates} == {"same", "other"}
    assert sum(c.chunk_id == "same" for batch in batches for c in batch.candidates) == 1
    assert all(batch.char_count <= 1100 for batch in batches)
    assert {"side_a", "side_b"} <= set(batches[0].fields)
    assert "".join(c.text for batch in batches for c in batch.candidates) == shared.text + other.text
    assert batches[0].pages == (1,)
    assert batches[0].text == json.dumps(batches[0].payload(), ensure_ascii=False, sort_keys=True)


def test_citation_must_exist_in_sent_fragment_and_original_page():
    source = doc("Side A concedida. INTERVALO OMITIDO. Side B concedida.")
    fragments = (candidate("first", "Side A concedida.", ("side_a",)),
                 candidate("second", "Side B concedida.", ("side_a",)))
    batch = ExtractionBatch("CORE_COVERAGES", ("side_a",), fragments)
    forged = GroupExtractionResponse(fields=[{
        "field_name": "side_a", "status": "FOUND",
        "evidence": FieldEvidence(valor="Cobertura", pagina=1,
                                 trecho_origem="Side A concedida.Side B concedida.", confianca=.9),
    }])
    assert GroupedExtractionAgent.evidence_errors(forged, batch, source) == ["side_a"]
    fake_source = doc("texto sem a evidencia")
    valid = GroupExtractionResponse(fields=[{
        "field_name": "side_a", "status": "FOUND",
        "evidence": FieldEvidence(valor="Side A concedida", pagina=1,
                                 trecho_origem="Side A concedida.", confianca=.9),
    }])
    assert GroupedExtractionAgent.evidence_errors(valid, batch, fake_source) == ["side_a"]


@pytest.mark.parametrize("name,value,quote,text", [
    ("limite_maximo_garantia", "R$ 9.000.000,00", "LMG R$ 1.000.000,00.", "LMG R$ 1.000.000,00."),
    ("data_retroativa", "01/02/2026", "Data retroativa 01/01/2026.", "Data retroativa 01/01/2026."),
    ("side_b", "Side B concedida", "Side A concedida.", "Side A concedida."),
    ("limite_maximo_garantia", "R$ 1.000,00", "Franquia R$ 1.000,00.", "Franquia R$ 1.000,00."),
])
def test_objective_and_cross_field_evidence_failures(name, value, quote, text):
    source = doc(text)
    batch = ExtractionBatch("test", (name,), (candidate("c1", text, (name,), title="LIMITES"),))
    output = GroupExtractionResponse(fields=[{
        "field_name": name, "status": "FOUND",
        "evidence": FieldEvidence(valor=value, pagina=1, trecho_origem=quote, confianca=.99),
    }])
    assert GroupedExtractionAgent.evidence_errors(output, batch, source) == [name]


def test_accented_explicit_labels_are_resolved_locally_without_extraction_requests():
    source = doc(
        "Seguradora: Seguradora Exemplo\nNúmero da apólice: DO-001\n"
        "Início da vigência: 01/01/2026\nFim da vigência: 31/12/2026\n"
        "Limite máximo de garantia: R$ 1.000.000,00\nPrêmio: R$ 1.000,00\n"
        "Retenção: R$ 100,00\nMoeda: BRL"
    )
    gateway = Gateway()
    result = run(source, gateway)
    assert result.policy.vigencia_inicio.valor == "01/01/2026"
    assert result.policy.limite_maximo_garantia.valor == "R$ 1.000.000,00"
    assert result.policy.premio.valor == "R$ 1.000,00"
    assert result.policy.retencao_franquia.valor == "R$ 100,00"
    locally_found = set(result.retrieval_diagnostics["deterministic_evidence"])
    assert {"vigencia_inicio", "vigencia_fim", "limite_maximo_garantia", "premio"} <= locally_found
    assert all("limite_maximo_garantia" not in json.loads(call["messages"][1]["content"])["field_names"]
               for call in gateway.calls)


def test_semantic_multi_field_request_and_completed_cache(tmp_path):
    source = doc("1. COBERTURAS\nSide A concedida. Side B concedida.")
    facts = {"side_a": ("Side A concedida", "Side A concedida.", .9),
             "side_b": ("Side B concedida", "Side B concedida.", .9)}
    gateway = Gateway(lambda call, payload: response_for(payload, facts))
    result = run(source, gateway, tmp_path)
    semantic = [call for call in gateway.calls if call["agent"] == "optimized_interpretation"]
    assert any({"side_a", "side_b"} <= set(json.loads(call["messages"][1]["content"])["field_names"])
               for call in semantic)
    assert {call["model"] for call in semantic} == {"semantic-local"}
    assert result.field_status["side_a"] == FieldStatus.FOUND
    assert result.policy.side_a.pagina == 1
    replay = Gateway(lambda call, payload: pytest.fail("Completed cache must avoid new inference."))
    cached = run(source, replay, tmp_path)
    assert not replay.calls
    assert cached.retrieval_diagnostics["calls_executed"] == 0
    assert cached.retrieval_diagnostics["aggregate_cache_hit"] is True
    assert cached.policy == result.policy


def test_missing_and_insufficient_retrieval_have_distinct_states():
    empty = run(doc(""), Gateway(lambda call, payload: pytest.fail("No text must not call provider.")))
    assert set(empty.field_status.values()) == {FieldStatus.NOT_RETRIEVED}
    complete = run(doc("Texto irrelevante sobre astronomia."), Gateway())
    assert complete.field_status["side_a"] == FieldStatus.NOT_FOUND
    assert "nao prova ausencia" in complete.retrieval_diagnostics["field_diagnostics"]["side_a"]["reason"]


def test_invalid_first_output_is_verified_once_and_replayed_without_new_calls(tmp_path):
    source = doc("1. COBERTURAS\nSide A concedida.")
    def handle(call, payload):
        if "side_a" in payload["field_names"] and call["agent"] != "optimized_verification":
            result = json.loads(response_for(payload))
            for item in result["fields"]:
                if item["field_name"] == "side_a":
                    item.update(status="FOUND", evidence=FieldEvidence(
                        valor="Inventado", pagina=1, trecho_origem="CITACAO INVENTADA", confianca=.99,
                    ).model_dump())
            return json.dumps(result)
        return response_for(payload, {"side_a": ("Side A concedida", "Side A concedida.", .9)})
    gateway = Gateway(handle)
    result = run(source, gateway, tmp_path)
    verifications = [call for call in gateway.calls if call["agent"] == "optimized_verification"]
    assert len(verifications) == 1
    assert verifications[0]["model"] == "verify-local"
    assert json.loads(verifications[0]["messages"][1]["content"])["field_names"] == ["side_a"]
    assert result.field_status["side_a"] == FieldStatus.FOUND
    assert result.policy.side_a.valor == "Side A concedida"
    replay = Gateway(lambda call, payload: pytest.fail("Repaired complete result should be cached."))
    assert run(source, replay, tmp_path).policy.side_a.valor == result.policy.side_a.valor


def test_api_failure_propagates_without_failed_cache_and_completed_batches_resume(tmp_path):
    source = doc("1. COBERTURAS\nSide A concedida.", "2. EXCLUSOES\nAtos dolosos excluidos.")
    facts = {"side_a": ("Side A concedida", "Side A concedida.", .9),
             "exclusoes": ("Atos dolosos excluidos", "Atos dolosos excluidos.", .9)}
    def fail_exclusions(call, payload):
        if "exclusoes" in payload["field_names"]:
            raise LLMClientError("offline-e1", "quota")
        return response_for(payload, facts)
    with pytest.raises(LLMClientError):
        run(source, Gateway(fail_exclusions), tmp_path)
    assert not list((tmp_path / "grouped_results").glob("*.json"))
    resumed_gateway = Gateway(lambda call, payload: response_for(payload, facts))
    resumed = run(source, resumed_gateway, tmp_path)
    assert resumed.cache_hits >= 1
    assert resumed.policy.exclusoes.valor == "Atos dolosos excluidos"
    assert all("side_a" not in json.loads(call["messages"][1]["content"])["field_names"]
               for call in resumed_gateway.calls)


def test_verifier_preserves_ambiguity_across_split_contexts():
    source = doc("1. COBERTURAS\nSide A concedida. " + "texto neutro " * 190,
                 "2. COBERTURAS\nSide A concedida. " + "texto neutro " * 190)
    stage_counts = {}
    def handle(call, payload):
        if "side_a" not in payload["field_names"]:
            return response_for(payload)
        stage = call["agent"]
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        return response_for(payload, {"side_a": ("Side A concedida", "Side A concedida.", .9)},
                            ambiguous=("side_a",) if stage_counts[stage] == 1 else ())
    result = run(source, Gateway(handle), batch_chars=4000)
    assert stage_counts["optimized_verification"] >= 2
    assert result.field_status["side_a"] == FieldStatus.AMBIGUOUS


def test_low_confidence_is_selectively_verified_and_remains_explicit():
    source = doc("1. COBERTURAS\nSide A concedida.")
    facts = {"side_a": ("Side A concedida", "Side A concedida.", .6)}
    gateway = Gateway(lambda call, payload: response_for(payload, facts))
    result = run(source, gateway)
    verifications = [call for call in gateway.calls if call["agent"] == "optimized_verification"]
    assert len(verifications) == 1
    assert json.loads(verifications[0]["messages"][1]["content"])["field_names"] == ["side_a"]
    assert result.policy.side_a.valor != NOT_FOUND
    assert result.field_status["side_a"] == FieldStatus.AMBIGUOUS
    assert any(issue.code == "low_confidence_review" for issue in result.issues)


def test_comparison_preserves_legacy_cache_identity_and_blocks_unknown_even_missing():
    missing = Phase2Report(source_name="A.pdf", sha256="a" * 64, clause_count=0,
                           policy=PolicyExtraction())
    old_shape = missing.model_dump(mode="json")
    old_shape.pop("field_status")
    old_shape.pop("retrieval_diagnostics")
    assert ComparisonAgent._cache_payload(missing) == old_shape
    uncertain = missing.model_copy(update={"field_status": {"side_a": FieldStatus.NOT_RETRIEVED}})
    other = missing.model_copy(update={"source_name": "B.pdf", "sha256": "b" * 64})
    comparison = ComparisonAgent(gateway=Gateway(), model_strong="unused").compare(uncertain, other)
    assert len(comparison.differences) == 1
    assert comparison.differences[0].field_name == "side_a"
    assert comparison.differences[0].classification == DifferenceClass.NOT_COMPARABLE


def test_auto_dispatch_keeps_short_legacy_and_uses_long_local_segmentation():
    class LegacyGateway:
        provider = "offline-legacy"
        def __init__(self):
            self.calls = []
        def complete(self, **call):
            self.calls.append(call)
            assert call["agent"] == "extraction"
            return PolicyExtraction().model_dump_json()
    short = doc("1. COBERTURAS\nSide A concedida.")
    legacy = LegacyGateway()
    _, result = Phase2Pipeline(gateway=legacy, model_fast="fast", model_strong="strong",
                              optimization=ExtractionOptimizationSettings()).run(short)
    assert len(legacy.calls) == 1 and result.retrieval_diagnostics == {}
    long = doc(*(["1. COBERTURAS\nSide A concedida."] * 8))
    gateway = Gateway()
    clauses, result = Phase2Pipeline(
        gateway=gateway, model_fast="fast", model_strong="strong",
        optimization=ExtractionOptimizationSettings(), routing=ROUTING,
    ).run(long)
    assert "".join(clause.text for clause in clauses) == "".join(page.text for page in long.pages)
    assert result.retrieval_diagnostics["full_local_chunks"] == len(clauses)
    assert all(call["agent"].startswith("optimized_") for call in gateway.calls)
    forced_legacy = LegacyGateway()
    Phase2Pipeline(gateway=forced_legacy, model_fast="fast", model_strong="strong",
                   optimization=ExtractionOptimizationSettings(strategy="legacy")).run(long)
    assert len(forced_legacy.calls) == len(clauses)


def test_same_chunk_new_field_association_triggers_selective_fallback():
    source = doc("1. COBERTURAS\nSide A concedida com assistência jurídica.")
    facts = {"side_a": ("Side A concedida", "Side A concedida", .9),
             "custos_defesa": ("Assistência jurídica", "assistência jurídica", .9)}
    gateway = Gateway(lambda call, payload: response_for(payload, facts))
    result = run(source, gateway)
    requests = [json.loads(call["messages"][1]["content"]) for call in gateway.calls]
    initial = [request for request in requests if request["retrieval_stage"] == 0
               and "side_a" in request["field_names"]]
    fallback = [request for request in requests if request["retrieval_stage"] >= 4
                and "custos_defesa" in request["field_names"]]
    assert initial and fallback
    assert {item["chunk_id"] for item in initial[0]["sources"]} == {
        item["chunk_id"] for item in fallback[0]["sources"]
    }
    assert result.field_status["custos_defesa"] == FieldStatus.FOUND
    assert result.retrieval_diagnostics["field_diagnostics"]["custos_defesa"]["fallback_used"]


def test_completed_cache_rejects_lost_status_and_false_broad_search(tmp_path):
    source = doc("1. COBERTURAS\nSide A concedida.")
    facts = {"side_a": ("Side A concedida", "Side A concedida.", .9)}
    result = run(source, Gateway(lambda call, payload: response_for(payload, facts)), tmp_path)
    cache_file = next((tmp_path / "grouped_results").glob("*.json"))
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    data["result"]["field_status"].pop("side_a")
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    repaired = run(source, Gateway(lambda call, payload: response_for(payload, facts)), tmp_path)
    assert repaired.field_status["side_a"] == FieldStatus.FOUND
    assert not repaired.retrieval_diagnostics.get("aggregate_cache_hit", False)
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    data["result"]["retrieval_diagnostics"]["field_diagnostics"]["side_c"]["searched_pages"] = []
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    repaired = run(source, Gateway(lambda call, payload: response_for(payload, facts)), tmp_path)
    assert repaired.field_status["side_c"] == FieldStatus.NOT_FOUND
    assert not repaired.retrieval_diagnostics.get("aggregate_cache_hit", False)
    assert repaired.policy.side_a == result.policy.side_a


def test_conflicting_values_with_same_quote_cannot_be_ranked():
    agent = GroupedExtractionAgent(gateway=Gateway(),
                                   optimization=ExtractionOptimizationSettings(),
                                   routing=ROUTING)
    quote = "Side A concedida sob condições específicas."
    agent._merge("side_a", FieldEvidence(valor="Concedida", pagina=1,
                 trecho_origem=quote, confianca=.9), FieldStatus.FOUND)
    agent._merge("side_a", FieldEvidence(valor="Não concedida", pagina=1,
                 trecho_origem=quote, confianca=.95), FieldStatus.FOUND)
    assert "side_a" in agent.conflicts
    assert any(issue.code == "multiple_evidence_candidates" for issue in agent.result.issues)


def test_comparison_cache_ignores_execution_telemetry_but_keeps_field_status(tmp_path):
    class SemanticGateway:
        provider = "offline-comparison"
        def __init__(self):
            self.calls = []
        def complete(self, **call):
            self.calls.append(call)
            return json.dumps({"comparisons": [{
                "field_name": "side_a", "classification": "diferente_nao_comparavel",
                "justification": "Escopos documentais distintos conforme os excertos.",
            }]})
    a = Phase2Report(source_name="A.pdf", sha256="a" * 64, clause_count=1,
                     policy=PolicyExtraction(side_a=FieldEvidence(
                         valor="Defesa incluída", pagina=1, trecho_origem="Defesa incluída", confianca=.9)),
                     field_status={"side_a": FieldStatus.FOUND},
                     retrieval_diagnostics={"calls_executed": 1, "cache_hits": 0,
                                            "batches": [{"cache_hit": False}]})
    b = Phase2Report(source_name="B.pdf", sha256="b" * 64, clause_count=1,
                     policy=PolicyExtraction(side_a=FieldEvidence(
                         valor="Defesa condicionada", pagina=1, trecho_origem="Defesa condicionada", confianca=.9)),
                     field_status={"side_a": FieldStatus.FOUND})
    gateway = SemanticGateway()
    agent = ComparisonAgent(gateway=gateway, model_strong="semantic-local", processed_dir=tmp_path)
    first = agent.compare(a, b)
    replay = a.model_copy(update={"retrieval_diagnostics": {
        "calls_executed": 0, "cache_hits": 1, "aggregate_cache_hit": True,
        "batches": [{"cache_hit": True}],
    }})
    assert agent.compare(replay, b) == first
    assert len(gateway.calls) == 1
    unresolved = replay.model_copy(update={"field_status": {"side_a": FieldStatus.AMBIGUOUS}})
    changed = agent.compare(unresolved, b)
    assert changed != first
    assert "revisao" in changed.differences[0].justification
    assert len(list((tmp_path / "comparison").glob("*.json"))) == 2


def test_model_not_retrieved_is_verified_without_claiming_contractual_absence():
    source = doc("1. COBERTURAS\nSide A concedida sob condição não determinada.")
    def handle(call, payload):
        output = json.loads(response_for(payload))
        for item in output["fields"]:
            if item["field_name"] == "side_a":
                item["status"] = "NOT_RETRIEVED"
        return json.dumps(output)
    gateway = Gateway(handle)
    result = run(source, gateway)
    verifier = [call for call in gateway.calls if call["agent"] == "optimized_verification"]
    assert len(verifier) == 1
    assert json.loads(verifier[0]["messages"][1]["content"])["field_names"] == ["side_a"]
    assert result.field_status["side_a"] == FieldStatus.NOT_RETRIEVED
    assert result.policy.side_a.valor == NOT_FOUND
    assert "nenhuma conclusao" in result.retrieval_diagnostics["field_diagnostics"]["side_a"]["reason"]


def test_incomplete_final_retrieval_with_text_candidates_gets_selective_verifier():
    source = doc("1. COBERTURAS\nSide A concedida sob condição não determinada.")
    clauses = SegmentationAgent(gateway=None).segment(source)
    index = LocalRetrievalIndex.build(source, clauses)
    original_expand = index.expand
    def limited_expand(plan, **kwargs):
        expanded = original_expand(plan, **kwargs)
        if expanded.stage == 4 and "side_a" in expanded.fields:
            diagnostics = dict(expanded.field_diagnostics)
            diagnostics["side_a"] = replace(diagnostics["side_a"], full_search=False,
                                             limited=True, no_hit_reason="synthetic_incomplete_context")
            expanded = replace(expanded, field_diagnostics=diagnostics)
        return expanded
    gateway = Gateway()
    with patch.object(index, "expand", side_effect=limited_expand), patch(
        "src.agents.grouped_extraction.LocalRetrievalIndex.build", return_value=index
    ):
        result = run(source, gateway)
    verifier = [call for call in gateway.calls if call["agent"] == "optimized_verification"]
    assert len(verifier) == 1
    assert json.loads(verifier[0]["messages"][1]["content"])["field_names"] == ["side_a"]
    assert result.field_status["side_a"] == FieldStatus.NOT_RETRIEVED
    assert result.policy.side_a.valor == NOT_FOUND


@pytest.mark.parametrize("split", [False, True])
def test_verifier_missing_does_not_silently_confirm_prior_located_fallback_evidence(split):
    text = "1. COBERTURAS\nSide A concedida com assistência jurídica."
    source = doc(text) if not split else doc(text + " texto neutro" * 190,
                                             text + " texto neutro" * 190)
    facts = {"side_a": ("Side A concedida", "Side A concedida", .9),
             "custos_defesa": ("Assistência jurídica", "assistência jurídica", .9)}
    verification_count = 0
    def handle(call, payload):
        nonlocal verification_count
        if call["agent"] == "optimized_verification" and "custos_defesa" in payload["field_names"]:
            verification_count += 1
            if verification_count == 1:
                return response_for(payload)
        return response_for(payload, facts)
    result = run(source, Gateway(handle), batch_chars=4000 if split else 20000)
    assert verification_count >= (2 if split else 1)
    assert result.policy.custos_defesa.valor == "Assistência jurídica"
    assert result.field_status["custos_defesa"] == FieldStatus.AMBIGUOUS
    assert any(issue.code == "verification_not_confirmed" and issue.field_name == "custos_defesa"
               for issue in result.issues)
