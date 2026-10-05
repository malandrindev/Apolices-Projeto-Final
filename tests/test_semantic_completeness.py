"""Delivery semantic regressions. Every provider is an offline deterministic double."""
from __future__ import annotations

import hashlib
import json

import pytest

from src.agents.grouped_extraction import GroupedExtractionAgent, SEMANTIC_FIELD_GROUPS
from src.agents.ocr import PageText, ProcessedDocument
from src.agents.segmentation import SegmentationAgent
from src.agents.semantic_completeness import validate_semantic_completeness
from src.config import ExtractionOptimizationSettings, ExtractionRouting
from src.llm.model_routing import RoutedGateway
from src.schemas.policy import FieldEvidence, PolicyExtraction, NOT_FOUND
from src.schemas.retrieval import FieldStatus


def evidence(value, quote):
    return FieldEvidence(valor=value, pagina=1, trecho_origem=quote, confianca=.95)


@pytest.mark.parametrize("field,value,quote,context,reason", [
    ("premio", "Premio", "Premio devido na especificacao.", (), "TOPIC_LABEL_NOT_INFORMATION"),
    ("limite_maximo_garantia", "R$ 5.000,00", "Sublimite de R$ 5.000,00.", (), "LMG_SUBLIMIT_CONFUSION"),
    ("retencao_franquia", "1000", "Franquia: R$ 1.000,00.", (), "MONETARY_UNIT_MISSING"),
    ("custos_defesa", "Custos de defesa razoaveis.", "Custos de defesa com anuencia previa da Seguradora.", (), "PRIOR_CONSENT_OMITTED"),
    ("side_c", "A cobertura se aplica.", "Side C, se contratada e indicada na especificacao.", (), "CONTRACTING_CONDITION_OMITTED"),
    ("territorialidade", "Local indicado na Especificacao.", "Territorialidade: local indicado na Especificacao.", ("Cobertura mundial, exceto Canada.",), "REMISSION_NOT_OPERATIVE_TERRITORY"),
    ("jurisdicao_lei", "Leis brasileiras.", "Aplicam-se as leis brasileiras.", ("O foro e o domicilio do segurado, salvo ausencia de hipossuficiencia.",), "FORUM_COMPONENT_OMITTED"),
    ("exclusoes", "Exclusoes somente mediante decisao definitiva ou confissao.", "Exclusoes por dolo.", ("Aplicam-se por decisao, confissao ou reconhecimento pelo segurado.",), "EXCLUSION_RECOGNITION_ALTERNATIVE_OMITTED"),
    ("consentimento_acordo", "Exige previa anuencia.", "O acordo exige previa anuencia.", ("Dispensa de consentimento quando pagamento limitado a franquia.",), "CONSENT_DEDUCTIBLE_EXCEPTION_OMITTED"),
    ("cancelamento_renovacao", "Cancelamento conforme clausula.", "Cancelamento conforme clausula.", ("Renovacao nao e automatica.",), "RENEWAL_COMPONENT_OMITTED"),
    ("periodo_estendido_notificacao", "Sujeito ao mesmo LMG.", "Prazo adicional sujeito ao mesmo LMG.", ("Aplicavel apos nao renovacao conforme especificacao.",), "ADDITIONAL_PERIOD_MECHANISM_OMITTED"),
    ("base_cobertura", "Claims made com notificacao.", "Claims made sujeito aos requisitos cumulativos.", (), "COVERAGE_TRIGGER_REQUIREMENTS_OMITTED"),
    ("definicoes_relevantes", "Principais termos prevalecem sobre outras definicoes.", "Principais termos prevalecem.", (), "GLOSSARY_INTRO_NOT_DEFINITIONS"),
    ("extensoes_cobertura", "Extensao para afiliadas, se contratada.", "Extensao para afiliadas, se contratada.", (), "EXTENSIONS_INVENTORY_NOT_ESTABLISHED"),
    ("data_retroativa", "Prazo adicional de 30 dias", "Prazo adicional de 30 dias.", (), "RETROACTIVITY_ADDITIONAL_PERIOD_CONFUSION"),
    ("custos_defesa", "Honorarios com anuencia previa.", "Honorarios com anuencia previa da", (), "SOURCE_CONTINUATION_UNRESOLVED"),
])
def test_material_omissions_are_detected_without_real_models(field, value, quote, context, reason):
    check = validate_semantic_completeness(field, evidence(value, quote), context)
    assert not check.passed and reason in check.reasons
    assert check.to_dict()["status"] == "SEMANTIC_ESCALATION_REQUIRED"


@pytest.mark.parametrize("field,value,quote,context", [
    ("premio", "R$ 1.000,00", "Premio total de R$ 1.000,00.", ()),
    ("territorialidade", "Mundial, exceto Canada.", "Cobertura mundial, exceto Canada.", ()),
    ("jurisdicao_lei", "Leis brasileiras; foro do domicilio, salvo ausencia de hipossuficiencia.",
     "Leis brasileiras; foro do domicilio, salvo ausencia de hipossuficiencia.", ()),
    ("side_c", "Side C para a Sociedade, se contratada.", "Side C para a Sociedade, se contratada.", ()),
    ("cancelamento_renovacao", "Cancelamento e renovacao nao automatica.", "Cancelamento e renovacao nao automatica.", ()),
    ("data_retroativa", "01/01/2025", "Data retroativa: 01/01/2025.", ()),
])
def test_complete_meaning_preserves_found(field, value, quote, context):
    assert validate_semantic_completeness(field, evidence(value, quote), context).passed


def run_provider(text, handler):
    class Provider:
        provider = "offline-semantic"
        events = []
        def complete(self, **call):
            payload = json.loads(call["messages"][1]["content"])
            rows = []
            for name in payload["field_names"]:
                val, quote, state = handler(call["model"], name)
                ev = FieldEvidence()
                if val != NOT_FOUND:
                    for src in payload["sources"]:
                        if name in src["fields"]:
                            for page in src["pages"]:
                                if quote in page["text"]:
                                    ev = FieldEvidence(valor=val, trecho_origem=quote,
                                                      pagina=page["page_number"], confianca=.95)
                if ev.valor == NOT_FOUND:
                    state = "NOT_FOUND"
                rows.append({"field_name": name, "status": state, "evidence": ev.model_dump()})
            return json.dumps({"fields": rows})
    doc = ProcessedDocument(source_name="generic.pdf", sha256=hashlib.sha256(text.encode()).hexdigest(),
        size_bytes=len(text.encode()), media_type="application/pdf",
        pages=[PageText(page_number=1, text=text, extraction_method="native")],
        processed_at="2026-10-04", cache_key="synthetic-semantic")
    gateway = RoutedGateway(Provider())
    chunks = SegmentationAgent(gateway=None).segment(doc)
    agent = GroupedExtractionAgent(gateway=gateway,
        routing=ExtractionRouting("unused", "unused", "unused"),
        optimization=ExtractionOptimizationSettings(strategy="optimized"))
    return agent.extract(doc, chunks), gateway


def test_semantic_failure_uses_selective_sol_and_replaces_incomplete_summary():
    quote = "Custos de defesa razoaveis com anuencia previa da Seguradora."
    def handler(model, name):
        if name != "custos_defesa":
            return NOT_FOUND, NOT_FOUND, "NOT_FOUND"
        return ("Custos de defesa razoaveis com anuencia previa." if model == "gpt-5.6-sol"
                else "Custos de defesa razoaveis.", quote, "FOUND")
    result, gateway = run_provider(quote, handler)
    relevant = [e for e in gateway.events if "custos_defesa" in e["fields"]]
    assert [e["requested_model"] for e in relevant] == ["gpt-5.6-terra", "gpt-5.6-sol"]
    assert relevant[0]["evidence_validation_status"] == "SEMANTIC_INCOMPLETE"
    assert relevant[0]["failure_category"] == "SEMANTIC_QUALITY_FAILURE"
    assert relevant[1]["semantic_escalation"]
    assert relevant[1]["retrieval_stage"] == 4
    assert result.field_status["custos_defesa"] == FieldStatus.FOUND
    assert "anuencia previa" in result.policy.custos_defesa.valor
    assert "custos_defesa" not in result.retrieval_diagnostics["semantic_completeness_failures"]
    assert len(result.field_status) == 27


def test_uncorrected_semantic_error_remains_ambiguous_after_one_selective_sol():
    quote = "Custos de defesa razoaveis com anuencia previa da Seguradora."
    result, gateway = run_provider(quote, lambda model, name:
        ("Custos de defesa razoaveis.", quote, "FOUND") if name == "custos_defesa"
        else (NOT_FOUND, NOT_FOUND, "NOT_FOUND"))
    assert result.field_status["custos_defesa"] == FieldStatus.AMBIGUOUS
    assert len([e for e in gateway.events if e["requested_model"] == "gpt-5.6-sol"]) == 1
    assert gateway.control.blocked_reason is None


def test_semantic_groups_cover_27_once_and_split_previous_heterogeneous_groups():
    fields = [n for names in SEMANTIC_FIELD_GROUPS.values() for n in names]
    assert len(fields) == len(set(fields)) == 27
    assert set(fields) == set(PolicyExtraction.model_fields)
    assert len(SEMANTIC_FIELD_GROUPS) == 14
    assert "territorialidade" in SEMANTIC_FIELD_GROUPS["TERRITORY"]
    assert "jurisdicao_lei" in SEMANTIC_FIELD_GROUPS["JURISDICTION"]

def test_selective_verifier_uses_authorized_terra_technical_fallback():
    from src.llm.resilience import LLMClientError
    quote = "Custos de defesa razoaveis com anuencia previa da Seguradora."
    terra_calls = 0
    def handler(model, name):
        nonlocal terra_calls
        if name != "custos_defesa":
            return NOT_FOUND, NOT_FOUND, "NOT_FOUND"
        if model == "gpt-5.6-sol":
            raise LLMClientError("offline-semantic", "timeout")
        terra_calls += 1
        value = ("Custos de defesa razoaveis." if terra_calls == 1
                 else "Custos de defesa razoaveis com anuencia previa.")
        return value, quote, "FOUND"
    result, gateway = run_provider(quote, handler)
    relevant = [e for e in gateway.events if "custos_defesa" in e["fields"]]
    assert [e["requested_model"] for e in relevant] == [
        "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-terra"]
    assert [e["role"] for e in relevant] == ["interpretation", "verifier", "verifier"]
    assert relevant[-1]["fallback_level"] == 1
    assert relevant[-1]["fallback_reason"] == "timeout"
    assert result.retrieval_diagnostics["verifier_calls"] == 1
    assert result.field_status["custos_defesa"] == FieldStatus.FOUND

def test_contradictory_definition_cannot_silently_remain_found():
    quote = ("O presente conceito tambem inclui Reclamacao no Mercado de Capitais. "
             "Nao se incluem no presente conceito processos dessa definicao.")
    check = validate_semantic_completeness("definicoes_relevantes",
        evidence("O conceito inclui Reclamacao no Mercado de Capitais.", quote))
    assert "CONTRADICTORY_DEFINITION_SCOPE" in check.reasons


def test_review_projection_downgrades_without_rewriting_facts_or_original():
    import copy
    from src.agents.semantic_completeness import apply_reviewed_conservative_statuses
    from src.agents.extraction import Phase2Report
    raw = Phase2Report(source_name="generic.pdf", sha256="original", clause_count=1,
        policy=PolicyExtraction(definicoes_relevantes=evidence("Definicao condicional.", "Definicao condicional.")),
        field_status={"definicoes_relevantes": FieldStatus.FOUND}).model_dump(mode="json")
    before = copy.deepcopy(raw)
    safe = apply_reviewed_conservative_statuses(raw, {"conservative_field_overrides": {
        "definicoes_relevantes": {"status": "AMBIGUOUS", "reason": "Scope conflict reviewed offline."}}})
    assert raw == before and safe["policy"] == raw["policy"]
    assert safe["field_status"]["definicoes_relevantes"] == "AMBIGUOUS"
    for override in ({"status": "FOUND", "reason": "unsafe"},
                     {"status": "NOT_FOUND", "reason": "unsafe"},
                     {"status": "AMBIGUOUS", "reason": ""}):
        with pytest.raises(ValueError):
            apply_reviewed_conservative_statuses(raw, {"conservative_field_overrides": {
                "definicoes_relevantes": override}})
