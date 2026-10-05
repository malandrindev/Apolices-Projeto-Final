"""One-shot Berkley E2 pilot: default is a local dry run, with no provider factory.

Only --execute performs the already-authorized single-document pilot. A durable
claim and manifest prohibit automatic replay after success, failure or interruption.
Golden/manual references are independent review controls, never model inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import ExtractionOptimizationSettings, ExtractionRouting, IngestionSettings
from src.retrieval.local import CRITICAL_FIELDS, FIELD_GROUPS, LocalRetrievalIndex
from src.schemas.policy import NOT_FOUND, PolicyExtraction

DOCUMENT_ID = "berkley_do_202512"
DOCUMENT_SHA256 = "038683e096c2ae0378df25ef18d147493f72627399d1530310af14023df8fea8"
GOLDEN_SHA256 = "673af23d838e61e267bafa44afd20b6fb89786688dbc5f64b0ebdc5ef143206f"
EXPECTED_PAGES = 83
EXPECTED_CHUNKS = 480
MAX_HTTP_ATTEMPTS = 50
FALLBACK_RESERVE = 35
VERIFIER_RESERVE = 8
MAX_OUTPUT_TOKENS = 4500


class PilotError(RuntimeError):
    """Safe local error; provider bodies and secrets never reach stdout."""


@dataclass
class PilotCorpus:
    ingested: Any
    document: Any
    clauses: list[Any]
    metadata: dict[str, Any]
    golden_items: list[dict[str, Any]]
    references: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    """Flush a complete JSON document before atomic replacement in its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            name = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        name = None
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def output_path(root: Path, filename: str) -> Path:
    directory = (root / "data" / "processed").resolve()
    path = (directory / filename).resolve()
    if path.parent != directory or not filename.startswith("berkley_pilot_"):
        raise PilotError("Pilot output must remain in data/processed.")
    return path


class PilotStore:
    """Persistent one-shot claim; reserving an attempt survives process interruption."""

    def __init__(self, root: Path = ROOT) -> None:
        self.root = root.resolve()
        self.claim_path = output_path(root, "berkley_pilot_claim.json")
        self.manifest_path = output_path(root, "berkley_pilot_manifest.json")
        self.result_path = output_path(root, "berkley_pilot_result.json")
        self.telemetry_path = output_path(root, "berkley_pilot_telemetry.json")
        self.state: dict[str, Any] = {}
        self.active = False

    def check_available(self) -> None:
        if any(path.exists() for path in (self.claim_path, self.manifest_path,
                                          self.result_path, self.telemetry_path)):
            raise PilotError("Existing pilot claim/manifest/result blocks replay; new authorization is required.")

    def start(self, plan: dict[str, Any]) -> None:
        validate_plan(plan)
        self.check_available()
        claim = {"run_id": uuid.uuid4().hex, "claimed_at": utc_now(),
                 "document_id": DOCUMENT_ID, "document_sha256": DOCUMENT_SHA256,
                 "plan_sha256": json_hash(plan), "references": plan["references"],
                 "max_http_attempts": MAX_HTTP_ATTEMPTS,
                 "replay_policy": "NO_AUTOMATIC_REPLAY"}
        self.claim_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.claim_path.open("x", encoding="utf-8") as stream:
                json.dump(claim, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise PilotError("Concurrent pilot claim blocks replay.") from None
        self.state = {**claim, "status": "CLAIMED", "updated_at": utc_now(),
                      "request_control": {"max_http_attempts": MAX_HTTP_ATTEMPTS,
                                          "http_attempts": 0, "remaining": MAX_HTTP_ATTEMPTS,
                                          "blocked_reason": "", "events": []},
                      "field_review_status": "PENDING_MANUAL_REVIEW",
                      "effective_billing": "PENDING_DASHBOARD_RECONCILIATION"}
        # If this persistence fails, the exclusive claim intentionally remains.
        atomic_json(self.manifest_path, self.state)
        self.active = True

    def persist_control(self, snapshot: dict[str, Any]) -> None:
        if not self.active:
            raise PilotError("Pilot was not durably claimed.")
        attempts = snapshot.get("http_attempts")
        if (not isinstance(attempts, int) or isinstance(attempts, bool)
                or not 0 <= attempts <= MAX_HTTP_ATTEMPTS
                or attempts < self.state["request_control"]["http_attempts"]):
            raise PilotError("Invalid or regressive HTTP reservation.")
        if snapshot.get("max_http_attempts") != MAX_HTTP_ATTEMPTS:
            raise PilotError("HTTP budget cannot be changed during the pilot.")
        self.state["request_control"] = snapshot
        self.state["status"] = "RUNNING"
        self.state["updated_at"] = utc_now()
        # Manifest is authoritative and is persisted before the secondary view.
        atomic_json(self.manifest_path, self.state)
        atomic_json(self.telemetry_path, {"run_id": self.state["run_id"], **snapshot})

    def persist_provider_return(self, event: dict[str, Any]) -> None:
        """Preserve safe response metadata before decoding or semantic validation."""
        if not self.active or not self.state["request_control"].get("events"):
            raise PilotError("Provider returned without a durable pilot reservation.")
        control = self.state["request_control"]
        latest = control["events"][-1]
        allowed = ("provider", "model", "actual_model", "stage", "status", "error_kind",
                   "request_id", "response_id", "effective_service_tier", "input_tokens",
                   "output_tokens", "cached_tokens", "total_tokens", "duration_ms")
        row = {"timestamp": utc_now(), "http_attempt": control["http_attempts"],
               "logical_step_id": latest["logical_step_id"],
               **{key: event.get(key) for key in allowed}}
        self.state.setdefault("provider_returns", []).append(row)
        self.state["updated_at"] = utc_now()
        atomic_json(self.manifest_path, self.state)

    def persist_partial(self, result: Any) -> None:
        value = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        if not isinstance(value, dict):
            raise PilotError("Partial extraction must be structured.")
        self.state["partial_extraction"] = value
        self.state["updated_at"] = utc_now()
        atomic_json(self.manifest_path, self.state)
        atomic_json(self.result_path, {
            "document_id": DOCUMENT_ID, "document_sha256": DOCUMENT_SHA256,
            "complete": False, "review_status": "PENDING_MANUAL_REVIEW",
            "extraction": value,
        })

    def finish(self, status: str, *, result: dict[str, Any] | None = None,
               metrics: dict[str, Any] | None = None, error_kind: str = "") -> None:
        self.state.update(status=status, finished_at=utc_now(), updated_at=utc_now(),
                          error_kind=error_kind, metrics=metrics or {})
        if result is not None:
            self.state["result_summary"] = {"complete": result.get("complete", False),
                                            "review_status": result.get("review_status")}
            atomic_json(self.result_path, result)
        atomic_json(self.manifest_path, self.state)
        atomic_json(self.telemetry_path, {
            "run_id": self.state["run_id"], **self.state["request_control"],
            "metrics": metrics or {}, "status": status,
        })


def forensic_recovery(root: Path = ROOT) -> dict[str, Any]:
    """Read persistent evidence only; never infer transmission from chat output."""
    store = PilotStore(root)
    paths = {"claim": store.claim_path, "manifest": store.manifest_path,
             "result": store.result_path, "telemetry": store.telemetry_path}
    present = {name: path.exists() for name, path in paths.items()}
    plan_path = output_path(root, "berkley_pilot_plan.json")
    report = {"runtime_files": present, "replay_blocked": any(present.values()),
              "planned_initial_calls": None, "reserved_attempts_found": 0,
              "sent_attempts_found": 0, "completed_attempts_found": 0,
              "response_ids_found": 0, "results_persisted_found": 0, "structured_fields_persisted": 0,
              "uncertain_attempts_found": 0, "returned_unmerged_attempts_found": 0,
              "remaining_budget": MAX_HTTP_ATTEMPTS, "duplicates_prevented": 0,
              "status": "NOT_STARTED"}
    try:
        if plan_path.exists():
            report["planned_initial_calls"] = json.loads(plan_path.read_text(encoding="utf-8"))["planned_initial_calls"]
        loaded = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in paths.items() if present[name]}
        if not loaded:
            return report
        if any(not isinstance(value, dict) for value in loaded.values()):
            raise PilotError("Persistent pilot files must contain JSON objects.")
        manifest = loaded.get("manifest")
        if not present["claim"] or manifest is None:
            raise PilotError("Orphan persistent pilot state cannot be safely resumed.")
        if manifest.get("document_sha256") != DOCUMENT_SHA256:
            raise PilotError("Persistent provenance mismatch.")
        control = manifest["request_control"]
        events = control["events"]
        reserved = control["http_attempts"]
        if type(reserved) is not int or not 0 <= reserved <= MAX_HTTP_ATTEMPTS or reserved != len(events):
            raise PilotError("Persistent attempt ledger is inconsistent.")
        returns = manifest.get("provider_returns", [])
        if not isinstance(returns, list) or any(not isinstance(row, dict) or type(row.get("http_attempt")) is not int or not 1 <= row["http_attempt"] <= reserved for row in returns):
            raise PilotError("Provider receipt exceeds or disagrees with the durable ledger.")
        if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
            raise PilotError("Attempt ledger must contain structured events.")
        receipts = {row["http_attempt"] for row in returns}
        response_ids = {row.get("response_id") for row in events + manifest.get("provider_returns", [])
                        if row.get("response_id")}
        completed = sum(row.get("result_status") == "completed" for row in events)
        sent = {number for number, row in enumerate(events, 1)
                if number in receipts or row.get("response_id") or row.get("request_id")}
        uncertain = [number for number, row in enumerate(events, 1)
                     if number not in sent and row.get("result_status") in {"ATTEMPTED", "RESERVED", "error"}]
        partial = manifest.get("partial_extraction", {})
        saved_steps = {row.get("logical_step_id") for row in partial.get("retrieval_diagnostics", {}).get("batches", []) if isinstance(row, dict) and not row.get("error_kind")}
        saved_attempts = sum(row.get("result_status") == "completed" and row.get("logical_step_id") in saved_steps for row in events)
        report.update(status="EXISTING_STATE_STOPPED_FOR_REVIEW", reserved_attempts_found=reserved,
                      sent_attempts_found=len(sent), completed_attempts_found=completed,
                      response_ids_found=len(response_ids),
                      results_persisted_found=saved_attempts,
                      structured_fields_persisted=len(partial.get("field_status", {})),
                      uncertain_attempts_found=len(uncertain),
                      returned_unmerged_attempts_found=sum(number in sent and row.get("result_status") == "ATTEMPTED"
                                                          for number, row in enumerate(events, 1)),
                      remaining_budget=MAX_HTTP_ATTEMPTS-reserved,
                      uncertain_attempt_numbers=uncertain,
                      uncertainty_status="UNCERTAIN_PREVIOUS_ATTEMPT" if uncertain else None,
                      previous_attempts_protected_from_replay=reserved)
    except (ValueError, KeyError, TypeError, OSError, PilotError):
        report.update(status="INCONSISTENT_BLOCKED", replay_blocked=True)
        for key in ("reserved_attempts_found", "sent_attempts_found", "completed_attempts_found",
                    "response_ids_found", "results_persisted_found", "uncertain_attempts_found", "remaining_budget"):
            report[key] = None
    return report


def load_corpus(root: Path = ROOT, *, ingestion_settings: IngestionSettings | None = None
                ) -> PilotCorpus:
    """Read only the authorized local PDF; there is no downloader/provider path."""
    from scripts.plan_public_extraction import local_path, validate_golden
    from src.agents.segmentation import SegmentationAgent
    from src.config import get_ingestion_settings
    from src.pipeline import process_document

    manifest_path = root / "data/processed/public_validation_preflight/candidates.json"
    golden_path = root / "tests/fixtures/public_retrieval_golden.json"
    manual_path = root / "data/processed/berkley_pilot_manual_reference.json"
    if not all(path.is_file() for path in (manifest_path, golden_path, manual_path)):
        raise PilotError("Local source manifest and both pre-request references are required.")
    if file_hash(golden_path) != GOLDEN_SHA256:
        raise PilotError("The independent E1 golden fixture changed; do not execute.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    golden = json.loads(golden_path.read_text(encoding="utf-8-sig"))
    manual = json.loads(manual_path.read_text(encoding="utf-8-sig"))
    records = [item for item in manifest["documents"] if item.get("id") == DOCUMENT_ID]
    if len(records) != 1 or DOCUMENT_ID not in golden["documents"]:
        raise PilotError("Exactly one Berkley source record is required.")
    metadata = dict(records[0])
    reference = golden["documents"][DOCUMENT_ID]
    if (metadata.get("sha256") != DOCUMENT_SHA256
            or reference.get("sha256") != DOCUMENT_SHA256
            or metadata.get("pages") != EXPECTED_PAGES
            or reference.get("pages") != EXPECTED_PAGES
            or metadata.get("chunk_count") != EXPECTED_CHUNKS):
        raise PilotError("Authorized Berkley source metadata changed.")
    source = local_path(root, metadata["local_path"])
    if source != local_path(root, reference["local_path"]) or file_hash(source) != DOCUMENT_SHA256:
        raise PilotError("Authorized Berkley PDF hash/path changed.")
    if (manual.get("document_id") != DOCUMENT_ID
            or manual.get("created_before_real_requests") is not True
            or len(manual.get("items", [])) != 17):
        raise PilotError("The independent pre-request manual reference is invalid.")
    effective = ingestion_settings or get_ingestion_settings()
    effective = replace(effective, processed_dir=root / "data/processed")
    ingested, document = process_document(source, settings=effective)
    clauses = SegmentationAgent(gateway=None).segment(document)
    if (document.sha256 != DOCUMENT_SHA256 or len(document.pages) != EXPECTED_PAGES
            or len(clauses) != EXPECTED_CHUNKS):
        raise PilotError("The complete Berkley OCR/segmentation corpus changed.")
    items = [item for item in golden["items"] if item["document_id"] == DOCUMENT_ID]
    validate_golden(document, items)
    if len(items) != 17 or sum(item["field"] in CRITICAL_FIELDS for item in items) != 11:
        raise PilotError("The 17 reference anchors / 11 critical anchors changed.")
    references = {
        "golden_fixture": {"path": str(golden_path.relative_to(root)),
                           "sha256": file_hash(golden_path), "anchors": 20,
                           "berkley_anchors": 17, "berkley_critical_anchors": 11},
        "manual_reference": {"path": str(manual_path.relative_to(root)),
                             "sha256": file_hash(manual_path), "anchors": 17,
                             "created_before_real_requests": True},
    }
    return PilotCorpus(ingested, document, clauses, metadata, items, references)


def policy_routing(policy: Any) -> ExtractionRouting:
    return ExtractionRouting(
        policy.role("extraction").primary, policy.role("interpretation").primary,
        policy.role("verifier").primary, policy.role("comparison").primary,
    )


def prepare_plan(corpus: PilotCorpus, policy: Any, *,
                 optimization: ExtractionOptimizationSettings | None = None) -> dict[str, Any]:
    """Use the existing local planner only; references are never batch payloads."""
    from scripts.plan_public_extraction import batch_totals, candidate_recall, measure_batch
    from src.agents.grouped_extraction import OBJECTIVE_FIELDS, build_semantic_batches
    controls = replace(optimization or ExtractionOptimizationSettings(), strategy="optimized")
    routing = policy_routing(policy)
    index = LocalRetrievalIndex.build(corpus.document, corpus.clauses)
    plans = {group: index.plan(group, per_field_limit=controls.initial_top_n)
             for group in FIELD_GROUPS}
    batches = []
    for group, plan in plans.items():
        for batch in build_semantic_batches(plan, controls.batch_chars):
            role = ("optimized_extraction" if set(batch.fields) <= OBJECTIVE_FIELDS
                    else "optimized_interpretation")
            measured = measure_batch(batch, role, routing)
            measured["primary_model"] = measured["model"]
            logical_role = "extraction" if role == "optimized_extraction" else "interpretation"
            route = policy.role(logical_role)
            measured["technical_fallbacks"] = list(route.technical_fallbacks)
            measured["semantic_escalations"] = list(route.escalations)
            batches.append(measured)
    totals = batch_totals(batches)
    initial = totals["logical_calls"]
    remaining = MAX_HTTP_ATTEMPTS - initial
    if remaining < 0:
        raise PilotError("Initial semantic batches exceed the hard 50 HTTP-attempt budget.")
    return {
        "schema_version": 1, "mode": "OFFLINE_DRY_RUN", "created_at": utc_now(),
        "document_id": DOCUMENT_ID, "document_sha256": DOCUMENT_SHA256,
        "source_url": corpus.metadata["source_url"], "origin": corpus.metadata["origin"],
        "access_date": corpus.metadata.get("access_date"),
        "document_type": corpus.metadata.get("document_type"),
        "pages": len(corpus.document.pages), "full_local_chunks": len(corpus.clauses),
        "full_local_corpus_retained": True,
        "local_text_chars": sum(len(page.text) for page in corpus.document.pages),
        "ocr_cache_hit": bool(corpus.document.cache_hit),
        "initial_candidates": len({c.chunk_id for p in plans.values() for c in p.candidates}),
        "groups": [plan.summary() for plan in plans.values()],
        "optimization": asdict(controls), "routing": asdict(routing),
        "routing_policy": policy.routing_signature,
        "initial": {"totals": totals, "batches": batches},
        "MAX_HTTP_ATTEMPTS": MAX_HTTP_ATTEMPTS, "planned_initial_calls": initial,
        "reserved_fallback_budget": min(FALLBACK_RESERVE, remaining),
        "reserved_verifier_budget": min(VERIFIER_RESERVE, max(0, remaining - FALLBACK_RESERVE)),
        "shared_remaining_budget": remaining,
        "reservation_note": "Informative reserves share the same hard global budget; not separate quotas or a completion guarantee.",
        "timeout_seconds": 60, "same_model_retries": 0, "sdk_retries": 0,
        "references": corpus.references,
        "independent_initial_retrieval_check": candidate_recall(plans, corpus.golden_items),
        "reference_note": "Golden anchors/manual meanings are independent local review controls, never LLM inputs or runtime selectors.",
        "genai_calls": 0, "providers_constructed": 0, "comparison_calls": 0,
        "other_documents_processed": 0, "review_status": "PENDING_MANUAL_REVIEW",
        "effective_billing": "PENDING_DASHBOARD_RECONCILIATION",
        "limitations": ["Published general conditions do not establish contracted coverage/client values.",
                        "Adaptive fallback/verifier requests depend on real evidence/status and must stop at 50.",
                        "Token estimates are planning assumptions, not API usage or measured accuracy."]}


def validate_plan(plan: dict[str, Any]) -> None:
    if (plan.get("document_id") != DOCUMENT_ID or plan.get("document_sha256") != DOCUMENT_SHA256
            or plan.get("pages") != EXPECTED_PAGES or plan.get("full_local_chunks") != EXPECTED_CHUNKS
            or plan.get("MAX_HTTP_ATTEMPTS") != MAX_HTTP_ATTEMPTS
            or plan.get("full_local_corpus_retained") is not True
            or plan.get("comparison_calls") != 0 or plan.get("other_documents_processed") != 0):
        raise PilotError("Invalid single-document Berkley pilot plan.")
    initial = plan.get("planned_initial_calls")
    if (not isinstance(initial, int) or isinstance(initial, bool) or initial < 0
            or initial > MAX_HTTP_ATTEMPTS or initial != len(plan["initial"]["batches"])):
        raise PilotError("Initial calls exceed or disagree with the semantic batch plan.")
    references = plan.get("references", {})
    if (references.get("golden_fixture", {}).get("sha256") != GOLDEN_SHA256
            or not references.get("manual_reference", {}).get("created_before_real_requests")):
        raise PilotError("Pre-request reference hashes are required.")

APPROVED_MODELS = frozenset({
    "gpt-5.6-luna", "gpt-5.4-mini-2026-03-17", "gpt-5.6-terra", "gpt-5.6-sol",
    "gpt-5.4-2026-03-05", "gpt-5.2-2025-12-11", "gpt-4.1-mini-2025-04-14",
})


def validate_policy(policy: Any) -> None:
    for name in ("extraction", "interpretation", "verifier", "comparison", "auxiliary"):
        route = policy.role(name)
        models = (route.primary,) + tuple(route.technical_fallbacks) + tuple(route.escalations)
        if any(model not in APPROVED_MODELS for model in models):
            raise PilotError("A configured model is outside the seven explicitly authorized pilot models.")


def verify_references(plan: dict[str, Any], root: Path) -> None:
    for reference in plan["references"].values():
        path = (root / reference["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or file_hash(path) != reference["sha256"]:
            raise PilotError("An independent reference changed after the offline plan.")


def build_gateway(*, settings: Any, policy: Any, store: PilotStore) -> Any:
    """Explicit official API endpoint; this function is never called by a dry run."""
    import logging
    from openai import OpenAI
    from src.llm.model_routing import RequestControl, RoutedGateway
    from src.llm.openai_client import OpenAIProvider
    for name in ("openai", "openai._base_client", "httpx", "httpcore"):
        logging.getLogger(name).disabled = True
    effective = replace(settings, llm_provider="openai", max_retries=1,
                        max_tokens=MAX_OUTPUT_TOKENS, timeout_seconds=60,
                        max_retry_wait_seconds=0)
    client = OpenAI(api_key=effective.openai_api_key,
                    base_url="https://api.openai.com/v1", timeout=60, max_retries=0)
    raw = OpenAIProvider(effective, client=client, local_json_repair=True, response_callback=store.persist_provider_return)
    last_printed: dict[int, tuple[Any, ...]] = {}

    def persist(snapshot: dict[str, Any]) -> None:
        store.persist_control(snapshot)
        for number, event in enumerate(snapshot.get("events", []), 1):
            signature = (event.get("result_status"), event.get("evidence_validation_status"))
            if last_printed.get(number) == signature:
                continue
            last_printed[number] = signature
            print(json.dumps({"http_attempt": number,
                              "max_http_attempts": MAX_HTTP_ATTEMPTS,
                              "field_group": event.get("field_group"),
                              "requested_model": event.get("requested_model"),
                              "actual_model": event.get("actual_model"),
                              "result_status": event.get("result_status"),
                              "input_tokens": event.get("input_tokens"),
                              "output_tokens": event.get("output_tokens"),
                              "latency_ms": event.get("latency_ms")}, ensure_ascii=True),
                  flush=True)

    control = RequestControl(max_http_attempts=MAX_HTTP_ATTEMPTS, persist_callback=persist)
    return RoutedGateway(raw, policy=policy, control=control)


def build_extractor(*, gateway: Any, optimization: ExtractionOptimizationSettings,
                    routing: ExtractionRouting, processed_dir: Path,
                    snapshot_callback: Callable) -> Any:
    from src.agents.grouped_extraction import GroupedExtractionAgent
    return GroupedExtractionAgent(gateway=gateway, optimization=optimization, routing=routing,
                                  processed_dir=processed_dir, snapshot_callback=snapshot_callback)


def local_evidence_review(corpus: PilotCorpus, extraction: Any) -> dict[str, Any]:
    """Literal page/quote checks only; semantic accuracy remains manual and pending."""
    from scripts.plan_public_extraction import normalise_whitespace
    originals = {page.page_number: normalise_whitespace(page.text)
                 for page in corpus.document.pages}
    fields = {}
    invalid_pages = invalid_quotes = valid_citations = 0
    statuses = {}
    for name in PolicyExtraction.model_fields:
        evidence = getattr(extraction.policy, name)
        raw_status = extraction.field_status.get(name)
        status = getattr(raw_status, "value", raw_status) or "TECHNICAL_UNAVAILABLE"
        statuses[name] = status
        located = evidence.valor != NOT_FOUND
        page_valid = located and evidence.pagina in originals
        quote = normalise_whitespace(evidence.trecho_origem)
        quote_valid = bool(page_valid and quote and quote != NOT_FOUND
                           and quote in originals[evidence.pagina])
        if located:
            invalid_pages += int(not page_valid)
            invalid_quotes += int(not quote_valid)
            valid_citations += int(page_valid and quote_valid)
        fields[name] = {"status": status, "page": evidence.pagina,
                        "located": located, "original_page_valid": page_valid if located else None,
                        "literal_source_quote_valid": quote_valid if located else None,
                        "meaning_review": "PENDING_MANUAL_REVIEW"}
    anchors = []
    for item in corpus.golden_items:
        check = fields[item["field"]]
        anchors.append({"id": item["id"], "field": item["field"],
                        "critical": item["field"] in CRITICAL_FIELDS,
                        "expected_page": item["page"], "actual_page": check["page"],
                        "same_reference_page": check["page"] == item["page"],
                        "literal_source_quote_valid": check["literal_source_quote_valid"],
                        "field_status": check["status"],
                        "correct_value_meaning_status": "PENDING_MANUAL_REVIEW"})
    return {
        "review_status": "PENDING_MANUAL_REVIEW", "fields": fields,
        "field_status_counts": dict(Counter(statuses.values())),
        "explicit_field_states": len(statuses), "golden_anchors": anchors,
        "critical_anchors_total": sum(item["critical"] for item in anchors),
        "golden_anchors_total": len(anchors), "critical_correct": None,
        "overall_correct": None, "unsupported_found_claims": None,
        "hallucination_rate": None, "meaning_accuracy": None,
        "valid_literal_citations": valid_citations,
        "invalid_literal_citations": invalid_quotes,
        "invalid_page_references": invalid_pages,
        "note": "Literal page/source validation does not prove semantic entailment, contracting or manual golden correctness.",
    }


def _known_sum(events: list[dict[str, Any]], key: str) -> dict[str, int | None]:
    values = [event.get(key) for event in events]
    known = [value for value in values if isinstance(value, int) and not isinstance(value, bool)
             and value >= 0]
    return {"total": sum(known) if len(known) == len(values) else None,
            "known_sum": sum(known), "unknown_requests": len(values) - len(known)}


def _latency(events: list[dict[str, Any]]) -> dict[str, Any]:
    values = [event.get("latency_ms") for event in events]
    measured = sorted(value / 1000 for value in values
                      if isinstance(value, (int, float)) and not isinstance(value, bool)
                      and math.isfinite(value) and value >= 0)
    # Nearest-rank p95 is explicit and works even for a single request.
    return {"measured_requests": len(measured), "unknown_requests": len(values) - len(measured),
            "sum_seconds": sum(measured),
            "median_seconds": statistics.median(measured) if measured else None,
            "p95_seconds": measured[math.ceil(.95 * len(measured)) - 1] if measured else None,
            "p95_method": "nearest_rank"}


def counterfactual_cost(events: list[dict[str, Any]], prices: dict[str, Any] | None) -> dict[str, Any]:
    total = 0.0
    details = []
    incomplete = False
    for number, event in enumerate(events, 1):
        model = event.get("actual_model")
        rate = prices.get("models", {}).get(model) if prices and model else None
        counts = [event.get(key) for key in ("input_tokens", "cached_input_tokens", "output_tokens")]
        if (not rate or any(not isinstance(value, int) or isinstance(value, bool) or value < 0
                            for value in counts) or counts[1] > counts[0]):
            incomplete = True
            details.append({"http_attempt": number, "model": model, "usd": None,
                            "reason": "Reported usage/model or verified rate is incomplete."})
            continue
        input_tokens, cached_tokens, output_tokens = counts
        amount = ((input_tokens - cached_tokens) * rate["input"]
                  + cached_tokens * rate["cached_input"] + output_tokens * rate["output"]) / 1_000_000
        total += amount
        details.append({"http_attempt": number, "model": model, "usd": round(amount, 9)})
    return {"status": "INCOMPLETE_REPORTED_USAGE_OR_RATES" if incomplete else "STANDARD_RATE_COUNTERFACTUAL_ONLY",
            "usd": None if incomplete else round(total, 9), "known_subtotal_usd": round(total, 9),
            "currency": "USD", "rate_verified_on": prices.get("verified_on") if prices else None,
            "requests": details, "effective_billing": "PENDING_DASHBOARD_RECONCILIATION",
            "note": "Standard text-token rates and reported cache usage only; no inferred incentive, quota, taxes or dashboard charge."}


def execution_metrics(snapshot: dict[str, Any], *, wall_seconds: float,
                      prices: dict[str, Any] | None = None,
                      review: dict[str, Any] | None = None) -> dict[str, Any]:
    events = snapshot.get("events", [])
    by_model: dict[str, Any] = {}
    for model in sorted({event.get("actual_model") or "UNKNOWN" for event in events}):
        selected = [event for event in events if (event.get("actual_model") or "UNKNOWN") == model]
        by_model[model] = {"requests": len(selected), "tokens": {
            key: _known_sum(selected, key) for key in (
                "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")},
            "latency": _latency(selected)}
    latencies = _latency(events)
    fallback = sum(bool(event.get("fallback_level")) for event in events)
    escalations = sum(bool(event.get("semantic_escalation"))
                      and not event.get("fallback_level") for event in events)
    metrics = {
        "http_attempts": snapshot.get("http_attempts", len(events)),
        "primary_calls": sum(not event.get("fallback_level") and not event.get("semantic_escalation")
                             for event in events),
        "fallback_calls": fallback, "fallback_percentage": 100 * fallback / len(events) if events else 0,
        "semantic_escalations": escalations,
        "semantic_escalation_http_attempts": sum(bool(event.get("semantic_escalation")) for event in events),
        "sol_calls": sum((event.get("actual_model") or event.get("requested_model")) == "gpt-5.6-sol"
                         for event in events),
        "requests_by_requested_model": dict(Counter(event.get("requested_model") or "UNKNOWN"
                                                     for event in events)),
        "by_actual_model": by_model,
        "tokens": {key: _known_sum(events, key) for key in (
            "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")},
        "performance": {"total_runtime_seconds": wall_seconds,
                        "llm_runtime_seconds": latencies["sum_seconds"],
                        "local_processing_runtime_seconds": max(0, wall_seconds - latencies["sum_seconds"]),
                        **latencies,
                        "local_runtime_method": "Sequential wall time including local preflight minus measured routed request latencies; not CPU time.",
                        "llm_runtime_method": "Sum of actual routed request latencies, including SDK response handling/local validation."},
        "technical_failures": snapshot.get("technical_failures"),
        "evidence_validation_failed_requests": sum(event.get("evidence_validation_status") == "EVIDENCE_INVALID" for event in events),
        "evidence_validation_failed_fields": sorted({name for event in events
            if event.get("evidence_validation_status") == "EVIDENCE_INVALID"
            for name in event.get("validation_fields", [])}),
        "blocked_reason": snapshot.get("blocked_reason"),
        "fallback_telemetry": [
            {key: event.get(key) for key in ("logical_step_id", "fallback_reason", "primary_model",
                                           "requested_model", "actual_model", "result_status",
                                           "semantic_escalation", "error_kind")}
            for event in events if event.get("fallback_level") or event.get("semantic_escalation")],
        "api_returned_service_tiers": dict(Counter(event.get("service_tier") or "UNKNOWN"
                                                   for event in events)),
        "standard_rate_counterfactual": counterfactual_cost(events, prices),
        "effective_billing": "PENDING_DASHBOARD_RECONCILIATION",
        "accuracy_review_status": "PENDING_MANUAL_REVIEW", "comparison_calls": 0,
    }
    if review:
        metrics["evidence_review"] = review
        final_fields = review.get("fields", {})
        primary_found, fallback_fields, sol_fields = set(), set(), set()
        for event in events:
            names = set(event.get("fields", []))
            if event.get("fallback_level"):
                fallback_fields.update(names)
            if (event.get("actual_model") or event.get("requested_model")) == "gpt-5.6-sol":
                sol_fields.update(names)
            if (not event.get("fallback_level") and not event.get("semantic_escalation")
                    and event.get("role") != "verifier"):
                primary_found.update(name for name, status in event.get("field_statuses", {}).items()
                    if name in names and status == "FOUND"
                    and final_fields.get(name, {}).get("status") == "FOUND")
        metrics["field_resolution"] = {
            "fields_solved_at_primary": sorted(primary_found),
            "fields_requiring_technical_fallback": sorted(fallback_fields),
            "fields_sent_to_sol": sorted(sol_fields),
            "counts": {"solved_at_primary": len(primary_found),
                       "technical_fallback": len(fallback_fields), "sent_to_sol": len(sol_fields)},
            "note": "Observed validated primary outputs and routed field sets; no counterfactual claim that Sol/fallback was necessary.",
        }
    return metrics


def persist_sqlite(corpus: PilotCorpus, extraction: Any, root: Path) -> dict[str, Any]:
    from src.agents.extraction import Phase2Report
    from src.storage.sqlite_repo import SqliteRepository
    from src.storage.vector_store import ChromaVectorStore
    source = {key: corpus.metadata.get(key) for key in (
        "source_url", "origin", "access_date", "insurer", "product_name",
        "susep_process", "document_type", "version_label", "scope_note")}
    source.update(document_id=DOCUMENT_ID, document_sha256=DOCUMENT_SHA256)
    diagnostics = {**extraction.retrieval_diagnostics, "source_reference": source}
    report = Phase2Report(source_name=corpus.ingested.source_name, sha256=DOCUMENT_SHA256,
                          clause_count=len(corpus.clauses), policy=extraction.policy,
                          issues=extraction.issues, field_status=extraction.field_status,
                          retrieval_diagnostics=diagnostics)
    # Pure local splitting: no encoder, Chroma client or model is instantiated.
    chunks = ChromaVectorStore.split_clauses(corpus.clauses, document_id=DOCUMENT_SHA256,
                                            chunk_size_chars=1200, chunk_overlap_chars=150)
    SqliteRepository(root / "data/processed/policies.sqlite3").save_document(report, corpus.clauses, chunks)
    return report.model_dump(mode="json")


def execute_pilot(corpus: PilotCorpus, plan: dict[str, Any], policy: Any, *,
                  settings: Any, root: Path = ROOT,
                  gateway_factory: Callable = build_gateway,
                  extractor_factory: Callable = build_extractor,
                  sqlite_writer: Callable = persist_sqlite,
                  prices: dict[str, Any] | None = None,
                  preflight_seconds: float = 0) -> dict[str, Any]:
    """One execution only; dependencies are injectable for offline fake-provider tests."""
    validate_plan(plan)
    validate_policy(policy)
    if plan["routing_policy"] != policy.routing_signature:
        raise PilotError("Routing changed after the offline plan.")
    if (corpus.document.sha256 != DOCUMENT_SHA256
            or len(corpus.document.pages) != EXPECTED_PAGES or len(corpus.clauses) != EXPECTED_CHUNKS):
        raise PilotError("The authorized complete corpus changed after planning.")
    verify_references(plan, root)
    if (corpus.ingested.sha256 != DOCUMENT_SHA256
            or file_hash(corpus.ingested.source_path) != DOCUMENT_SHA256):
        raise PilotError("The authorized local PDF changed after planning.")
    store = PilotStore(root)
    store.start(plan)  # Claim + durable manifest precede client/provider construction.
    started = time.perf_counter() - max(0, preflight_seconds)
    gateway = None
    extractor = None
    try:
        gateway = gateway_factory(settings=settings, policy=policy, store=store)
        controls = ExtractionOptimizationSettings(**plan["optimization"])
        routing = ExtractionRouting(**plan["routing"])
        extractor = extractor_factory(
            gateway=gateway, optimization=controls, routing=routing,
            processed_dir=root / "data/processed/berkley_pilot_cache",
            snapshot_callback=store.persist_partial,
        )
        extraction = extractor.extract(corpus.document, corpus.clauses)
        store.persist_partial(extraction)
        report = sqlite_writer(corpus, extraction, root)
        review = local_evidence_review(corpus, extraction)
        snapshot = gateway.control.snapshot()
        wall = time.perf_counter() - started
        metrics = execution_metrics(snapshot, wall_seconds=wall, prices=prices, review=review)
        stop_reason = extraction.retrieval_diagnostics.get("stop_reason") or snapshot.get("blocked_reason")
        complete = not stop_reason and len(extraction.field_status) == len(PolicyExtraction.model_fields)
        result = {
            "document_id": DOCUMENT_ID, "document_sha256": DOCUMENT_SHA256,
            "source_reference": report["retrieval_diagnostics"]["source_reference"],
            "references": plan["references"], "plan_sha256": json_hash(plan),
            "complete": complete, "review_status": "PENDING_MANUAL_REVIEW",
            "report": report, "extraction": extraction.model_dump(mode="json"),
            "metrics": metrics, "stop_reason": stop_reason,
            "effective_billing": "PENDING_DASHBOARD_RECONCILIATION",
            "comparison_calls": 0, "other_documents_processed": 0,
        }
        store.finish("COMPLETED_PENDING_REVIEW" if complete else "PARTIAL_STOPPED",
                     result=result, metrics=metrics, error_kind=stop_reason or "")
        return result
    except BaseException as error:
        # Best-effort terminal persistence must not mask the original interruption.
        # Disk failure cannot remove the durable claim or enable a subsequent HTTP.
        try:
            if extractor is not None and hasattr(extractor, "result"):
                store.persist_partial(extractor.result)
        except Exception:
            pass
        snapshot = gateway.control.snapshot() if gateway is not None else store.state["request_control"]
        try:
            if gateway is not None:
                store.persist_control(snapshot)
        except Exception:
            pass
        metrics = execution_metrics(snapshot, wall_seconds=time.perf_counter() - started, prices=prices)
        kind = getattr(error, "kind", "interrupted" if isinstance(error, KeyboardInterrupt) else type(error).__name__)
        try:
            store.finish("INTERRUPTED" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "FAILED",
                         metrics=metrics, error_kind=kind)
        except Exception:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(
        "Default: offline Berkley dry run. --execute: one authorized pilot, hard50, no automatic replay."))
    parser.add_argument("--execute", action="store_true", help="execute the single Berkley pilot after valid offline plan")
    args = parser.parse_args(argv)
    try:
        from src.config import get_extraction_optimization_settings, get_settings
        from src.llm.model_routing import ModelRoutingPolicy
        recovery = forensic_recovery(ROOT)
        print(json.dumps({"mode": "FORENSIC_RECOVERY", **recovery}, ensure_ascii=True), flush=True)
        if args.execute:
            if recovery["replay_blocked"] or recovery["status"] != "NOT_STARTED":
                raise PilotError("Persistent pilot state blocks replay; review the ledger before any resume.")
            PilotStore(ROOT).check_available()
        elif recovery["replay_blocked"]:
            return 1 if recovery["status"] == "INCONSISTENT_BLOCKED" else 0
        preflight_started = time.perf_counter()
        optimization = get_extraction_optimization_settings()
        policy = ModelRoutingPolicy.from_env()
        validate_policy(policy)
        corpus = load_corpus(ROOT)
        plan = prepare_plan(corpus, policy, optimization=optimization)
        plan["local_preflight_seconds"] = time.perf_counter() - preflight_started
        validate_plan(plan)
        atomic_json(output_path(ROOT, "berkley_pilot_plan.json"), plan)
        print(json.dumps({"mode": "OFFLINE_DRY_RUN", "document_id": DOCUMENT_ID,
                          "pages": plan["pages"], "full_local_chunks": plan["full_local_chunks"],
                          "initial_candidates": plan["initial_candidates"],
                          "planned_initial_calls": plan["planned_initial_calls"],
                          "max_http_attempts": MAX_HTTP_ATTEMPTS,
                          "shared_remaining_budget": plan["shared_remaining_budget"],
                          "providers_constructed": 0, "genai_calls": 0}, ensure_ascii=True), flush=True)
        if not args.execute:
            return 0
        settings = get_settings(provider="openai", require_api_key=True)
        price_path = ROOT / "docs/OPENAI_PRICES_2026-10-04.json"
        prices = json.loads(price_path.read_text(encoding="utf-8-sig")) if price_path.is_file() else None
        result = execute_pilot(corpus, plan, policy, settings=settings, root=ROOT, prices=prices,
                               preflight_seconds=time.perf_counter() - preflight_started)
        print(json.dumps({"mode": "EXECUTED_ONE_SHOT", "document_id": DOCUMENT_ID,
                          "complete": result["complete"],
                          "http_attempts": result["metrics"]["http_attempts"],
                          "review_status": result["review_status"],
                          "effective_billing": result["effective_billing"]}, ensure_ascii=True))
        return 0 if result["complete"] else 1
    except KeyboardInterrupt:
        print("Pilot interrupted; durable claim blocks automatic replay.", file=sys.stderr)
        return 130
    except Exception as error:
        # Safe class/kind only. An exception message may contain provider body/config.
        print(json.dumps({"status": "STOPPED", "error_kind": getattr(error, "kind", type(error).__name__)},
                         ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
