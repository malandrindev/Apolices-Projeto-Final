"""Offline final-release audit. Runtime evidence is read; providers are never sent."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_files():
    paths = [p for folder in ("src", "interface") for p in (ROOT / folder).rglob("*.py")]
    paths += [p for p in (ROOT / "data" / "processed").rglob("*.json")
              if "final_release" not in p.parts]
    paths += [ROOT / name for name in (
        "docs/OPENAI_BILLING_AUDIT.md", "docs/COMPLIMENTARY_MODEL_VALIDATION.md",
        "docs/OPENAI_PRICES_2026-10-04.json")]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in paths if p.is_file()}


def integrity(before):
    changed = [name for name, sha in before.items()
               if not (ROOT / name).is_file() or digest(ROOT / name) != sha]
    return {"protected_files": len(before), "preservation_mismatches": changed,
            "status": "PASS" if not changed else "FAIL"}


def quality(out):
    import openai
    import groq
    import httpx
    import pytest

    blocked = {"sdk_constructions": 0, "provider_http": 0}
    def no_sdk(*args, **kwargs):
        blocked["sdk_constructions"] += 1
        raise AssertionError("Final release: provider SDK construction blocked.")
    def no_http(*args, **kwargs):
        blocked["provider_http"] += 1
        raise AssertionError("Final release: outbound HTTP transport blocked.")
    async def no_async_http(*args, **kwargs):
        return no_http(*args, **kwargs)

    openai.OpenAI.__init__ = no_sdk
    openai.AsyncOpenAI.__init__ = no_sdk
    groq.Groq.__init__ = no_sdk
    groq.AsyncGroq.__init__ = no_sdk
    httpx.Client.send = no_http
    httpx.AsyncClient.send = no_async_http

    class Counts:
        def __init__(self):
            self.results = Counter()
        def pytest_terminal_summary(self, terminalreporter):
            self.results = Counter({key: len(value) for key, value in terminalreporter.stats.items() if key})

    counts = Counts()
    started = time.perf_counter()
    with (out / "pytest.log").open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        pytest_code = pytest.main(["-q", "tests"], plugins=[counts])
    elapsed = time.perf_counter() - started
    compile_result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "src", "interface", "scripts", "tests"],
        cwd=ROOT, capture_output=True, text=True)
    (out / "compileall.log").write_text(compile_result.stdout + compile_result.stderr, encoding="utf-8")
    import_names = ["src.config", "src.pipeline", "src.demo", "src.workspace",
                    "src.workspace_recovery", "src.agents.grouped_extraction",
                    "src.agents.comparison", "src.llm.model_routing",
                    "src.storage.sqlite_repo", "scripts.build_demo_dataset"]
    import_failures = []
    for name in import_names:
        try:
            importlib.import_module(name)
        except Exception as error:
            import_failures.append({"module": name, "error_type": type(error).__name__})
    pip_result = subprocess.run([sys.executable, "-m", "pip", "check"], cwd=ROOT,
                                capture_output=True, text=True)
    (out / "pip_check.log").write_text(pip_result.stdout + pip_result.stderr, encoding="utf-8")
    result = {"pytest_exit": int(pytest_code), "pytest_counts": dict(counts.results),
              "duration_seconds": round(elapsed, 2), "blocked_attempts": blocked,
              "compileall_exit": compile_result.returncode,
              "imports": len(import_names), "import_failures": import_failures,
              "pip_check_exit": pip_result.returncode}
    result["status"] = "PASS" if (pytest_code == compile_result.returncode == pip_result.returncode == 0
                                 and not import_failures and not any(blocked.values())) else "FAIL"
    write_json(out / "quality.json", result)
    return result


def evidence(out):
    from src.agents.ocr import ProcessedDocument
    from src.agents.extraction import Phase2Report
    from src.schemas.comparison import PolicyComparison
    from src.agents._evidence import normalize
    from src.product_presentation import comparison_counts
    from src.workspace import build_matrix

    ledger_path = ROOT / "data/processed/workspace_usage/9086f70de148448f8c84b4223a446dd5-attempt-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    events = ledger["events"]
    paths = [
        ("Allianz", "a121cd8de77198f5d926a09a065d4ad3ba76adbee8a5237f866871a0ce72a5c8.json",
         "a2eb63a41032ff1fc481227d11a4bb5fb2b6cdfa0c78f8ab5726ce82bb28da58.json"),
        ("Porto", "5341e1fbda5abb8fc2db115fa1c909c09489853ec948fea678f35510a27dd538.json",
         "9b82127c610c7cac31948441fd648ce9fdabb8e46caf653256fc8582b022136d.json"),
    ]
    documents = []
    reports = {}
    quote_failures = []
    for brand, processed_name, report_name in paths:
        processed_path, report_path = (ROOT / "data/processed" / name for name in (processed_name, report_name))
        processed = ProcessedDocument.model_validate_json(processed_path.read_text(encoding="utf-8"))
        report = Phase2Report.model_validate_json(report_path.read_text(encoding="utf-8"))
        assert processed.sha256 == report.sha256
        reports[report.sha256] = report
        quotes = []
        for field, value in report.policy.model_dump(mode="json").items():
            page = value.get("pagina")
            if page is None:
                continue
            literal = (1 <= page <= len(processed.pages)
                       and normalize(value["trecho_origem"]) in normalize(processed.pages[page - 1].text))
            quotes.append({"field_name": field, "source_page": page, "literal": literal,
                           "field_status": report.field_status.get(field).value})
            if not literal:
                quote_failures.append({"document": brand, "field_name": field, "source_page": page})
        diagnostic = report.retrieval_diagnostics
        documents.append({
            "brand": brand, "filename": processed.source_name, "document_sha256": processed.sha256,
            "pages": len(processed.pages),
            "extraction_methods": dict(Counter(page.extraction_method for page in processed.pages)),
            "ocr_record": processed_path.relative_to(ROOT).as_posix(), "ocr_record_sha256": digest(processed_path),
            "report_record": report_path.relative_to(ROOT).as_posix(), "report_record_sha256": digest(report_path),
            "chunks": report.clause_count, "field_count": len(type(report.policy).model_fields),
            "field_status_counts": dict(Counter(status.value for status in report.field_status.values())),
            "issues": len(report.issues), "issue_codes": dict(Counter(item.code for item in report.issues)),
            "partial": diagnostic.get("partial"), "stop_reason": diagnostic.get("stop_reason"),
            "calls_executed": diagnostic.get("calls_executed"), "cache_hits": diagnostic.get("cache_hits"),
            "page_evidence": quotes,
            "individual_policy_specifications_claimed": False,
            "note": "Recorte de quatro páginas de condições gerais; não corresponde à íntegra de 75/52 páginas.",
        })
    comparison_path = ROOT / "data/processed/comparacao_1078704469057bd9192f.json"
    comparison = PolicyComparison.model_validate_json(comparison_path.read_text(encoding="utf-8"))
    assert len(comparison.differences) == 27 and comparison.document_id_a in reports and comparison.document_id_b in reports
    comparison_quotes = []
    for row in comparison.differences:
        for side, sha, citation in (("a", comparison.document_id_a, row.citation_a),
                                   ("b", comparison.document_id_b, row.citation_b)):
            original = getattr(reports[sha].policy, row.field_name)
            if (citation.page_number, citation.excerpt) != (original.pagina, original.trecho_origem):
                comparison_quotes.append({"field_name": row.field_name, "side": side, "matches_input": False})
    summary = comparison_counts(build_matrix(reports[comparison.document_id_a],
                                             [reports[comparison.document_id_b]], [comparison]))
    result = {
        "status": "PASS" if not quote_failures and not comparison_quotes else "REVIEW_REQUIRED",
        "evidence_kind": "persisted_real_execution", "new_provider_requests_during_audit": 0,
        "schema_valid": True, "run_id": ledger["run_id"], "workspace_status": ledger["workspace_status"],
        "ledger": ledger_path.relative_to(ROOT).as_posix(), "ledger_sha256": digest(ledger_path),
        "http_attempts": ledger["http_attempts"], "events": len(events),
        "returned_response_ids": sum(bool(event.get("response_id")) for event in events),
        "unique_response_ids": len({event["response_id"] for event in events if event.get("response_id")}),
        "results": dict(Counter(event.get("result_status") for event in events)),
        "models": dict(Counter(event.get("requested_model") for event in events)),
        "tokens": {key: sum(event.get(key) or 0 for event in events)
                   for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")},
        "quality_outcomes": dict(Counter(event.get("evidence_validation_status") for event in events)),
        "start_utc": events[0]["timestamp"], "end_utc": events[-1]["timestamp"],
        "fallback_count": ledger["summary"]["fallback_count"],
        "semantic_escalation_count": ledger["summary"]["semantic_escalation_count"],
        "provider_transport_failures": ledger["summary"]["provider_transport_failures"],
        "documents": documents,
        "comparison": {"path": comparison_path.relative_to(ROOT).as_posix(), "sha256": digest(comparison_path),
                       "fields": len(comparison.differences),
                       "classifications": dict(Counter(row.classification.value for row in comparison.differences)),
                       "business_summary": summary, "citation_input_mismatches": comparison_quotes},
        "literal_quote_failures": quote_failures,
        "billing": "Efetivo não reconciliado; cached input é subconjunto de input.",
        "limitations": [
            "Execução concluída não comprova precisão semântica perfeita.",
            "Estados conservadores e revisão humana permanecem necessários.",
            "Nenhuma classificação equivale a escolha global de melhor apólice.",
            "Registros brutos e documentos originais não são automaticamente publicáveis.",
        ],
    }
    write_json(out / "persisted_e2e_evidence.json", result)
    return result


def browser(out, mode, preservation):
    script = ROOT / "scripts" / ("check_product_browser.py" if mode == "demo" else "check_workspace_browser.py")
    source = script.read_text(encoding="utf-8")
    qa = out / ("browser_demo" if mode == "demo" else "browser_validated_cache")
    if mode == "demo":
        source = source.replace('qa = ROOT / "data" / "processed" / "product_browser_qa"',
                                "qa = Path(" + repr(str(qa)) + ")")
        source = source.replace('preservation = ROOT / "data/processed/product_simplification_preservation.json"',
                                "preservation = Path(" + repr(str(preservation)) + ")")
    else:
        source = source.replace('qa = ROOT / "data" / "processed" / "workspace_browser_qa_product_validated"',
                                "qa = Path(" + repr(str(qa)) + ")")
        source = source.replace("config.get_ingestion_settings=original",
                                "config.get_ingestion_settings=lambda:replace(original(),processed_dir=Path("
                                + repr((qa / "cache").as_posix()) + "))")
        cache_hook = """providers.get_gateway=reject_provider
from src.agents.ocr import OcrAgent,ProcessedDocument
def use_preserved_ocr(self,document):
    for path in Path({str(ROOT/'data/processed')!r}).glob('*.json'):
        try:
            raw=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(raw,dict) or raw.get('sha256')!=document.sha256 or not raw.get('pages'):
                continue
            cached=ProcessedDocument.model_validate(raw)
            if (cached.size_bytes==document.size_bytes and cached.media_type==document.media_type
                    and len(cached.pages)==document.page_count):
                return cached.model_copy(update={{'source_name':document.source_name,'cache_hit':True}})
        except (ValueError,OSError):
            continue
    raise RuntimeError('Final cache QA requires preserved OCR; local reprocessing blocked.')
OcrAgent.extract=use_preserved_ocr"""
        source = source.replace("providers.get_gateway=reject_provider", cache_hook)
    source = source.replace("httpx.Client.request=reject_http", "httpx.Client.request=reject_http\nhttpx.Client.send=reject_http")
    source = source.replace("httpx.AsyncClient.request=reject_http",
                            "httpx.AsyncClient.request=reject_http\nhttpx.AsyncClient.send=reject_http")
    source = source.replace("httpx.Client.request=reject_provider",
                            "httpx.Client.request=reject_provider\nhttpx.Client.send=reject_provider")
    source = source.replace("httpx.AsyncClient.request=reject_provider",
                            "httpx.AsyncClient.request=reject_provider\nhttpx.AsyncClient.send=reject_provider")
    namespace = {"__name__": "_final_release_offline_browser", "__file__": str(script)}
    exec(compile(source, str(script), "exec"), namespace)
    args = argparse.Namespace(port=8516 if mode == "demo" else 8517)
    function = namespace["run"] if mode == "demo" else namespace["run_validated_qa"]
    with (out / ("browser_" + mode + ".log")).open("w", encoding="utf-8") as log:
        with redirect_stdout(log), redirect_stderr(log):
            code = function(args)
    return {"status": "PASS" if code == 0 else "FAIL", "diagnostics": (qa / "diagnostics.json").relative_to(ROOT).as_posix()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("quality", "evidence", "browser-demo", "browser-cache"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    out = args.output or ROOT / "data/processed/final_release/audit" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out.resolve()
    if not out.is_relative_to((ROOT / "data/processed/final_release/audit").resolve()):
        raise ValueError("Audit output must remain inside the final release audit directory.")
    out.mkdir(parents=True, exist_ok=True)
    before = protected_files()
    preservation = out / "preservation.json"
    write_json(preservation, {"checkpoint": subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "files": before})
    if args.mode == "quality":
        result = quality(out)
    elif args.mode == "evidence":
        result = evidence(out)
    else:
        result = browser(out, "demo" if args.mode == "browser-demo" else "cache", preservation)
    protected = integrity(before)
    write_json(out / (args.mode + "_integrity.json"), protected)
    print(json.dumps({"output": out.relative_to(ROOT).as_posix(), "result": result, "integrity": protected}, ensure_ascii=True))
    return 0 if result["status"] == "PASS" and protected["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
