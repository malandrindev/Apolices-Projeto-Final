"""Pure, conservative summaries of recorded provider usage; never performs inference."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

BILLING_NOTE = (
    "O custo efetivo é determinado pelo provedor da API.\n"
    "Os valores locais representam uso técnico e, quando exibido,\n"
    "custo contrafactual em tarifa padrão."
)
TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "uncached_input_tokens",
              "output_tokens", "total_tokens")


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _number(value: Any) -> float | None:
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def load_standard_rates(path: Path) -> dict[str, Any]:
    """Read the dated local reference without refreshing prices or contacting a provider."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value.get("models", {}) if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _event_key(event: Mapping[str, Any], run_id: str, index: int) -> str:
    for key in ("response_id", "request_id", "reservation_id"):
        if event.get(key):
            return key + ":" + str(event[key])
    if event.get("timestamp") and event.get("logical_step_id"):
        return "attempt:" + json.dumps([run_id, event.get("timestamp"), event.get("document_id"),
            event.get("logical_step_id"), event.get("requested_model", event.get("model")),
            event.get("fallback_level", 0)], sort_keys=True)
    # Events without a durable identity are counted once in their original run;
    # positional IDs never deduplicate two genuinely separate unknown attempts.
    return f"{run_id}:event:{index}"


def _canonical(event: Mapping[str, Any]) -> dict[str, Any]:
    inp = _count(event.get("input_tokens"))
    cached = _count(event.get("cached_input_tokens", event.get("cached_tokens")))
    out = _count(event.get("output_tokens"))
    total = _count(event.get("total_tokens"))
    if cached is not None and inp is not None and cached > inp:
        cached = None
    return {
        "model": str(event.get("requested_model") or event.get("model") or "não informado"),
        "input_tokens": inp, "cached_input_tokens": cached,
        "uncached_input_tokens": inp - cached if inp is not None and cached is not None else None,
        "output_tokens": out, "total_tokens": total,
        "latency_ms": _number(event.get("latency_ms", event.get("duration_ms"))),
        "technical_fallback": bool(event.get("fallback_level", 0)),
        "semantic_escalation": bool(event.get("semantic_promotion_new",
                                            event.get("semantic_escalation", False))),
        "service_tier": event.get("service_tier", event.get("effective_service_tier")),
    }


def _totals(events: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"requests": len(events)}
    for key in TOKEN_KEYS:
        counts = [event[key] for event in events]
        result[key] = sum(counts) if all(value is not None for value in counts) else None
        result["known_" + key] = sum(value for value in counts if value is not None)
    result["unknown_usage_requests"] = sum(
        any(event[key] is None for key in ("input_tokens", "output_tokens", "total_tokens"))
        for event in events)
    result["technical_fallbacks"] = sum(event["technical_fallback"] for event in events)
    result["semantic_escalations"] = sum(event["semantic_escalation"] for event in events)
    result["sol_invocations"] = sum(event["model"].startswith("gpt-5.6-sol") for event in events)
    return result


def summarize_run(run_id: str, label: str, events: Iterable[Mapping[str, Any]], *,
                  rates: Mapping[str, Any] | None = None,
                  retrieval: Mapping[str, Any] | None = None,
                  duration_seconds: float | None = None,
                  processing_tier_evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Unknown usage stays unknown; provider service tier never implies a billing incentive."""
    unique: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        if isinstance(event, Mapping):
            unique[_event_key(event, run_id, index)] = _canonical(event)
    rows = list(unique.values())
    result = {"run_id": run_id, "label": label, **_totals(rows)}
    models = sorted({event["model"] for event in rows})
    result["requests_by_model"] = dict(Counter(event["model"] for event in rows))
    result["tokens_by_model"] = {
        model: _totals([event for event in rows if event["model"] == model]) for model in models
    }
    latencies = [event["latency_ms"] for event in rows]
    known_latencies = [value for value in latencies if value is not None]
    result["llm_runtime_seconds"] = sum(known_latencies) / 1000 if len(known_latencies) == len(rows) else None
    result["median_latency_seconds"] = statistics.median(known_latencies) / 1000 if known_latencies else None
    result["p95_latency_seconds"] = sorted(known_latencies)[math.ceil(len(known_latencies) * .95) - 1] / 1000 if known_latencies else None
    result["duration_seconds"] = _number(duration_seconds)
    result["api_service_tiers"] = sorted({str(event["service_tier"]) for event in rows if event["service_tier"]})
    diagnostics = retrieval or {}
    full = _count(diagnostics.get("full_local_chunks", diagnostics.get("full_corpus_chunks")))
    candidates = _count(diagnostics.get("unique_candidate_chunks", diagnostics.get("candidate_chunks")))
    result.update(full_corpus_chunks=full, candidate_chunks=candidates,
        context_reduction_percent=(100 * (1 - candidates / full)
                                   if full and candidates is not None and candidates <= full else None))
    # Candidate coverage is local retrieval breadth, not the sum of payload chunks.
    result["candidate_scope"] = "unique_local_retrieval_candidates"
    cost = 0.0
    for event in rows:
        rate = (rates or {}).get(event["model"], {})
        values = [event[key] for key in ("uncached_input_tokens", "cached_input_tokens", "output_tokens")]
        prices = [_number(rate.get(key)) for key in ("input", "cached_input", "output")]
        if any(value is None for value in values + prices):
            cost = None
            break
        cost += sum(value * price for value, price in zip(values, prices)) / 1_000_000
    result["standard_rate_counterfactual_usd"] = round(cost, 8) if cost is not None else None
    result["effective_billing"] = "PENDING_DASHBOARD_RECONCILIATION"
    evidence = processing_tier_evidence or {}
    confirmed = (evidence.get("status") == "CONFIRMED"
                 and run_id in evidence.get("run_ids", [])
                 and isinstance(evidence.get("source"), str) and bool(evidence["source"].strip())
                 and isinstance(evidence.get("label"), str))
    result["processing_tier"] = evidence["label"] if confirmed else "não reconciliado para este run"
    result["processing_tier_evidence_source"] = evidence.get("source") if confirmed else None
    # IDs are kept for exact session deduplication, not displayed as provider payloads.
    result["_usage_events"] = unique
    return result


def aggregate_session(runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Rerenders/reloads replace the same run; shared response IDs count once globally."""
    unique_runs = {str(run["run_id"]): run for run in runs}
    unique_events: dict[str, dict[str, Any]] = {}
    for run in unique_runs.values():
        unique_events.update(run.get("_usage_events", {}))
    rows = list(unique_events.values())
    return {"label": "SESSION TOTAL", "run_count": len(unique_runs), **_totals(rows),
            "requests_by_model": dict(Counter(event["model"] for event in rows)),
            "tokens_by_model": {model: _totals([event for event in rows if event["model"] == model])
                               for model in sorted({event["model"] for event in rows})}}


def save_usage_runs(directory: Path, runs: Iterable[Mapping[str, Any]]) -> None:
    """Persist usage metadata only; no prompts, API keys, or actual billing assertions."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for run in runs:
        safe = dict(run)
        identity = hashlib.sha256(str(run["run_id"]).encode()).hexdigest()
        path = directory / (identity + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
