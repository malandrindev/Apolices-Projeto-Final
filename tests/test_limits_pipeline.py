"""Regression against original Porto/Allianz PDFs through live-style routed paths."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from scripts.check_limits_browser import ORIGINALS, ROOT, OfflineGroupedProvider, preserved_runtime_hashes
from src.agents.comparison import ComparisonAgent
from src.config import IngestionSettings, Settings
from src.llm.model_routing import ModelRoutingPolicy, RequestControl, RoutedGateway
from src.pipeline import process_and_structure_document, process_document
from src.schemas.policy import PolicyExtraction
from src.sources import UploadSource
from tests.test_interface import button


def offline_settings():
    return Settings(groq_api_key="", openai_api_key="offline-qa-placeholder", llm_provider="openai",
                    model_fast="gpt-5.6-luna", model_strong="gpt-5.6-sol",
                    model_intermediate="gpt-5.6-terra", model_vision="unused",
                    temperature=0, max_tokens=4096, timeout_seconds=60, max_retries=1)


def local_settings(path):
    return IngestionSettings(30, "por+eng", 20, 60, 200, Path(path))


@pytest.fixture
def no_provider_http():
    """A blocked construction/HTTP attempt fails even if the app catches its error."""
    attempted = []

    def reject(*args, **kwargs):
        attempted.append("real-provider-dispatch")
        raise AssertionError("Offline LIMITS regression may not dispatch a real provider.")

    with ExitStack() as stack:
        for target in ("openai.OpenAI.__init__", "openai.AsyncOpenAI.__init__",
                       "groq.Groq.__init__", "groq.AsyncGroq.__init__",
                       "httpx.Client.request", "httpx.AsyncClient.request"):
            stack.enter_context(patch(target, side_effect=reject))
        yield attempted
    assert attempted == []


@pytest.mark.parametrize("filename,pages,digest", ORIGINALS)
def test_original_document_preparation_preserves_all_pages_without_provider(tmp_path, no_provider_http, filename, pages, digest):
    path = ROOT / "data/demo_sources" / filename
    protected = preserved_runtime_hashes()
    ingested, processed = process_document(path, settings=local_settings(tmp_path / "processed"))
    assert ingested.sha256 == processed.sha256 == digest
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert len(processed.pages) == pages
    assert [page.page_number for page in processed.pages] == list(range(1, pages + 1))
    assert all(page.extraction_method == "native" and page.text.strip() for page in processed.pages)
    assert preserved_runtime_hashes() == protected


def assert_unique_attempts(gateway, provider):
    fingerprints = [call["request_fingerprint"] for call in provider.calls]
    keys = [(event["document_id"], event["logical_step_id"], event["requested_model"])
            for event in gateway.events]
    assert keys and len(keys) == len(set(keys))
    assert len(fingerprints) == len(set(fingerprints)) == len(gateway.events)
    assert all(event["result_status"] == "completed" for event in gateway.events)
    assert all(event["error_kind"] is None for event in gateway.events)


def test_two_original_documents_execute_real_routed_extraction_progress_and_comparison(tmp_path, no_provider_http, monkeypatch):
    monkeypatch.setenv("PUBLIC_EXTRACTION_STRATEGY", "optimized")
    protected = preserved_runtime_hashes()
    provider = OfflineGroupedProvider()
    gateway = RoutedGateway(provider, ModelRoutingPolicy(), RequestControl(50))
    reports, callbacks = [], {}
    for filename, pages, digest in ORIGINALS:
        callbacks[digest] = []
        _, processed, report, report_path, clauses = process_and_structure_document(
            ROOT / "data/demo_sources" / filename,
            ingestion_settings=local_settings(tmp_path / "processed"),
            llm_settings=offline_settings(), gateway=gateway,
            progress_callback=lambda *values, digest=digest: callbacks[digest].append(values),
        )
        assert len(processed.pages) == pages and clauses and report_path.is_file()
        assert report.sha256 == digest
        assert set(report.field_status) == set(PolicyExtraction.model_fields)
        assert report.retrieval_diagnostics["version"].startswith("grouped-routed")
        groups = [group["group_id"] for group in report.retrieval_diagnostics["groups"]]
        assert len(groups) == len(set(groups)) == 14
        assert set(groups) == {values[2] for values in callbacks[digest]}
        assert [values[0] for values in callbacks[digest]] == list(range(1, 15))
        assert {values[1] for values in callbacks[digest]} == {14}
        assert all(type(values[3]) is bool for values in callbacks[digest])
        reports.append(report)
    comparison = ComparisonAgent(gateway=gateway, model_strong="gpt-5.6-sol",
                                 processed_dir=tmp_path / "comparison").compare(*reports)
    assert len(comparison.differences) == 27
    assert comparison.document_id_a == ORIGINALS[0][2]
    assert comparison.document_id_b == ORIGINALS[1][2]
    assert_unique_attempts(gateway, provider)
    assert preserved_runtime_hashes() == protected


def test_original_upload_ui_callback_results_repeat_and_demo_are_all_offline(tmp_path, no_provider_http, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_ROUTING_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_EXTRACTION_STRATEGY", "optimized")
    protected = preserved_runtime_hashes()
    gateways, providers = [], []

    def get_fake_gateway(*args, **kwargs):
        provider = OfflineGroupedProvider()
        gateway = RoutedGateway(provider, ModelRoutingPolicy(), kwargs.get("request_control") or RequestControl(50))
        providers.append(provider)
        gateways.append(gateway)
        return gateway

    sources = [UploadSource().resolve(filename, (ROOT / "data/demo_sources" / filename).read_bytes())
               for filename, _, _ in ORIGINALS]
    with patch("src.config.get_settings", side_effect=lambda **kwargs: offline_settings()), \
         patch("src.config.get_ingestion_settings", return_value=local_settings(tmp_path / "processed")), \
         patch("src.llm.providers.get_gateway", side_effect=get_fake_gateway):
        app = AppTest.from_file("interface/app.py").run(timeout=30)
        assert not app.exception
        app.session_state["workspace_docs"] = sources
        app.session_state["workspace_reference"] = sources[0].sha256
        app.run(timeout=30)
        app.number_input(key="max_calls").set_value(50).run(timeout=30)
        button(app, "analyze").click().run(timeout=90)
        assert not app.exception and app.session_state["workspace_result"]
        result = app.session_state["workspace_result"]
        assert len(result["reports"]) == 2 and len(result["comparisons"]) == 1
        assert set(result["reports"]) == {row[2] for row in ORIGINALS}
        assert {sha:len(info["processed"].pages) for sha,info in app.session_state["prepared_docs"].items()} == {
            digest:pages for _,pages,digest in ORIGINALS
        }
        assert [tab.label for tab in app.tabs] == ["Resumo", "Coberturas", "Limites & Franquias",
                                                 "Exclusões", "Cláusulas", "Evidências"]
        assert not app.error
        assert any("Não localizado com segurança" in item.value for item in app.markdown)
        assert_unique_attempts(gateways[0], providers[0])
        attempts = len(providers[0].calls)
        app.run(timeout=30)
        button(app, "analyze").click().run(timeout=30)
        assert not app.exception
        assert len(gateways) == 1 and len(providers[0].calls) == attempts
        assert len(app.session_state["workspace_result"]["stats"]["events"]) == attempts
        button(app, "new_comparison").click().run(timeout=30)
        assert not app.exception and app.session_state["workspace_docs"] == []
        button(app, "use_demo").click().run(timeout=30)
        button(app, "analyze").click().run(timeout=30)
        assert not app.exception and app.session_state["workspace_result"]["stats"]["demo"]
        assert len(gateways) == 1 and len(providers[0].calls) == attempts
        assert not app.session_state["workspace_result"]["stats"]["calls"]
    assert preserved_runtime_hashes() == protected
