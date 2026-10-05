"""Package the committed local delivery, excluding credentials and runtime caches.

Creates a real ZIP from HEAD. Run only after reviewing/checkpointing the deliverables.
The generated ZIP excludes itself and is intentionally ignored by Git.
"""
from __future__ import annotations
import hashlib, io, json, subprocess, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/"Projeto_Final_Artefatos"/"InsurMinds_Codigo_e_Artefatos.zip"
REQUIRED=[
 "README.md","LICENSE",".env.example","requirements.txt","main.py",
 "docs/REQUIREMENTS_TRACEABILITY.md","docs/DELIVERY_CHECKLIST.md",
 "Projeto_Final_Artefatos/InsurMinds_Relatorio_Tecnico.pdf",
 "Projeto_Final_Artefatos/InsurMinds_Projeto_Final.pptx",
 "Projeto_Final_Artefatos/InsurMinds_Projeto_Final.mp4",
]

def main():
    git=["git","-c",f"safe.directory={ROOT.as_posix()}"]
    changed=subprocess.check_output(git+["status","--porcelain","--untracked-files=no"],cwd=ROOT).decode()
    if changed.strip():raise RuntimeError("Commit tracked changes before packaging HEAD.")
    revision=subprocess.check_output(git+["rev-parse","HEAD"],cwd=ROOT).decode().strip()
    raw=subprocess.check_output(git+["archive","--format=zip","HEAD"],cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        names=package.namelist()
        missing=[name for name in REQUIRED if name not in names]
        if missing:raise RuntimeError("Required deliverables are missing from the reviewed commit.")
        forbidden=[name for name in names if not name.endswith("/") and (Path(name).name in {".env","secrets.toml"}
                   or name.startswith((".git/",".venv/","data/processed/")) and name!="data/processed/.gitkeep")]
        if forbidden:raise RuntimeError("Runtime/secret files detected in package; not written.")
        if package.testzip() is not None:raise RuntimeError("ZIP integrity failed.")
    DEST.parent.mkdir(exist_ok=True)
    DEST.write_bytes(raw)
    summary={"status":"PASS","source_commit":revision,"zip":str(DEST.relative_to(ROOT)),
             "files":len(names),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),
             "integrity":"PASS","secrets_and_runtime_paths_excluded":True,
             "limitations":["Team identification and final publication/submission are pending.",
                            "Execution after extraction must be recorded separately; package integrity alone does not prove runtime."]}
    local=ROOT/"data"/"processed"/"delivery_package"
    local.mkdir(parents=True,exist_ok=True)
    (local/"package_manifest.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=True))

if __name__=="__main__":
    main()
