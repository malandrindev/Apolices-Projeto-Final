"""Real Edge QA for the simplified copilot, demo and conventional upload; no provider HTTP."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright
from scripts.smoke_end_to_end import create_synthetic_documents


def run(args) -> int:
    qa = ROOT / "data" / "processed" / "product_browser_qa"
    qa.mkdir(parents=True, exist_ok=True)
    preservation = ROOT / "data/processed/product_simplification_preservation.json"
    if preservation.is_file():
        protected = json.loads(preservation.read_text(encoding="utf-8"))["files"]
    else:
        # A fresh checkout can still verify that QA never changes tracked files.
        names = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        protected = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                     for name in names if name and (ROOT / name).is_file()}
    a, b = create_synthetic_documents(qa / "inputs")
    calls_path, state_path, mode_path = (qa / name for name in ("calls.json", "ui_state.json", "mode.json"))
    calls_path.write_text(json.dumps({"provider_operations": 0, "provider_http": 0, "gateway_attempts": 0, "fake_operations": 0}), encoding="utf-8")
    mode_path.write_text('{"mode":"demo"}', encoding="utf-8")
    wrapper = qa / "offline_product_app.py"
    wrapper.write_text(f"""import runpy,sys,json
from pathlib import Path
from dataclasses import replace
sys.path.insert(0,{str(ROOT)!r})
import openai,groq,httpx
def record(name):
    p=Path({str(calls_path)!r})
    d=json.loads(p.read_text(encoding="utf-8"));d[name]+=1
    p.write_text(json.dumps(d),encoding="utf-8")
def reject_provider(*args,**kwargs):
    record("provider_operations")
    raise RuntimeError("QA blocks all real provider construction.")
def reject_http(*args,**kwargs):
    record("provider_http")
    raise RuntimeError("QA blocks all provider HTTP.")
openai.OpenAI.__init__=reject_provider
groq.Groq.__init__=reject_provider
httpx.Client.request=reject_http
httpx.AsyncClient.request=reject_http
import src.config as config
import src.llm.providers as providers
from scripts.smoke_end_to_end import OfflineSyntheticGateway
original=config.get_ingestion_settings
config.get_ingestion_settings=lambda:replace(original(),processed_dir=Path({str(qa/'cache')!r}),min_native_chars=20)
class RecordingGateway(OfflineSyntheticGateway):
    def __init__(self):self.events=[]
    def complete(self,**kwargs):
        record("fake_operations")
        return super().complete(**kwargs)
def get_offline_gateway(*args,**kwargs):
    if json.loads(Path({str(mode_path)!r}).read_text())["mode"]!="upload":
        record("gateway_attempts")
        raise RuntimeError("Demo must return before constructing a gateway.")
    return RecordingGateway()
providers.get_gateway=get_offline_gateway
runpy.run_path({str(ROOT/'interface'/'app.py')!r},run_name="__main__")
import streamlit as st
docs=st.session_state.get("workspace_docs",[])
result=st.session_state.get("workspace_result") or {{}}
reference=st.session_state.get("workspace_reference")
snapshot={{"documents":len(docs),"filenames":[d.filename for d in docs],"reference_filename":next((d.filename for d in docs if d.sha256==reference),None),"has_result":bool(result),"reviews":st.session_state.get("workspace_reviews",{{}}),"prepared":len(st.session_state.get("prepared_docs",{{}})),"demo":result.get("stats",{{}}).get("demo",False) or result.get("demo",False)}}
Path({str(state_path)!r}).write_text(json.dumps(snapshot,ensure_ascii=False),encoding="utf-8")
""", encoding="utf-8")
    log = (qa / "streamlit.log").open("w", encoding="utf-8")
    env = dict(os.environ, LLM_PROVIDER="openai", MODEL_ROUTING_ENABLED="false", OPENAI_API_KEY="offline-browser-qa")
    server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(wrapper),
        "--server.address", "127.0.0.1", "--server.port", str(args.port),
        "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    downloads, errors, screenshots = [], [], []

    def state():
        return json.loads(state_path.read_text(encoding="utf-8"))

    def assert_no_provider():
        calls = json.loads(calls_path.read_text(encoding="utf-8"))
        assert calls["provider_operations"] == calls["provider_http"] == calls["gateway_attempts"] == 0, calls
        return calls

    def expander(page, name):
        item = page.get_by_text(name, exact=True).first
        if item.locator("xpath=ancestor::details[1]").get_attribute("open") is None:
            item.click()
        return item

    def shot(page, name):
        page.screenshot(path=str(qa / name), full_page=True)
        screenshots.append(name)

    try:
        for _ in range(100):
            try:
                with urlopen(f"http://127.0.0.1:{args.port}/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.2)
        else:
            raise RuntimeError("Product QA Streamlit did not become healthy.")
        with sync_playwright() as engine:
            browser = engine.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{args.port}", wait_until="networkidle")
            page.get_by_role("heading", name="Compare apólices D&O em minutos.", exact=True).wait_for(timeout=30000)
            for name in ("Configuração avançada", "Outras formas de adicionar documentos"):
                assert page.get_by_text(name, exact=True).first.locator("xpath=ancestor::details[1]").get_attribute("open") is None
            page.locator('input[type="file"]').first.wait_for(state="attached", timeout=15000)
            assert not page.get_by_role("button", name="Preparar documentos", exact=True).count()
            assert not page.get_by_role("checkbox").count()
            shot(page, "01_home.png")
            page.get_by_role("button", name="Usar exemplo de demonstração", exact=True).click()
            page.get_by_text("Exemplo de demonstração — dados fictícios", exact=False).first.wait_for(timeout=30000)
            page.wait_for_timeout(600)
            assert state()["documents"] == 2 and state()["reference_filename"] == "porto_demo_policy.pdf"
            assert_no_provider()
            page.get_by_role("button", name="Comparar apólices", exact=True).click()
            page.get_by_role("tab", name="Resumo", exact=True).wait_for(timeout=30000)
            page.get_by_text("Resultado de demonstração pré-processado.", exact=False).first.wait_for(timeout=15000)
            page.wait_for_timeout(600)
            assert state()["has_result"]
            assert_no_provider()
            intake = page.get_by_text("Documentos desta comparação", exact=True).first
            assert intake.locator("xpath=ancestor::details[1]").get_attribute("open") is None
            page.get_by_role("heading", name="Resumo da comparação", exact=True).scroll_into_view_if_needed()
            shot(page, "02_demo_summary.png")
            for name in ("Coberturas", "Limites & Franquias", "Exclusões", "Cláusulas", "Evidências"):
                page.get_by_role("tab", name=name, exact=True).click()
                page.wait_for_timeout(180)
                assert not page.locator('[data-testid="stException"]').count()
            page.get_by_role("tab", name="Resumo", exact=True).click()
            evidence = page.get_by_text(re.compile(r"^Ver evidência")).first
            evidence.click()
            page.wait_for_timeout(400)
            assert page.get_by_text(re.compile(r"Página [0-9]+")).count() >= 1
            page.get_by_role("button", name=re.compile(r"^Abrir documento na página ")).first.click()
            page.wait_for_timeout(600)
            frame = page.locator('[data-testid="stImage"] img').first
            frame.wait_for(timeout=15000, state="attached")
            frame.wait_for(timeout=10000)
            assert frame.evaluate("element => element.complete && element.naturalWidth > 0")
            assert not page.locator('[data-testid="stException"]').count()
            frame.scroll_into_view_if_needed()
            shot(page, "03_evidence.png")
            with page.expect_download(timeout=15000) as event:
                page.get_by_role("button", name="Exportar relatório", exact=True).first.click()
            download = event.value
            target = qa / "downloads" / download.suggested_filename
            target.parent.mkdir(exist_ok=True)
            download.save_as(target)
            assert target.stat().st_size > 0
            import pymupdf
            with pymupdf.open(target) as report:
                assert len(report) > 0
                report_text = " ".join(page.get_text() for page in report)
                assert "SEM VALIDADE CONTRATUAL" in report_text
            downloads.append(str(target.relative_to(ROOT)))
            page.wait_for_timeout(500)
            expander(page, "Detalhes técnicos")
            expander(page, "Uso da IA")
            assert_no_provider()
            shot(page, "04_technical.png")
            # Human review is optional and records an annotation without inference.
            expander(page, "Revisão humana — opcional")
            page.get_by_role("button", name="Precisa de revisão", exact=True).first.click()
            page.wait_for_timeout(600)
            assert any(value["status"] == "Requer análise" for value in state()["reviews"].values())
            assert_no_provider()
            shot(page, "05_optional_review.png")
            page.get_by_role("button", name="Nova comparação", exact=True).click()
            page.wait_for_timeout(600)
            assert state()["documents"] == 0 and not state()["has_result"]
            assert_no_provider()
            # Conventional upload follows the same single CTA, with a fake gateway.
            mode_path.write_text('{"mode":"upload"}', encoding="utf-8")
            page.locator('input[type="file"]').first.set_input_files([str(a), str(b)])
            page.wait_for_timeout(700)
            assert state()["documents"] == 2
            page.get_by_role("button", name="Comparar apólices", exact=True).click()
            page.get_by_role("tab", name="Resumo", exact=True).wait_for(timeout=45000)
            page.wait_for_timeout(500)
            assert state()["has_result"] and state()["prepared"] == 2
            calls = assert_no_provider()
            assert calls["fake_operations"] >= 2
            shot(page, "06_upload_fake.png")
            assert not page.locator('[data-testid="stException"]').count() and not errors
            browser.close()
        changed = [name for name, digest in protected.items()
                   if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
        assert not changed, changed
        result = {"status": "PASS", "browser": "Microsoft Edge", "mode": "offline_demo_and_fake_upload",
                  "demo_documents": 2, "default_reference": "PORTO", "business_tabs": 6,
                  "auto_preparation": True, "no_consent_checkbox": True, "upload_primary": True,
                  "secondary_sources_collapsed": True, "advanced_collapsed": True,
                  "evidence_on_demand": True, "open_pdf_page": True, "export_pdf": True, "optional_human_review": True,
                  "technical_details": True, "reset": True, "upload_fake_comparison": True,
                  "provider_http": 0, "provider_operations": 0, "demo_gateway_attempts": 0,
                  "fake_operations": calls["fake_operations"], "browser_errors": errors,
                  "preserved_files": len(protected), "preservation_mismatches": changed,
                  "downloads": downloads, "screenshots": screenshots, "academic_artifacts_regenerated": False}
        (qa / "diagnostics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception:
        try:
            (qa / "failure_ui.txt").write_text(page.locator("body").inner_text(), encoding="utf-8")
            page.screenshot(path=str(qa / "failure.png"), full_page=True)
        except Exception:
            pass
        raise
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8506)
    raise SystemExit(run(parser.parse_args()))
