"""Scan Git files and ZIP contents; report locations, never credential values."""
from pathlib import Path
import argparse, io, json, re, subprocess, zipfile
ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [re.compile(r"(?<![A-Za-z0-9])(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,})"), re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")]
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    git = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
    command = ["diff","--cached","--name-only","--diff-filter=ACM","-z"] if args.staged else ["ls-files","-z"]
    names = list(filter(None, subprocess.check_output(git+command,cwd=ROOT).decode().split("\0")))
    hits = []
    def scan(name,data):
        for number,line in enumerate(data.decode("utf-8",errors="replace").splitlines(),1):
            if any(pattern.search(line) for pattern in PATTERNS):
                hits.append({"file":name,"line":number})
    for name in names:
        if Path(name).name == ".env" or name.endswith("secrets.toml"):
            hits.append({"file":name,"reason":"secret_configuration_tracked"})
            continue
        data = subprocess.check_output(git+["show",f":{name}"],cwd=ROOT) if args.staged else (ROOT/name).read_bytes()
        if Path(name).suffix.lower() in {".zip", ".pptx"}:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for item in archive.infolist():
                    if not item.is_dir():scan(name+"::"+item.filename,archive.read(item))
        else:
            scan(name,data)
            if Path(name).suffix.lower()==".pdf":
                import pymupdf
                with pymupdf.open(stream=data,filetype="pdf") as document:
                    for index,page in enumerate(document,1):
                        scan(name+f"::page-{index}",page.get_text().encode("utf-8"))
    print(json.dumps({"files_checked":len(names),"secret_locations":hits,"status":"STOP" if hits else "PASS"}))
    return 1 if hits else 0
if __name__ == "__main__":raise SystemExit(main())
