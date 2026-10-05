"""Actual Streamlit/Edge QA using offline fixtures or reviewed real cache.

Provider SDKs and dispatch are blocked. Screenshots and operational downloads
stay in their local QA directory; final academic artifacts remain frozen.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymupdf
from playwright.sync_api import sync_playwright
from scripts.smoke_end_to_end import create_synthetic_documents, SYNTHETIC_B_TEXT

PORT = 8502



def run_validated_qa(args) -> int:
    """View the approved real package with every provider dispatch blocked."""
    import hashlib
    from src.schemas.policy import PolicyExtraction
    from src.workspace import load_validated_session
    from src.llm.usage_reporting import aggregate_session, summarize_run

    base = ROOT / "data" / "processed" / "fasttrack_validation"
    loaded = load_validated_session(base)
    expected_comparison = loaded["comparisons"][0].model_dump(mode="json")
    expected_usage = aggregate_session([
        summarize_run(run["run_id"], run["label"], run["events"]) for run in loaded["runs"]
    ])
    tracked = [base / "validated_ui_session.json", base / "session_manifest.json"]
    for run in loaded["runs"]:
        tracked.extend((base / "runs" / run["run_id"] / filename)
                       for filename in ("result.json", "review.json", "telemetry.json"))
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in tracked if path.exists()}
    qa = ROOT / "data" / "processed" / "workspace_browser_qa_product_validated"
    qa.mkdir(parents=True, exist_ok=True)
    calls_path = qa / "calls.json"
    state_path = qa / "ui_state.json"
    calls_path.write_text('{"attempted_provider_operations":0,"http_calls":0}', encoding="utf-8")
    wrapper = qa / "offline_validated_app.py"
    wrapper.write_text(f"""import runpy, sys, json
from pathlib import Path
from dataclasses import replace
sys.path.insert(0,{str(ROOT)!r})
import openai, groq, httpx
def reject_provider(*args,**kwargs):
    path=Path({str(calls_path)!r})
    value=json.loads(path.read_text(encoding="utf-8"))
    value["attempted_provider_operations"]+=1
    path.write_text(json.dumps(value),encoding="utf-8")
    raise RuntimeError("QA: provider construction and outbound requests are blocked.")
openai.OpenAI.__init__=reject_provider
groq.Groq.__init__=reject_provider
httpx.Client.request=reject_provider
httpx.AsyncClient.request=reject_provider
import src.config as config
import src.llm.providers as providers
providers.get_gateway=reject_provider
original=config.get_ingestion_settings
config.get_ingestion_settings=original
runpy.run_path({str(ROOT/'interface'/'app.py')!r},run_name="__main__")
import streamlit as st
from src.llm.usage_reporting import aggregate_session
result=st.session_state.get("workspace_result") or {{}}
snapshot={{"documents":len(st.session_state["workspace_docs"]),
          "restored":result.get("stats",{{}}).get("restored_validated_session",False),
          "usage":aggregate_session(st.session_state.get("workspace_usage_runs",{{}}).values()),
          "run_durations":[run.get("duration_seconds") for run in result.get("stats",{{}}).get("usage_runs",[])]}}
Path({str(state_path)!r}).write_text(json.dumps(snapshot,ensure_ascii=False),encoding="utf-8")
""", encoding="utf-8")
    errors, downloads = [], []
    log = (qa / "streamlit.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(wrapper),
         "--server.address", "127.0.0.1", "--server.port", str(args.port),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    def assert_offline():
        if json.loads(calls_path.read_text())["attempted_provider_operations"]:
            raise RuntimeError("A validated-cache action attempted a provider operation.")
    def assert_usage():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        for key in ("requests", "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
            if state["usage"][key] != expected_usage[key]:
                raise RuntimeError("Validated UI usage differs from the reviewed provider ledger.")
        return state
    try:
        for _ in range(80):
            try:
                with urlopen(f"http://127.0.0.1:{args.port}/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.25)
        else:
            raise RuntimeError("Local validated QA Streamlit did not become healthy.")
        with sync_playwright() as engine:
            browser = engine.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width":1440,"height":1000},accept_downloads=True)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{args.port}", wait_until="networkidle")
            page.get_by_role("heading",name="Compare apólices D&O",exact=False).wait_for(timeout=30000)
            advanced = page.get_by_text("Configuração avançada",exact=True)
            if advanced.locator("xpath=ancestor::details[1]").get_attribute("open") is not None:
                raise RuntimeError("Advanced settings must start collapsed.")
            advanced.click()
            page.get_by_role("button",name="Carregar sessão validada",exact=True).click()
            try:
                page.get_by_role("tab",name="Resumo",exact=True).wait_for(timeout=60000)
            except Exception:
                (qa/"failure_ui.txt").write_text(page.locator("body").inner_text(),encoding="utf-8")
                page.screenshot(path=str(qa/"failure.png"),full_page=True)
                raise
            page.wait_for_timeout(700)
            page.get_by_text("Resultados locais aprovados.",exact=False).wait_for(timeout=15000)
            state = assert_usage()
            if not state["restored"] or state["documents"] != 2:
                raise RuntimeError("The approved real session was not restored.")
            assert_offline()
            page.get_by_text("Documentos desta comparação",exact=True).first.click()
            page.get_by_role("button",name="Comparar apólices",exact=True).click()
            page.get_by_text("Resultados locais aprovados reutilizados; nenhuma nova chamada de IA.",exact=True).wait_for(timeout=15000)
            page.wait_for_timeout(600)
            assert_usage()
            assert_offline()
            page.screenshot(path=str(qa/"01_real_results.png"),full_page=True)
            for tab_name in ("Coberturas","Limites & Franquias","Exclusões","Cláusulas","Evidências"):
                page.get_by_role("tab",name=tab_name,exact=True).click()
                page.wait_for_timeout(200)
                if page.locator('[data-testid="stException"]').count():
                    raise RuntimeError("An approved-session results tab raised an exception.")
            page.get_by_role("tab",name="Evidências",exact=True).click()
            page.get_by_role("heading",name="Conferir evidências",exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(qa/"02_real_evidence.png"),full_page=True)
            page.get_by_role("tab",name="Resumo",exact=True).click()
            page.get_by_text("Detalhes técnicos",exact=True).first.click()
            technical = page.get_by_text("Uso da IA",exact=True)
            if technical.locator("xpath=ancestor::details[1]").get_attribute("open") is not None:
                raise RuntimeError("AI usage details must start collapsed.")
            technical.click()
            page.get_by_role("heading",name="SESSION TOTAL",exact=True).wait_for(timeout=15000)
            page.get_by_text("O custo efetivo é determinado pelo provedor da API.",exact=False).first.wait_for(timeout=15000)
            if page.get_by_text("Total runtime:",exact=False).count() != len(loaded["runs"]):
                raise RuntimeError("Recorded run durations are missing from the usage panel.")
            page.screenshot(path=str(qa/"03_real_ai_usage.png"),full_page=True)
            page.get_by_text("Outros formatos",exact=True).first.click()
            for label in ("Markdown","PDF","JSON"):
                report_header = page.get_by_text("Outros formatos",exact=True).first
                if report_header.locator("xpath=ancestor::details[1]").get_attribute("open") is None:
                    report_header.click()
                button = (page.get_by_role("button",name=re.compile(r"^Exportar relatório")).first if label == "PDF" else page.get_by_role("button",name=f"Baixar {label}",exact=True).first)
                button.scroll_into_view_if_needed()
                with page.expect_download(timeout=15000) as event:
                    button.click()
                download = event.value
                target = qa / "downloads" / download.suggested_filename
                target.parent.mkdir(exist_ok=True)
                download.save_as(target)
                if not target.stat().st_size:
                    raise RuntimeError("An approved-session report download is empty.")
                downloads.append(str(target.relative_to(ROOT)))
                page.wait_for_timeout(500)
            downloaded = json.loads((ROOT / next(name for name in downloads if name.endswith(".json"))).read_text(encoding="utf-8"))
            if downloaded != expected_comparison:
                raise RuntimeError("The downloaded comparison differs from the approved result.")
            if {item["field_name"] for item in downloaded["differences"]} != set(PolicyExtraction.model_fields):
                raise RuntimeError("The approved comparison download does not contain all 27 fields.")
            optional = page.get_by_text("Revisão humana — opcional",exact=True).first
            if optional.locator("xpath=ancestor::details[1]").get_attribute("open") is None:
                optional.click()
            page.get_by_role("button",name="Precisa de revisão",exact=True).click()
            page.wait_for_timeout(600)
            assert_usage()
            assert_offline()
            page.screenshot(path=str(qa/"04_real_review.png"),full_page=True)
            # Reloading the same approved runs must keep the aggregate unchanged.
            advanced = page.get_by_text("Configuração avançada",exact=True)
            if advanced.locator("xpath=ancestor::details[1]").get_attribute("open") is None:
                advanced.click()
            page.get_by_role("button",name="Carregar sessão validada",exact=True).click()
            page.get_by_role("tab",name="Resumo",exact=True).wait_for(timeout=60000)
            page.wait_for_timeout(600)
            assert_usage()
            assert_offline()
            page.get_by_role("button",name="Nova comparação",exact=True).click()
            page.get_by_text("O primeiro documento será a referência.",exact=False).wait_for(timeout=15000)
            page.wait_for_timeout(500)
            assert_offline()
            if page.locator('[data-testid="stException"]').count() or errors:
                raise RuntimeError("Approved-session browser QA raised an error.")
            context.close()
            browser.close()
        after = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in before}
        if before != after:
            raise RuntimeError("Approved review/result/ledger records changed during browser QA.")
        result = {
            "status":"PASS","mode":"actual_streamlit_edge_reviewed_real_cache",
            "workspace_documents":2,"reference_candidates":1,"tabs":6,
            "http_calls":0,"attempted_provider_operations":0,"new_inference_calls":0,
            "validated_session_restored":True,"analyze_reused_validated_session":True,
            "ai_usage_panel":True,"session_aggregation_matches_ledger":True,
            "recorded_run_durations":state["run_durations"],
            "approved_requests_displayed":expected_usage["requests"],
            "review_reload_download_no_inference":True,"new_comparison_reset":True,
            "advanced_settings_collapsed":True,"reviewed_records_unchanged":True,
            "downloads":downloads,"browser_errors":errors,
            "screenshots":["01_real_results.png","02_real_evidence.png","03_real_ai_usage.png","04_real_review.png"],
            "final_artifacts_regenerated":False,
        }
        (qa/"diagnostics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=True))
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--retrieval-status", action="store_true", help="Inject offline uncertainty fixture to check E1 presentation.")
    parser.add_argument("--technical-status", action="store_true", help="Inject offline technical-unavailability fixture for E2 QA.")
    parser.add_argument("--validated-session", action="store_true", help="Inspect reviewed real FAST-TRACK cache with provider dispatch blocked.")
    args = parser.parse_args()
    if args.validated_session:
        return run_validated_qa(args)
    qa = ROOT / "data" / "processed" / "workspace_browser_qa_product_sources"
    qa.mkdir(parents=True, exist_ok=True)
    a, b = create_synthetic_documents(qa / "inputs")
    c = qa / "inputs" / "sintetica_C_textual.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), SYNTHETIC_B_TEXT.replace("Exemplo B", "Exemplo C").replace("SINTETICO B", "SINTETICO C"), fontsize=11)
        document.save(c)
    public_fixtures = []
    for label in ("D", "E"):
        path = qa / "inputs" / ("sintetica_" + label + "_textual.pdf")
        with pymupdf.open() as document:
            document.new_page().insert_text((72, 72), SYNTHETIC_B_TEXT.replace("Exemplo B", "Exemplo " + label).replace("SINTETICO B", "SINTETICO " + label), fontsize=11)
            document.save(path)
        public_fixtures.append(path)
    wrapper = qa / "offline_app.py"
    wrapper.write_text(f"""import runpy, sys, json
from pathlib import Path
from dataclasses import replace
sys.path.insert(0,{str(ROOT)!r})
import openai, groq
def reject_provider(*args,**kwargs):
    raise RuntimeError("QA: real provider SDK construction is blocked.")
openai.OpenAI.__init__=reject_provider
groq.Groq.__init__=reject_provider
import src.config as config
import src.llm.providers as providers
import src.sources as sources
from dataclasses import replace as source_replace
def offline_public_resolve(self,url,refresh=False):
    catalog_url=sources.PUBLIC_CATALOG[0]["source_url"]
    if url not in {{catalog_url,"https://example.com/offline-fixture.pdf"}}:
        raise sources.SourceError("QA accepts only the two fixed offline fixture URLs.")
    source_path=Path({str(public_fixtures[0])!r}) if url==catalog_url else Path({str(public_fixtures[1])!r})
    resolved=sources.UploadSource().resolve(source_path.name,source_path.read_bytes())
    return source_replace(resolved,origin="public_url",source_url=url,access_date="offline_qa_fixture",cache_hit=True)
sources.PublicURLSource.resolve=offline_public_resolve
from scripts.smoke_end_to_end import OfflineSyntheticGateway
original=config.get_ingestion_settings
config.get_ingestion_settings=lambda:replace(original(),processed_dir=Path({str(qa/'cache')!r}),min_native_chars=20)
class RecordingGateway(OfflineSyntheticGateway):
    def __init__(self):self.logical_calls=0;self.events=[]
    def complete(self,**kwargs):
        self.logical_calls+=1
        Path({str(qa/'calls.json')!r}).write_text(json.dumps({{"logical_calls":self.logical_calls,"http_calls":0}}),encoding="utf-8")
        return super().complete(**kwargs)
gateway=RecordingGateway()
providers.get_gateway=lambda *args,**kwargs:gateway
if {(args.retrieval_status or args.technical_status)!r}:
    import src.pipeline as pipeline
    from src.schemas.retrieval import FieldStatus
    original_structure=pipeline.process_and_structure_document
    def with_retrieval_fixture(*args,**kwargs):
        result=original_structure(*args,**kwargs)
        result[2].field_status["side_c"]=FieldStatus.TECHNICAL_UNAVAILABLE if {args.technical_status!r} else FieldStatus.NOT_RETRIEVED
        result[2].retrieval_diagnostics={{"strategy":"offline_qa_fixture","full_local_corpus_retained":True}}
        return result
    pipeline.process_and_structure_document=with_retrieval_fixture
runpy.run_path({str(ROOT/'interface'/'app.py')!r},run_name="__main__")
""", encoding="utf-8")
    (qa / "calls.json").write_text('{"logical_calls":0,"http_calls":0}', encoding="utf-8")
    errors, downloads = [], []
    log = (qa / "streamlit.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(wrapper),
         "--server.address", "127.0.0.1", "--server.port", str(args.port),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    def call_count():
        path = qa / "calls.json"
        return json.loads(path.read_text())["logical_calls"] if path.exists() else 0
    try:
        for _ in range(80):
            try:
                with urlopen(f"http://127.0.0.1:{args.port}/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.25)
        else:
            raise RuntimeError("Local QA Streamlit did not become healthy.")
        with sync_playwright() as engine:
            browser = engine.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width":1440,"height":1000},accept_downloads=True)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{args.port}", wait_until="networkidle")
            page.get_by_role("heading",name="Compare apólices D&O",exact=False).wait_for(timeout=30000)
            page.wait_for_timeout(1000)
            page.screenshot(path=str(qa/"01_home.png"),full_page=True)
            advanced = page.get_by_text("Configuração avançada", exact=True).locator("xpath=ancestor::details[1]")
            if advanced.get_attribute("open") is not None:
                raise RuntimeError("Advanced configuration must start collapsed.")
            page.locator('input[type=file]').first.set_input_files([str(a),str(b),str(c)])
            page.wait_for_timeout(1500)
            page.get_by_text("Outras formas de adicionar documentos",exact=True).first.click()
            page.get_by_text("Catálogo público",exact=True).first.click()
            page.get_by_role("button",name="Adicionar do catálogo",exact=True).click()
            page.wait_for_timeout(800)
            secondary = page.get_by_text("Outras formas de adicionar documentos",exact=True).first
            if secondary.locator("xpath=ancestor::details[1]").get_attribute("open") is None:
                secondary.click()
            page.get_by_text("URL pública",exact=True).first.click()
            page.get_by_role("textbox",name="URL pública direta do PDF ou imagem",exact=True).fill("https://example.com/offline-fixture.pdf")
            page.get_by_role("button",name="Adicionar URL pública",exact=True).click()
            page.wait_for_timeout(800)
            assert page.get_by_role("button",name="Usar como referência",exact=True).count() == 5
            page.get_by_role("button",name="Usar como referência",exact=True).nth(1).click()
            page.wait_for_timeout(800)
            page.screenshot(path=str(qa/"02_documents.png"),full_page=True)
            page.wait_for_timeout(800)
            page.get_by_role("button",name="Comparar apólices",exact=True).click()
            page.get_by_role("tab",name="Resumo",exact=True).wait_for(timeout=45000)
            page.wait_for_timeout(1000)
            if page.locator('[data-testid="stException"]').count():
                raise RuntimeError("Streamlit raised an exception during workspace processing.")
            before_reruns = call_count()
            page.get_by_role("heading", name="Resumo da comparação", exact=True).evaluate("element => element.scrollIntoView({block: 'start'})")
            page.wait_for_timeout(350)
            page.screenshot(path=str(qa/"03_results.png"),full_page=True)
            for tab_name in ("Coberturas","Limites & Franquias","Exclusões","Cláusulas","Evidências"):
                page.get_by_role("tab",name=tab_name,exact=True).click()
                page.wait_for_timeout(250)
                if page.locator('[data-testid="stException"]').count():
                    raise RuntimeError("An exception appeared in a results tab.")
            page.get_by_role("tab",name="Evidências",exact=True).click()
            page.get_by_role("heading", name="Conferir evidências", exact=True).evaluate("element => element.scrollIntoView({block: 'start'})")
            page.wait_for_timeout(350)
            page.screenshot(path=str(qa/"04_evidence.png"),full_page=True)
            page.get_by_role("tab",name="Resumo",exact=True).click()
            page.get_by_text("Detalhes técnicos",exact=True).first.click()
            technical = page.get_by_text("Uso da IA",exact=True)
            if technical.locator("xpath=ancestor::details[1]").get_attribute("open") is not None:
                raise RuntimeError("AI usage details must start collapsed.")
            technical.click()
            page.get_by_role("heading",name="SESSION TOTAL",exact=True).wait_for(timeout=15000)
            page.get_by_text("O custo efetivo é determinado pelo provedor da API.",exact=False).first.wait_for(timeout=15000)
            if page.locator('[data-testid="stException"]').count():
                raise RuntimeError("AI usage panel raised an exception.")
            page.screenshot(path=str(qa/"06_ai_usage.png"),full_page=True)
            report_expander = page.get_by_text("Outros formatos",exact=True).first
            report_expander.click()
            page.wait_for_timeout(350)
            for label in ("Markdown","PDF","JSON"):
                report_header = page.get_by_text("Outros formatos",exact=True).first
                if report_header.locator("xpath=ancestor::details[1]").get_attribute("open") is None:
                    report_header.click()
                button = (page.get_by_role("button",name=re.compile(r"^Exportar relatório")).first if label == "PDF" else page.get_by_role("button",name=f"Baixar {label}",exact=True).first)
                button.scroll_into_view_if_needed()
                with page.expect_download(timeout=15000) as event:
                    button.click()
                download = event.value
                target = qa/"downloads"/download.suggested_filename
                target.parent.mkdir(exist_ok=True)
                download.save_as(target)
                if not target.stat().st_size:
                    raise RuntimeError("Report download is empty.")
                downloads.append(str(target.relative_to(ROOT)))
                page.wait_for_timeout(600)
            path = ROOT / next(name for name in downloads if name.endswith(".json"))
            comparison = json.loads(path.read_text(encoding="utf-8"))
            actual = {item["field_name"]:item for item in comparison["differences"]}
            if not {"limite_maximo_garantia","retencao_franquia","side_a"}.issubset(actual):
                raise RuntimeError("Expected synthetic differences absent from actual browser download.")
            if actual["limite_maximo_garantia"]["citation_a"]["page_number"] != 1:
                raise RuntimeError("Source page evidence was not preserved.")
            if args.retrieval_status or args.technical_status:
                page.get_by_role("tab",name="Evidências",exact=True).click()
                selector=page.locator('[data-testid="stSelectbox"]').filter(has=page.get_by_text("Item",exact=True)).first
                selector.get_by_role("combobox").click()
                selector.get_by_role("combobox").fill("Side C")
                page.get_by_role("option",name="Side C",exact=True).click()
                page.wait_for_timeout(500)
                page.get_by_text("Não foi possível analisar" if args.technical_status else "Não localizado com segurança",exact=True).first.wait_for(timeout=15000)
                if page.locator('[data-testid="stException"]').count():
                    raise RuntimeError("Retrieval uncertainty fixture raised a UI error.")
            optional = page.get_by_text("Revisão humana — opcional",exact=True).first
            if optional.locator("xpath=ancestor::details[1]").get_attribute("open") is None:
                optional.click()
            page.get_by_role("button",name="Precisa de revisão",exact=True).click()
            page.wait_for_timeout(800)
            # State-changing human review and download reruns must never infer.
            if call_count() != before_reruns:
                raise RuntimeError("A render/download/review unexpectedly invoked the gateway.")
            page.screenshot(path=str(qa/"05_review.png"),full_page=True)
            page.get_by_role("button",name="Nova comparação",exact=True).click()
            page.wait_for_timeout(800)
            page.get_by_text("O primeiro documento será a referência.",exact=False).wait_for(timeout=15000)
            if call_count() != before_reruns:
                raise RuntimeError("Reset unexpectedly invoked the gateway.")
            if page.locator('[data-testid="stException"]').count():
                raise RuntimeError("Reset raised an exception.")
            if errors:
                raise RuntimeError("Browser page errors detected.")
            context.close()
            browser.close()
        result = {
            "status":"PASS","mode":"actual_streamlit_edge_synthetic_mock_inference",
            "uploads":3,"public_catalog":True,"public_url":True,"workspace_documents":5,
            "reference_candidates":4,"explicit_reference_selection":True,"http_calls":0,
            "ai_usage_panel":True,"session_aggregation":True,"advanced_settings_collapsed":True,
            "new_comparison_reset":True,
            "retrieval_status_fixture_checked":args.retrieval_status,
            "technical_status_fixture_checked":args.technical_status,
            "logical_mock_calls":call_count(),"downloads":downloads,
            "tabs":6,"review_rerun_no_inference":True,"browser_errors":errors,
            "screenshots":["01_home.png","02_documents.png","03_results.png","04_evidence.png","05_review.png","06_ai_usage.png"],
            "final_artifacts_regenerated":False,
        }
        (qa/"diagnostics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=True))
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
