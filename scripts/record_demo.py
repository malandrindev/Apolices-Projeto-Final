"""Browser QA/demonstration using the exact cached results of a prior live smoke.

Optional tools: pip install -r requirements-artifacts.txt
Requires scripts/smoke_end_to_end.py --live already successful in e2e_smoke_openai.
No inference is allowed: an unexpected cache miss stops with an explicit error.
"""
from __future__ import annotations
import json, os, subprocess, sys, time, wave
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
QA=ROOT/"data"/"processed"/"browser_qa"
LIVE=ROOT/"data"/"processed"/"e2e_smoke_openai"
ART=ROOT/"Projeto_Final_Artefatos"
PORT=8502

SCENES=[
("O problema", "Apólices longas. Diferenças difíceis de conferir.",
 ["D&O: responsabilidade de administradores e diretores","MVP para extrair, comparar e apresentar evidências","Demonstração educacional com duas fontes sintéticas"],
 "O projeto InsurMinds organiza e compara informações de documentos de seguro D e O. A dificuldade é localizar cláusulas, valores e diferenças em textos extensos. Esta demonstração usa duas fontes sintéticas, sem dados de clientes."),
("A arquitetura", "Um fluxo simples, com responsabilidades claras.",
 ["PDF ou imagem → texto nativo ou Tesseract local","Cláusulas → IA generativa → validação Pydantic → SQLite","Comparação objetiva e semântica → evidências → MD, PDF e JSON","Groq e OpenAI configuráveis; RAG opcional fora do caminho principal"],
 "O fluxo recebe PDF ou imagem, extrai texto nativo ou usa o Tesseract local. A inteligência artificial estrutura campos por cláusula. O código confere o trecho e a página, salva no SQLite e compara valores por regras. Diferenças textuais usam análise semântica. A interface oferece evidências e três formatos de relatório."),
("Preparação", "A interface real recebe duas fontes.",
 ["A: PDF textual, limite de dez milhões de reais","B: imagem, limite de oito milhões de reais","A inclui custos de defesa; B os exclui","Reexecução em cache de um processamento OpenAI real já validado"],
 "A interface permite escolher o provedor e receber duas fontes. O documento A é um PDF textual. O documento B é uma imagem que exigiu OCR. A gravação reutiliza resultados de chamadas reais anteriores à OpenAI. Não há novas chamadas de inteligência artificial durante esta gravação."),
("Resultados", "Diferenças acompanhadas da fonte.",
 ["Limite: R$ 10 milhões em A; R$ 8 milhões em B","Franquia: R$ 100 mil em A; R$ 200 mil em B","Side A: inclui versus exclui custos de defesa","Datas diferentes não significam vantagem automática"],
 "Os resultados mostram limite, franquia e cobertura Side A, com justificativas, página e trecho original. A tem limite maior e franquia menor neste exemplo comparável. A diferença de data não recebe uma vantagem automática. Informações não localizadas não comprovam ausência de cobertura."),
("Validação e limites", "Evidência real, escopo declarado.",
 ["OpenAI: 2 extrações Luna + 1 comparação Sol; 3 HTTP","Groq: 3 HTTP com gpt-oss-120b; nenhuma repetição","Retomada: 2 cláusulas em cache; zero chamadas novas","OCR real, SQLite e relatórios verificados; revisão humana necessária"],
 "O fluxo completo foi validado separadamente com OpenAI e Groq, três chamadas HTTP em cada provedor e sem repetição por falha. A retomada reutilizou as duas cláusulas, sem chamadas novas. Os resultados são informativos e precisam de revisão humana. O cenário sintético não demonstra qualidade para todos os contratos ou layouts."),
("Entrega e evolução", "Funcionamento antes de complexidade.",
 ["PDF técnico, pitch e vídeo preparados localmente","Prazo oficial: 06/10/2026 às 23h59","Equipe e revisão final ainda pendentes","Próximos passos: avaliar fontes públicas completas e qualidade do OCR"],
 "A preparação acadêmica inclui relatório técnico, apresentação e este vídeo de funcionamento. A identificação da equipe e a revisão final continuam pendentes. Não houve publicação nem envio de e-mail. A evolução proposta é avaliar documentos públicos completos e ampliar a medição de qualidade. Funcionamento tem prioridade sobre complexidade.")
]

def html_scene(index):
    title,subtitle,bullets,_=SCENES[index]
    import html
    return f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8">
    <style>body{{margin:0;background:#102e40;color:#f3f7f9;font-family:Arial;padding:60px 75px}}
    small{{color:#87d1e4;letter-spacing:3px;font-size:19px}}h1{{font-size:48px;margin:42px 0 18px}}
    h2{{font-size:26px;font-weight:400;color:#a9dae6;margin-bottom:38px}}li{{font-size:24px;margin:22px 0;line-height:1.4}}
    footer{{position:fixed;bottom:34px;color:#aac3cd;font-size:16px}}</style>
    <small>INSURMINDS · PROJETO FINAL I2A2</small><h1>{html.escape(title)}</h1>
    <h2>{html.escape(subtitle)}</h2><ul>{''.join('<li>'+html.escape(b)+'</li>' for b in bullets)}</ul>
    <footer>03/10/2026 · MVP local · fontes sintéticas · {index+1} / {len(SCENES)}</footer></html>"""

def main():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",str(ROOT/"data"/"processed"/"tooling"/"playwright"))
    from playwright.sync_api import sync_playwright
    import imageio_ffmpeg
    QA.mkdir(parents=True,exist_ok=True);ART.mkdir(exist_ok=True)
    diagnostic=json.loads((LIVE/"diagnostics.json").read_text(encoding="utf-8"))
    if diagnostic.get("status")!="PASS" or not diagnostic.get("generative_ai_verified"):
        raise RuntimeError("Successful live synthetic smoke is required before browser QA.")
    (QA/"narration.json").write_text(json.dumps([s[3] for s in SCENES],ensure_ascii=False),encoding="utf-8")
    ps=QA/"narrate.ps1"
    ps.write_text("""Add-Type -AssemblyName System.Speech
$voice=New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice.SelectVoice('Microsoft Maria Desktop')
$voice.Rate=1
$texts=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'narration.json') -Raw -Encoding UTF8 | ConvertFrom-Json
for($i=0;$i -lt $texts.Count;$i++){
  $voice.SetOutputToWaveFile((Join-Path $PSScriptRoot ('voice_'+$i+'.wav')))
  $voice.Speak($texts[$i])
  $voice.SetOutputToNull()
}
$voice.Dispose()
""",encoding="utf-8-sig")
    subprocess.run(["powershell","-NoProfile","-Command",ps.read_text(encoding="utf-8-sig").replace("$PSScriptRoot", "'"+str(QA).replace("'", "''")+"'")],check=True,timeout=60)
    durations=[]
    for index in range(len(SCENES)):
        with wave.open(str(QA/f"voice_{index}.wav")) as audio:
            durations.append(audio.getnframes()/audio.getframerate())
    wrapper=QA/"cached_demo_app.py"
    wrapper.write_text(f'''import runpy
from pathlib import Path
from dataclasses import replace
import sys
sys.path.insert(0,{str(ROOT)!r})
import src.config as config
import src.llm.providers as providers
original=config.get_ingestion_settings
config.get_ingestion_settings=lambda:replace(original(),processed_dir=Path({str(LIVE)!r}),min_native_chars=20)
factory=providers.get_gateway
def cached_gateway(*args,**kwargs):
    gateway=factory(*args,**kwargs)
    def reject_api(**call):
        raise RuntimeError("QA interrompido: cache ausente. Nenhuma chamada API foi realizada.")
    gateway.complete=reject_api
    return gateway
providers.get_gateway=cached_gateway
runpy.run_path({str(ROOT/"interface"/"app.py")!r},run_name="__main__")
''',encoding="utf-8")
    errors=[];audio_starts=[];screens=[];downloaded=[]
    log=(QA/"streamlit.log").open("w",encoding="utf-8")
    server=subprocess.Popen([sys.executable,"-m","streamlit","run",str(wrapper),"--server.address","127.0.0.1","--server.port",str(PORT),"--server.headless","true","--browser.gatherUsageStats","false"],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    try:
        for _ in range(80):
            try:
                with urlopen(f"http://127.0.0.1:{PORT}/_stcore/health",timeout=1) as response:
                    if response.status==200:break
            except Exception:time.sleep(.25)
        else:raise RuntimeError("Browser QA server did not become healthy.")
        with sync_playwright() as engine:
            browser=engine.chromium.launch(channel="msedge",headless=True)
            context=browser.new_context(viewport={"width":1280,"height":720},record_video_dir=str(QA/"recordings"),record_video_size={"width":1280,"height":720},accept_downloads=True)
            page=context.new_page()
            page.on("pageerror",lambda error:errors.append(str(error)))
            start=time.perf_counter()
            for index in [0,1]:
                page.set_content(html_scene(index))
                audio_starts.append(round(time.perf_counter()-start,3))
                page.wait_for_timeout(int((durations[index]+.7)*1000))
            page.goto(f"http://127.0.0.1:{PORT}",wait_until="networkidle")
            page.get_by_role("heading",name="Comparador de apólices D&O",exact=True).wait_for(timeout=20000)
            page.locator('input[type=file]').nth(0).set_input_files(str(LIVE/"synthetic_inputs"/"sintetica_A_textual.pdf"))
            page.locator('input[type=file]').nth(1).set_input_files(str(LIVE/"synthetic_inputs"/"sintetica_B_imagem.png"))
            page.get_by_text("Confirmo que os documentos são apropriados para envio ao provedor selecionado.",exact=True).click()
            if not page.get_by_role("checkbox").is_checked():raise RuntimeError("Consent confirmation did not reach the app.")
            page.wait_for_timeout(800)
            audio_starts.append(round(time.perf_counter()-start,3))
            page.screenshot(path=str(QA/"01_uploads.png"),full_page=True);screens.append("01_uploads.png")
            page.wait_for_timeout(int((durations[2]+.7)*1000))
            page.get_by_role("button",name="Processar e comparar",exact=True).click()
            page.get_by_role("heading",name="Resumo executivo",exact=True).wait_for(timeout=30000)
            if page.get_by_text("ETAPA:",exact=False).count():raise RuntimeError("The UI reported a processing error.")
            page.get_by_role("heading",name="Diferenças e evidências",exact=True).scroll_into_view_if_needed()
            audio_starts.append(round(time.perf_counter()-start,3))
            page.screenshot(path=str(QA/"02_results.png"),full_page=True);screens.append("02_results.png")
            page.wait_for_timeout(int((durations[3]+.7)*1000))
            for label in ["Markdown","PDF","JSON"]:
                button=page.get_by_role("button",name=f"Baixar {label}",exact=True).last
                button.scroll_into_view_if_needed()
                with page.expect_download(timeout=15000) as download:
                    button.click()
                actual=download.value
                target=QA/"downloads"/actual.suggested_filename
                target.parent.mkdir(exist_ok=True);actual.save_as(target)
                if target.stat().st_size==0:raise RuntimeError("Empty download.")
                downloaded.append(str(target.relative_to(ROOT)))
                page.wait_for_timeout(1000)
            downloaded_json=next((QA/"downloads").glob("*.json"))
            downloaded_comparison=json.loads(downloaded_json.read_text(encoding="utf-8"))
            expected={"limite_maximo_garantia","retencao_franquia","side_a"}
            rows={item["field_name"]:item for item in downloaded_comparison["differences"]}
            if not all(rows[field]["classification"]=="mais_favoravel_A" for field in expected):
                raise RuntimeError("Actual browser download has unexpected comparison results.")
            page.screenshot(path=str(QA/"03_downloads.png"),full_page=True);screens.append("03_downloads.png")
            for index in [4,5]:
                page.set_content(html_scene(index))
                audio_starts.append(round(time.perf_counter()-start,3))
                page.wait_for_timeout(int((durations[index]+.7)*1000))
            elapsed=time.perf_counter()-start
            video_path=Path(page.video.path())
            context.close();browser.close()
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        output=ART/"InsurMinds_Projeto_Final.mp4"
        command=[ffmpeg,"-y","-i",str(video_path)]
        for index in range(len(SCENES)):command+=["-i",str(QA/f"voice_{index}.wav")]
        filters=[]
        for index,offset in enumerate(audio_starts):
            filters.append(f"[{index+1}:a]adelay={round(offset*1000)}:all=1[a{index}]")
        filters.append("".join(f"[a{i}]" for i in range(len(SCENES)))+f"amix=inputs={len(SCENES)}:duration=longest:normalize=0[a]")
        command+=["-filter_complex",";".join(filters),"-map","0:v","-map","[a]","-c:v","libx264","-preset","fast","-crf","24","-pix_fmt","yuv420p","-c:a","aac","-b:a","96k","-t",str(round(elapsed+.5,3)),"-movflags","+faststart",str(output)]
        conversion=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=90)
        if conversion.returncode:raise RuntimeError("Local MP4 encoding failed; ffmpeg output retained only in QA.")
        inspection=subprocess.run([ffmpeg,"-i",str(output),"-f","null","-"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=45)
        import re
        match=re.search(r"Duration: (\d+):(\d+):([\d.]+)",inspection.stderr)
        if inspection.returncode or not match:raise RuntimeError("MP4 full decode failed.")
        seconds=int(match[1])*3600+int(match[2])*60+float(match[3])
        if seconds>300:raise RuntimeError("Official five minute limit exceeded.")
        if errors:raise RuntimeError("Browser page exceptions were detected.")
        result={"status":"PASS","date":"2026-10-03","mode":"actual_streamlit_cached_live_results","inference_during_recording":False,"api_calls_during_recording":0,"live_evidence":str((LIVE/"diagnostics.json").relative_to(ROOT)),"uploads":2,"downloads":downloaded,"browser_errors":errors,"screenshots":screens,"video":str(output.relative_to(ROOT)),"duration_seconds":seconds,"full_mp4_decode":"PASS","narration":"local Windows pt-BR speech synthesis"}
        (QA/"diagnostics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=True))
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait()
        log.close()

if __name__=="__main__":
    main()
