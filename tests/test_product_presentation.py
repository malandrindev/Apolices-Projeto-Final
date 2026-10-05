"""Offline business presentation regressions; facts and backend enums are unchanged."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from src.product_presentation import (RESULT_TABS, business_relevance, classification_text,
    comparison_counts, document_title, document_titles, important_differences, pair_state, value_text)
from src.schemas.policy import NOT_FOUND

def source(name, insurer=None, sha="a"):
    return SimpleNamespace(filename=name, metadata={"insurer": insurer} if insurer else {}, sha256=sha)

def row(name="limite_maximo_garantia", a="BRL 20000000", b="BRL 30000000",
        status_a="FOUND", status_b="FOUND", classification="mais_favoravel_B"):
    return {"field_name": name, "label": "Limite máximo de garantia", "group": "Limites",
        "reference_value": a, "reference_status": status_a,
        "reference_evidence": {"page_number": 2, "excerpt": a},
        "candidates": {"b": {"value": b, "field_status": status_b,
            "classification": classification, "justification": "Limite maior para o mesmo risco e moeda.",
            "evidence": {"page_number": 2, "excerpt": b}}}}

@pytest.mark.parametrize("raw,title", [
    ("Berkley International do Brasil Seguros S.A. Apólice:", "BERKLEY"),
    ("AXA Seguros S.A.", "AXA SEGUROS"),
    ("Porto Seguro Cia de Seguros Gerais", "PORTO SEGURO"),
    ("Allianz Seguros S.A.", "ALLIANZ"),
])
def test_clean_titles_do_not_mutate_source_metadata(raw, title):
    document = source("wording.pdf", raw)
    metadata = SimpleNamespace(insurer=raw)
    original = deepcopy(document.metadata)
    assert document_title(document, metadata) == title
    assert metadata.insurer == raw and document.metadata == original

def test_unknown_insurer_is_not_inferred_from_filename():
    assert document_title(source("AXA_documento_do_usuario.pdf")) == "AXA_documento_do_usuario.pdf"

def test_duplicate_insurers_have_filename_disambiguation_without_number_labels():
    documents = [source("proposta-1.pdf", "AXA Seguros", "a"), source("proposta-2.pdf", "AXA Seguros", "b")]
    titles = document_titles(documents, {})
    assert len(set(titles.values())) == 2
    assert titles["a"] == "AXA SEGUROS · proposta-1.pdf"
    assert titles["b"] == "AXA SEGUROS · proposta-2.pdf"

@pytest.mark.parametrize("status", ["AMBIGUOUS", "NOT_RETRIEVED", "TECHNICAL_UNAVAILABLE", "NOT_FOUND"])
def test_uncertainty_never_becomes_executive_difference_or_advantage(status):
    item = row(status_b=status)
    assert pair_state(item, item["candidates"]["b"]) == "insufficient"
    assert comparison_counts([item]) == {"differences": 0, "attention": 0, "equivalent": 0, "insufficient": 1}
    assert important_differences([item]) == []
    assert classification_text(item, item["candidates"]["b"]) == "Informação insuficiente"

@pytest.mark.parametrize("name", ["seguradora", "numero_apolice", "tomador_segurado", "moeda", "vigencia_inicio", "vigencia_fim"])
def test_identification_fields_are_retained_but_excluded_from_business_counts(name):
    item = row(name=name)
    before = deepcopy(item)
    assert comparison_counts([item]) == {"differences": 0, "attention": 0, "equivalent": 0, "insufficient": 0}
    assert important_differences([item]) == []
    assert item == before

def test_business_counts_prioritize_evidenced_differences_and_preserve_uncertain_items():
    lmg = row()
    equivalent = row("side_a", a="Cobertura A", b="Cobertura A", classification="igual")
    unknown = row("territorialidade", status_b="NOT_RETRIEVED")
    unsafe = row("exclusoes", classification="diferente_nao_comparavel")
    original = deepcopy([lmg, equivalent, unknown, unsafe])
    assert comparison_counts(original) == {"differences": 2, "attention": 2, "equivalent": 1, "insufficient": 1}
    assert [item["field_name"] for item in important_differences(original)] == ["limite_maximo_garantia", "exclusoes"]
    assert classification_text(unsafe, unsafe["candidates"]["b"]) == "Diferente sem direção segura"
    assert [lmg, equivalent, unknown, unsafe] == original
    assert "preço sozinho" in business_relevance(row("premio"))

def test_literal_evidence_is_required_even_when_status_is_found():
    item = row()
    item["candidates"]["b"]["evidence"] = {"page_number": None, "excerpt": NOT_FOUND}
    assert comparison_counts([item])["differences"] == 0

def test_explicit_insufficient_semantic_context_never_becomes_a_claimed_difference():
    item = row("rateio", classification="diferente_nao_comparavel")
    item["candidates"]["b"]["justification"] = "Evidência insuficiente: trechos extensos não permitem concluir."
    assert pair_state(item, item["candidates"]["b"]) == "insufficient"

@pytest.mark.parametrize("status,expected", [
    ("AMBIGUOUS", "Requer confirmação"),
    ("NOT_RETRIEVED", "Não localizado com segurança"),
    ("NOT_FOUND", "Não localizado com segurança"),
    ("TECHNICAL_UNAVAILABLE", "Não foi possível analisar"),
])
def test_inconclusive_values_use_business_language_without_claiming_contractual_absence(status, expected):
    assert value_text(NOT_FOUND, status) == expected
    assert status not in value_text("Condição a conferir", status)

def test_six_business_tabs_have_no_technical_or_workflow_steps():
    assert RESULT_TABS == ("Resumo", "Coberturas", "Limites & Franquias", "Exclusões", "Cláusulas", "Evidências")
