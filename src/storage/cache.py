"""Cache local em JSON gravado atomicamente; conteúdo nunca é registrado em logs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JsonCache:
    """Cache por chave SHA-256, com escrita atômica para evitar arquivos parciais."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        path = self.path_for(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, key: str, data: dict[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.path_for(key)
        encoded = json.dumps(data, ensure_ascii=False, indent=2)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f".{key}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_file.write(encoded)
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return destination


def make_cache_key(*, file_sha256: str, pipeline_version: str, options: dict[str, Any]) -> str:
    """Combina hash do arquivo, versão do pipeline e opções que afetam o OCR."""
    payload = json.dumps(
        {"file_sha256": file_sha256, "pipeline_version": pipeline_version, "options": options},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
