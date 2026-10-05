"""Offline replay guards over synthetic attempt ledgers; no SDK is needed."""
import hashlib
import json

import pytest

from src.workspace_recovery import HISTORY_UNAVAILABLE_MESSAGE, find_unsafe_workspace_runs


PORTO = "a" * 64
ALLIANZ = "b" * 64
OTHER = "c" * 64
RUN = "1" * 32


def write_ledger(tmp_path, **updates):
    ledger = {
        "run_id": RUN, "http_attempts": 1,
        "events": [{"document_id": PORTO, "result_status": "completed", "response_id": "resp-test"}],
        **updates,
    }
    directory = tmp_path / "workspace_usage"
    directory.mkdir(exist_ok=True)
    path = directory / (RUN + "-attempt-ledger.json")
    path.write_text(json.dumps(ledger), encoding="utf-8")
    return path


def test_manual_24_returned_attempts_without_completion_marker_block_replay(tmp_path):
    events = [{"document_id": PORTO, "result_status": "error" if index in {1, 16} else "completed",
               "response_id": "resp-" + str(index), "payload": "must not be returned"}
              for index in range(24)]
    write_ledger(tmp_path, http_attempts=24, events=events)
    risks = find_unsafe_workspace_runs(tmp_path, [PORTO, ALLIANZ])
    assert len(risks) == 1
    risk = risks[0]
    assert risk["workspace_status"] == "LEGACY"
    assert risk["document_ids"] == [PORTO]
    assert risk["http_attempts"] == risk["returned_responses"] == risk["recorded_events"] == 24
    assert risk["completed_events"] == 22 and risk["error_events"] == 2
    assert risk["uncertain_attempts"] == risk["unknown_attempts"] == 0
    assert "payload" not in json.dumps(risks) and "must not be returned" not in json.dumps(risks)


def test_completed_terminal_run_does_not_block(tmp_path):
    write_ledger(tmp_path, workspace_status="COMPLETED", workspace_documents=[PORTO, ALLIANZ])
    assert find_unsafe_workspace_runs(tmp_path, [PORTO, ALLIANZ]) == []


@pytest.mark.parametrize("status", ["ACTIVE", "INTERRUPTED"])
def test_new_unfinished_markers_with_consumed_attempts_block(tmp_path, status):
    write_ledger(tmp_path, workspace_status=status, workspace_documents=[PORTO, ALLIANZ])
    assert find_unsafe_workspace_runs(tmp_path, [ALLIANZ])[0]["workspace_status"] == status


@pytest.mark.parametrize("status", ["ACTIVE", "INTERRUPTED", "COMPLETED"])
def test_uncertain_reservation_blocks_even_with_completed_marker(tmp_path, status):
    write_ledger(tmp_path, workspace_status=status, workspace_documents=[PORTO],
                 events=[{"document_id": PORTO, "result_status": "ATTEMPTED", "response_id": None}])
    risk = find_unsafe_workspace_runs(tmp_path, [PORTO])[0]
    assert risk["uncertain_attempts"] == 1 and risk["returned_responses"] == 0


def test_consumed_count_without_event_is_unknown_and_blocks_completed(tmp_path):
    write_ledger(tmp_path, workspace_status="COMPLETED", workspace_documents=[PORTO],
                 http_attempts=2)
    risk = find_unsafe_workspace_runs(tmp_path, [PORTO])[0]
    assert risk["unknown_attempts"] == risk["uncertain_attempts"] == 1
    assert risk["recorded_events"] == 1 and risk["http_attempts"] == 2


def test_unknown_consumed_count_without_source_attribution_blocks_conservatively(tmp_path):
    write_ledger(tmp_path, http_attempts=3, events=[])
    risk = find_unsafe_workspace_runs(tmp_path, [PORTO])[0]
    assert risk["unattributed_documents"] is True and risk["document_ids"] == []
    assert risk["unknown_attempts"] == risk["uncertain_attempts"] == 3


def test_source_uninvolved_in_unfinished_run_does_not_block(tmp_path):
    write_ledger(tmp_path, workspace_status="INTERRUPTED", workspace_documents=[PORTO, ALLIANZ])
    assert find_unsafe_workspace_runs(tmp_path, [OTHER]) == []


def test_legacy_derives_all_source_hashes_and_ignores_opaque_legacy_id(tmp_path):
    write_ledger(tmp_path, http_attempts=3, events=[
        {"document_id": PORTO, "result_status": "completed"},
        {"document_id": ALLIANZ, "result_status": "completed"},
        {"document_id": "legacy", "result_status": "completed"},
    ])
    assert find_unsafe_workspace_runs(tmp_path, [ALLIANZ])[0]["document_ids"] == [PORTO, ALLIANZ]
    assert find_unsafe_workspace_runs(tmp_path, [OTHER]) == []


@pytest.mark.parametrize("status", ["ACTIVE", "INTERRUPTED", "COMPLETED"])
def test_zero_attempts_never_block(tmp_path, status):
    write_ledger(tmp_path, workspace_status=status, workspace_documents=[PORTO], http_attempts=0, events=[])
    assert find_unsafe_workspace_runs(tmp_path, [PORTO]) == []


def test_scan_never_mutates_attempts_cache_or_claims(tmp_path):
    path = write_ledger(tmp_path)
    cache = tmp_path / "grouped_extraction" / "cache.json"
    cache.parent.mkdir()
    cache.write_text('{"response":"preserved"}', encoding="utf-8")
    claim = tmp_path / "claim.json"
    claim.write_text('{"reserved":true}', encoding="utf-8")
    paths = [path, cache, claim]
    before = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths}
    assert find_unsafe_workspace_runs(tmp_path, [PORTO])
    assert find_unsafe_workspace_runs(tmp_path, [PORTO])
    assert before == {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths}


@pytest.mark.parametrize("updates", [
    {"events": {}}, {"events": [None]}, {"events": [{"result_status": {}}]},
    {"http_attempts": -1}, {"http_attempts": True}, {"http_attempts": 0},
    {"workspace_documents": ["sk-sensitive"]}, {"workspace_documents": None},
    {"workspace_status": "bad-secret"}, {"run_id": "sk-sensitive"},
])
def test_malformed_schema_fails_closed_without_disclosing_contents(tmp_path, updates):
    path = write_ledger(tmp_path, **updates)
    with pytest.raises(ValueError) as error:
        find_unsafe_workspace_runs(tmp_path, [PORTO])
    assert str(error.value) == HISTORY_UNAVAILABLE_MESSAGE
    assert str(path) not in str(error.value) and "sk-sensitive" not in str(error.value)


@pytest.mark.parametrize("raw", ["{bad sk-sensitive", "[]", "null"])
def test_invalid_json_or_root_fails_closed(tmp_path, raw):
    path = write_ledger(tmp_path)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="histórico de execução"):
        find_unsafe_workspace_runs(tmp_path, [PORTO])


def test_unreadable_history_fails_closed_without_path(tmp_path, monkeypatch):
    write_ledger(tmp_path)
    def denied(*args, **kwargs):
        raise PermissionError("secret/path")
    monkeypatch.setattr(type(tmp_path), "read_text", denied)
    with pytest.raises(ValueError) as error:
        find_unsafe_workspace_runs(tmp_path, [PORTO])
    assert str(error.value) == HISTORY_UNAVAILABLE_MESSAGE


def test_no_history_and_non_attempt_json_are_safe(tmp_path):
    assert find_unsafe_workspace_runs(tmp_path, [PORTO]) == []
    directory = tmp_path / "workspace_usage"
    directory.mkdir()
    (directory / "usage-summary.json").write_text("not a ledger", encoding="utf-8")
    assert find_unsafe_workspace_runs(tmp_path, [PORTO]) == []
    assert find_unsafe_workspace_runs(tmp_path, []) == []


@pytest.mark.parametrize("left,right", [(PORTO, ALLIANZ), (ALLIANZ, PORTO)])
def test_legacy_comparison_pair_hash_blocks_both_reference_orderings(tmp_path, left, right):
    pair_id = hashlib.sha256((left + ":" + right).encode("utf-8")).hexdigest()
    path = write_ledger(tmp_path, events=[{
        "document_id": pair_id, "result_status": "completed", "response_id": "resp-pair",
    }])
    before = path.read_bytes()
    for selected in ([PORTO, ALLIANZ], [ALLIANZ, PORTO]):
        risks = find_unsafe_workspace_runs(tmp_path, selected)
        assert len(risks) == 1 and risks[0]["document_ids"] == [pair_id]
        assert risks[0]["http_attempts"] == risks[0]["returned_responses"] == 1
    assert find_unsafe_workspace_runs(tmp_path, [OTHER]) == []
    assert find_unsafe_workspace_runs(tmp_path, [PORTO, OTHER]) == []
    assert path.read_bytes() == before


def test_legacy_comparison_pair_hash_is_detected_in_five_document_workspace(tmp_path):
    pair_id = hashlib.sha256((ALLIANZ + ":" + PORTO).encode("utf-8")).hexdigest()
    write_ledger(tmp_path, events=[{"document_id": pair_id, "result_status": "ATTEMPTED"}])
    risks = find_unsafe_workspace_runs(tmp_path, [OTHER, "d" * 64, ALLIANZ, "e" * 64, PORTO])
    assert len(risks) == 1 and risks[0]["uncertain_attempts"] == 1


@pytest.mark.parametrize("kind", ["timeout", "connection", "schema_invalid"])
def test_error_without_response_receipt_is_uncertain_even_when_workspace_completed(tmp_path, kind):
    write_ledger(tmp_path, workspace_status="COMPLETED", workspace_documents=[PORTO], events=[{
        "document_id": PORTO, "result_status": "error", "error_kind": kind, "response_id": None,
    }])
    risk = find_unsafe_workspace_runs(tmp_path, [PORTO])[0]
    assert risk["error_events"] == 1 and risk["recorded_events"] == risk["http_attempts"] == 1
    assert risk["uncertain_attempts"] == 1 and risk["unknown_attempts"] == 0
    assert risk["returned_responses"] == 0


def test_error_with_response_receipt_is_known_and_completed_run_is_safe(tmp_path):
    events = [{"document_id": PORTO, "result_status": "error", "error_kind": "schema_invalid",
               "response_id": "resp-schema-received"}]
    write_ledger(tmp_path, workspace_status="COMPLETED", workspace_documents=[PORTO], events=events)
    assert find_unsafe_workspace_runs(tmp_path, [PORTO]) == []
    write_ledger(tmp_path, workspace_status="INTERRUPTED", workspace_documents=[PORTO], events=events)
    risk = find_unsafe_workspace_runs(tmp_path, [PORTO])[0]
    assert risk["error_events"] == risk["returned_responses"] == 1
    assert risk["uncertain_attempts"] == risk["unknown_attempts"] == 0
