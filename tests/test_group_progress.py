"""Offline regression for legacy/routed grouping and the LIMITS progress crash."""
from __future__ import annotations

import hashlib
import json

import groq
import httpx
import openai
import pytest

from src.agents.grouped_extraction import (
    GroupedExtractionAgent, _notify_group_progress, prepare_routed_semantic_plans,
)
from src.agents.ocr import PageText, ProcessedDocument
from src.agents.segmentation import SegmentationAgent
from src.config import ExtractionOptimizationSettings, ExtractionRouting
from src.llm.model_routing import ModelRoutingPolicy, RoutedGateway
from src.retrieval import local
from src.retrieval.groups import (
    FIELD_GROUPS, SEMANTIC_FIELD_GROUPS, LEGACY_TO_ROUTED_GROUPS,
    ROUTED_TO_LEGACY_GROUPS, resolve_group_ids,
)
from src.schemas.policy import FieldEvidence, PolicyExtraction
from src.schemas.retrieval import FieldStatus


@pytest.fixture(autouse=True)
def block_provider_http(monkeypatch):
    attempted = []

    def reject(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Real SDK/provider HTTP is forbidden in group regression tests.")

    for sdk, names in ((openai, ("OpenAI", "AsyncOpenAI")),
                       (groq, ("Groq", "AsyncGroq"))):
        for name in names:
            monkeypatch.setattr(getattr(sdk, name), "__init__", reject)
    monkeypatch.setattr(httpx.Client, "send", reject)
    monkeypatch.setattr(httpx.AsyncClient, "send", reject)
    yield
    assert attempted == [], "Even a swallowed real provider attempt is a test failure."


class OfflineProvider:
    provider = "offline-progress-regression"

    def __init__(self, *, forbidden=False):
        self.calls = []
        self.forbidden = forbidden

    def complete(self, **call):
        assert not self.forbidden, "No-candidate/cache replay must not dispatch even the fake."
        self.calls.append(call)
        payload = json.loads(call["messages"][1]["content"])
        rows = []
        for name in payload["field_names"]:
            evidence, status = FieldEvidence(), "NOT_FOUND"
            if name == "side_a":
                quote = "Side A concedida."
                for source in payload["sources"]:
                    if name not in source["fields"]:
                        continue
                    for page in source["pages"]:
                        if quote in page["text"]:
                            evidence = FieldEvidence(valor="Side A concedida", pagina=page["page_number"],
                                trecho_origem=quote, confianca=.95)
                            status = "FOUND"
            rows.append({"field_name": name, "evidence": evidence.model_dump(), "status": status})
        return json.dumps({"fields": rows})


def document(text="1. COBERTURAS\nSide A concedida."):
    return ProcessedDocument(source_name="progress.pdf",
        sha256=hashlib.sha256(text.encode()).hexdigest(), size_bytes=max(1, len(text.encode())),
        media_type="application/pdf", pages=[PageText(page_number=1, text=text, extraction_method="native")],
        processed_at="2026-10-05", cache_key="offline-progress")


def agent(provider, tmp_path=None, *, routed):
    gateway = (RoutedGateway(provider, policy=ModelRoutingPolicy.from_env({}))
               if routed else provider)
    return GroupedExtractionAgent(gateway=gateway,
        optimization=ExtractionOptimizationSettings(strategy="optimized"),
        routing=ExtractionRouting("unused", "unused", "unused"), processed_dir=tmp_path)


def test_registries_are_shared_and_partition_the_same_27_fields_once():
    from src.agents import grouped_extraction
    assert local.FIELD_GROUPS is FIELD_GROUPS
    assert grouped_extraction.FIELD_GROUPS is FIELD_GROUPS
    assert grouped_extraction.SEMANTIC_FIELD_GROUPS is SEMANTIC_FIELD_GROUPS
    assert tuple(FIELD_GROUPS) == (
        "IDENTIFICATION", "LIMITS", "CORE_COVERAGES", "TEMPORAL", "SCOPE",
        "EXCLUSIONS", "EXTENSIONS_DEFINITIONS")
    assert tuple(SEMANTIC_FIELD_GROUPS) == (
        "LIMITS_SUBLIMITS", "RETENTIONS_DEDUCTIBLES", "SIDE_COVERAGES", "DEFENSE_COSTS",
        "TERRITORY", "JURISDICTION", "EXCLUSIONS", "TEMPORAL", "IDENTIFICATION",
        "CONSENT", "ALLOCATION", "EXTENSIONS", "CANCELLATION_RENEWAL", "DEFINITIONS")
    for registry in (FIELD_GROUPS, SEMANTIC_FIELD_GROUPS):
        names = [name for fields in registry.values() for name in fields]
        assert len(names) == len(set(names)) == 27
        assert set(names) == set(PolicyExtraction.model_fields)
    for legacy, routed_ids in LEGACY_TO_ROUTED_GROUPS.items():
        routed_names = [name for routed_id in routed_ids for name in SEMANTIC_FIELD_GROUPS[routed_id]]
        assert len(routed_names) == len(set(routed_names))
        assert set(routed_names) == set(FIELD_GROUPS[legacy])
    assert all(len(owners) == 1 for owners in ROUTED_TO_LEGACY_GROUPS.values())


@pytest.mark.parametrize("name,expected", [
    ("LIMITS", ("LIMITS_SUBLIMITS", "RETENTIONS_DEDUCTIBLES")),
    ("COVERAGES", ("SIDE_COVERAGES", "DEFENSE_COSTS", "CONSENT", "ALLOCATION")),
    ("CORE_COVERAGES", ("SIDE_COVERAGES", "DEFENSE_COSTS", "CONSENT", "ALLOCATION")),
    ("SCOPE", ("TERRITORY", "JURISDICTION")),
    ("EXTENSIONS_DEFINITIONS", ("EXTENSIONS", "CANCELLATION_RENEWAL", "DEFINITIONS")),
    ("TEMPORAL", ("TEMPORAL",)),
    ("DEFENSE_COSTS", ("DEFENSE_COSTS",)),
])
def test_renamed_or_split_group_resolves_without_losing_fields(name, expected):
    assert resolve_group_ids(name, routed=True) == expected


def test_reverse_alias_and_unknown_group_are_explicit():
    assert resolve_group_ids("RETENTIONS_DEDUCTIBLES", routed=False) == ("LIMITS",)
    assert resolve_group_ids("DEFENSE_COSTS", routed=False) == ("CORE_COVERAGES",)
    assert resolve_group_ids("COVERAGES", routed=False) == ("CORE_COVERAGES",)
    with pytest.raises(ValueError, match="Unknown extraction field group"):
        resolve_group_ids("INVENTED_GROUP", routed=True)


@pytest.mark.parametrize("routed", [False, True])
def test_defensive_progress_with_missing_group_hits(routed):
    events = []
    # Exact old mismatch: LIMITS is a legacy key, while hits has only split IDs.
    _notify_group_progress(lambda *event: events.append(event),
                           {"LIMITS_SUBLIMITS": False}, routed=routed)
    expected = SEMANTIC_FIELD_GROUPS if routed else FIELD_GROUPS
    assert [event[2] for event in events] == list(expected)
    assert [(event[0], event[1]) for event in events] == [
        (completed, len(expected)) for completed in range(1, len(expected) + 1)]
    assert all(event[3] is False for event in events)
    if not routed:
        assert next(event for event in events if event[2] == "LIMITS")[3] is False


@pytest.mark.parametrize("routed", [False, True])
def test_no_candidates_reports_incomplete_and_progress_without_dispatch(routed):
    source = document("")
    provider = OfflineProvider(forbidden=True)
    extraction = agent(provider, routed=routed)
    events = []
    result = extraction.extract(source, [], progress_callback=lambda *event: events.append(event))
    assert provider.calls == []
    assert set(result.field_status.values()) == {FieldStatus.NOT_RETRIEVED}
    assert result.retrieval_diagnostics["field_diagnostics"]["limite_maximo_garantia"]["candidate_count"] == 0
    assert any(issue.code == "retrieval_incomplete" and issue.field_name == "limite_maximo_garantia"
               for issue in result.issues)
    assert len(events) == (14 if routed else 7)
    assert all(event[3] is False for event in events)


@pytest.mark.parametrize("routed", [False, True])
def test_cold_and_completed_cache_use_same_progress_contract_and_preserve_evidence(tmp_path, routed):
    source = document()
    clauses = SegmentationAgent(gateway=None).segment(source)
    provider, first_events = OfflineProvider(), []
    result = agent(provider, tmp_path, routed=routed).extract(source, clauses,
        progress_callback=lambda *event: first_events.append(event))
    assert result.policy.side_a.valor == "Side A concedida"
    assert result.policy.side_a.trecho_origem == "Side A concedida."
    assert result.policy.side_a.pagina == 1
    assert result.field_status["side_a"] == FieldStatus.FOUND
    replay, cached_events = OfflineProvider(forbidden=True), []
    cached = agent(replay, tmp_path, routed=routed).extract(source, clauses,
        progress_callback=lambda *event: cached_events.append(event))
    assert cached.policy == result.policy
    assert cached.field_status == result.field_status
    assert cached.retrieval_diagnostics["aggregate_cache_hit"] is True
    assert cached.retrieval_diagnostics["calls_executed"] == 0
    assert replay.calls == []
    expected = list(SEMANTIC_FIELD_GROUPS if routed else FIELD_GROUPS)
    assert [event[2] for event in first_events] == [event[2] for event in cached_events] == expected
    assert len(expected) == len(set(expected)) == (14 if routed else 7)
    assert sum(event[3] for event in cached_events) == 1
    assert all(event[1] == len(expected) for event in first_events + cached_events)
    if routed:
        sent_groups = {json.loads(call["messages"][1]["content"])["group_id"] for call in provider.calls}
        assert sent_groups <= set(SEMANTIC_FIELD_GROUPS)
        assert not {"LIMITS", "CORE_COVERAGES", "SCOPE", "EXTENSIONS_DEFINITIONS"}.intersection(sent_groups)


def test_routed_plans_do_not_duplicate_fields_or_change_source_provenance():
    source = document()
    clauses = SegmentationAgent(gateway=None).segment(source)
    original_pages = [page.model_dump() for page in source.pages]
    original_clauses = [clause.model_dump() for clause in clauses]
    full, focused = prepare_routed_semantic_plans(local.LocalRetrievalIndex.build(source, clauses),
        ExtractionOptimizationSettings(strategy="optimized"))
    assert list(full) == list(focused) == list(SEMANTIC_FIELD_GROUPS)
    names = [name for plan in focused.values() for name in plan.fields]
    assert len(names) == len(set(names)) == 27
    assert [page.model_dump() for page in source.pages] == original_pages
    assert [clause.model_dump() for clause in clauses] == original_clauses
    for group, plan in focused.items():
        assert plan.group_id == group
        assert plan.fields == SEMANTIC_FIELD_GROUPS[group]
        assert len({candidate.chunk_id for candidate in plan.candidates}) == len(plan.candidates)


def test_progress_fix_preserves_pre_bugfix_completed_cache_key():
    source = document()
    clauses = SegmentationAgent(gateway=None).segment(source)
    extraction = agent(OfflineProvider(), routed=True)
    extraction.corpus_hash = hashlib.sha256(json.dumps(
        [page.model_dump(mode="json") for page in source.pages],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    # Captured from checkpoint f8122b0 before changing the grouping/progress code.
    assert extraction._completed_key(source, clauses) == (
        "116c005f3617f88a69869a191625664bec58ec1bf16d2655ac095184f6f1602b")
