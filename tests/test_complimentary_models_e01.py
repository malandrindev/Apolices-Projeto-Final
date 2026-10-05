"""Offline E0.1 safeguards; production manifest and clients are never used."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from scripts import validate_complimentary_models as e0
from scripts import validate_complimentary_models_e01 as controller


def fake_response(model):
    return SimpleNamespace(id="resp_offline", _request_id="req_offline", model=model,
        status="completed", output_text="OK", service_tier="default",
        usage=SimpleNamespace(input_tokens=13, output_tokens=5, total_tokens=18,
                              input_tokens_details=SimpleNamespace(cached_tokens=0)))


def run(path, callback):
    return controller.run_validation(manifest_path=path,
        client_factory=lambda: SimpleNamespace(responses=SimpleNamespace(create=callback)))


def test_exact_two_reservations_safe_parameters_and_e0_unchanged(tmp_path):
    original = e0.MODELS
    path = tmp_path / "e01.json"
    sent = []
    def create(**params):
        persisted = json.loads(path.read_text())
        assert persisted["attempts_reserved"] == len(sent) + 1
        assert persisted["requests"][len(sent)]["status"] == "attempted"
        sent.append(params)
        return fake_response(params["model"])
    result = run(path, create)
    assert [row["model"] for row in sent] == ["gpt-5.6-terra", "gpt-5.6-sol"]
    for params in sent:
        assert params == {"model": params["model"], "input": "Responda somente com OK.",
                          "store": False, "max_output_tokens": 64,
                          "reasoning": {"effort": "none"}}
    assert result["attempts_reserved"] == 2
    assert result["experiment"] == "E01_TERRA_SOL_CONTROLLED_VALIDATION"
    assert [r["model_returned"] for r in result["requests"]] == list(controller.MODELS)
    assert all(r["dashboard_cost"] == "PENDING_DASHBOARD_RECONCILIATION"
               for r in result["requests"])
    assert e0.MODELS == original and e0.MANIFEST != controller.MANIFEST
    with pytest.raises(controller.AlreadyAttempted):
        run(path, create)
    assert len(sent) == 2


@pytest.mark.parametrize("previous", ["", "not-json", '{"run_status":"running"}'])
def test_existing_manifest_blocks_before_sdk(tmp_path, previous):
    path = tmp_path / "e01.json"
    path.write_text(previous)
    factory = Mock()
    with pytest.raises(controller.AlreadyAttempted):
        controller.run_validation(client_factory=factory, manifest_path=path)
    factory.assert_not_called()
    assert path.read_text() == previous


def test_failure_is_never_retried_raw_error_never_recorded(tmp_path):
    path = tmp_path / "e01.json"
    sent = []
    class FakeError(Exception):
        status_code = 429
        request_id = "req_error"
    def create(**params):
        sent.append(params["model"])
        if len(sent) == 1:
            raise FakeError("SENSITIVE_OFFLINE_SENTINEL")
        return fake_response(params["model"])
    result = run(path, create)
    assert sent == list(controller.MODELS)
    assert result["requests"][0]["error"] == {"kind": "rate_limit", "http_status": 429}
    assert "SENSITIVE_OFFLINE_SENTINEL" not in path.read_text()
    assert result["requests"][1]["status"] == "completed"


def test_interruption_blocks_replay(tmp_path):
    path = tmp_path / "e01.json"
    calls = []
    def create(**params):
        calls.append(params)
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run(path, create)
    assert json.loads(path.read_text())["attempts_reserved"] == 1
    with pytest.raises(controller.AlreadyAttempted):
        run(path, create)
    assert len(calls) == 1


def test_failed_persistence_stops_before_next_request(tmp_path, monkeypatch):
    path = tmp_path / "e01.json"
    original = controller._atomic_write
    writes = []
    calls = []
    def write(path, payload):
        writes.append(1)
        if len(writes) == 3:
            raise OSError("offline persistence failure")
        return original(path, payload)
    monkeypatch.setattr(controller, "_atomic_write", write)
    def create(**params):
        calls.append(params)
        return fake_response(params["model"])
    with pytest.raises(OSError):
        run(path, create)
    assert len(calls) == 1
    with pytest.raises(controller.AlreadyAttempted):
        run(path, create)


def test_plan_only_does_not_load_client_or_create_manifest(monkeypatch, capsys):
    factory = Mock(side_effect=AssertionError("No client in plan mode"))
    monkeypatch.setattr(controller, "_real_client", factory)
    assert controller.main([]) == 0
    factory.assert_not_called()
    assert json.loads(capsys.readouterr().out)["requests_max"] == 2


def test_other_models_rejected():
    with pytest.raises(ValueError):
        controller.request_parameters("gpt-5.6-luna")
