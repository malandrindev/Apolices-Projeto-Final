"""Edge QA over original PDFs and the actual routed pipeline; every provider is fake."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGINALS = (
    ("Apólice-PORTO_D&O_teste.pdf", 52, "bf9e103a33e941e9df44a30d553e429825d66b48f658c17e3be807bec8be1145"),
    ("Apólice-Allianz Condições_Gerais- 2025_teste.pdf", 75, "1778f9d09c187996e5cef8fda73a51c89ced0d0dc1772aaa4f327a9f5eb9a6cc"),
)


def preserved_runtime_hashes():
    """Snapshot originals, historical ledgers, claims and billing without rewriting."""
    paths = set((ROOT / "data/processed/workspace_usage").glob("*.json"))
    for directory in ("fasttrack_validation", "public_validation"):
        paths.update((ROOT / "data/processed" / directory).rglob("*.json"))
    for directory in ("Projeto_Final_Artefatos", "data/demo", "data/demo_sources"):
        paths.update((ROOT / directory).glob("*"))
    paths.update(ROOT / "docs" / name for name in (
        "OPENAI_BILLING_AUDIT.md", "COMPLIMENTARY_MODEL_VALIDATION.md", "OPENAI_PRICES_2026-10-04.json"))
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if path.is_file()}


class OfflineGroupedProvider:
    """Fake output; production retrieval, routing, validators and progress stay real."""
    provider = "offline-limits-mock"

    def __init__(self, record=None):
        self.events, self.calls, self.record = [], [], record

    def complete(self, **request):
        from src.schemas.retrieval import GroupExtractionResponse
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()
        self.calls.append({"request_fingerprint": fingerprint, "agent": request["agent"]})
        if self.record:
            self.record("fake_operations", fingerprint)
        payload = json.loads(request["messages"][1]["content"])
        if isinstance(payload, dict) and "field_names" in payload:
            return GroupExtractionResponse(fields=[
                {"field_name": name, "status": "NOT_RETRIEVED"}
                for name in payload["field_names"]
            ]).model_dump_json()
        if isinstance(payload, list):
            return json.dumps({"comparisons": [
                {"field_name": pair["field_name"], "classification": "diferente_nao_comparavel",
                 "justification": "Resposta mock de QA; nenhuma conclusão contratual."}
                for pair in payload
            ]})
        raise AssertionError("Unexpected fake-provider request contract.")


def run(args):
    from playwright.sync_api import sync_playwright
    qa = ROOT / "data/processed/limits_browser_qa" / time.strftime("%Y%m%d-%H%M%S")
    qa.mkdir(parents=True, exist_ok=False)
    protected = preserved_runtime_hashes()
    files = [ROOT / "data/demo_sources" / name for name, _, _ in ORIGINALS]
    for path, (_, _, expected_sha) in zip(files, ORIGINALS):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha
    calls_path, state_path = qa / "calls.json", qa / "ui_state.json"
    calls_path.write_text(json.dumps({"provider_operations": 0, "provider_http": 0,
                                     "fake_operations": 0, "request_fingerprints": []}), encoding="utf-8")
    wrapper = qa / "offline_limits_app.py"
    wrapper.write_text(f'''import runpy,sys,json
from pathlib import Path
from dataclasses import replace
sys.path.insert(0,{str(ROOT)!r})
import openai,groq,httpx
from scripts.check_limits_browser import OfflineGroupedProvider
def record(name,fingerprint=None):
    path=Path({str(calls_path)!r}); value=json.loads(path.read_text(encoding="utf-8"))
    value[name]+=1
    if fingerprint is not None:value["request_fingerprints"].append(fingerprint)
    path.write_text(json.dumps(value),encoding="utf-8")
def reject_provider(*args,**kwargs):
    record("provider_operations");raise AssertionError("All real provider SDKs blocked in QA.")
def reject_http(*args,**kwargs):
    record("provider_http");raise AssertionError("All provider HTTP blocked in QA.")
for client in (openai.OpenAI,openai.AsyncOpenAI,groq.Groq,groq.AsyncGroq):
    client.__init__=reject_provider
httpx.Client.request=reject_http
httpx.AsyncClient.request=reject_http
import src.config as config
import src.llm.providers as providers
from src.llm.model_routing import ModelRoutingPolicy,RequestControl,RoutedGateway
import traceback
from src.agents.ocr import OcrAgent
if not hasattr(OcrAgent,"_limits_qa_original_extract"):
    OcrAgent._limits_qa_original_extract=OcrAgent.extract
def capture_ocr_error(self,*args,_original=OcrAgent._limits_qa_original_extract,**kwargs):
    try:return _original(self,*args,**kwargs)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise
OcrAgent.extract=capture_ocr_error
if not hasattr(config,"_limits_qa_original_ingestion"):
    config._limits_qa_original_ingestion=config.get_ingestion_settings
config.get_ingestion_settings=lambda _original=config._limits_qa_original_ingestion:replace(_original(),processed_dir=Path({str(qa/'cache')!r}),min_native_chars=20)
def get_offline_gateway(*args,**kwargs):
    return RoutedGateway(OfflineGroupedProvider(record),ModelRoutingPolicy(),kwargs.get("request_control") or RequestControl(50))
providers.get_gateway=get_offline_gateway
runpy.run_path({str(ROOT/'interface/app.py')!r},run_name="__main__")
import streamlit as st
docs=st.session_state.get("workspace_docs",[])
result=st.session_state.get("workspace_result") or {{}}
reports=result.get("reports",{{}})
snapshot={{"documents":len(docs),"filenames":[d.filename for d in docs],
"reference":st.session_state.get("workspace_reference"),"has_result":bool(result),"max_calls":st.session_state.get("max_calls"),
"prepared":len(st.session_state.get("prepared_docs",{{}})),
"pages":{{sha:len(item["processed"].pages) for sha,item in st.session_state.get("prepared_docs",{{}}).items()}},
"groups":{{sha:[g["group_id"] for g in r.retrieval_diagnostics.get("groups",[])] for sha,r in reports.items()}},
"events":result.get("stats",{{}}).get("events",[]),"demo":result.get("stats",{{}}).get("demo",False)}}
Path({str(state_path)!r}).write_text(json.dumps(snapshot,ensure_ascii=False),encoding="utf-8")
''', encoding="utf-8")
    env = dict(os.environ, LLM_PROVIDER="openai", MODEL_ROUTING_ENABLED="true",
               OPENAI_API_KEY="offline-qa-placeholder", PUBLIC_EXTRACTION_STRATEGY="optimized")
    log = (qa / "streamlit.log").open("w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(wrapper),
        "--server.address", "127.0.0.1", "--server.port", str(args.port),
        "--server.headless", "true", "--browser.gatherUsageStats", "false", "--server.fileWatcherType", "none"], cwd=ROOT,
        env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    screenshots, errors = [], []

    def state():
        return json.loads(state_path.read_text(encoding="utf-8"))

    def safe():
        calls = json.loads(calls_path.read_text(encoding="utf-8"))
        assert calls["provider_operations"] == calls["provider_http"] == 0, calls
        assert len(calls["request_fingerprints"]) == len(set(calls["request_fingerprints"])), calls
        return calls

    def shot(page, name):
        page.screenshot(path=str(qa / name), full_page=True)
        screenshots.append(name)

    def wait_snapshot(predicate):
        for _ in range(120):
            if state_path.is_file() and predicate(state()):
                return state()
            time.sleep(.25)
        raise AssertionError("Offline UI snapshot did not reach expected state.")

    def expand(page, name):
        label = page.get_by_text(name, exact=True).first
        if label.locator("xpath=ancestor::details[1]").get_attribute("open") is None:
            label.click()

    try:
        for _ in range(100):
            try:
                with urlopen(f"http://127.0.0.1:{args.port}/_stcore/health", timeout=1) as response:
                    if response.status == 200:break
            except Exception:
                time.sleep(.2)
        else:raise RuntimeError("Offline QA server did not become healthy.")
        with sync_playwright() as engine:
            browser = engine.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width":1440,"height":1000})
            page = context.new_page()
            page.on("pageerror", lambda error:errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{args.port}", wait_until="networkidle")
                page.get_by_role("heading",name="Compare apólices D&O em minutos.",exact=True).wait_for(timeout=30000)
                page.locator('input[type="file"]').first.set_input_files([str(path) for path in files])
                initial = wait_snapshot(lambda value:value["documents"] == 2)
                assert initial["reference"] == ORIGINALS[0][2]
                expand(page,"Configuração avançada")
                limit = page.get_by_role("spinbutton",name="Limite de operações de IA")
                limit.fill("50")
                limit.press("Enter")
                wait_snapshot(lambda value:value["max_calls"] == 50)
                page.wait_for_timeout(500)
                shot(page,"01_original_upload_reference.png")
                page.get_by_role("button",name="Comparar apólices",exact=True).click()
                for _ in range(360):
                    if page.get_by_role("tab",name="Resumo",exact=True).is_visible():break
                    if page.get_by_text("Preparação interrompida",exact=True).is_visible():
                        raise AssertionError("Original PDF preparation failed; inspect QA streamlit.log.")
                    page.wait_for_timeout(250)
                else:raise AssertionError("Original routed results did not appear.")
                original_result = wait_snapshot(lambda value:value["has_result"])
                assert original_result["prepared"] == 2
                assert original_result["pages"] == {item[2]:item[1] for item in ORIGINALS}
                assert all(len(groups) == len(set(groups)) == 14 for groups in original_result["groups"].values())
                keys = [(event["document_id"],event["logical_step_id"],event["requested_model"])
                        for event in original_result["events"]]
                assert keys and len(keys) == len(set(keys))
                assert not page.locator('[data-testid="stException"]').count()
                assert "KeyError" not in page.locator("body").inner_text()
                assert "Traceback" not in page.locator("body").inner_text()
                original_calls = safe()["fake_operations"]
                assert original_calls > 0
                shot(page,"02_original_routed_results.png")
                expand(page,"Documentos desta comparação")
                page.get_by_role("button",name="Comparar apólices",exact=True).click()
                page.wait_for_timeout(1000)
                assert state()["has_result"] and safe()["fake_operations"] == original_calls
                page.get_by_role("button",name="Nova comparação",exact=True).click()
                wait_snapshot(lambda value:value["documents"] == 0 and not value["has_result"])
                page.get_by_role("button",name="Usar exemplo de demonstração",exact=True).click()
                wait_snapshot(lambda value:value["documents"] == 2 and value["filenames"][0] == "porto_demo_policy.pdf")
                page.get_by_role("button",name="Comparar apólices",exact=True).click()
                page.get_by_role("tab",name="Resumo",exact=True).wait_for(timeout=30000)
                demo_result = wait_snapshot(lambda value:value["has_result"] and value["demo"])
                assert sorted(demo_result["pages"].values()) == [30,33]
                assert safe()["fake_operations"] == original_calls
                assert not page.locator('[data-testid="stException"]').count() and not errors
                shot(page,"03_demo_results.png")
            except Exception:
                (qa/"failure_ui.txt").write_text(page.locator("body").inner_text(),encoding="utf-8")
                page.screenshot(path=str(qa/"failure.png"),full_page=True)
                raise
            finally:
                browser.close()
        changed = [name for name,digest in protected.items()
                   if not (ROOT/name).is_file() or hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest]
        assert not changed, changed
        result = {"status":"PASS","browser":"Microsoft Edge","mode":"original_PDFs_real_routed_pipeline_fake_provider",
            "notice":"All reservations/events here are MOCK QA only; no real provider HTTP or billing evidence.",
            "original_documents":[{"filename":name,"pages":pages,"sha256":sha} for name,pages,sha in ORIGINALS],
            "routed_groups_per_document":14,"logical_event_duplicates":0,"request_fingerprint_duplicates":0,
            "provider_http":0,"provider_operations":0,"fake_operations":safe()["fake_operations"],
            "original_flow":"PASS","demo_flow":"PASS","repeat_compare_no_inference":True,
            "browser_errors":errors,"preserved_files":len(protected),"preservation_mismatches":changed,
            "screenshots":screenshots,"academic_artifacts_regenerated":False}
        (qa/"diagnostics.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
        print(json.dumps({"diagnostics":str((qa/"diagnostics.json").relative_to(ROOT)),**result},ensure_ascii=True))
        return 0
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",type=int,default=8508)
    raise SystemExit(run(parser.parse_args()))
