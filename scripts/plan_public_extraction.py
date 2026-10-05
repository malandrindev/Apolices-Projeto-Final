"""Plan public extraction locally; no model, gateway, network or coverage inference.

The initial plan assumes no locally resolved fields and no GenAI cache credits.
Progressive plans measure exact candidate recall. The stress scenario keeps every
field unresolved through stages 0..4 and flags all fields for one verifier pass.
It is a scenario, not a promise of completion within the initial call estimate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.grouped_extraction import (
    GROUPED_EXTRACTION_VERSION, OBJECTIVE_FIELDS, PROMPTS, build_semantic_batches,
)
from src.agents.segmentation import SegmentationAgent
from src.config import (
    ExtractionOptimizationSettings, ExtractionRouting, get_extraction_optimization_settings,
    get_extraction_routing, get_ingestion_settings, get_settings,
)
from src.llm.openai_client import schema_for_stage
from src.pipeline import process_document
from src.retrieval.local import CRITICAL_FIELDS, FIELD_GROUPS, RETRIEVAL_VERSION, LocalRetrievalIndex
from src.schemas.retrieval import GroupExtractionResponse

OUTPUT_CAP = 4500
OUTPUT_PLANNING_ASSUMPTION = 1500
DEFAULT_DOCUMENTS = ("berkley_do_202512", "axa_do_202512_v1")
ROLES = ("optimized_extraction", "optimized_interpretation", "optimized_verification")


def normalise_whitespace(value: str) -> str:
    """Preserve spelling, accents, case and punctuation when checking citations."""
    return " ".join(value.split())


def validate_golden(document: Any, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reject stale/invalid anchors; never change references to suit retrieval."""
    items = list(items)
    fields = {field for names in FIELD_GROUPS.values() for field in names}
    pages = {page.page_number: normalise_whitespace(page.text) for page in document.pages}
    for item in items:
        quote = normalise_whitespace(item.get("quote", ""))
        if (item.get("sha256") != document.sha256 or item.get("field") not in fields
                or type(item.get("page")) is not int or item["page"] not in pages
                or not quote or quote not in pages[item["page"]]):
            raise ValueError("Golden anchor does not match its unchanged source/page/field.")
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("Golden anchor IDs must be unique.")
    return items


def candidate_recall(plans: dict[str, Any], items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """A hit requires the requested field, exact page AND literal source fragment."""
    field_groups = {field: group for group, fields in FIELD_GROUPS.items() for field in fields}
    hits = []
    for item in items:
        candidates = plans[field_groups[item["field"]]].candidates
        quote = normalise_whitespace(item["quote"])
        matching = [
            candidate.chunk_id for candidate in candidates
            if item["field"] in candidate.fields and any(
                page.page_number == item["page"]
                and quote in normalise_whitespace(page.text)
                for page in candidate.source_pages
            )
        ]
        page_selected = any(
            item["field"] in candidate.fields and any(
                page.page_number == item["page"] for page in candidate.source_pages)
            for candidate in candidates)
        miss_reason = (None if matching else
                       "exact_quote_not_in_selected_field_page_fragments" if page_selected else
                       "expected_page_not_selected_for_field")
        hits.append({"id": item["id"], "field": item["field"], "page": item["page"],
                     "hit": bool(matching), "candidate_ids": matching, "miss_reason": miss_reason})
    count = sum(item["hit"] for item in hits)
    critical = [item for item in hits if item["field"] in CRITICAL_FIELDS]
    critical_hits = sum(item["hit"] for item in critical)
    return {"hits": count, "total": len(hits),
            "fraction": count / len(hits) if hits else None,
            "critical": {"hits": critical_hits, "total": len(critical),
                         "fraction": critical_hits / len(critical) if critical else None},
            "anchors": hits}


def request_shape(batch: Any, role: str, routing: ExtractionRouting, *,
                  reasoning_effort: str = "low", fast_model: str = "") -> dict[str, Any]:
    """Serialize the existing OpenAI adapter shape without constructing a client."""
    model = {
        "optimized_extraction": routing.simple_model,
        "optimized_interpretation": routing.interpretation_model,
        "optimized_verification": routing.verification_model,
    }[role]
    agent_schema = json.dumps(GroupExtractionResponse.model_json_schema(),
                              ensure_ascii=False, sort_keys=True)
    result = {
        "model": model, "store": False, "max_output_tokens": OUTPUT_CAP,
        "input": [
            {"role": "system", "content": PROMPTS[role] + "\nSchema:\n" + agent_schema},
            {"role": "user", "content": json.dumps(batch.payload(), ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_schema", "name": "result", "strict": True,
                            "schema": schema_for_stage(role)}},
    }
    if model.startswith(("gpt-5.6", "gpt-5.4", "gpt-5.2")):
        result["reasoning"] = {
            "effort": "none" if model == fast_model or role == "optimized_extraction"
            else reasoning_effort,
        }
    return result


def measure_batch(batch: Any, role: str, routing: ExtractionRouting, **kwargs: Any) -> dict[str, Any]:
    shape = request_shape(batch, role, routing, **kwargs)
    serialised = json.dumps(shape, ensure_ascii=False, sort_keys=True)
    system, user = shape["input"]
    strict_schema_chars = len(json.dumps(shape["text"]["format"]["schema"],
                                        ensure_ascii=False, sort_keys=True))
    return {
        "batch_id": batch.batch_id, "group_id": batch.group_id, "stage": batch.stage,
        "role": role, "model": shape["model"], "fields": list(batch.fields),
        "pages": list(batch.pages), "candidate_ids": [c.chunk_id for c in batch.candidates],
        "candidate_chunks": len(batch.candidates), "payload_chars": batch.char_count,
        "system_chars": len(system["content"]), "user_chars": len(user["content"]),
        "strict_schema_chars": strict_schema_chars,
        "serialized_request_chars": len(serialised),
        "serialized_request_utf8_bytes": len(serialised.encode("utf-8")),
        "input_tokens_approx": math.ceil(len(serialised) / 3.5) + 1000,
        "input_tokens_utf8_reserve": len(serialised.encode("utf-8")) + 1000,
        "output_tokens_assumed": OUTPUT_PLANNING_ASSUMPTION, "max_output_tokens": OUTPUT_CAP,
        "reasoning_effort": shape.get("reasoning", {}).get("effort"),
    }


def measured_batches(plans: Iterable[Any], controls: ExtractionOptimizationSettings,
                     routing: ExtractionRouting, *, verification: bool = False,
                     requested_fields: dict[str, tuple[str, ...]] | None = None,
                     **kwargs: Any) -> list[dict[str, Any]]:
    result = []
    for plan in plans:
        for batch in build_semantic_batches(
                plan, controls.batch_chars,
                requested_fields.get(plan.group_id, ()) if requested_fields is not None else None):
            role = ("optimized_verification" if verification else
                    "optimized_extraction" if set(batch.fields) <= OBJECTIVE_FIELDS
                    else "optimized_interpretation")
            result.append(measure_batch(batch, role, routing, **kwargs))
    return result


def batch_totals(batches: list[dict[str, Any]]) -> dict[str, Any]:
    numeric = ("payload_chars", "system_chars", "user_chars", "strict_schema_chars",
               "serialized_request_chars", "serialized_request_utf8_bytes",
               "input_tokens_approx", "input_tokens_utf8_reserve",
               "output_tokens_assumed", "max_output_tokens")
    roles = {role: {"logical_calls": 0, **dict.fromkeys(numeric, 0)} for role in ROLES}
    for batch in batches:
        roles[batch["role"]]["logical_calls"] += 1
        for key in numeric:
            roles[batch["role"]][key] += batch[key]
    return {"logical_calls": len(batches),
            **{key: sum(batch[key] for batch in batches) for key in numeric}, "by_role": roles}


def price_scenario(batches: list[dict[str, Any]], prices: dict[str, Any] | None,
                   *, input_key: str = "input_tokens_approx",
                   output_key: str = "output_tokens_assumed") -> dict[str, Any]:
    """Dated reference costs; no complimentary or provider-cache credit."""
    if not prices:
        return {"usd": None, "status": "UNVERIFIED_PRICES"}
    unknown = sorted({batch["model"] for batch in batches
                      if batch["model"] not in prices.get("models", {})})
    if unknown:
        return {"usd": None, "status": "UNVERIFIED_MODEL_PRICES", "models": unknown}
    multiplier = prices.get("cache_write_input_multiplier", 1.0)
    subtotal = Counter()
    for batch in batches:
        rate = prices["models"][batch["model"]]
        subtotal[batch["role"]] += (
            batch[input_key] * rate["input"] * multiplier
            + batch[output_key] * rate["output"]
        ) / 1_000_000
    return {"usd": round(sum(subtotal.values()), 6), "status": "REFERENCE_SCENARIO_ONLY",
            "by_role_usd": {key: round(value, 6) for key, value in subtotal.items()},
            "prices_verified_on": prices.get("verified_on"), "currency": prices.get("currency"),
            "input_multiplier_scenario": multiplier,
            "input_measure": input_key, "output_measure": output_key,
            "notes": ["Not actual billing or a guaranteed financial ceiling.",
                      "Global input multiplier is a scenario reserve, not a surcharge claim.",
                      "No cached-input or complimentary discount assumed; reasoning output is billed.",
                      "Tokens are heuristics including duplicated schema and 1000 overhead per call."]}


def associations(plan: Any, fields: Iterable[str] | None = None) -> set[tuple[str, str]]:
    selected = set(fields if fields is not None else plan.fields)
    return {(name, candidate.chunk_id) for candidate in plan.candidates
            for name in candidate.fields if name in selected}


def golden_guided_scenario(index: Any, initial_plans: dict[str, Any],
                           items: list[dict[str, Any]], controls: ExtractionOptimizationSettings,
                           routing: ExtractionRouting, **kwargs: Any) -> dict[str, Any]:
    """An explicitly diagnostic oracle over fixed anchors, never a production selector.

    Only fields whose verified anchors remain unretrieved are expanded. This
    cannot predict model success, confidence, undocumented facts or missing fields.
    """
    plans = dict(initial_plans)
    seen = {group: associations(plan) for group, plan in plans.items()}
    expanded_fields = {group: set() for group in FIELD_GROUPS}
    stages, fallback = [], []
    for stage in range(1, 5):
        before = candidate_recall(plans, items)
        missing = {item["field"] for item in before["anchors"] if not item["hit"]}
        if not missing:
            break
        changed, requested = [], {}
        for group, plan in list(plans.items()):
            pending = tuple(name for name in plan.fields if name in missing)
            if not pending:
                continue
            expanded = index.expand(plan, fields=pending, stage=stage,
                                    per_field_limit=controls.expanded_top_n if stage == 1 else None)
            plans[group] = expanded
            expanded_fields[group].update(pending)
            current = associations(expanded, pending)
            if current - seen[group]:
                changed.append(expanded)
                requested[group] = pending
                seen[group].update(current)
        batches = measured_batches(changed, controls, routing,
                                   requested_fields=requested, **kwargs)
        fallback.extend(batches)
        stages.append({
            "stage": stage, "fields_expanded": sorted(missing),
            "recall": candidate_recall(plans, items),
            "new_logical_batches": len(batches),
            "groups": [plan.summary() for plan in plans.values()],
        })
    verification_fields = {group: tuple(name for name in FIELD_GROUPS[group]
                                        if name in fields)
                           for group, fields in expanded_fields.items() if fields}
    verification = measured_batches(
        (plans[group] for group in verification_fields), controls, routing, verification=True,
        requested_fields=verification_fields, **kwargs)
    return {
        "method": "GOLDEN_GUIDED_RETRIEVAL_DIAGNOSTIC_ONLY",
        "limitations": [
            "Fixed known anchors control expansion/stopping; this is not a production oracle.",
            "No LLM outcomes or extraction accuracy are simulated or inferred.",
            "Ungolden fields, contractual absence, ambiguities and low confidence may require more calls.",
            "Verifier payloads are hypothetical for fields that needed expansion; no verification ran.",
        ],
        "progressive": stages, "final_recall": candidate_recall(plans, items),
        "fallback": {"totals": batch_totals(fallback), "batches": fallback},
        "verifier_if_expanded_fields_flagged": {
            "totals": batch_totals(verification), "batches": verification},
        "verification_fields": {group: list(fields) for group, fields in verification_fields.items()},
    }


def scenario_summary(initial: list[dict[str, Any]], fallback: list[dict[str, Any]],
                     verifier: list[dict[str, Any]], prices: dict[str, Any] | None, *,
                     http_attempts_per_operation: int, input_key: str, output_key: str,
                     assumptions: list[str]) -> dict[str, Any]:
    batches = initial + fallback + verifier
    cost = price_scenario(batches, prices, input_key=input_key, output_key=output_key)
    single_usd = cost.get("usd")
    return {
        "assumptions": assumptions,
        "initial_calls": len(initial), "fallback_calls": len(fallback),
        "verifier_calls": len(verifier), "comparison_calls": 0,
        "separate_json_repair_calls": 0,
        "repair_note": "Grouped fallback/verifier provide semantic escalation; no separate repair loop.",
        "totals_logical": batch_totals(batches),
        "http_attempts_per_operation": http_attempts_per_operation,
        "http_attempts": len(batches) * http_attempts_per_operation,
        "retry_attempts": len(batches) * (http_attempts_per_operation - 1),
        "input_tokens_scenario": sum(b[input_key] for b in batches) * http_attempts_per_operation,
        "output_tokens_scenario": sum(b[output_key] for b in batches) * http_attempts_per_operation,
        "usd_reference_scenario": round(single_usd * http_attempts_per_operation, 6)
                                   if single_usd is not None else None,
        "one_attempt_cost": cost,
        "not_guaranteed_total_calls_or_financial_ceiling": True,
    }


def plan_document(document: Any, clauses: list[Any], *,
                  controls: ExtractionOptimizationSettings | None = None,
                  routing: ExtractionRouting | None = None,
                  golden_items: Iterable[dict[str, Any]] = (), document_id: str = "",
                  baseline: dict[str, Any] | None = None, prices: dict[str, Any] | None = None,
                  reasoning_effort: str = "low", fast_model: str = "") -> dict[str, Any]:
    """Pure local planning; never instantiate the extraction agent or a gateway."""
    controls = controls or ExtractionOptimizationSettings()
    routing = routing or ExtractionRouting("gpt-5.6-luna", "gpt-5.6-sol",
                                          "gpt-5.6-terra", "gpt-5.6-sol")
    items = validate_golden(document, golden_items)
    index = LocalRetrievalIndex.build(document, clauses)
    plans = {group: index.plan(group, per_field_limit=controls.initial_top_n)
             for group in FIELD_GROUPS}
    arguments = {"reasoning_effort": reasoning_effort, "fast_model": fast_model}
    initial = measured_batches(plans.values(), controls, routing, **arguments)
    guided = golden_guided_scenario(index, plans, items, controls, routing, **arguments)
    stages = [{"stage": 0, "recall": candidate_recall(plans, items),
               "unique_candidate_chunks": len({c.chunk_id for p in plans.values()
                                               for c in p.candidates}),
               "groups": [p.summary() for p in plans.values()],
               "new_logical_batches_in_all_unresolved_scenario": len(initial)}]
    seen = {group: associations(plan) for group, plan in plans.items()}
    fallback = []
    for stage in range(1, 5):
        changed = []
        for group, previous in list(plans.items()):
            expanded = index.expand(previous, stage=stage,
                                    per_field_limit=controls.expanded_top_n if stage == 1 else None)
            current_ids = associations(expanded)
            if not seen[group] <= current_ids:
                raise ValueError("Progressive retrieval discarded previous candidates.")
            plans[group] = expanded
            if current_ids - seen[group]:
                changed.append(expanded)
                seen[group].update(current_ids)
        stage_batches = measured_batches(changed, controls, routing, **arguments)
        fallback.extend(stage_batches)
        stages.append({"stage": stage, "recall": candidate_recall(plans, items),
                       "unique_candidate_chunks": len({c.chunk_id for p in plans.values()
                                                       for c in p.candidates}),
                       "groups": [p.summary() for p in plans.values()],
                       "new_logical_batches_in_all_unresolved_scenario": len(stage_batches)})
    verification = measured_batches(plans.values(), controls, routing, verification=True, **arguments)
    stress = initial + fallback + verification
    chunk_count = len(clauses)
    classification = baseline.get("classification_batches_20", 0) if baseline else None
    cold_legacy = chunk_count + classification if classification is not None else None
    return {
        "id": document_id or document.source_name, "sha256": document.sha256,
        "pages": len(document.pages), "full_local_chunks": chunk_count,
        "full_local_text_chars": sum(len(p.text) for p in document.pages),
        "full_local_corpus_retained": len(index.pages) == len(document.pages)
                                     and len(index.chunks) == chunk_count,
        "ocr_cache_hit": document.cache_hit, "index": index.summary(),
        "strategy_requested": controls.strategy,
        "strategy_auto_would_use_optimized": len(document.pages) >= controls.auto_min_pages
                                           or chunk_count >= controls.auto_min_chunks,
        "optimization": asdict(controls), "routing": asdict(routing),
        "legacy": {"chunk_calls": chunk_count, "classification_batches": classification,
                   "logical_normal_without_repairs": cold_legacy,
                   "logical_all_repairs": 2 * chunk_count + classification
                                         if classification is not None else None},
        "initial": {"assumption": "All fields pending; local deterministic fills/cache may reduce calls.",
                    "totals": batch_totals(initial), "batches": initial,
                    "usd_planning": price_scenario(initial, prices),
                    "usd_input_reserve_output_cap": price_scenario(
                        initial, prices, input_key="input_tokens_utf8_reserve",
                        output_key="max_output_tokens")},
        "progressive": stages,
        "golden_guided": guided,
        "scenarios": {
            "EXPECTED": scenario_summary(
                initial, guided["fallback"]["batches"],
                guided["verifier_if_expanded_fields_flagged"]["batches"], prices,
                http_attempts_per_operation=1, input_key="input_tokens_approx",
                output_key="output_tokens_assumed",
                assumptions=[
                    "Planning scenario only: initial all-field batches plus fixed-anchor guided expansion.",
                    "Only fields with unretrieved golden anchors expand; expanded fields are flagged once.",
                    "1500 output tokens per call is an assumption; zero HTTP retries/cache credits.",
                    "Does not predict LLM correctness, confidence or full-document completion.",
                ]),
            "CONSERVATIVE": scenario_summary(
                initial, fallback, verification, prices, http_attempts_per_operation=1,
                input_key="input_tokens_utf8_reserve", output_key="max_output_tokens",
                assumptions=[
                    "Every field remains unresolved through all four expansions and is flagged for verifier.",
                    "Complete payloads resent; UTF8+1000 input reserve and 4500 output cap per operation.",
                    "One HTTP attempt per operation; no cached-input/complimentary credit.",
                    "All-unresolved scenario, not a mathematical bound on every adaptive path.",
                ]),
            "WORST_REASONABLE": scenario_summary(
                initial, fallback, verification, prices, http_attempts_per_operation=3,
                input_key="input_tokens_utf8_reserve", output_key="max_output_tokens",
                assumptions=[
                    "Same all-unresolved/flagged workload with maximum three HTTP attempts per operation.",
                    "First attempt plus two retries; each attempt assumed to consume reserved input/capped output.",
                    "No claim failed HTTP attempts are free; no cached-input or complimentary credits.",
                    "Scenario reserve, not guaranteed actual billing or a universal call bound.",
                ]),
        },
        "fallback_all_unresolved": {"totals": batch_totals(fallback), "batches": fallback},
        "verifier_all_flagged": {"totals": batch_totals(verification), "batches": verification},
        "stress_all_unresolved_and_flagged": {
            "assumption": "All fields unresolved through stages 1..4, one verifier pass over all fields.",
            "totals": batch_totals(stress),
            "http_attempts_at_current_max3": 3 * len(stress),
            "usd_one_attempt_input_reserve_output_cap": price_scenario(
                stress, prices, input_key="input_tokens_utf8_reserve", output_key="max_output_tokens"),
            "not_a_bound_on_every_adaptive_path": True,
        },
        "structural_absolute_bound": {
            "logical_calls": len(FIELD_GROUPS) * 6 * chunk_count,
            "http_attempts": len(FIELD_GROUPS) * 6 * chunk_count * 3,
            "basis": "7 groups x (initial+4 expansions+one verifier) x at most one batch per chunk.",
            "note": "Loose structural bound, not a feasible pilot or expected workload.",
        },
        "golden_scope": "Candidate quote/page recall only; does not validate extraction or coverage.",
        "genai_calls": 0,
    }


def local_path(root: Path, value: str) -> Path:
    path = (root / value.replace("\\", "/")).resolve()
    if not path.is_relative_to((root / "data" / "processed").resolve()):
        raise ValueError("Public preflight paths must remain inside data/processed.")
    return path


def run_preflight(*, root: Path = ROOT, golden_path: Path | None = None,
                  manifest_path: Path | None = None, prices_path: Path | None = None,
                  document_ids: Iterable[str] = DEFAULT_DOCUMENTS,
                  controls: ExtractionOptimizationSettings | None = None,
                  routing: ExtractionRouting | None = None, ingestion_settings: Any = None,
                  reasoning_effort: str = "low", fast_model: str = "") -> dict[str, Any]:
    root = root.resolve()
    golden_path = golden_path or root / "tests/fixtures/public_retrieval_golden.json"
    manifest_path = manifest_path or root / "data/processed/public_validation_preflight/candidates.json"
    prices_path = prices_path or root / "docs/OPENAI_PRICES_2026-10-04.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prices = json.loads(prices_path.read_text(encoding="utf-8")) if prices_path.is_file() else None
    lookup = {record["id"]: record for record in manifest["documents"]}
    records = []
    document_ids = tuple(document_ids)
    if not document_ids or len(set(document_ids)) != len(document_ids):
        raise ValueError("Specify distinct public document IDs.")
    for document_id in document_ids:
        if document_id not in lookup or document_id not in golden["documents"]:
            raise ValueError("Document lacks candidate metadata or unchanged golden provenance.")
        record, reference = lookup[document_id], golden["documents"][document_id]
        path = local_path(root, record["local_path"])
        if (record["sha256"] != reference["sha256"]
                or path != local_path(root, reference["local_path"])
                or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]):
            raise ValueError("Public document hash/path differs from its recorded provenance.")
        ingested, document = process_document(path, settings=ingestion_settings or get_ingestion_settings())
        if document.sha256 != ingested.sha256 or ingested.sha256 != record["sha256"]:
            raise ValueError("Ingestion hash changed.")
        if len(document.pages) != record["pages"] or len(document.pages) != reference["pages"]:
            raise ValueError("Public page count differs from its recorded provenance.")
        clauses = SegmentationAgent(gateway=None).segment(document)
        if len(clauses) != record["chunk_count"]:
            raise ValueError("Local segmentation count differs from the measured baseline.")
        result = plan_document(
            document, clauses, controls=controls, routing=routing, baseline=record, prices=prices,
            document_id=document_id, golden_items=[item for item in golden["items"]
                                                  if item["document_id"] == document_id],
            reasoning_effort=reasoning_effort, fast_model=fast_model)
        result["source_url"] = reference["source_url"]
        records.append(result)
    return {
        "schema_version": 1, "planned_at_utc": datetime.now(timezone.utc).isoformat(),
        "genai_calls": 0, "providers_constructed": 0, "method": "LOCAL_ONLY",
        "retrieval_version": RETRIEVAL_VERSION, "extraction_version": GROUPED_EXTRACTION_VERSION,
        "golden_version": golden["version"], "prices_verified_on": prices.get("verified_on") if prices else None,
        "token_method": "Serialized real messages plus strict schema; chars/3.5+1000 and UTF8bytes+1000 per call.",
        "limitations": [
            "No tokenization library; token estimates/reserves are not guaranteed billable ceilings.",
            "Includes schema in system prompt and response text.format; output includes reasoning.",
            "No GenAI cache-hit assumptions; local OCR cache does not imply provider input cache.",
            "Initial calls exclude adaptive fallbacks/verifier; actual field outcomes are unknown.",
            "Exact quote/page recall on fixed anchors does not establish full semantic recall or accuracy.",
            "No extracted facts, coverage conclusions, live latency or measured billing.",
            "No APIs, provider construction, hosted tools, embeddings or document downloads.",
        ],
        "documents": records,
        "comparison_separate": {
            "semantic_fields_max": 16, "initial_batches_max_per_pair": 2,
            "logical_with_one_repair_max_per_pair": 4, "http_attempts_max_per_pair": 12,
            "note": "Separate baseline comparison bound; no comparison or report extraction executed.",
        },
    }


def save_plan(plan: dict[str, Any], output: Path, *, root: Path = ROOT) -> None:
    if not output.resolve().is_relative_to((root / "data" / "processed").resolve()):
        raise ValueError("Planning output must remain inside data/processed.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=output.parent, prefix=".optimized_plan_",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(plan, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", nargs="+", default=list(DEFAULT_DOCUMENTS))
    args = parser.parse_args()
    if (not args.documents or len(set(args.documents)) != len(args.documents)
            or not set(args.documents) <= set(DEFAULT_DOCUMENTS)):
        parser.error("This E1 preflight only accepts distinct approved Berkley/AXA local IDs.")
    settings = get_settings(require_api_key=False, provider="openai")
    plan = run_preflight(document_ids=args.documents,
                         controls=get_extraction_optimization_settings(),
                         routing=get_extraction_routing(settings),
                         reasoning_effort=settings.openai_reasoning_effort,
                         fast_model=settings.model_fast)
    output = ROOT / "data/processed/public_validation_preflight/optimized_plan.json"
    save_plan(plan, output)
    print(json.dumps({
        "status": "LOCAL_PREFLIGHT_ONLY", "genai_calls": 0, "providers_constructed": 0,
        "output": str(output.relative_to(ROOT)),
        "documents": [
            {"id": record["id"], "pages": record["pages"], "chunks": record["full_local_chunks"],
             "initial_calls": record["initial"]["totals"]["logical_calls"],
             "fallback_calls_stress": record["fallback_all_unresolved"]["totals"]["logical_calls"],
             "verifier_calls_stress": record["verifier_all_flagged"]["totals"]["logical_calls"],
             "recall_by_stage": [{"stage": stage["stage"], "hits": stage["recall"]["hits"],
                                 "total": stage["recall"]["total"]} for stage in record["progressive"]]}
            for record in plan["documents"]],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
