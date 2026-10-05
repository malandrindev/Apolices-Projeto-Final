"""One-shot E0.1: exactly one neutral Terra and one neutral Sol request.

Default CLI is offline plan-only. An independent persistent claim/manifest
prevents replay, including after failure. E0 evidence is never modified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_complimentary_models import (
    AlreadyAttempted, INPUT, MAX_OUTPUT_TOKENS, PENDING_COST,
    _atomic_write, _claim, _empty_record, _error, _real_client,
    _response_record, _value, utc_now,
)
from src.llm.resilience import response_metadata

MODELS = ("gpt-5.6-terra", "gpt-5.6-sol")
RETURNED_MODEL_PATTERN = re.compile(
    r"gpt-5\.6-(?:terra|sol)(?:-\d{4}-\d{2}-\d{2})?", re.ASCII)
MANIFEST = ROOT / "data" / "processed" / "complimentary_model_test_e01.json"


def request_parameters(model: str) -> dict[str, Any]:
    if model not in MODELS:
        raise ValueError("Model outside the fixed E0.1 allowlist.")
    return {"model": model, "input": INPUT, "store": False,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "reasoning": {"effort": "none"}}


def run_validation(*, client_factory: Callable[[], Any],
                   manifest_path: Path = MANIFEST,
                   clock: Callable[[], str] = utc_now,
                   timer: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    path = Path(manifest_path)
    _claim(path)
    manifest = {
        "schema_version": 1, "experiment": "E01_TERRA_SOL_CONTROLLED_VALIDATION",
        "created_at_utc": clock(), "finished_at_utc": None, "run_status": "running",
        "request_limit": 2, "attempts_reserved": 0, "sdk_max_retries": 0,
        "endpoint": "/v1/responses", "input": INPUT, "store": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS, "reasoning_effort": "none",
        "dashboard_reconciliation": PENDING_COST,
        "requests": [_empty_record(model) for model in MODELS],
    }
    _atomic_write(path, manifest)
    try:
        client = client_factory()
    except Exception as error:
        manifest.update(run_status="initialization_failed",
                        initialization_error=_error(error), finished_at_utc=clock())
        _atomic_write(path, manifest)
        return manifest

    for record in manifest["requests"]:
        record.update(timestamp=clock(), attempted=True, status="attempted")
        manifest["attempts_reserved"] += 1
        _atomic_write(path, manifest)  # Durable reservation BEFORE HTTP.
        started = timer()
        try:
            response = client.responses.create(**request_parameters(record["model_requested"]))
        except Exception as error:
            record.update(status="error", error=_error(error))
            record["safe_request_id"] = response_metadata({
                "request_id": getattr(error, "request_id", None)}).get("request_id")
        else:
            safe = _response_record(response)
            returned = _value(response, "model")
            safe["model_returned"] = returned if (
                isinstance(returned, str) and len(returned) <= 96
                and RETURNED_MODEL_PATTERN.fullmatch(returned)) else None
            record.update(safe)
        record["duration"] = round(max(0.0, timer() - started), 6)
        _atomic_write(path, manifest)
    manifest.update(run_status="completed", finished_at_utc=clock())
    _atomic_write(path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "PLAN_ONLY", "requests_max": 2,
                          "sdk_max_retries": 0,
                          "requests": [request_parameters(model) for model in MODELS]}))
        return 0
    try:
        manifest = run_validation(client_factory=_real_client)
    except AlreadyAttempted:
        print(json.dumps({"status": "BLOCKED_ALREADY_ATTEMPTED", "requests_sent": 0}))
        return 2
    except Exception:
        print(json.dumps({"status": "STOPPED_SAFE",
                          "action": "Inspect persistent E0.1 evidence; never rerun automatically."}))
        return 1
    print(json.dumps({"status": manifest["run_status"],
                      "attempts_reserved": manifest["attempts_reserved"],
                      "dashboard_reconciliation": PENDING_COST,
                      "results": [{"model": row["model_requested"], "status": row["status"],
                                   "ok_received": row["ok_received"]}
                                  for row in manifest["requests"]]}))
    return 0 if manifest["run_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
