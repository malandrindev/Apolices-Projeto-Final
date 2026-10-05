"""Valida documentos locais e identifica seu conteúdo sem usar serviços externos."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

import pymupdf
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from src.config import IngestionSettings


class IngestionError(ValueError):
    """Documento ausente, inválido ou incompatível com os limites do MVP."""


class IngestedDocument(BaseModel):
    """Metadados locais; o caminho original não entra nos relatórios públicos."""

    model_config = ConfigDict(frozen=True)

    source_path: Path
    source_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    media_type: str
    page_count: int = Field(gt=0)


class IngestionAgent:
    """Aceita PDF, PNG, JPEG, TIFF e BMP, verificando extensão e assinatura."""

    MEDIA_TYPES: ClassVar[dict[str, str]] = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
    }
    IMAGE_FORMATS: ClassVar[dict[str, str]] = {
        "image/png": "PNG", "image/jpeg": "JPEG", "image/tiff": "TIFF", "image/bmp": "BMP",
    }

    def __init__(self, settings: IngestionSettings) -> None:
        self.settings = settings

    def ingest(self, source: str | Path) -> IngestedDocument:
        """Valida o arquivo antes de calcular seu hash e processar qualquer página."""
        path = Path(source).expanduser()
        media_type = self.MEDIA_TYPES.get(path.suffix.lower())
        if media_type is None:
            raise IngestionError("Formato não suportado. Use PDF, PNG, JPEG, TIFF ou BMP.")
        try:
            if not path.is_file():
                raise IngestionError("O documento não existe ou não é um arquivo regular.")
            size_bytes = path.stat().st_size
            if size_bytes == 0:
                raise IngestionError("O documento está vazio.")
            if size_bytes > self.settings.max_document_mb * 1024 * 1024:
                raise IngestionError("O documento excede o limite de tamanho configurado.")
            digest = hashlib.sha256()
            with path.open("rb") as document_file:
                signature = document_file.read(16)
                digest.update(signature)
                while chunk := document_file.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as error:
            raise IngestionError("Não foi possível ler o documento local.") from error
        if not self._has_signature(signature, media_type):
            if media_type == "application/pdf":
                raise IngestionError("O arquivo não parece ser um PDF válido.")
            raise IngestionError("A assinatura da imagem não corresponde ao formato informado.")
        page_count = (
            self._validate_pdf(path)
            if media_type == "application/pdf"
            else self._validate_image(path, media_type)
        )
        return IngestedDocument(
            source_path=path.resolve(), source_name=path.name,
            sha256=digest.hexdigest(), size_bytes=size_bytes,
            media_type=media_type, page_count=page_count,
        )

    @staticmethod
    def _has_signature(signature: bytes, media_type: str) -> bool:
        if media_type == "application/pdf":
            return signature.startswith(b"%PDF-")
        if media_type == "image/png":
            return signature.startswith(b"\x89PNG\r\n\x1a\n")
        if media_type == "image/jpeg":
            return signature.startswith(b"\xff\xd8\xff")
        if media_type == "image/tiff":
            return signature[:4] in {b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"}
        return media_type == "image/bmp" and signature.startswith(b"BM")

    @staticmethod
    def _validate_pdf(path: Path) -> int:
        try:
            with pymupdf.open(path) as document:
                if not document.is_pdf:
                    raise IngestionError("O arquivo não parece ser um PDF válido.")
                if document.needs_pass:
                    raise IngestionError("PDF protegido por senha; forneça uma cópia desbloqueada.")
                if document.page_count < 1:
                    raise IngestionError("O PDF não contém páginas para processar.")
                for number in range(document.page_count):
                    document.load_page(number)
                return document.page_count
        except IngestionError:
            raise
        except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError, ValueError, OSError) as error:
            raise IngestionError("Não foi possível abrir o PDF; o documento pode estar corrompido.") from error

    @classmethod
    def _validate_image(cls, path: Path, media_type: str) -> int:
        try:
            with Image.open(path) as image:
                if image.format != cls.IMAGE_FORMATS[media_type]:
                    raise IngestionError("A imagem não corresponde ao formato informado.")
                page_count = getattr(image, "n_frames", 1)
                image.verify()
            return page_count
        except IngestionError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
            raise IngestionError("Não foi possível abrir a imagem; o documento pode estar corrompido.") from error
