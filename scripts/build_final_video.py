"""Build the academic video from the user's recorded real E2E, entirely locally.

Requires Pillow/imageio-ffmpeg, ffprobe and an installed Windows pt-BR voice.
Never opens a provider, synthesizes UI footage or republishes the raw master.
Editing sources are saved in video_versions; intermediates stay ignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import wave

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "Projeto_Final_Artefatos"
MASTER_SHA = "2971a61548ca1bf7ec0354a8e3669f6edb2b3f8fc85ace2133c1cd8fbced5aa4"
FONT_DIR = Path("C:/Windows/Fonts")
SCENES = [
    {"id":"problem","seconds":22,"title":"InsurMinds | Análise e comparação de D&O",
     "kind":"card","lines":["Documentos longos. Linguagem jurídica.","Coberturas, exclusões, defesa, limites e prazos.","Comparar exige localizar e conferir evidências."],
     "narration":"Documentos de seguro Dê e Ó são longos e juridicamente complexos. Comparar coberturas, exclusões, defesa, franquias, limites e prazos exige tempo de especialistas. O InsurMinds é um protótipo acadêmico que apoia essa análise."},
    {"id":"architecture","seconds":24,"title":"Arquitetura da solução","kind":"architecture",
     "narration":"A solução recebe documentos e extrai o texto nativo ou aplica Tesseract local. Cláusulas e trechos relevantes seguem para a inteligência artificial generativa. Depois da validação de estrutura e evidências, as informações são armazenadas e comparadas. A interface reúne resultados, revisão e exportação."},
    {"id":"upload","seconds":30,"title":"1. Documentos e referência","kind":"footage","clips":[[30,60,30]],
     "footer":"Execução real gravada | Recortes de condições gerais: 4 páginas por documento",
     "narration":"Nesta gravação real, foram utilizados dois recortes de condições gerais, com quatro páginas cada. O documento da Allianz contém texto nativo. O recorte da Porto foi digitalizado e não possui camada textual. Após o upload, o usuário define o documento de referência e inicia a comparação com um único comando. Condições gerais não equivalem a apólices individuais emitidas."},
    {"id":"processing","seconds":30,"title":"2. Leitura e identificação das condições","kind":"footage","clips":[[72,282,30]],
     "footer":"Processamento acelerado para demonstração | Espera original: aproximadamente 3 min 30 s",
     "narration":"A preparação é automática. O texto nativo é extraído diretamente, enquanto o documento digitalizado passa por reconhecimento óptico local. Em seguida, são identificadas e estruturadas as informações contratuais com apoio de inteligência artificial generativa. O sistema valida os trechos e preserva a origem documental. Este período da gravação foi acelerado para demonstração; o processamento real não foi instantâneo."},
    {"id":"summary","seconds":24,"title":"3. Resumo da comparação","kind":"footage","clips":[[293,317,24]],
     "footer":"Execução real: 2 diferenças documentadas | 19 condições sem conclusão segura",
     "narration":"O resumo apresenta os pontos que podem ser comparados e os que ainda exigem conferência. Nesta execução, aparecem duas diferenças documentadas e dezenove condições sem conclusão segura. A insuficiência de evidência permanece explícita. O sistema não inventa números de apólice, prêmios ou limites individuais que não constem nas condições gerais."},
    {"id":"differences","seconds":32,"title":"4. Diferenças com contexto e evidência","kind":"footage","clips":[[319,351,32]],
     "footer":"Uma diferença entre trechos não determina a melhor apólice como um todo",
     "narration":"Os resultados organizam as condições em abas de coberturas, limites e franquias, exclusões, cláusulas e evidências. O usuário pode examinar os valores e os trechos de cada documento lado a lado. Uma diferença textual precisa ser interpretada no seu escopo e nas suas condições. A ferramenta não declara que uma apólice inteira é automaticamente melhor que a outra."},
    {"id":"evidence","seconds":24,"title":"5. Conferência da página de origem","kind":"footage","clips":[[351,375,24]],
     "footer":"Documento + página + trecho | Evidência acessível para conferência",
     "narration":"Ao expandir a evidência, são mostrados o documento, a página e o trecho que sustentam a informação. A página original pode ser aberta para conferir o contexto completo. Essa rastreabilidade permite distinguir o que foi efetivamente localizado daquilo que continua ambíguo ou não foi recuperado com segurança."},
    {"id":"review","seconds":24,"title":"6. Revisão humana e exportação","kind":"footage","clips":[[529,543,14],[475,485,10]],
     "footer":"Revisão opcional registra anotações | Exportação mantém a rastreabilidade",
     "narration":"A revisão humana é opcional. É possível confirmar uma condição, registrar que precisa de revisão ou acrescentar uma correção. A anotação não transforma automaticamente uma informação incerta em fato contratual. Os resultados também podem ser exportados para apoiar a conferência e a apresentação do trabalho."},
    {"id":"ocr_ai","seconds":25,"title":"7. OCR local e uso real de IA","kind":"mixed","clips":[[545,558,13]],
     "footer":"Evidência persistida: 4 páginas nativas + 4 páginas com Tesseract",
     "narration":"A evidência registra quatro páginas nativas e quatro com Tesseract. A execução gravada utilizou inteligência artificial generativa, com trinta e sete requisições. O painel apresenta modelos, tokens e cache do provedor, separado do reaproveitamento local. Esses números não comprovam precisão perfeita."},
    {"id":"conclusion","seconds":26,"title":"Resultados, limites e evolução","kind":"card",
     "lines":["OCR + IA Generativa + comparação rastreável.","Abstenção quando a evidência é insuficiente.","MVP acadêmico: apoio à decisão, com revisão especializada."],
     "narration":"O projeto integrou leitura documental, OCR, inteligência artificial e comparação com evidências por página. É um protótipo acadêmico de apoio à decisão. Não substitui especialistas em seguros, revisão jurídica ou subscrição. A evolução prevê ampliar a avaliação e melhorar a qualidade da extração."},
]


def run_command(command, log_path=None):
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if log_path:
        Path(log_path).write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("Media command failed: " + result.stderr[-2000:])
    return result


def font(size, bold=False):
    return ImageFont.truetype(str(FONT_DIR / ("segoeuib.ttf" if bold else "segoeui.ttf")), size)


def wrapped(draw, text, size, width, bold=False):
    selected = font(size, bold)
    lines, pending = [], ""
    for word in text.split():
        proposed = pending + (" " if pending else "") + word
        if draw.textlength(proposed, font=selected) > width and pending:
            lines.append(pending)
            pending = word
        else:
            pending = proposed
    if pending:
        lines.append(pending)
    return lines


def card(path, scene, evidence=None):
    image = Image.new("RGB", (1920,1080), "#142f40")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0,0,1920,16), fill="#32bbc6")
    draw.text((110,76), "INSURMINDS • I2A2", font=font(34,True), fill="#82dfe3")
    for index,line in enumerate(wrapped(draw,scene["title"],66,1690,True)):
        draw.text((110,175+index*80),line,font=font(66,True),fill="white")
    y = 365
    for line in scene.get("lines",[]):
        for part in wrapped(draw,line,44,1600):
            draw.text((130,y),part,font=font(44),fill="#eef4f5")
            y += 61
        y += 38
    if scene["id"] == "conclusion":
        draw.text((110,850),"Vitor Ferreira • José Leonardo Alves Vilela • Wagner Assis",
                  font=font(32),fill="#a7cad2")
    if evidence:
        draw.text((110,840), evidence, font=font(31), fill="#a7cad2")
    draw.text((110,1000),"MVP acadêmico | Evidência primeiro | Revisão especializada permanece necessária",
              font=font(27),fill="#a7cad2")
    image.save(path)


def overlay(path, title, footer):
    image = Image.new("RGBA",(1920,1080),(0,0,0,0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0,0,1920,76),fill=(20,47,64,245))
    draw.text((42,14),title,font=font(36,True),fill="white")
    draw.rectangle((0,1006,1920,1080),fill=(20,47,64,245))
    for index,line in enumerate(wrapped(draw,footer,28,1810)):
        draw.text((42,1020+index*34),line,font=font(28),fill="#eef4f5")
    image.save(path)


def encode_options():
    return ["-c:v","libx264","-preset","medium","-crf","20","-maxrate","2300k",
            "-bufsize","4600k","-pix_fmt","yuv420p","-r","30","-threads","4",
            "-an","-movflags","+faststart"]


def encode_still(ffmpeg, image, seconds, output):
    run_command([ffmpeg,"-hide_banner","-loglevel","error","-y","-loop","1","-i",str(image),
                 "-t",str(seconds),*encode_options(),str(output)])


def encode_clip(ffmpeg, master, clip, decoration, output):
    start,end,seconds = clip
    speed = (end-start)/seconds
    graph = (f"[0:v]crop=1600:900:300:95,setpts=(PTS-STARTPTS)/{speed:.10f},"
             "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p[base];"
             "[base][1:v]overlay=0:0:format=auto[out]")
    run_command([ffmpeg,"-hide_banner","-loglevel","error","-y","-ss",str(start),
                 "-t",str(end-start),"-i",str(master),"-loop","1","-i",str(decoration),
                 "-filter_complex",graph,"-map","[out]","-t",str(seconds),
                 *encode_options(),str(output)])


def concatenate(ffmpeg, paths, output, list_path):
    list_path.write_text("\n".join("file '" + str(path.resolve()).replace("\\","/").replace("'","'\\''") + "'"
                                   for path in paths)+"\n",encoding="utf-8")
    run_command([ffmpeg,"-hide_banner","-loglevel","error","-y","-f","concat","-safe","0",
                 "-i",str(list_path),"-c","copy","-movflags","+faststart",str(output)])


def timestamp(seconds):
    return f"{int(seconds)//60:02d}:{int(seconds)%60:02d}"


def synthesize(work, voice):
    texts = work / "narration_texts.json"
    texts.write_text(json.dumps([scene["narration"] for scene in SCENES],ensure_ascii=False),encoding="utf-8")
    script = work / "narrate.ps1"
    script.write_text("""Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.SelectVoice('""" + voice.replace("'","''") + """')
$speaker.Rate = 1
$speaker.Volume = 100
$texts = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'narration_texts.json') -Raw -Encoding UTF8 | ConvertFrom-Json
for ($i = 0; $i -lt $texts.Count; $i++) {
    $speaker.SetOutputToWaveFile((Join-Path $PSScriptRoot ('voice_' + $i + '.wav')))
    $speaker.Speak($texts[$i])
    $speaker.SetOutputToNull()
}
$speaker.Dispose()
""",encoding="utf-8-sig")
    run_command(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-File",str(script)])


def probe(ffprobe, path):
    data = json.loads(run_command([str(ffprobe),"-v","error","-show_format","-show_streams",
                                  "-of","json",str(path)]).stdout)
    video = next(item for item in data["streams"] if item["codec_type"] == "video")
    audios = [item for item in data["streams"] if item["codec_type"] == "audio"]
    numerator,denominator = map(int,video["avg_frame_rate"].split("/"))
    return {"duration_seconds":float(data["format"]["duration"]),
            "width":video["width"],"height":video["height"],"fps":numerator/denominator,
            "video_codec":video["codec_name"],"audio_codec":audios[0]["codec_name"] if audios else None,
            "audio_sample_rate":int(audios[0]["sample_rate"]) if audios else None,
            "size_bytes":path.stat().st_size,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}


def build(args):
    master = Path(args.master).resolve()
    official = ARTIFACTS / "InsurMinds_Projeto_Final.mp4"
    versions = ARTIFACTS / "video_versions"
    versions.mkdir(parents=True,exist_ok=True)
    work = ROOT / "data/processed/final_release/video_build"
    work.mkdir(parents=True,exist_ok=True)
    preflight = ROOT / "data/processed/final_release/video_inspection/visual_secret_preflight.json"
    preflight_data = json.loads(preflight.read_text(encoding="utf-8"))
    master_digest = hashlib.sha256(master.read_bytes()).hexdigest()
    if master_digest != MASTER_SHA or preflight_data["master_sha256"] != master_digest or preflight_data["status"] != "PASS":
        raise RuntimeError("Master identity/visual secret preflight must be validated before rendering.")
    backups = ROOT / "data/processed/final_release/backups"
    backups.mkdir(parents=True,exist_ok=True)
    for source in (master,official):
        if source.is_file():
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            backup = backups / (source.stem + "-" + digest[:12] + source.suffix)
            if not backup.is_file():
                shutil.copy2(source,backup)
            assert hashlib.sha256(backup.read_bytes()).hexdigest() == digest
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    ffprobe = Path(args.ffprobe) if args.ffprobe else Path(
        shutil.which("ffprobe") or ROOT / "data/processed/tooling/final_video/ffprobe.exe")
    segments=[]
    previous_plan=json.loads((versions/"video_edit_plan.json").read_text(encoding="utf-8")) if args.reuse_visual_edit else None
    reusable=set()
    if previous_plan:
        visual_keys=("id","seconds","title","kind","clips","lines","footer")
        assert previous_plan["master_sha256"]==master_digest
        previous_scenes={row["id"]:row for row in previous_plan["scenes"]}
        reusable={scene["id"] for scene in SCENES if scene["id"] in previous_scenes and
                  {key:previous_scenes[scene["id"]].get(key) for key in visual_keys}==
                  {key:scene.get(key) for key in visual_keys}}
    for scene in SCENES:
        prefix = work / scene["id"]
        if scene["id"] in reusable and prefix.with_suffix(".mp4").is_file():
            segments.append(prefix.with_suffix(".mp4"))
            print("reused valid visual segment",scene["id"],flush=True)
            continue
        if scene["kind"] == "card":
            picture = prefix.with_suffix(".png")
            card(picture,scene)
            segment = prefix.with_suffix(".mp4")
            encode_still(ffmpeg,picture,scene["seconds"],segment)
        elif scene["kind"] == "architecture":
            picture = ARTIFACTS / "InsurMinds_Arquitetura.png"
            if not picture.is_file():
                raise RuntimeError("Final architecture diagram is required.")
            segment = prefix.with_suffix(".mp4")
            encode_still(ffmpeg,picture,scene["seconds"],segment)
        else:
            decoration = work / (scene["id"]+"_overlay.png")
            overlay(decoration,scene["title"],scene["footer"])
            clips=[]
            if scene["kind"] == "mixed":
                picture = work / "ocr_proof.png"
                card(picture,{"id":"ocr_proof","title":"OCR confirmado na evidência persistida",
                    "lines":["IM-ALLIANZ_native.pdf: 4 páginas • texto nativo",
                             "IM-PORTO_scan_OCR.pdf: 4 páginas • OCR local",
                             'extraction_method: "tesseract"',
                             "37 requisições na execução real gravada"]},
                    evidence="Fonte: registros locais da execução 9086f70… | Recortes controlados de wordings")
                clip = work / "ocr_proof.mp4"
                encode_still(ffmpeg,picture,12,clip)
                clips.append(clip)
            for index,clip in enumerate(scene["clips"]):
                output = work / (scene["id"]+f"_{index}.mp4")
                encode_clip(ffmpeg,master,clip,decoration,output)
                clips.append(output)
            segment = prefix.with_suffix(".mp4")
            concatenate(ffmpeg,clips,segment,work/(scene["id"]+"_concat.txt"))
        segments.append(segment)
        print("rendered",scene["id"],scene["seconds"],flush=True)
    silent = versions / "InsurMinds_Projeto_Final_SEM_NARRACAO.mp4"
    concatenate(ffmpeg,segments,silent,work/"visual_concat.txt")
    elapsed = 0
    script_text = ["# Roteiro de narração — InsurMinds","",
        "Fonte visual: gravação E2E real do usuário. Recortes de 4 páginas de cada wording.",
        "Voz local Windows, português brasileiro; nenhuma síntese ou inferência externa.",
        "O período de processamento foi acelerado e identificado na tela.",""]
    editing=[]
    for scene in SCENES:
        start,end=elapsed,elapsed+scene["seconds"]
        script_text += [f"## {timestamp(start)}–{timestamp(end)}",
                        "Visual: "+scene["title"],"","Narração: "+scene["narration"],""]
        editing.append({"start_seconds":start,"end_seconds":end,**scene})
        elapsed=end
    (versions/"ROTEIRO_NARRACAO_PTBR.md").write_text("\n".join(script_text),encoding="utf-8")
    (versions/"video_edit_plan.json").write_text(json.dumps({"master_sha256":master_digest,
        "duration_seconds":elapsed,"crop":[300,95,1600,900],"voice":args.voice,
        "real_e2e_source":"9086f70…; 4 pages native + 4 scanned; 37 recorded requests",
        "scenes":editing},indent=2,ensure_ascii=False),encoding="utf-8")
    narration_status="BLOCKER"
    narrated=versions/"InsurMinds_Projeto_Final_COM_NARRACAO.mp4"
    audio_durations=[]
    if not args.no_narration:
        synthesize(work,args.voice)
        normalized=[]
        for index,scene in enumerate(SCENES):
            audio=work/f"voice_{index}.wav"
            with wave.open(str(audio)) as stream:
                duration=stream.getnframes()/stream.getframerate()
            audio_durations.append(duration)
            factor=max(1,duration/(scene["seconds"]-.8))
            if factor>1.2:
                raise RuntimeError(f"Narration too long for scene {scene['id']}; edit script instead of rushing speech.")
            normalized_audio=work/f"normalized_{index}.wav"
            filters=(f"atempo={factor:.8f},loudnorm=I=-16:TP=-1.5:LRA=8,"
                     f"adelay=400,apad,atrim=duration={scene['seconds']}")
            run_command([ffmpeg,"-hide_banner","-loglevel","error","-y","-i",str(audio),
                "-af",filters,"-ar","48000","-ac","1","-c:a","pcm_s16le",str(normalized_audio)])
            # Keep chapter timing exact even when a filter changes the audio time base.
            with wave.open(str(normalized_audio), "rb") as stream:
                parameters = stream.getparams()
                samples = stream.readframes(stream.getnframes())
            required_bytes = scene["seconds"] * parameters.framerate * parameters.nchannels * parameters.sampwidth
            samples = samples[:required_bytes].ljust(required_bytes, b"\x00")
            with wave.open(str(normalized_audio), "wb") as stream:
                stream.setparams(parameters)
                stream.writeframes(samples)
            normalized.append(normalized_audio)
        narration=work/"narration.wav"
        concatenate(ffmpeg,normalized,narration,work/"audio_concat.txt")
        run_command([ffmpeg,"-hide_banner","-loglevel","error","-y","-i",str(silent),
            "-i",str(narration),"-map","0:v:0","-map","1:a:0","-c:v","copy",
            "-c:a","aac","-b:a","128k","-ar","48000","-movflags","+faststart","-shortest",str(narrated)])
        narration_status="PASS"
    selected = narrated if args.official_candidate == "narrated" and narration_status == "PASS" else silent
    if not ffprobe.is_file():
        raise RuntimeError("ffprobe is required before selecting/publishing the official video.")
    master_metadata = probe(ffprobe,master)
    validated={}
    for path in ([silent,narrated] if narration_status=="PASS" else [silent]):
        metadata=probe(ffprobe,path)
        assert metadata["duration_seconds"]<=300 and metadata["size_bytes"]<95_000_000,metadata
        assert (metadata["width"],metadata["height"],metadata["video_codec"])==(1920,1080,"h264")
        assert abs(metadata["fps"]-30)<.01
        run_command([ffmpeg,"-hide_banner","-v","error","-i",str(path),"-f","null","-"],
                    work/(path.stem+"_decode.log"))
        metadata["decode_integrity"]="PASS"
        validated[path.name]=metadata
    if narration_status=="PASS":
        metrics=run_command([ffmpeg,"-hide_banner","-i",str(narrated),"-vn","-af","volumedetect","-f","null","-"])
        (work/"audio_metrics.log").write_text(metrics.stderr,encoding="utf-8")
        hashes=[]
        for path in (silent,narrated):
            hashes.append(run_command([ffmpeg,"-hide_banner","-loglevel","error","-i",str(path),
                "-map","0:v:0","-c","copy","-f","hash","-hash","sha256","-"]).stdout.strip())
        assert hashes[0]==hashes[1],"Narrated and silent versions must share the exact same visual edit."
    shutil.copy2(selected,official)
    official_metadata=probe(ffprobe,official)
    official_metadata["decode_integrity"] = validated[selected.name]["decode_integrity"]
    assert official_metadata["sha256"]==validated[selected.name]["sha256"]
    assert hashlib.sha256(master.read_bytes()).hexdigest()==master_digest
    outcome={"status":"PASS","master":master_metadata,"master_unchanged":True,"backups":str(backups),
        "candidates":validated,"official":official_metadata,"selected_candidate":selected.name,
        "narration_quality":"PENDING_HUMAN_LISTENING" if narration_status == "PASS" else "BLOCKER",
        "narration_technical_quality":narration_status,
        "narration_tts_quality_blocker":True,
        "perceptual_listening":"Unavailable: this agent's audio input channel does not support audio; no listening claim.",
        "official_selection_reason":"Validated silent candidate satisfies official requirements without unverified perceptual audio quality.",
        "tts_method":"Windows System.Speech; offline system voice",
        "tts_voice":args.voice if narration_status=="PASS" else None,
        "narration_original_segment_seconds":audio_durations,"same_visual_edit":True,
        "visual_secret_preflight":preflight_data,"provider_requests_during_generation":0,
        "limitations":["Recorded E2E is a controlled 4+4 page excerpt, not complete 75/52-page originals.",
                       "TTS is an installed system voice, not a claimed neural narrator.",
                       "No claim of perfect semantic accuracy or globally best policy."],
        "official_requirements":["problem","architecture","application_functioning","main_results"]}
    (work/"video_validation.json").write_text(json.dumps(outcome,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(outcome,ensure_ascii=True),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master",default=str(ARTIFACTS/"source/InsurMinds_E2E_master.mp4"))
    parser.add_argument("--ffprobe")
    parser.add_argument("--voice",default="Microsoft Daniel")
    parser.add_argument("--no-narration",action="store_true")
    parser.add_argument("--official-candidate", choices=["silent","narrated"], default="silent",
                        help="Select silent unless a human has confirmed narrated voice quality.")
    parser.add_argument("--reuse-visual-edit",action="store_true",help="Reuse matching locally rendered visual segments while revising narration.")
    build(parser.parse_args())
