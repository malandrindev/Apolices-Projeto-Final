"""Portable offline planner tests and optional real public-PDF anchor checks."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.plan_public_extraction import (
    ROOT, batch_totals, candidate_recall, local_path, measure_batch, plan_document,
    price_scenario, request_shape, run_preflight, save_plan, validate_golden,
)
from src.agents.grouped_extraction import build_semantic_batches
from src.agents.ocr import PageText, ProcessedDocument
from src.config import ExtractionOptimizationSettings, ExtractionRouting, IngestionSettings
from src.retrieval.local import LocalRetrievalIndex
from src.schemas.clause import ClauseCategory, ClauseChunk, SourcePage

ROUTING = ExtractionRouting("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-sol")


def synthetic_corpus():
    texts = [
        "FRANQUIA\nParticipacao obrigatoria R$ 1000.\n" for _ in range(12)
    ]
    texts[0] += "Seguradora: Companhia Teste.\n"
    document = ProcessedDocument(
        source_name="local-only.pdf", sha256="a" * 64, size_bytes=1,
        media_type="application/pdf", processed_at="2026-10-04T00:00:00Z",
        cache_key="local", pages=[PageText(page_number=i, text=text,
        extraction_method="native") for i, text in enumerate(texts, 1)])
    chunks = [ClauseChunk(
        clause_id=f"chunk-{page.page_number:03d}", title="FRANQUIA",
        category=ClauseCategory.LIMITS, page_start=page.page_number,
        page_end=page.page_number, text=page.text,
        source_pages=[SourcePage(page_number=page.page_number, text=page.text)]
    ) for page in document.pages]
    anchors = [{"id": "ANCHOR", "field": "retencao_franquia", "page": 12,
                "quote": "FRANQUIA\nParticipacao obrigatoria",
                "sha256": document.sha256}]
    return document, chunks, anchors


def first_batch():
    document, chunks, _ = synthetic_corpus()
    plan = LocalRetrievalIndex.build(document, chunks).plan("LIMITS")
    return build_semantic_batches(plan)[0]


def test_recall_requires_exact_page_quote_and_field_in_sent_fragment():
    batch = first_batch()
    candidate = batch.candidates[0]
    plan = LocalRetrievalIndex.build(*synthetic_corpus()[:2]).plan("LIMITS")
    plans = {"LIMITS": replace(plan, candidates=(candidate,))}
    item = {"id": "x", "field": "retencao_franquia",
            "page": candidate.source_pages[0].page_number,
            "quote": "FRANQUIA\nParticipacao obrigatoria"}
    assert candidate_recall(plans, [item])["hits"] == 1
    assert candidate_recall(plans, [{**item, "quote": "different content"}])["hits"] == 0
    assert candidate_recall(plans, [{**item, "page": 12}])["hits"] == 0
    wrong_field = replace(candidate, fields=("sublimites",))
    assert candidate_recall({"LIMITS": replace(plan, candidates=(wrong_field,))},
                            [item])["hits"] == 0
    # Case/accent/punctuation are never erased by the exact reference check.
    assert candidate_recall(plans, [{**item, "quote": "franquia"}])["hits"] == 0


@pytest.mark.parametrize("changes", [
    {"sha256": "b" * 64}, {"page": 99}, {"page": True},
    {"field": "unknown"}, {"quote": "not present"}, {"quote": ""},
])
def test_invalid_golden_reference_rejected_without_changing_it(changes):
    document, _, items = synthetic_corpus()
    invalid = {**items[0], **changes}
    before = copy.deepcopy(invalid)
    with pytest.raises(ValueError, match="Golden anchor"):
        validate_golden(document, [invalid])
    assert invalid == before


def test_duplicate_golden_anchor_ids_are_rejected():
    document, _, items = synthetic_corpus()
    with pytest.raises(ValueError, match="unique"):
        validate_golden(document, items * 2)


def test_pure_planning_retains_corpus_and_reports_partial_initial_recall_honestly():
    document, chunks, items = synthetic_corpus()
    with patch("src.llm.openai_client.OpenAI", side_effect=AssertionError("No API")), \
            patch("src.pipeline.get_gateway", side_effect=AssertionError("No gateway")):
        first = plan_document(document, chunks, golden_items=items)
        second = plan_document(document, chunks, golden_items=items)
    assert first == second
    assert first["genai_calls"] == 0
    assert first["full_local_corpus_retained"]
    assert first["full_local_chunks"] == len(chunks)
    assert first["index"]["local_text_chars"] == sum(len(p.text) for p in document.pages)
    assert first["initial"]["totals"]["logical_calls"] < len(chunks)
    assert first["progressive"][0]["recall"]["hits"] == 0
    assert first["progressive"][-1]["recall"]["hits"] == 1
    totals = [stage["recall"]["hits"] for stage in first["progressive"]]
    assert totals == sorted(totals)
    for previous, current in zip(first["progressive"], first["progressive"][1:]):
        for old_group, new_group in zip(previous["groups"], current["groups"]):
            assert set(old_group["candidate_ids"]) <= set(new_group["candidate_ids"])
    stress = first["stress_all_unresolved_and_flagged"]["totals"]["logical_calls"]
    assert stress == sum(first[key]["totals"]["logical_calls"] for key in (
        "initial", "fallback_all_unresolved", "verifier_all_flagged"))
    assert first["verifier_all_flagged"]["totals"]["by_role"]["optimized_verification"]["logical_calls"] > 0
    assert first["structural_absolute_bound"]["logical_calls"] >= stress


def test_input_estimates_include_real_messages_and_response_schema_without_requests():
    batch = first_batch()
    measured = measure_batch(batch, "optimized_extraction", ROUTING)
    shape = request_shape(batch, "optimized_extraction", ROUTING)
    assert shape["input"][1]["content"] == json.dumps(batch.payload(), ensure_ascii=False)
    assert shape["text"]["format"]["schema"]["additionalProperties"] is False
    assert '"fields"' in shape["input"][0]["content"]
    assert measured["serialized_request_chars"] > measured["system_chars"] + measured["user_chars"]
    assert measured["strict_schema_chars"] > 0
    assert measured["input_tokens_utf8_reserve"] >= measured["input_tokens_approx"]
    assert measured["output_tokens_assumed"] == 1500
    assert measured["max_output_tokens"] == 4500
    assert measured["payload_chars"] == batch.char_count
    assert measured["pages"] == list(batch.pages)
    assert shape["reasoning"] == {"effort": "none"}
    assert not {"tools", "service_tier", "temperature"} & shape.keys()
    assert shape["store"] is False


def test_request_shape_respects_configured_family_and_fast_model_reasoning():
    batch = first_batch()
    custom = ExtractionRouting("gpt-4.1-mini-2025-04-14",
                               "gpt-5.4-2026-03-05", "gpt-5.2-2025-12-11")
    assert "reasoning" not in request_shape(batch, "optimized_extraction", custom)
    assert request_shape(batch, "optimized_interpretation", custom)["reasoning"] == {"effort": "low"}
    assert request_shape(batch, "optimized_verification", custom)["reasoning"] == {"effort": "low"}
    assert request_shape(batch, "optimized_interpretation", custom,
                         fast_model=custom.interpretation_model)["reasoning"] == {"effort": "none"}


def test_prices_are_scenarios_unknown_models_are_never_filled_or_free():
    batch = measure_batch(first_batch(), "optimized_extraction", ROUTING)
    prices = {"verified_on": "2026-10-04", "currency": "USD",
              "cache_write_input_multiplier": 1.25,
              "models": {"gpt-5.6-luna": {"input": .2, "cached_input": .02, "output": 1.2}}}
    priced = price_scenario([batch], prices)
    expected = (batch["input_tokens_approx"] * .2 * 1.25 + 1500 * 1.2) / 1e6
    assert priced["usd"] == round(expected, 6)
    assert priced["status"] == "REFERENCE_SCENARIO_ONLY"
    assert price_scenario([batch], None)["usd"] is None
    assert price_scenario([{**batch, "model": "other"}], prices)["usd"] is None
    assert batch_totals([])["by_role"]["optimized_verification"]["logical_calls"] == 0


def test_runtime_output_atomic_and_confined_to_processed_directory(tmp_path):
    output = tmp_path / "data/processed/preflight/optimized_plan.json"
    save_plan({"genai_calls": 0, "value": "citacao"}, output, root=tmp_path)
    assert json.loads(output.read_text(encoding="utf-8"))["genai_calls"] == 0
    assert not list(output.parent.glob(".optimized_plan_*.tmp"))
    assert local_path(tmp_path, r"data\processed\example.pdf").is_relative_to(tmp_path)
    with pytest.raises(ValueError, match="inside data/processed"):
        save_plan({}, tmp_path / "outside.json", root=tmp_path)
    with pytest.raises(ValueError, match="inside data/processed"):
        local_path(tmp_path, "../../outside.pdf")


def test_real_public_pdfs_fixed_anchor_recall_without_sdk_or_api(tmp_path):
    golden_path = ROOT / "tests/fixtures/public_retrieval_golden.json"
    manifest_path = ROOT / "data/processed/public_validation_preflight/candidates.json"
    if not golden_path.is_file() or not manifest_path.is_file():
        pytest.skip("Optional verified public-PDF fixture/manifest is not installed.")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    if any(not local_path(ROOT, record["local_path"]).is_file()
           for record in golden["documents"].values()):
        pytest.skip("Optional Berkley/AXA public PDFs are not installed.")
    settings = IngestionSettings(max_document_mb=30, ocr_languages="por+eng",
        min_native_chars=20, ocr_timeout_seconds=60, render_dpi=200,
        processed_dir=tmp_path / "ocr")
    with patch("src.llm.openai_client.OpenAI", side_effect=AssertionError("No SDK")), \
            patch("src.pipeline.get_gateway", side_effect=AssertionError("No gateway")), \
            patch("src.config.load_dotenv"):
        result = run_preflight(ingestion_settings=settings,
                              controls=ExtractionOptimizationSettings(), routing=ROUTING)
    assert result["genai_calls"] == result["providers_constructed"] == 0
    assert [d["full_local_chunks"] for d in result["documents"]] == [480, 620]
    assert sum(d["progressive"][-1]["recall"]["hits"] for d in result["documents"]) == 20
    assert sum(d["progressive"][-1]["recall"]["total"] for d in result["documents"]) == 20
    for record in result["documents"]:
        assert record["full_local_corpus_retained"]
        assert record["initial"]["totals"]["logical_calls"] < record["full_local_chunks"]
        assert record["progressive"][-1]["recall"]["fraction"] == 1
        assert record["progressive"][0]["recall"]["hits"] <= record["progressive"][-1]["recall"]["hits"]
        assert record["stress_all_unresolved_and_flagged"]["totals"]["logical_calls"] > 30


def test_golden_guided_expansion_is_selective_and_explicitly_not_an_llm_oracle():
    document, chunks, anchors = synthetic_corpus()
    result = plan_document(document, chunks, golden_items=anchors)
    guided = result["golden_guided"]
    assert guided["method"] == "GOLDEN_GUIDED_RETRIEVAL_DIAGNOSTIC_ONLY"
    assert guided["final_recall"]["hits"] == 1
    assert guided["fallback"]["totals"]["logical_calls"] > 0
    for batch in guided["fallback"]["batches"]:
        assert batch["fields"] == ["retencao_franquia"]
    assert guided["verification_fields"] == {"LIMITS": ["retencao_franquia"]}
    assert result["scenarios"]["EXPECTED"]["http_attempts_per_operation"] == 1
    assert result["scenarios"]["CONSERVATIVE"]["http_attempts_per_operation"] == 1
    worst = result["scenarios"]["WORST_REASONABLE"]
    assert worst["http_attempts_per_operation"] == 3
    assert worst["retry_attempts"] == worst["totals_logical"]["logical_calls"] * 2
    assert worst["comparison_calls"] == worst["separate_json_repair_calls"] == 0
    assert worst["not_guaranteed_total_calls_or_financial_ceiling"]
    assert result["scenarios"]["EXPECTED"]["totals_logical"]["logical_calls"] <= worst["totals_logical"]["logical_calls"]
    assert all("valor" not in batch for batch in guided["fallback"]["batches"])


@pytest.mark.parametrize("role", [
    "optimized_extraction", "optimized_interpretation", "optimized_verification",
])
def test_planned_request_shape_matches_existing_adapter_using_fake_responses(role):
    import os
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.config import get_settings
    from src.llm.openai_client import OpenAIProvider

    with patch.dict(os.environ, {}, clear=True), patch("src.config.load_dotenv"):
        settings = get_settings(provider="openai", require_api_key=False)
    batch = first_batch()
    shape = request_shape(batch, role, ROUTING, fast_model=settings.model_fast)
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(
        status="completed", output_text="{}", usage=SimpleNamespace(
            input_tokens=1, output_tokens=1, input_tokens_details=SimpleNamespace(cached_tokens=0)))
    provider = OpenAIProvider(settings, client=client)
    provider.complete(model=shape["model"], agent=role, messages=shape["input"],
                      max_tokens=4500, temperature=0, response_format={"type": "json_object"})
    client.responses.create.assert_called_once_with(**shape)


def test_recall_reports_only_observable_miss_reason():
    batch = first_batch()
    document, chunks, _ = synthetic_corpus()
    plan = LocalRetrievalIndex.build(document, chunks).plan("LIMITS")
    candidate = batch.candidates[0]
    plans = {"LIMITS": replace(plan, candidates=(candidate,))}
    item = {"id": "reason", "field": "retencao_franquia", "page": 12, "quote": "FRANQUIA"}
    assert candidate_recall(plans, [item])["anchors"][0]["miss_reason"] == "expected_page_not_selected_for_field"
    item["page"] = candidate.source_pages[0].page_number
    item["quote"] = "different quote"
    assert candidate_recall(plans, [item])["anchors"][0]["miss_reason"] == "exact_quote_not_in_selected_field_page_fragments"


@pytest.mark.parametrize("ids", [
    ["unapproved"], ["berkley_do_202512", "berkley_do_202512"],
])
def test_cli_rejects_unapproved_or_duplicate_ids_before_local_processing(ids):
    from scripts.plan_public_extraction import main
    with patch("sys.argv", ["plan_public_extraction.py", "--documents", *ids]), \
            patch("scripts.plan_public_extraction.run_preflight") as preflight, \
            patch("scripts.plan_public_extraction.get_settings") as settings:
        with pytest.raises(SystemExit) as caught:
            main()
    assert caught.value.code == 2
    preflight.assert_not_called()
    settings.assert_not_called()


def test_cli_can_plan_one_approved_local_document_for_pilot_estimates():
    from scripts.plan_public_extraction import main
    with patch("sys.argv", ["plan_public_extraction.py", "--documents", "berkley_do_202512"]), \
            patch("scripts.plan_public_extraction.run_preflight", return_value={"documents": []}) as preflight, \
            patch("scripts.plan_public_extraction.save_plan") as save, \
            patch("scripts.plan_public_extraction.get_settings"), \
            patch("scripts.plan_public_extraction.get_extraction_routing"), \
            patch("scripts.plan_public_extraction.get_extraction_optimization_settings"):
        assert main() == 0
    assert preflight.call_args.kwargs["document_ids"] == ["berkley_do_202512"]
    save.assert_called_once()
