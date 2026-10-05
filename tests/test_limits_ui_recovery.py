"""Offline UI replay protection and business-safe interrupted progress errors."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from tests.test_interface import button, fixture_documents, ui_patches
from tests.test_limits_pipeline import no_provider_http


def test_unfinished_history_blocks_before_provider_configuration_and_demo_still_works(tmp_path, no_provider_http):
    documents, processed, reports = fixture_documents(2)
    directory = tmp_path / "workspace_usage"
    directory.mkdir()
    ledger_path = directory / ("2" * 32 + "-attempt-ledger.json")
    ledger_path.write_text(json.dumps({
        "run_id": "2" * 32, "http_attempts": 24,
        "events": [{"document_id": documents[0].sha256, "result_status": "completed",
                    "response_id": "offline-response-" + str(index)} for index in range(24)],
    }), encoding="utf-8")
    original = ledger_path.read_bytes()
    structured_calls = []
    with ui_patches(tmp_path, processed, reports, structured_calls)[0], \
         patch("src.llm.providers.get_gateway", side_effect=AssertionError("Guard must precede provider.")) as factory:
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        app.session_state["workspace_docs"] = documents
        app.session_state["workspace_reference"] = documents[0].sha256
        app.run(timeout=20)
        button(app, "analyze").click().run(timeout=20)
        assert not app.exception and not structured_calls and not factory.called
        assert any("análise anterior" in item.value for item in app.error)
        assert any("Nenhuma nova chamada" in item.value for item in app.info)
        assert ledger_path.read_bytes() == original
        button(app, "new_comparison").click().run(timeout=20)
        button(app, "use_demo").click().run(timeout=30)
        button(app, "analyze").click().run(timeout=30)
        assert not app.exception and app.session_state["workspace_result"]["stats"]["demo"]
        assert not factory.called and not structured_calls
        assert ledger_path.read_bytes() == original


def test_keyerror_after_consumed_attempt_is_sanitized_and_replay_blocked(tmp_path, no_provider_http, monkeypatch):
    monkeypatch.setenv("MODEL_ROUTING_ENABLED", "true")
    documents, processed, reports = fixture_documents(2)
    stack, gateway = ui_patches(tmp_path, processed, reports, [])
    controls = []

    def construct_fake(*args, **kwargs):
        controls.append(kwargs["request_control"])
        return gateway

    def fail_after_returned_response(*args, **kwargs):
        control = controls[-1]
        event = {"document_id": documents[0].sha256, "logical_step_id": "offline-progress",
                 "requested_model": "offline-model", "role": "interpretation",
                 "semantic_level": 0, "result_status": "ATTEMPTED", "response_id": None,
                 "latency_ms": None, "input_tokens": None, "output_tokens": None,
                 "cached_input_tokens": None, "total_tokens": None, "fallback_level": 0,
                 "semantic_escalation": False, "evidence_validation_status": "NOT_CHECKED"}
        index = control.reserve(event, "offline-unique-fingerprint")
        control.finish(index, {"result_status": "completed", "response_id": "offline-response"},
                       technical_failure=False)
        gateway.events.append(dict(control.events[index]))
        raise KeyError("LIMITS")

    with stack, patch("src.llm.providers.get_gateway", side_effect=construct_fake), \
         patch("src.pipeline.process_and_structure_document", side_effect=fail_after_returned_response) as structured:
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        app.session_state["workspace_docs"] = documents
        app.session_state["workspace_reference"] = documents[0].sha256
        app.run(timeout=20)
        button(app, "analyze").click().run(timeout=20)
        assert not app.exception and structured.call_count == 1 and len(controls) == 1
        assert any("não pôde ser concluída com segurança" in item.value for item in app.error)
        visible = " ".join(item.value for item in [*app.error, *app.info, *app.markdown])
        assert "KeyError" not in visible and "LIMITS" not in visible and "Traceback" not in visible
        ledgers = list((tmp_path / "workspace_usage").glob("*-attempt-ledger.json"))
        assert len(ledgers) == 1
        persisted = json.loads(ledgers[0].read_text(encoding="utf-8"))
        assert persisted["workspace_status"] == "INTERRUPTED" and persisted["http_attempts"] == 1
        assert persisted["workspace_documents"] == [item.sha256 for item in documents]
        assert persisted["events"][0]["response_id"] == "offline-response"
        original = ledgers[0].read_bytes()
        button(app, "analyze").click().run(timeout=20)
        assert not app.exception and structured.call_count == 1 and len(controls) == 1
        assert any("análise anterior" in item.value for item in app.error)
        assert ledgers[0].read_bytes() == original


@pytest.mark.parametrize("failure", [KeyError("LIMITS"), OSError("private/path")])
def test_summary_write_failure_keeps_business_error_without_traceback(tmp_path, no_provider_http, failure):
    documents, processed, reports = fixture_documents(2)
    with ui_patches(tmp_path, processed, reports, [])[0], \
         patch("src.pipeline.process_and_structure_document", side_effect=failure), \
         patch("src.llm.usage_reporting.save_usage_runs", side_effect=OSError("private/path")):
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        app.session_state["workspace_docs"] = documents
        app.session_state["workspace_reference"] = documents[0].sha256
        app.run(timeout=20)
        button(app, "analyze").click().run(timeout=20)
        assert not app.exception and app.error
        visible = " ".join(item.value for item in [*app.error, *app.info, *app.warning])
        assert "KeyError" not in visible and "LIMITS" not in visible and "private/path" not in visible
        assert "Não foi possível salvar" in visible


def test_partial_result_retains_interrupted_marker_and_blocks_restart(tmp_path, no_provider_http, monkeypatch):
    monkeypatch.setenv("MODEL_ROUTING_ENABLED", "true")
    documents, processed, reports = fixture_documents(2)
    for report in reports.values():
        report.retrieval_diagnostics.update(partial=True, stop_reason="budget_exhausted")
    stack, gateway = ui_patches(tmp_path, processed, reports, [])
    controls = []

    def construct_fake(*args, **kwargs):
        controls.append(kwargs["request_control"])
        # A consumed terminal attempt still cannot justify an automatic new budget.
        event = {"document_id": documents[0].sha256, "logical_step_id": "offline-partial",
                 "requested_model": "offline-model", "role": "interpretation",
                 "semantic_level": 0, "result_status": "completed", "response_id": "offline-response",
                 "latency_ms": None, "input_tokens": None, "output_tokens": None,
                 "cached_input_tokens": None, "total_tokens": None, "fallback_level": 0,
                 "semantic_escalation": False, "evidence_validation_status": "NOT_CHECKED"}
        controls[-1].reserve(event, "offline-partial-fingerprint")
        return gateway

    with stack, patch("src.llm.providers.get_gateway", side_effect=construct_fake):
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        app.session_state["workspace_docs"] = documents
        app.session_state["workspace_reference"] = documents[0].sha256
        app.run(timeout=20)
        button(app, "analyze").click().run(timeout=30)
        assert not app.exception and app.session_state["workspace_result"]
        ledger = next((tmp_path / "workspace_usage").glob("*-attempt-ledger.json"))
        assert json.loads(ledger.read_text(encoding="utf-8"))["workspace_status"] == "INTERRUPTED"
        button(app, "analyze").click().run(timeout=20)
        assert not app.exception and len(controls) == 1
        button(app, "new_comparison").click().run(timeout=20)
        app.session_state["workspace_docs"] = documents
        app.session_state["workspace_reference"] = documents[0].sha256
        app.run(timeout=20)
        button(app, "analyze").click().run(timeout=20)
        assert not app.exception and len(controls) == 1
        assert any("análise anterior" in item.value for item in app.error)
