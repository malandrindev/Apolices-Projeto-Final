"""Create the clean academic snapshot and ZIP without providers or publication.

Use after staging the reviewed source/artifacts. Exclusions keep private runtime,
raw footage, alternate renders and obsolete ZIPs outside the final snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_secrets import PATTERNS
PREFIXES = ("src/", "interface/", "tests/", "scripts/", "docs/", "data/demo/",
            "data/demo_sources/", "data/samples/", "Projeto_Final_Artefatos/")
ROOT_FILES = {"README.md", "LICENSE", ".env.example", ".gitignore", ".gitattributes", "pytest.ini", "main.py"}
REQUIRED = ["README.md", "LICENSE", ".env.example", "requirements.txt", "requirements-dev.txt",
            "src/pipeline.py", "interface/app.py", "docs/FINAL_DELIVERY_VALIDATION.md",
            "docs/InsurMinds_Relatorio_Tecnico.md", "docs/DELIVERY_CHECKLIST.md",
            "Projeto_Final_Artefatos/InsurMinds_Relatorio_Tecnico.pdf",
            "Projeto_Final_Artefatos/InsurMinds_Projeto_Final.pptx",
            "Projeto_Final_Artefatos/InsurMinds_Projeto_Final.mp4"]


def publishable(name: str) -> bool:
    path = Path(name)
    if any(part in {".git", ".venv", "__pycache__", ".pytest_cache", ".codex", ".agents"} for part in path.parts):
        return False
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        return False
    if path.name == "secrets.toml" or path.suffix.lower() in {".zip", ".pyc", ".wav", ".tmp", ".log"}:
        return False
    if name.startswith(("data/processed/", "Projeto_Final_Artefatos/source/")):
        return False
    if name.startswith("Projeto_Final_Artefatos/video_versions/"):
        return name == "Projeto_Final_Artefatos/video_versions/ROTEIRO_NARRACAO_PTBR.md"
    return name in ROOT_FILES or (name.startswith("requirements") and path.suffix == ".txt") or name.startswith(PREFIXES)


def scan_bytes(name: str, data: bytes) -> list[dict]:
    hits = []
    def scan_text(label, raw):
        for index, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                hits.append({"file": label, "line": index})
    scan_text(name, data)
    if name.lower().endswith(".pdf"):
        import pymupdf
        with pymupdf.open(stream=data, filetype="pdf") as document:
            scan_text(name + "::metadata", json.dumps(document.metadata).encode())
            for index, page in enumerate(document, 1):
                scan_text(name + "::page-" + str(index), page.get_text().encode())
    elif name.lower().endswith(".pptx"):
        import io
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for item in archive.infolist():
                if not item.is_dir():
                    scan_text(name + "::" + item.filename, archive.read(item))
    return hits


def source_names() -> list[str]:
    git = ["git", "-c", "safe.directory=" + ROOT.as_posix()]
    names = subprocess.check_output(git + ["ls-files", "-z"], cwd=ROOT).decode().split("\0")
    selected = sorted(name for name in names if name and publishable(name) and (ROOT / name).is_file())
    missing = sorted(set(REQUIRED) - set(selected))
    if missing:
        raise ValueError("Reviewed final files must be staged first: " + ", ".join(missing))
    return selected


def build(snapshot: Path, destination: Path) -> dict:
    snapshot = snapshot.resolve()
    # Only fresh local build directories are accepted; nothing is deleted or moved.
    snapshot.relative_to((ROOT / "data/processed/final_release").resolve())
    if snapshot.exists():
        raise ValueError("Use a fresh snapshot directory; prior release evidence is preserved.")
    destination = destination.resolve()
    destination.relative_to(ROOT)
    if destination.suffix.lower() != ".zip":
        raise ValueError("Delivery destination must be a ZIP inside this project.")
    selected = source_names()
    hits = []
    for name in selected:
        path = (ROOT / name).resolve()
        path.relative_to(ROOT.resolve())
        if path.stat().st_size >= 95_000_000:
            raise ValueError("Publishable blob exceeds the delivery target: " + name)
        hits.extend(scan_bytes(name, path.read_bytes()))
    if hits:
        raise ValueError("Secrets detected; no snapshot or ZIP created: " + json.dumps(hits))
    snapshot.mkdir(parents=True)
    files = {}
    for name in selected:
        data = (ROOT / name).read_bytes()
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    # An empty runtime directory enables a fresh installation without shipping caches.
    runtime = snapshot / "data/processed"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / ".gitkeep").write_bytes(b"")
    files["data/processed/.gitkeep"] = hashlib.sha256(b"").hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            archive.write(snapshot / name, name)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert all(item in names for item in REQUIRED)
        assert all(Path(name).name != ".env" for name in names)
        assert all(publishable(name) or name == "data/processed/.gitkeep" for name in names)
        zip_hits = [hit for name in names for hit in scan_bytes(name, archive.read(name))]
        assert not zip_hits, zip_hits
    summary = {"status": "PASS", "snapshot": str(snapshot), "zip": str(destination),
               "files": files, "entries": len(files), "bytes": destination.stat().st_size,
               "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
               "zip_secret_locations": zip_hits, "env_absent": True,
               "required_artifacts_present": True, "top_level": sorted({Path(name).parts[0] for name in files})}
    (snapshot.parent / (snapshot.name + "-manifest.json")).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--zip", type=Path, default=ROOT / "InsurMinds_Projeto_Final.zip")
    args = parser.parse_args()
    summary = build(args.snapshot, args.zip)
    print(json.dumps({key:value for key,value in summary.items() if key != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
