"""Read-only replay guard for unfinished workspace provider-attempt ledgers."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


HISTORY_UNAVAILABLE_MESSAGE = (
    "Não foi possível verificar o histórico de execução com segurança. "
    "Use o exemplo de demonstração; nenhuma nova chamada de IA foi iniciada."
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
_SUFFIX = "-attempt-ledger.json"


def find_unsafe_workspace_runs(
    processed_dir: str | Path, document_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Return safe metadata for previous attempts that must not be replayed.

    Completed runs are safe only when every consumed attempt has a terminal
    record. Legacy ledgers derive document or ordered-pair hashes from events. No payload,
    response text, cache or ledger is changed, and no provider is constructed.
    """
    selected = set(document_ids)
    if not selected:
        return []
    if any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in selected):
        raise ValueError(HISTORY_UNAVAILABLE_MESSAGE)
    # ComparisonAgent historically records the SHA of an ordered pair, rather
    # than either document SHA. Check both orientations for every selected pair.
    selected_identifiers = selected | {
        hashlib.sha256((left + ":" + right).encode("utf-8")).hexdigest()
        for left in selected for right in selected if left != right
    }
    directory = Path(processed_dir) / "workspace_usage"
    try:
        paths = sorted(path for path in directory.iterdir() if path.name.endswith(_SUFFIX))
    except FileNotFoundError:
        return []
    except OSError:
        raise ValueError(HISTORY_UNAVAILABLE_MESSAGE) from None

    unsafe = []
    for path in paths:
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(ledger, dict):
                raise ValueError
            filename_id = path.name.removesuffix(_SUFFIX)
            run_id = ledger.get("run_id", filename_id)
            if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) or run_id != filename_id:
                raise ValueError
            status = ledger.get("workspace_status", "LEGACY")
            if status not in {"LEGACY", "ACTIVE", "COMPLETED", "INTERRUPTED"}:
                raise ValueError
            events = ledger.get("events")
            if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
                raise ValueError
            attempts = ledger.get("http_attempts", len(events))
            if type(attempts) is not int or attempts < len(events) or attempts < 0:
                raise ValueError
            known_documents = set()
            if "workspace_documents" in ledger:
                documents = ledger["workspace_documents"]
                if not isinstance(documents, list) or any(
                    not isinstance(value, str) or not _SHA256.fullmatch(value) for value in documents
                ):
                    raise ValueError
                known_documents.update(documents)
            completed = errors = returned = uncertain = 0
            for event in events:
                source_id = event.get("document_id")
                if isinstance(source_id, str) and _SHA256.fullmatch(source_id):
                    known_documents.add(source_id)
                event_status = event.get("result_status")
                if event_status is not None and not isinstance(event_status, str):
                    raise ValueError
                terminal = (event_status or "").lower()
                completed += terminal == "completed"
                errors += terminal == "error"
                has_response = bool(event.get("response_id"))
                uncertain += (terminal not in {"completed", "error"}
                              or (terminal == "error" and not has_response))
                returned += has_response
            unknown = attempts - len(events)
            uncertain += unknown
            # Missing source attribution cannot justify sending the same work again.
            unattributed = not known_documents
            involved = bool(known_documents & selected_identifiers) or unattributed
            if involved and attempts and (status != "COMPLETED" or uncertain):
                unsafe.append({
                    "run_id": run_id,
                    "workspace_status": status,
                    "document_ids": sorted(known_documents),
                    "unattributed_documents": unattributed,
                    "http_attempts": attempts,
                    "recorded_events": len(events),
                    "returned_responses": returned,
                    "completed_events": completed,
                    "error_events": errors,
                    "uncertain_attempts": uncertain,
                    "unknown_attempts": unknown,
                })
        except (OSError, UnicodeError, ValueError, TypeError):
            raise ValueError(HISTORY_UNAVAILABLE_MESSAGE) from None
    return unsafe
