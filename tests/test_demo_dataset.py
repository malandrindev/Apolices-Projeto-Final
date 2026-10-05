"""Ground-truth, provenance and no-provider regressions for the academic demo."""
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import httpx
import openai
import groq
import pymupdf
import pytest
from streamlit.testing.v1 import AppTest

from src.config import get_ingestion_settings
from src.demo import DEFAULT_DIRECTORY, ROOT, WARNINGS, demo_comparisons, load_demo_dataset
from src.product_presentation import comparison_counts
from src.schemas.policy import PolicyExtraction
from src.schemas.retrieval import FieldStatus
from src.workspace import build_matrix
from tests.test_interface import button, selectbox


@pytest.fixture(scope="module")
def dataset():
    return load_demo_dataset()


def test_two_compact_documents_keep_all_ground_truth_and_origins(dataset):
    assert [source.filename for source in dataset["sources"]] == ["porto_demo_policy.pdf", "allianz_demo_policy.pdf"]
    assert [len(dataset["processed"][source.sha256].pages) for source in dataset["sources"]] == [30, 33]
    for source in dataset["sources"]:
        gt = dataset["ground_truth"][source.sha256]
        assert set(gt["fields"]) == set(PolicyExtraction.model_fields)
        assert gt["document_sha256"] == source.sha256
        assert source.metadata["demo"] and source.origin == "demo"
        assert set(dataset["reports"][source.sha256].field_status) == set(PolicyExtraction.model_fields)


@pytest.mark.parametrize("key", ["porto", "allianz"])
@pytest.mark.parametrize("page_number", [1, 2])
def test_each_synthetic_page_explicitly_has_no_contractual_validity(dataset, key, page_number):
    source = next(source for source in dataset["sources"] if source.filename == key + "_demo_policy.pdf")
    text = dataset["processed"][source.sha256].pages[page_number - 1].text
    assert all(warning in text for warning in WARNINGS)
    assert dataset["page_mapping"][source.sha256][str(page_number)]["kind"] == "synthetic"


@pytest.mark.parametrize("key,limit,premium,retention,retroactive", [
    ("porto", "R$ 20.000.000", "R$ 120.000", "R$ 100.000", "01/01/2020"),
    ("allianz", "R$ 30.000.000", "R$ 135.000", "R$ 250.000", "01/01/2018"),
])
def test_explicit_synthetic_risk_metadata_and_values(dataset, key, limit, premium, retention, retroactive):
    source = next(source for source in dataset["sources"] if source.filename == key + "_demo_policy.pdf")
    gt = dataset["ground_truth"][source.sha256]
    spec = gt["specification"]
    assert spec["insured"] == "ALPHA TECNOLOGIA S.A."
    assert spec["currency"] == "BRL" and spec["effective_date"] == "2026-01-01"
    assert spec["expiration_date"] == "2026-12-31"
    assert (spec["LMG"], spec["premium"], spec["retention"], spec["retroactive_date"]) == (limit, premium, retention, retroactive)
    for name in ("seguradora", "numero_apolice", "tomador_segurado", "vigencia_inicio",
                 "vigencia_fim", "moeda", "premio", "limite_maximo_garantia", "retencao_franquia", "data_retroativa"):
        field = gt["fields"][name]
        assert field["source_type"] == "synthetic_specification"
        assert field["source_page"] == 2 and field["original_source_page"] is None
        assert field["confidence_basis"]


def test_every_selected_real_page_preserves_original_legal_text(dataset):
    count = 0
    for source in dataset["sources"]:
        gt = dataset["ground_truth"][source.sha256]
        with pymupdf.open(ROOT / "data/demo_sources" / gt["original_source_file"]) as original:
            for mapping in dataset["page_mapping"][source.sha256].values():
                if mapping["kind"] == "synthetic":
                    continue
                text = dataset["processed"][source.sha256].pages[mapping["demo_page"] - 1].text
                assert text == original[mapping["source_page"] - 1].get_text()
                assert hashlib.sha256(text.encode()).hexdigest() == mapping["text_sha256"]
                count += 1
    assert count == 59


def test_all_primary_and_supporting_quotes_are_literal_and_mapped(dataset):
    located = 0
    for source in dataset["sources"]:
        for field in dataset["ground_truth"][source.sha256]["fields"].values():
            for quote in [field, *field["supporting_evidence"]]:
                if quote["source_page"] is None:
                    continue
                page = dataset["processed"][source.sha256].pages[quote["source_page"] - 1]
                assert " ".join(quote["evidence_excerpt"].split()) in " ".join(page.text.split())
                mapping = dataset["page_mapping"][source.sha256][str(quote["source_page"])]
                if quote["source_type"] == "real_wording":
                    assert mapping["kind"] == "original_excerpt"
                    assert quote["original_source_page"] == mapping["source_page"]
                located += 1
    assert located >= 52


def test_demo_comparison_is_scoped_and_never_a_global_ranking(dataset):
    comparison = demo_comparisons(dataset["reference_sha"], dataset)[0]
    assert len(comparison.differences) == 27
    assert dataset["comparison_truth"]["no_global_ranking"]
    directions = {row.field_name: row.classification.value for row in comparison.differences
                  if row.classification.value.startswith("mais_favoravel")}
    assert directions == {"limite_maximo_garantia": "mais_favoravel_B",
                          "premio": "mais_favoravel_A", "retencao_franquia": "mais_favoravel_A"}
    assert all(row["no_global_ranking"] for row in dataset["comparison_truth"]["fields"])
    matrix = build_matrix(dataset["reports"][comparison.document_id_a],
                          [dataset["reports"][comparison.document_id_b]], [comparison])
    counts = comparison_counts(matrix)
    assert counts == {"differences": 14, "attention": 14, "equivalent": 3, "insufficient": 4}
    by_name = {row["field_name"]: row for row in matrix}
    for name in ("sublimites", "periodo_estendido_notificacao", "exclusoes", "definicoes_relevantes"):
        assert by_name[name]["candidates"][comparison.document_id_b]["classification"] == "diferente_nao_comparavel"


def test_reference_change_reverses_values_evidence_and_only_local_directions(dataset):
    forward = demo_comparisons(dataset["reference_sha"], dataset)[0]
    reverse = demo_comparisons(forward.document_id_b, dataset)[0]
    assert reverse.document_id_a == forward.document_id_b
    for a, b in zip(forward.differences, reverse.differences):
        assert (a.value_a, a.value_b) == (b.value_b, b.value_a)
        assert a.citation_a == b.citation_b and a.citation_b == b.citation_a
    assert next(row for row in reverse.differences if row.field_name == "limite_maximo_garantia").classification.value == "mais_favoravel_A"


@pytest.mark.parametrize("tamper", ["pdf", "quote", "source_type", "page_map", "comparison_value", "legal_direction"])
def test_loader_refuses_tampered_assets_or_ground_truth_even_with_rebound_hashes(tmp_path, tamper):
    target = tmp_path / "demo"
    shutil.copytree(DEFAULT_DIRECTORY, target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if tamper == "pdf":
        path = target / "porto_demo_policy.pdf"
        path.write_bytes(path.read_bytes() + b"tampered")
    else:
        name = "ground_truth_comparison.json" if tamper.startswith("comparison") or tamper == "legal_direction" else "page_map_porto.json" if tamper == "page_map" else "ground_truth_porto.json"
        path = target / name
        data = json.loads(path.read_text(encoding="utf-8"))
        if tamper == "quote":
            data["fields"]["side_a"]["evidence_excerpt"] = "Fabricated legal coverage."
        elif tamper == "source_type":
            data["fields"]["side_a"]["source_type"] = "synthetic_specification"
        elif tamper == "page_map":
            data["pages"][2]["source_page"] = 5
        elif tamper == "comparison_value":
            data["fields"][0]["value_a"] = "Invented insurer"
        else:
            next(row for row in data["fields"] if row["field_name"] == "exclusoes")["classification"] = "mais_favoravel_A"
        path.write_text(json.dumps(data), encoding="utf-8")
        manifest["assets"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_demo_dataset(target)


def test_demo_full_journey_fails_on_any_provider_dispatch_and_preserves_review(tmp_path):
    attempted = []
    def reject(*args, **kwargs):
        attempted.append("provider")
        raise AssertionError("Demo must never call a provider.")
    ingestion = replace(get_ingestion_settings(), processed_dir=tmp_path / "processed")
    with ExitStack() as stack:
        for name in ("src.llm.providers.get_gateway", "openai.OpenAI.__init__", "groq.Groq.__init__",
                     "httpx.Client.request", "httpx.AsyncClient.request"):
            stack.enter_context(patch(name, side_effect=reject))
        stack.enter_context(patch("src.config.get_ingestion_settings", return_value=ingestion))
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        assert not app.exception
        assert not any(item.key in {"prepare", "consent"} for item in [*app.button, *app.checkbox])
        button(app, "use_demo").click().run(timeout=20)
        assert not app.exception
        docs = app.session_state["workspace_docs"]
        assert len(docs) == 2 and docs[0].filename == "porto_demo_policy.pdf"
        assert app.session_state["workspace_reference"] == docs[0].sha256
        button(app, "analyze").click().run(timeout=20)
        assert not app.exception and app.session_state["workspace_result"]["stats"]["calls"] == 0
        assert any("dados fictícios e wordings reais" in item.value for item in app.info)
        assert any("pré-processado" in item.value for item in app.caption)
        pdf_button = next(item for item in app.button if str(item.key).startswith("pdf_page_summary_"))
        button(app, pdf_button.key).click().run()
        assert app.get("imgs")
        app.run()
        assert app.get("imgs")
        original = app.session_state["workspace_result"]["reports"][docs[0].sha256].model_dump()
        selectbox(app, "detail_field").set_value("limite_maximo_garantia").run()
        review_key = f"{docs[0].sha256}:{docs[1].sha256}:limite_maximo_garantia"
        button(app, "review_" + review_key).click().run()
        assert app.session_state["workspace_reviews"][review_key]["status"] == "Requer análise"
        assert app.session_state["workspace_result"]["reports"][docs[0].sha256].model_dump() == original
        app.run()
        selectbox(app, "reference_select").set_value(docs[1].sha256).run()
        assert not app.exception and app.session_state["workspace_result"] is None
        button(app, "analyze").click().run(timeout=20)
        assert not app.exception
        assert app.session_state["workspace_result"]["comparisons"][0].document_id_a == docs[1].sha256
        assert app.session_state["workspace_result"]["artifacts"][docs[0].sha256].pdf_path.is_file()
        button(app, "new_comparison").click().run()
        assert not app.exception and app.session_state["workspace_docs"] == []
        assert not app.session_state["workspace_demo"]
    assert attempted == []
