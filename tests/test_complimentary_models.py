"""Offline E0 safeguards: no SDK request, credentials or production manifest."""
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from scripts import validate_complimentary_models as controller


def response(requested_model, **overrides):
    result = dict(id="resp_offline", _request_id="req_offline", model=requested_model,
                  status="completed", output_text="OK", service_tier="default",
                  usage=SimpleNamespace(input_tokens=12, output_tokens=5,
                                        total_tokens=17,
                                        input_tokens_details=SimpleNamespace(cached_tokens=0)))
    result.update(overrides)
    return SimpleNamespace(**result)


def fake_client(callback):
    return SimpleNamespace(responses=SimpleNamespace(create=callback))


def run(tmp_path, callback, **kwargs):
    return controller.run_validation(client_factory=lambda: fake_client(callback),
                                     manifest_path=tmp_path / "e0.json", **kwargs)


def test_exact_five_requests_and_reservation_before_each_send(tmp_path):
    expected = (
        "gpt-5.4-mini-2026-03-17", "gpt-4.1-mini-2025-04-14",
        "gpt-5.4-2026-03-05", "gpt-5.2-2025-12-11", "gpt-5.6-luna",
    )
    sent = []
    def create(**params):
        persisted = json.loads((tmp_path / "e0.json").read_text(encoding="utf-8"))
        assert persisted["attempts_reserved"] == len(sent) + 1
        assert persisted["requests"][len(sent)]["attempted"] is True
        assert persisted["requests"][len(sent)]["status"] == "attempted"
        assert sum(row["attempted"] for row in persisted["requests"]) == len(sent) + 1
        sent.append(params)
        return response(params["model"])
    manifest = run(tmp_path, create)
    assert tuple(item["model"] for item in sent) == expected
    assert len(sent) == 5 and manifest["attempts_reserved"] == 5
    for params in sent:
        assert params["input"] == "Responda somente com OK."
        assert params["store"] is False and params["max_output_tokens"] == 64
        allowed = {"model", "input", "store", "max_output_tokens"}
        if params["model"] != expected[1]:
            allowed.add("reasoning")
            assert params["reasoning"] == {"effort": "none"}
        assert set(params) == allowed
    assert manifest["run_status"] == "completed"
    assert all(row["total_tokens"] == 17 and row["cached_input_tokens"] == 0
               and row["dashboard_cost"] == "PENDING_DASHBOARD_RECONCILIATION"
               for row in manifest["requests"])
    assert json.loads((tmp_path / "e0.json").read_text()) == manifest


@pytest.mark.parametrize("previous", ["", "not-json", '{"run_status":"running"}',
                                    '{"run_status":"completed"}'])
def test_any_existing_manifest_blocks_before_client_creation(tmp_path, previous):
    path = tmp_path / "e0.json"
    path.write_text(previous)
    factory = Mock()
    with pytest.raises(controller.AlreadyAttempted):
        controller.run_validation(client_factory=factory, manifest_path=path)
    factory.assert_not_called()
    assert path.read_text() == previous


def test_failed_request_is_not_retried_or_logged_raw(tmp_path, capsys):
    sent = []
    secret = "SENSITIVE_OFFLINE_ERROR_SENTINEL"
    class FakeRateError(Exception):
        status_code = 429
        request_id = "req_rejected"
    def create(**params):
        sent.append(params["model"])
        if len(sent) == 1:
            raise FakeRateError(secret)
        return response(params["model"])
    manifest = run(tmp_path, create)
    assert len(sent) == 5 and len(set(sent)) == 5
    row = manifest["requests"][0]
    assert row["status"] == "error"
    assert row["error"] == {"kind": "rate_limit", "http_status": 429}
    assert row["safe_request_id"] == "req_rejected"
    assert row["input_tokens"] is None
    assert secret not in (tmp_path / "e0.json").read_text()
    assert secret not in capsys.readouterr().out


def test_crash_during_send_cannot_resume_or_repeat(tmp_path):
    calls = []
    def interrupted(**params):
        calls.append(params)
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, interrupted)
    persisted = json.loads((tmp_path / "e0.json").read_text())
    assert persisted["attempts_reserved"] == 1
    assert persisted["requests"][0]["attempted"] is True
    assert persisted["requests"][0]["status"] == "attempted"
    with pytest.raises(controller.AlreadyAttempted):
        run(tmp_path, interrupted)
    assert len(calls) == 1


@pytest.mark.parametrize("write_failure_number, expected_sends", [(2, 0), (3, 1)])
def test_persistence_failure_stops_sends_and_rerun(tmp_path, monkeypatch,
                                                write_failure_number, expected_sends):
    real_write = controller._atomic_write
    writes = []
    calls = []
    def failing_write(path, payload):
        writes.append(1)
        if len(writes) == write_failure_number:
            raise OSError("offline disk failure")
        return real_write(path, payload)
    monkeypatch.setattr(controller, "_atomic_write", failing_write)
    def create(**params):
        calls.append(params)
        return response(params["model"])
    with pytest.raises(OSError):
        run(tmp_path, create)
    assert len(calls) == expected_sends
    with pytest.raises(controller.AlreadyAttempted):
        run(tmp_path, create)
    assert len(calls) == expected_sends


def test_concurrent_starts_only_one_claim_can_send(tmp_path):
    calls = []
    path = tmp_path / "e0.json"
    def create(**params):
        calls.append(params["model"])
        return response(params["model"])
    def attempt():
        try:
            return controller.run_validation(client_factory=lambda: fake_client(create),
                                             manifest_path=path)["run_status"]
        except controller.AlreadyAttempted:
            return "blocked"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["blocked", "completed"]
    assert len(calls) == 5 and len(set(calls)) == 5


def test_stale_claim_blocks_even_without_manifest(tmp_path):
    path = tmp_path / "e0.json"
    path.with_name("e0.json.lock").write_text("previous claim")
    factory = Mock()
    with pytest.raises(controller.AlreadyAttempted):
        controller.run_validation(client_factory=factory, manifest_path=path)
    factory.assert_not_called()


def test_client_initialization_failure_has_no_sends_or_raw_error(tmp_path):
    factory = Mock(side_effect=RuntimeError("SENSITIVE_OFFLINE_CLIENT_SENTINEL"))
    path = tmp_path / "e0.json"
    manifest = controller.run_validation(client_factory=factory, manifest_path=path)
    assert manifest["run_status"] == "initialization_failed"
    assert manifest["attempts_reserved"] == 0
    assert all(not row["attempted"] for row in manifest["requests"])
    assert "SENSITIVE_OFFLINE_CLIENT_SENTINEL" not in path.read_text()
    with pytest.raises(controller.AlreadyAttempted):
        controller.run_validation(client_factory=factory, manifest_path=path)
    assert factory.call_count == 1


def test_unknown_usage_is_null_and_untrusted_metadata_is_not_written(tmp_path):
    sentinel = "SENSITIVE_OFFLINE_RESPONSE_SENTINEL"
    def create(**params):
        return response(params["model"], id=sentinel, _request_id=sentinel,
                        model=sentinel, service_tier=sentinel, status=sentinel,
                        output_text=sentinel, usage=None)
    manifest = run(tmp_path, create)
    for row in manifest["requests"]:
        assert row["status"] == "UNKNOWN_RESPONSE_STATUS"
        assert all(row[key] is None for key in ("response_id", "safe_request_id",
                    "model_returned", "service_tier", "input_tokens",
                    "cached_input_tokens", "output_tokens", "total_tokens"))
    assert sentinel not in (tmp_path / "e0.json").read_text()


def test_bad_usage_counts_are_not_coerced_into_known_zero():
    usage = SimpleNamespace(input_tokens=True, output_tokens=-1, total_tokens="17",
                            input_tokens_details={"cached_tokens": False})
    record = controller._response_record(response(controller.MODELS[0], usage=usage))
    assert all(record[name] is None for name in ("input_tokens", "output_tokens",
                                                "cached_input_tokens", "total_tokens"))


def test_plan_mode_does_not_initialize_sdk_or_reserve_experiment(monkeypatch, tmp_path, capsys):
    factory = Mock(side_effect=AssertionError("must not initialize client"))
    monkeypatch.setattr(controller, "_real_client", factory)
    monkeypatch.setattr(controller, "MANIFEST", tmp_path / "production-e0.json")
    assert controller.main([]) == 0
    factory.assert_not_called()
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "PLAN_ONLY" and len(plan["requests"]) == 5
    assert not list(tmp_path.iterdir())


def test_real_client_configuration_without_real_client(monkeypatch):
    import dotenv
    import openai
    factory = Mock(return_value=object())
    monkeypatch.setattr(dotenv, "load_dotenv", Mock(return_value=False))
    monkeypatch.setattr(openai, "OpenAI", factory)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-placeholder")
    controller._real_client()
    kwargs = factory.call_args.kwargs
    assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 30.0
    assert kwargs["base_url"] == "https://api.openai.com/v1"
    assert not any(name in kwargs for name in ("service_tier", "tools", "model"))

def test_valid_new_snapshot_is_recorded_without_accepting_arbitrary_metadata():
    valid = controller._response_record(response(
        controller.MODELS[-1], model="gpt-5.6-luna-2026-10-04"))
    assert valid["model_returned"] == "gpt-5.6-luna-2026-10-04"
    rejected = controller._response_record(response(
        controller.MODELS[-1], model="gpt-5.6-luna-private-secret"))
    assert rejected["model_returned"] is None


def test_document_renderer_is_offline_financial_pending_and_sanitized(tmp_path):
    manifest = run(tmp_path, lambda **params: response(params["model"]))
    text = controller.render_validation_document(manifest)
    assert text.count("| PENDING_DASHBOARD_RECONCILIATION |") == 5
    assert "req_offline / resp_offline" in text
    assert "TECHNICAL_RESULTS_RECORDED" in text
    manifest["requests"][0].update(safe_request_id="SENSITIVE_RENDERER_SENTINEL",
        response_id="SENSITIVE_RENDERER_SENTINEL", service_tier="SENSITIVE_RENDERER_SENTINEL",
        status={"untrusted": "SENSITIVE_RENDERER_SENTINEL"},
        input_tokens="SENSITIVE_RENDERER_SENTINEL")
    assert "SENSITIVE_RENDERER_SENTINEL" not in controller.render_validation_document(manifest)
    pending = controller.render_validation_document()
    assert pending.count("| PENDING_EXECUTION |") == 5
    assert "| 0 |" not in pending
