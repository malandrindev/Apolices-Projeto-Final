"""Offline usage and validated-session regressions; no SDK is constructed."""
import hashlib
import json
from pathlib import Path

import pytest

from src.llm.usage_reporting import (
    BILLING_NOTE, aggregate_session, load_standard_rates, save_usage_runs, summarize_run,
)
from src.workspace import load_validated_session
from src.schemas.policy import PolicyExtraction
from src.schemas.retrieval import FieldStatus
from src.agents.extraction import Phase2Report
from src.agents.comparison import ComparisonAgent


def event(response_id="resp-test", **updates):
    return {"response_id": response_id, "requested_model": "gpt-5.6-terra",
            "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10,
            "total_tokens": 110, "latency_ms": 2000, "fallback_level": 0,
            "semantic_promotion_new": False, "service_tier": "default", **updates}


RATES = {"gpt-5.6-terra": {"input": 2, "cached_input": .2, "output": 12},
         "gpt-5.6-sol": {"input": 4, "cached_input": .4, "output": 20}}


def test_usage_subset_formula_and_no_implied_free_billing():
    run = summarize_run("a", "Berkley", [event()], rates=RATES,
                        retrieval={"full_local_chunks": 480, "unique_candidate_chunks": 80})
    assert run["input_tokens"] == 100 and run["cached_input_tokens"] == 40
    assert run["uncached_input_tokens"] == 60 and run["total_tokens"] == 110
    assert run["standard_rate_counterfactual_usd"] == .000248
    assert run["context_reduction_percent"] == pytest.approx(83.33333333)
    assert run["processing_tier"] == "não reconciliado para este run"
    assert run["effective_billing"] == "PENDING_DASHBOARD_RECONCILIATION"
    assert run["api_service_tiers"] == ["default"]
    assert BILLING_NOTE == ("O custo efetivo é determinado pelo provedor da API.\n"
                           "Os valores locais representam uso técnico e, quando exibido,\n"
                           "custo contrafactual em tarifa padrão.")


def test_unknown_usage_and_missing_rates_stay_unknown():
    run = summarize_run("a", "AXA", [event(), event("r2", input_tokens=None)])
    assert run["requests"] == 2 and run["input_tokens"] is None
    assert run["known_input_tokens"] == 100 and run["unknown_usage_requests"] == 1
    assert run["uncached_input_tokens"] is None and run["standard_rate_counterfactual_usd"] is None
    bad = summarize_run("b", "test", [event(cached_input_tokens=101)], rates=RATES)
    assert bad["cached_input_tokens"] is None and bad["uncached_input_tokens"] is None


def test_rerun_cache_and_shared_response_session_deduplicate():
    berkley = summarize_run("berkley", "Berkley", [event(), event()], rates=RATES)
    axa = summarize_run("axa", "AXA", [event("axa")], rates=RATES)
    restored = summarize_run("restored", "Shared", [event()], rates=RATES)
    assert berkley["requests"] == 1
    total = aggregate_session([berkley, berkley, axa, restored])
    assert total["run_count"] == 3 and total["requests"] == 2
    assert total["input_tokens"] == 200 and total["total_tokens"] == 220


def test_fallback_escalation_sol_and_latency_are_separate():
    rows = [event("a"), event("b", fallback_level=1),
            event("c", requested_model="gpt-5.6-sol", semantic_promotion_new=True,
                  latency_ms=9000)]
    run = summarize_run("run", "Comparison", rows, rates=RATES)
    assert run["technical_fallbacks"] == 1 and run["semantic_escalations"] == 1
    assert run["sol_invocations"] == 1 and run["requests_by_model"] == {
        "gpt-5.6-terra": 2, "gpt-5.6-sol": 1}
    assert run["llm_runtime_seconds"] == 13
    assert run["median_latency_seconds"] == 2 and run["p95_latency_seconds"] == 9


def test_processing_tier_requires_matching_persisted_proof():
    proof = {"status": "CONFIRMED", "label": "DATA SHARING INCENTIVE — CONFIRMED",
             "run_ids": ["old"], "source": "user_dashboard_reconciliation"}
    assert summarize_run("new", "Run", [event()], processing_tier_evidence=proof)["processing_tier"] != proof["label"]
    assert summarize_run("old", "Run", [event()], processing_tier_evidence=proof)["processing_tier"] == proof["label"]


def test_usage_persistence_preserves_id_and_safe_counterfactual(tmp_path):
    run = summarize_run("a", "Run", [event()], rates=RATES)
    save_usage_runs(tmp_path, [run])
    save_usage_runs(tmp_path, [run])
    paths = list(tmp_path.glob("*.json"))
    assert len(paths) == 1
    loaded = json.loads(paths[0].read_text(encoding="utf-8"))
    assert aggregate_session([run, loaded])["requests"] == 1
    assert load_standard_rates(Path("missing_rates.json")) == {}


def make_validated_package(base):
    import pymupdf
    reports, docs, stages, runs = {}, [], {}, []
    def write(path, value):
        (base / path).parent.mkdir(parents=True, exist_ok=True)
        (base / path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    for number, stage in enumerate(("berkley", "axa"), 1):
        run_id = str(number) * 32
        folder = f"runs/{run_id}"
        path = base / folder / "source.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), stage)
            pdf.save(path)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        report = Phase2Report(source_name=stage + ".pdf", sha256=sha, clause_count=1,
                              policy=PolicyExtraction(),
                              field_status={name: FieldStatus.NOT_RETRIEVED for name in PolicyExtraction.model_fields})
        reports[stage] = report
        result = {"stage": stage, "run_id": run_id, "document_sha256": sha,
                  "report": report.model_dump(mode="json"), "metrics": {}}
        write(folder + "/result.json", result)
        write(folder + "/report.json", result["report"])
        write(folder + "/review.json", {"status": "PASS", "stage": stage, "run_id": run_id,
            "result_sha256": hashlib.sha256((base / folder / "result.json").read_bytes()).hexdigest()})
        write(folder + "/telemetry.json", {"run_id": run_id, "events": [event(stage)]})
        stages[stage] = {"run_id": run_id}
        docs.append({"stage": stage, "pdf_path": folder + "/source.pdf", "sha256": sha,
                     "filename": stage + ".pdf", "report_path": folder + "/report.json",
                     "source_url": "https://example.com/" + stage + ".pdf"})
        runs.append({"stage": stage, "run_id": run_id, "label": stage,
                     "telemetry_path": folder + "/telemetry.json"})
    comparison = ComparisonAgent(gateway=None, model_strong="offline").compare(reports["berkley"], reports["axa"])
    run_id = "3" * 32
    folder = f"runs/{run_id}"
    result = {"stage": "comparison", "run_id": run_id, "comparison": comparison.model_dump(mode="json")}
    write(folder + "/result.json", result)
    write(folder + "/comparison.json", result["comparison"])
    write(folder + "/review.json", {"status": "PASS", "stage": "comparison", "run_id": run_id,
        "result_sha256": hashlib.sha256((base / folder / "result.json").read_bytes()).hexdigest()})
    write(folder + "/telemetry.json", {"run_id": run_id, "events": []})
    stages["comparison"] = {"run_id": run_id}
    runs.append({"stage": "comparison", "run_id": run_id, "label": "Comparison",
                 "telemetry_path": folder + "/telemetry.json"})
    index = {"schema_version": 1, "quality_status": "PASS", "stages": stages,
             "documents": docs, "reference_sha": reports["berkley"].sha256,
             "comparisons": [{"path": folder + "/comparison.json"}], "runs": runs}
    write("validated_ui_session.json", index)
    return index


def test_validated_session_loads_without_inference_and_preserves_states(tmp_path):
    make_validated_package(tmp_path)
    loaded = load_validated_session(tmp_path)
    assert len(loaded["documents"]) == 2 and len(loaded["comparisons"]) == 1
    assert all(len(report.field_status) == 27 for report in loaded["reports"].values())
    assert len(loaded["runs"]) == 3 and loaded["quality_status"] == "PASS"


@pytest.mark.parametrize("corrupt", ["source", "result", "review", "report", "traversal"])
def test_validated_session_rejects_unreviewed_or_mismatched_bytes(tmp_path, corrupt):
    index = make_validated_package(tmp_path)
    doc = index["documents"][0]
    if corrupt == "source":
        (tmp_path / doc["pdf_path"]).write_bytes(b"tampered")
    elif corrupt == "result":
        path = tmp_path / "runs" / ("1" * 32) / "result.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["unreviewed"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
    elif corrupt == "review":
        path = tmp_path / "runs" / ("1" * 32) / "review.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["status"] = "FAIL"
        path.write_text(json.dumps(value), encoding="utf-8")
    elif corrupt == "report":
        path = tmp_path / doc["report_path"]
        value = json.loads(path.read_text(encoding="utf-8"))
        value["field_status"].pop("side_c")
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        index["documents"][0]["pdf_path"] = "../outside.pdf"
        (tmp_path / "validated_ui_session.json").write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValueError):
        load_validated_session(tmp_path)


def test_validated_ui_analyze_reuses_signature_before_gateway_configuration():
    import ast
    from types import SimpleNamespace
    tree = ast.parse(Path("interface/app.py").read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "analyze_documents")
    calls = []
    existing = {"signature": ("ref", "sources"), "stats": {"restored_validated_session": True}}
    def reject(*args, **kwargs):
        raise AssertionError("Cache-backed UI must not construct a provider.")
    st = SimpleNamespace(session_state={"workspace_result": existing}, success=calls.append)
    namespace = {"st": st, "workspace_signature": lambda docs, ref: ("ref", "sources"),
                 "get_settings": reject, "get_gateway": reject}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "interface/app.py", "exec"), namespace)
    namespace["analyze_documents"]([], "ref", "openai", None, 30)
    assert len(calls) == 1 and st.session_state["workspace_result"] is existing


@pytest.mark.parametrize("missing_duration", [False, True])
def test_restored_ui_uses_recorded_performance_duration_without_inference(tmp_path, missing_duration):
    import ast
    import tempfile
    from types import SimpleNamespace
    tree = ast.parse(Path("interface/app.py").read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "restore_validated_session")
    documents = [SimpleNamespace(filename=stage + ".pdf", data=b"local-cache", sha256=stage)
                 for stage in ("berkley", "axa")]
    reports = {stage: SimpleNamespace(clause_count=1, issues=[]) for stage in ("berkley", "axa")}
    runs = [{"run_id": str(index), "label": stage, "events": [event(stage)],
             "retrieval": {}, "metrics": {"performance": {"total_runtime_seconds": index + 1}}}
            for index, stage in enumerate(("berkley", "axa", "comparison"), 1)]
    if missing_duration:
        runs[-1]["metrics"] = {}
    loaded = {"documents": documents, "reports": reports, "reference_sha": "berkley",
              "comparisons": [SimpleNamespace(document_id_b="axa", source_references={})],
              "runs": runs, "processing_tier_evidence": {}}
    state = {}
    namespace = {
        "ROOT": tmp_path, "Path": Path, "tempfile": tempfile,
        "st": SimpleNamespace(session_state=state),
        "load_validated_session": lambda directory: loaded,
        "process_document": lambda path, settings: (None, SimpleNamespace()),
        "infer_metadata": lambda processed, report: {},
        "ComparisonReportAgent": lambda: SimpleNamespace(generate=lambda *args, **kwargs: object()),
        "load_standard_rates": lambda path: RATES, "summarize_run": summarize_run,
        "aggregate_session": aggregate_session, "invalidate": lambda: None,
        "workspace_signature": lambda docs, ref: (ref, tuple(doc.sha256 for doc in docs)),
        "register_usage_runs": lambda summaries, directory: state.update(workspace_usage_runs=summaries),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "interface/app.py", "exec"), namespace)
    namespace["restore_validated_session"](SimpleNamespace(processed_dir=tmp_path))
    summaries = state["workspace_usage_runs"]
    assert summaries[0]["duration_seconds"] == 2
    assert summaries[1]["duration_seconds"] == 3
    assert summaries[2]["duration_seconds"] == (None if missing_duration else 4)
    assert state["workspace_result"]["stats"]["duration_seconds"] == (None if missing_duration else 9)
    assert state["workspace_result"]["stats"]["restored_validated_session"] is True

@pytest.mark.parametrize("tamper", [False, True])
def test_validated_reader_applies_only_sha_bound_conservative_review(tmp_path, tamper):
    from src.agents.semantic_completeness import apply_reviewed_conservative_statuses
    index = make_validated_package(tmp_path)
    folder = tmp_path / "runs" / ("2" * 32)
    raw = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    raw["report"]["field_status"]["definicoes_relevantes"] = "FOUND"
    (folder / "result.json").write_text(json.dumps(raw), encoding="utf-8")
    (folder / "report.json").write_text(json.dumps(raw["report"]), encoding="utf-8")
    review = json.loads((folder / "review.json").read_text(encoding="utf-8"))
    review.update(result_sha256=hashlib.sha256((folder / "result.json").read_bytes()).hexdigest(),
        conservative_field_overrides={"definicoes_relevantes": {
            "status": "AMBIGUOUS", "reason": "Independent scope conflict review."}})
    projected = apply_reviewed_conservative_statuses(raw["report"], review)
    if tamper:
        projected["policy"]["definicoes_relevantes"]["valor"] = "Invented."
    (folder / "reviewed_report.json").write_text(json.dumps(projected), encoding="utf-8")
    review["reviewed_report_sha256"] = hashlib.sha256((folder / "reviewed_report.json").read_bytes()).hexdigest()
    (folder / "review.json").write_text(json.dumps(review), encoding="utf-8")
    if tamper:
        with pytest.raises(ValueError, match="conservadora"):
            load_validated_session(tmp_path)
    else:
        loaded = load_validated_session(tmp_path)
        assert loaded["reports"][index["documents"][1]["sha256"]].field_status[
            "definicoes_relevantes"] == FieldStatus.AMBIGUOUS
        assert json.loads((folder / "result.json").read_text(encoding="utf-8")) == raw
