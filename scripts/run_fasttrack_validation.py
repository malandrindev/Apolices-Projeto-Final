"""Authorized fast-track stages, each one-shot; default is strictly offline.

Stage quality is NEVER self-approved. A root review, bound to the immutable
result SHA, is required before the next stage. All HTTP attempts are durably
reserved; unknown outcomes consume budget and prohibit replay.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
import uuid
from typing import Any, Callable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.run_berkley_pilot import (
    PilotCorpus, PilotError, atomic_json, execution_metrics, file_hash,
    json_hash, local_evidence_review, policy_routing, utc_now, validate_policy, GOLDEN_SHA256,
)
from src.config import ExtractionOptimizationSettings
from src.schemas.policy import PolicyExtraction

LIMITS = {"berkley": 30, "axa": 30, "comparison": 10}
GLOBAL_LIMIT = 70
SOURCES = {
    "berkley": {"id": "berkley_do_202512", "sha256": "038683e096c2ae0378df25ef18d147493f72627399d1530310af14023df8fea8", "pages": 83, "chunks": 480},
    "axa": {"id": "axa_do_202512_v1", "sha256": "1798723c7a3078496bfc9dcedcc5a02ac5e1a5466784264d517f3ad9f8bc5f19", "pages": 104, "chunks": 620},
}

def implementation_signature(root: Path = ROOT) -> str:
    paths = ("src/llm/model_routing.py", "src/llm/openai_client.py", "src/agents/grouped_extraction.py", "src/agents/semantic_completeness.py", "src/agents/comparison.py", "src/retrieval/local.py", "src/config.py", "scripts/run_fasttrack_validation.py")
    return json_hash({path: file_hash(root / path) for path in paths})

def base_path(root: Path = ROOT) -> Path:
    return root.resolve() / "data/processed/fasttrack_validation"

def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise PilotError("Persistent object is invalid.")
    return value

def require_offline_gate(root: Path = ROOT) -> None:
    gate = read_json(base_path(root) / "offline_gate.json")
    if gate.get("status") != "PASS" or gate.get("implementation_signature") != implementation_signature(root):
        raise PilotError("Offline gate must PASS and match the exact implementation.")
    preserved = read_json(root / "data/processed/fasttrack_preservation_manifest.json")
    for relative, expected in preserved.get("files", {}).items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or file_hash(path) != expected:
            raise PilotError("A preserved first-pilot or frozen artifact SHA changed.")
    if not preserved.get("files"):
        raise PilotError("First-pilot preservation record is required.")

class SessionStore:
    """One session, exactly three ordered stages, exclusive durable claims."""
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.base = base_path(root)
        self.session_path = self.base / "session_manifest.json"
        self.claim_path = self.base / "session_claim.json"
        self.state = read_json(self.session_path) if self.session_path.exists() else {}
        self.run: dict[str, Any] = {}
        self.run_dir: Path | None = None
        if self.state:
            self.validate_session()

    def validate_session(self) -> None:
        if self.state.get("stage_limits") != LIMITS or self.state.get("global_limit") != GLOBAL_LIMIT:
            raise PilotError("Persisted session budgets must not change.")
        attempts = 0
        for stage, row in self.state.get("stages", {}).items():
            if stage not in LIMITS or type(row.get("http_attempts")) is not int or not 0 <= row["http_attempts"] <= LIMITS[stage]:
                raise PilotError("Invalid stage reservation ledger.")
            attempts += row["http_attempts"]
        if attempts != self.state.get("global_http_attempts") or attempts > GLOBAL_LIMIT:
            raise PilotError("Invalid global reservation ledger.")

    def stage_path(self, stage: str, filename: str) -> Path:
        row = self.state.get("stages", {}).get(stage)
        if not row or not isinstance(row.get("run_id"), str) or len(row["run_id"]) != 32 or any(c not in "0123456789abcdef" for c in row["run_id"]):
            raise PilotError("Invalid or missing run identity.")
        return self.base / "runs" / row["run_id"] / filename

    def require_review(self, stage: str) -> dict[str, Any]:
        row = self.state.get("stages", {}).get(stage, {})
        if row.get("status") != "COMPLETED_PENDING_REVIEW":
            raise PilotError("Prior stage did not complete safely.")
        result_path = self.stage_path(stage, "result.json")
        review = read_json(self.stage_path(stage, "review.json"))
        if (review.get("status") != "PASS" or review.get("stage") != stage or review.get("run_id") != row["run_id"] or review.get("result_sha256") != file_hash(result_path)):
            raise PilotError("Prior stage requires independent PASS review bound to its result.")
        if review.get("conservative_field_overrides"):
            from src.agents.semantic_completeness import apply_reviewed_conservative_statuses
            expected = apply_reviewed_conservative_statuses(read_json(result_path)["report"], review)
            projected_path = self.stage_path(stage, "reviewed_report.json")
            if (review.get("reviewed_report_sha256") != file_hash(projected_path)
                    or read_json(projected_path) != expected):
                raise PilotError("Conservative reviewed projection must match its independent review and SHA.")
        return review

    def _persist_session(self) -> None:
        self.state["global_http_attempts"] = sum(row["http_attempts"] for row in self.state["stages"].values())
        self.state["global_remaining"] = GLOBAL_LIMIT - self.state["global_http_attempts"]
        self.state["updated_at"] = utc_now()
        self.validate_session()
        atomic_json(self.session_path, self.state)

    def start(self, stage: str, plan: dict[str, Any]) -> None:
        if stage not in LIMITS:
            raise PilotError("Unknown authorized stage.")
        if stage in self.state.get("stages", {}):
            raise PilotError("Existing stage claim prohibits replay, including uncertain requests.")
        if stage != "berkley":
            self.require_review("berkley")
        if stage == "comparison":
            self.require_review("axa")
        if any(row.get("status") in {"CLAIMED", "RUNNING"} for row in self.state.get("stages", {}).values()):
            raise PilotError("A prior in-flight stage prohibits further requests.")
        self.base.mkdir(parents=True, exist_ok=True)
        if not self.state:
            if stage != "berkley":
                raise PilotError("Berkley must be first.")
            claim = {"session_id": uuid.uuid4().hex, "claimed_at": utc_now(), "global_limit": GLOBAL_LIMIT}
            try:
                with self.claim_path.open("x", encoding="utf-8") as stream:
                    json.dump(claim, stream); stream.flush(); os.fsync(stream.fileno())
            except FileExistsError:
                raise PilotError("Existing session claim prohibits replay.") from None
            self.state = {**claim, "stage_limits": LIMITS.copy(), "stages": {}, "global_http_attempts": 0}
            self._persist_session()
        run_id = uuid.uuid4().hex
        self.run_dir = self.base / "runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        claim = {"stage": stage, "run_id": run_id, "session_id": self.state["session_id"], "claimed_at": utc_now(), "plan_sha256": json_hash(plan), "max_http_attempts": LIMITS[stage]}
        with (self.base / (stage + "_claim.json")).open("x", encoding="utf-8") as stream:
            json.dump(claim, stream); stream.flush(); os.fsync(stream.fileno())
        self.run = {**claim, "status": "CLAIMED", "plan": plan, "request_control": {"max_http_attempts": LIMITS[stage], "http_attempts": 0, "events": []}, "provider_returns": [], "payloads": [], "responses": []}
        self.state["stages"][stage] = {"run_id": run_id, "status": "CLAIMED", "http_attempts": 0}
        self._save()

    def _save(self) -> None:
        if self.run_dir is None:
            raise PilotError("Run must be durably claimed before dispatch.")
        stage = self.run["stage"]
        row = self.state["stages"][stage]
        row.update(status=self.run["status"], http_attempts=self.run["request_control"]["http_attempts"])
        # Global authority first: an interruption can overcount but never permit excess HTTP.
        self._persist_session()
        atomic_json(self.run_dir / "manifest.json", self.run)
        atomic_json(self.run_dir / "telemetry.json", {"run_id": self.run["run_id"], "stage": stage, **self.run["request_control"], "metrics": self.run.get("metrics", {})})

    def persist_control(self, snapshot: dict[str, Any]) -> None:
        prior = self.run["request_control"]["http_attempts"]
        count = snapshot.get("http_attempts")
        if (type(count) is not int or not prior <= count <= LIMITS[self.run["stage"]] or count != len(snapshot.get("events", [])) or snapshot.get("max_http_attempts") != LIMITS[self.run["stage"]]):
            raise PilotError("Invalid attempt reservation.")
        global_count = sum(row["http_attempts"] for name, row in self.state["stages"].items() if name != self.run["stage"]) + count
        if global_count > GLOBAL_LIMIT:
            raise PilotError("Global HTTP budget exceeded; no dispatch allowed.")
        self.run["request_control"] = snapshot
        self.run["status"] = "RUNNING"
        self._save()

    def _latest(self) -> dict[str, Any]:
        events = self.run.get("request_control", {}).get("events", [])
        if not events:
            raise PilotError("No durable reservation precedes provider work.")
        return {"http_attempt": len(events), "logical_step_id": events[-1]["logical_step_id"], "reserved_context": {key: events[-1].get(key) for key in ("document_id", "field_group", "fields", "retrieval_stage", "candidate_count", "page_count_used")}}

    def persist_payload(self, parameters: dict[str, Any]) -> None:
        # User content is the public document context. System prompt, key, endpoint,
        # headers and credentials are never persisted. Exact system version hash suffices.
        row = {**self._latest(), "model": parameters["model"], "max_output_tokens": parameters.get("max_output_tokens"), "user_payloads": [message["content"] for message in parameters.get("input", []) if message.get("role") == "user"], "system_prompt_sha256": json_hash([message["content"] for message in parameters.get("input", []) if message.get("role") == "system"])}
        self.run["payloads"].append(row)
        self._save()
        atomic_json(self.run_dir / "payloads.json", {"run_id": self.run["run_id"], "payloads": self.run["payloads"]})

    def persist_receipt(self, receipt: dict[str, Any]) -> None:
        self.run["provider_returns"].append({**self._latest(), **receipt})
        self._save()

    def persist_response(self, result: dict[str, Any]) -> None:
        self.run["responses"].append({**self._latest(), **result})
        self._save()
        atomic_json(self.run_dir / "responses.json", {"run_id": self.run["run_id"], "responses": self.run["responses"]})

    def persist_partial(self, extraction: Any) -> None:
        value = extraction.model_dump(mode="json") if hasattr(extraction, "model_dump") else extraction
        self.run["partial_extraction"] = value
        self._save()
        atomic_json(self.run_dir / "partial.json", value)

    def finish(self, result: dict[str, Any], metrics: dict[str, Any], status: str) -> None:
        self.run.update(status=status, metrics=metrics, finished_at=utc_now())
        atomic_json(self.run_dir / "result.json", result)
        if result.get("report"):
            atomic_json(self.run_dir / "report.json", result["report"])
        if result.get("comparison"):
            atomic_json(self.run_dir / "comparison.json", result["comparison"])
        self.run["result_sha256"] = file_hash(self.run_dir / "result.json")
        self._save()
        events = []
        wall = 0.0
        for stage in self.state["stages"]:
            path = self.stage_path(stage, "manifest.json")
            if path.exists():
                item = read_json(path)
                events.extend(item["request_control"]["events"])
                wall += item.get("metrics", {}).get("performance", {}).get("total_runtime_seconds", 0)
        total = execution_metrics({"http_attempts": len(events), "events": events}, wall_seconds=wall, prices=load_prices(self.root))
        total["session_id"] = self.state["session_id"]
        total["stages"] = self.state["stages"]
        atomic_json(self.base / "session_telemetry.json", total)

    def recovery(self) -> dict[str, Any]:
        report = {"session_id": self.state.get("session_id"), "global_reserved_attempts": self.state.get("global_http_attempts", 0), "global_remaining": GLOBAL_LIMIT-self.state.get("global_http_attempts", 0), "stages": {}, "providers_constructed": 0, "genai_calls": 0}
        for stage, row in self.state.get("stages", {}).items():
            path = self.stage_path(stage, "manifest.json")
            manifest = read_json(path) if path.exists() else {}
            events = manifest.get("request_control", {}).get("events", [])
            receipts = {r["http_attempt"] for r in manifest.get("provider_returns", [])}
            conclusive = receipts | {i for i, event in enumerate(events, 1) if event.get("result_status") == "completed" or (event.get("result_status") == "error" and (event.get("response_id") or event.get("request_id")))}
            uncertain = row["http_attempts"] - len(conclusive)
            report["stages"][stage] = {**row, "replay_blocked": True, "response_ids": len({r.get("response_id") for r in manifest.get("provider_returns", []) if r.get("response_id")}), "uncertain_attempts": max(0, uncertain), "manifest_events_found": len(events), "unmerged_authority_reservations": max(0, row["http_attempts"]-len(events)), "contract_failures": sum(e.get("failure_category") == "MODEL_OUTPUT_CONTRACT_FAILURE" for e in events), "provider_failures": sum(e.get("failure_category") == "PROVIDER_TRANSPORT_FAILURE" for e in events)}
        return report

def load_prices(root: Path) -> dict[str, Any] | None:
    path = root / "docs/OPENAI_PRICES_2026-10-04.json"
    return read_json(path) if path.is_file() else None

def load_corpus(stage: str, root: Path = ROOT) -> PilotCorpus:
    """Same native/OCR/segmentation pipeline, never a downloader or LLM."""
    from src.agents.segmentation import SegmentationAgent
    from src.config import get_ingestion_settings
    from src.pipeline import process_document
    spec = SOURCES[stage]
    metadata = read_json(root / "data/processed/public_validation_preflight/candidates.json")
    records = [row for row in metadata["documents"] if row.get("id") == spec["id"]]
    if len(records) != 1:
        raise PilotError("Official local source must have a unique record.")
    record = records[0]
    source = (root / record["local_path"]).resolve()
    if not source.is_relative_to(root.resolve()) or file_hash(source) != spec["sha256"] or record.get("sha256") != spec["sha256"]:
        raise PilotError("Official local source SHA changed.")
    settings = replace(get_ingestion_settings(), processed_dir=root / "data/processed")
    ingested, document = process_document(source, settings=settings)
    clauses = SegmentationAgent(gateway=None).segment(document)
    if len(document.pages) != spec["pages"] or len(clauses) != spec["chunks"]:
        raise PilotError("Complete source corpus changed.")
    if file_hash(root / "tests/fixtures/public_retrieval_golden.json") != GOLDEN_SHA256:
        raise PilotError("Independent golden fixture SHA changed.")
    golden = read_json(root / "tests/fixtures/public_retrieval_golden.json")
    items = [item for item in golden["items"] if item["document_id"] == spec["id"]]
    return PilotCorpus(ingested, document, clauses, record, items, {"golden": {"path": "tests/fixtures/public_retrieval_golden.json", "sha256": file_hash(root / "tests/fixtures/public_retrieval_golden.json")}})

def make_plan(stage: str, corpus: PilotCorpus | None, policy: Any, root: Path = ROOT) -> dict[str, Any]:
    from src.config import get_extraction_optimization_settings
    controls = replace(get_extraction_optimization_settings(), strategy="optimized")
    plan = {"stage": stage, "implementation_signature": implementation_signature(root), "stage_limit": LIMITS[stage], "global_limit": GLOBAL_LIMIT, "routing_policy": policy.routing_signature, "optimization": asdict(controls), "same_model_retries": 0, "sdk_retries": 0, "genai_calls": 0}
    if corpus:
        plan.update(document_id=SOURCES[stage]["id"], document_sha256=corpus.document.sha256, pages=len(corpus.document.pages), full_local_chunks=len(corpus.clauses), source_url=corpus.metadata["source_url"], references=corpus.references)
        from src.agents.grouped_extraction import prepare_routed_semantic_plans, build_semantic_batches, GroupedExtractionAgent
        from src.retrieval.local import LocalRetrievalIndex, FIELD_GROUPS
        from types import SimpleNamespace
        from scripts.plan_public_extraction import measure_batch, batch_totals, candidate_recall, price_scenario
        full, focused = prepare_routed_semantic_plans(LocalRetrievalIndex.build(corpus.document, corpus.clauses), controls)
        batches = [measure_batch(batch, GroupedExtractionAgent._routed_stage(batch.fields), policy_routing(policy)) for group in focused.values() for batch in build_semantic_batches(group, controls.batch_chars)]
        totals = batch_totals(batches)
        recall_plans = {group: SimpleNamespace(candidates=tuple(candidate for semantic in full.values() for candidate in semantic.candidates if set(candidate.fields) & set(fields))) for group, fields in FIELD_GROUPS.items()}
        focused_recall_plans = {group: SimpleNamespace(candidates=tuple(candidate for semantic in focused.values() for candidate in semantic.candidates if set(candidate.fields) & set(fields))) for group, fields in FIELD_GROUPS.items()}
        prices = load_prices(root)
        plain_prices = {**prices, "cache_write_input_multiplier": 1.0} if prices else None
        plan.update(planned_initial_batches=len(batches), semantic_groups=len(focused), initial_candidates=len({item.chunk_id for group in focused.values() for item in group.candidates}), expanded_local_candidates=len({item.chunk_id for group in full.values() for item in group.candidates}), initial={"totals": totals, "batches": batches}, shared_fallback_and_adjudication_reserve=LIMITS[stage]-len(batches), independent_local_recall=candidate_recall(recall_plans, corpus.golden_items), focused_payload_recall=candidate_recall(focused_recall_plans, corpus.golden_items), standard_rate_initial_estimate=price_scenario(batches, plain_prices), token_estimate_note="Heuristic, no cache credit or incentive assumed; optional selective verifier and technical fallback share the hard stage limit.")
        if len(batches) > LIMITS[stage]:
            raise PilotError("Initial semantic batches exceed the authorized stage budget.")
    return plan

def validate_runtime_environment() -> None:
    # The installed SDK reads this environment variable and could override
    # auth/project headers. The authorized one-shot profile uses only SDK auth.
    if os.environ.get("OPENAI_CUSTOM_HEADERS", "").strip():
        raise PilotError("Custom OpenAI SDK headers are not part of the authorized profile.")
    endpoint = os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    if endpoint and endpoint != "https://api.openai.com/v1":
        raise PilotError("Only the explicitly authorized official API endpoint is allowed.")

def build_gateway(settings: Any, policy: Any, store: SessionStore) -> Any:
    validate_runtime_environment()
    from openai import OpenAI
    from src.llm.model_routing import RequestControl, RoutedGateway
    from src.llm.openai_client import OpenAIProvider
    for name in ("openai", "openai._base_client", "httpx", "httpcore"):
        logging.getLogger(name).disabled = True
    effective = replace(settings, max_retries=1, timeout_seconds=60, max_tokens=4500, max_retry_wait_seconds=0)
    client = OpenAI(api_key=effective.openai_api_key, base_url="https://api.openai.com/v1", timeout=60, max_retries=0)
    raw = OpenAIProvider(effective, client=client, local_json_repair=True, response_callback=store.persist_receipt, result_callback=store.persist_response, request_callback=store.persist_payload)
    def persist(snapshot):
        store.persist_control(snapshot)
        latest = snapshot["events"][-1] if snapshot["events"] else {}
        print(json.dumps({"stage": store.run["stage"], "reserved_http": snapshot["http_attempts"], "global_reserved_http": store.state["global_http_attempts"], "model": latest.get("requested_model"), "result": latest.get("result_status"), "failure_category": latest.get("failure_category")}), flush=True)
    return RoutedGateway(raw, policy=policy, control=RequestControl(LIMITS[store.run["stage"]], persist_callback=persist))

def make_report(corpus: PilotCorpus, extraction: Any) -> dict[str, Any]:
    from src.agents.extraction import Phase2Report
    source = {key: corpus.metadata.get(key) for key in ("source_url", "origin", "access_date", "insurer", "product_name", "susep_process", "document_type", "version_label", "scope_note")}
    source.update(document_id=corpus.metadata["id"], document_sha256=corpus.document.sha256)
    return Phase2Report(source_name=corpus.ingested.source_name, sha256=corpus.document.sha256, clause_count=len(corpus.clauses), policy=extraction.policy, issues=extraction.issues, field_status=extraction.field_status, retrieval_diagnostics={**extraction.retrieval_diagnostics, "source_reference": source}).model_dump(mode="json")

def execute_stage(stage: str, corpus: PilotCorpus | None, plan: dict[str, Any], policy: Any, *, settings: Any, root: Path = ROOT, gateway_factory: Callable = build_gateway) -> dict[str, Any]:
    from src.agents.grouped_extraction import GroupedExtractionAgent
    from src.agents.comparison import ComparisonAgent
    from src.agents.extraction import Phase2Report
    require_offline_gate(root)
    validate_policy(policy)
    validate_runtime_environment()
    if plan["implementation_signature"] != implementation_signature(root) or plan["routing_policy"] != policy.routing_signature:
        raise PilotError("Exact dry-run plan changed before dispatch.")
    store = SessionStore(root)
    store.start(stage, plan)  # Claim and all limits saved before SDK construction.
    if corpus:
        shutil.copyfile(corpus.ingested.source_path, store.run_dir / "source.pdf")
        if file_hash(store.run_dir / "source.pdf") != corpus.document.sha256:
            raise PilotError("Persisted source SHA mismatch.")
    started = time.perf_counter()
    gateway = None
    result: dict[str, Any] = {"stage": stage, "run_id": store.run["run_id"], "session_id": store.state["session_id"], "complete": False, "review_status": "PENDING_MANUAL_REVIEW", "processing_tier": "PENDING_DASHBOARD_RECONCILIATION", "effective_billing": "PENDING_DASHBOARD_RECONCILIATION"}
    try:
        gateway = gateway_factory(settings, policy, store)
        if stage == "comparison":
            from src.agents.semantic_completeness import apply_reviewed_conservative_statuses
            report_a = Phase2Report.model_validate(apply_reviewed_conservative_statuses(
                read_json(store.stage_path("berkley", "report.json")), store.require_review("berkley")))
            report_b = Phase2Report.model_validate(apply_reviewed_conservative_statuses(
                read_json(store.stage_path("axa", "report.json")), store.require_review("axa")))
            comparison = ComparisonAgent(gateway=gateway, model_strong=policy.role("comparison").primary, processed_dir=store.run_dir / "cache").compare(report_a, report_b)
            result.update(comparison=comparison.model_dump(mode="json"), complete=True, document_id_a=report_a.sha256, document_id_b=report_b.sha256)
        else:
            extractor = GroupedExtractionAgent(gateway=gateway, optimization=ExtractionOptimizationSettings(**plan["optimization"]), routing=policy_routing(policy), processed_dir=store.run_dir / "cache", snapshot_callback=store.persist_partial)
            extraction = extractor.extract(corpus.document, corpus.clauses)
            store.persist_partial(extraction)
            result.update(document_id=SOURCES[stage]["id"], document_sha256=corpus.document.sha256, report=make_report(corpus, extraction), extraction=extraction.model_dump(mode="json"), evidence_review=local_evidence_review(corpus, extraction), complete=not extraction.retrieval_diagnostics.get("stop_reason") and len(extraction.field_status) == len(PolicyExtraction.model_fields))
        snapshot = gateway.control.snapshot()
        result["complete"] = bool(result["complete"] and not snapshot.get("blocked_reason"))
        metrics = execution_metrics(snapshot, wall_seconds=time.perf_counter()-started, prices=load_prices(root))
        metrics["comparison_calls"] = snapshot["http_attempts"] if stage == "comparison" else 0
        result["metrics"] = metrics
        store.finish(result, metrics, "COMPLETED_PENDING_REVIEW" if result["complete"] else "PARTIAL_STOPPED")
        return result
    except BaseException as error:
        snapshot = gateway.control.snapshot() if gateway else store.run["request_control"]
        metrics = execution_metrics(snapshot, wall_seconds=time.perf_counter()-started, prices=load_prices(root))
        result.update(metrics=metrics, stop_reason=getattr(error, "kind", type(error).__name__))
        store.finish(result, metrics, "INTERRUPTED" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "FAILED")
        raise

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline recovery/preflight; --execute runs exactly one independently gated stage.")
    parser.add_argument("--stage", choices=tuple(LIMITS), default="berkley")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        from src.config import get_settings
        from src.llm.model_routing import ModelRoutingPolicy
        store = SessionStore(ROOT)
        print(json.dumps({"mode": "OFFLINE_RECOVERY", **store.recovery()}), flush=True)
        if args.stage in store.state.get("stages", {}):
            if args.execute:
                raise PilotError("Existing run prohibits replay; review preserved evidence.")
            return 0
        policy = ModelRoutingPolicy.from_env()
        validate_policy(policy)
        corpus = load_corpus(args.stage) if args.stage != "comparison" else None
        plan = make_plan(args.stage, corpus, policy)
        print(json.dumps({"mode": "OFFLINE_PREFLIGHT", **plan}), flush=True)
        if not args.execute:
            return 0
        result = execute_stage(args.stage, corpus, plan, policy, settings=get_settings(provider="openai", require_api_key=True))
        print(json.dumps({"stage": args.stage, "run_id": result["run_id"], "complete": result["complete"], "http_attempts": result["metrics"]["http_attempts"], "review_status": result["review_status"]}), flush=True)
        return 0 if result["complete"] else 1
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        print(json.dumps({"status": "STOPPED", "kind": getattr(error, "kind", type(error).__name__)}), file=sys.stderr)
        return 130 if isinstance(error, KeyboardInterrupt) else 1

if __name__ == "__main__":
    raise SystemExit(main())
