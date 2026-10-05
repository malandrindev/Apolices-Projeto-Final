"""Extrai texto por página e aplica Tesseract local somente quando necessário."""

from __future__ import annotations

import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

import pymupdf
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.agents.ingestion import IngestedDocument
from src.config import IngestionSettings
from src.storage.cache import JsonCache, make_cache_key

OCR_PIPELINE_VERSION = "ocr-page-v1"


class OcrError(RuntimeError):
    """Falha local de OCR com mensagem que não inclui o conteúdo do documento."""


class PageText(BaseModel):
    """Texto com proveniência de página, método, resolução e tempo de extração."""

    model_config = ConfigDict(extra="forbid")
    page_number: int = Field(ge=1)
    text: str
    extraction_method: Literal["native", "tesseract"]
    render_dpi: int | None = Field(default=None, ge=72)
    duration_ms: float = Field(default=0.0, ge=0)


class ProcessedDocument(BaseModel):
    """Contrato consumido pela segmentação e pelo cache local."""

    model_config = ConfigDict(extra="forbid")
    source_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    media_type: str
    pages: list[PageText] = Field(min_length=1)
    processed_at: str
    cache_key: str
    cache_hit: bool = False

    @model_validator(mode="after")
    def validate_page_sequence(self) -> ProcessedDocument:
        if [page.page_number for page in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("As páginas devem ser contíguas e iniciar em 1.")
        return self


class OcrAgent:
    """Mantém cache atômico completo e checkpoint local de cada página concluída."""

    def __init__(
        self, settings: IngestionSettings,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.progress_callback = progress_callback
        self.cache = JsonCache(settings.processed_dir)
        self.page_cache = JsonCache(settings.processed_dir / "ocr_pages")
        self._tesseract_ready = False

    def extract(self, document: IngestedDocument) -> ProcessedDocument:
        cache_key = make_cache_key(
            file_sha256=document.sha256,
            pipeline_version=OCR_PIPELINE_VERSION,
            options={
                "languages": self.settings.ocr_languages,
                "min_native_chars": self.settings.min_native_chars,
                "render_dpi": self.settings.render_dpi,
                "timeout_seconds": self.settings.ocr_timeout_seconds,
            },
        )
        cached = self.cache.load(cache_key)
        if cached:
            try:
                result = ProcessedDocument.model_validate(cached)
                if (
                    result.sha256 == document.sha256
                    and result.size_bytes == document.size_bytes
                    and result.media_type == document.media_type
                    and result.cache_key == cache_key
                    and len(result.pages) == document.page_count
                ):
                    for page in result.pages:
                        self._notify(page.page_number, document.page_count, "cache")
                    return result.model_copy(
                        update={"source_name": document.source_name, "cache_hit": True}
                    )
            except ValidationError:
                pass
        pages = self._load_progress(cache_key, document)
        try:
            if document.media_type == "application/pdf":
                self._extract_pdf(document, cache_key, pages)
            else:
                self._extract_image(document, cache_key, pages)
            result = ProcessedDocument(
                source_name=document.source_name, sha256=document.sha256,
                size_bytes=document.size_bytes, media_type=document.media_type,
                pages=[pages[number] for number in range(1, document.page_count + 1)],
                processed_at=datetime.now(timezone.utc).isoformat(), cache_key=cache_key,
            )
            self.cache.save(cache_key, result.model_dump(mode="json"))
        except OcrError:
            raise
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            raise OcrError("Não foi possível concluir a extração local do documento.") from error
        try:
            self.page_cache.path_for(cache_key).unlink(missing_ok=True)
        except OSError:
            pass
        return result

    def _notify(self, number: int, total: int, method: str) -> None:
        """Notifica somente progresso e método, sem texto do documento."""
        if self.progress_callback is not None:
            self.progress_callback(number, total, method)

    def _load_progress(self, key: str, document: IngestedDocument) -> dict[int, PageText]:
        progress = self.page_cache.load(key)
        pages: dict[int, PageText] = {}
        if not progress or progress.get("cache_key") != key or progress.get("sha256") != document.sha256:
            return pages
        raw_pages = progress.get("pages")
        if not isinstance(raw_pages, list):
            return pages
        for item in raw_pages:
            try:
                page = PageText.model_validate(item)
            except ValidationError:
                continue
            if page.page_number <= document.page_count:
                pages[page.page_number] = page
        return pages

    def _checkpoint(self, key: str, document: IngestedDocument, pages: dict[int, PageText]) -> None:
        self.page_cache.save(
            key,
            {
                "cache_key": key, "sha256": document.sha256,
                "pages": [pages[number].model_dump(mode="json") for number in sorted(pages)],
            },
        )

    def _extract_pdf(self, document: IngestedDocument, key: str, pages: dict[int, PageText]) -> None:
        with pymupdf.open(document.source_path) as pdf:
            if pdf.needs_pass or pdf.page_count != document.page_count:
                raise OcrError("O PDF mudou ou passou a exigir senha após a ingestão.")
            for number in range(1, pdf.page_count + 1):
                if number in pages:
                    self._notify(number, document.page_count, "cache")
                    continue
                started = time.perf_counter()
                page = pdf.load_page(number - 1)
                text = page.get_text("text").strip()
                method: Literal["native", "tesseract"] = "native"
                dpi = None
                if len(text) < self.settings.min_native_chars:
                    pixmap = page.get_pixmap(
                        dpi=self.settings.render_dpi, colorspace=pymupdf.csRGB, alpha=False
                    )
                    try:
                        text = self._ocr_pixmap(pixmap).strip()
                    except OcrError as error:
                        raise OcrError(f"Página {number}: {error}") from error
                    method = "tesseract"
                    dpi = self.settings.render_dpi
                pages[number] = PageText(
                    page_number=number, text=text, extraction_method=method,
                    render_dpi=dpi, duration_ms=round((time.perf_counter() - started) * 1000, 3),
                )
                self._checkpoint(key, document, pages)
                self._notify(number, document.page_count, pages[number].extraction_method)

    def _extract_image(self, document: IngestedDocument, key: str, pages: dict[int, PageText]) -> None:
        with Image.open(document.source_path) as image:
            for number in range(1, document.page_count + 1):
                if number in pages:
                    self._notify(number, document.page_count, "cache")
                    continue
                started = time.perf_counter()
                image.seek(number - 1)
                frame = image.convert("RGB")
                try:
                    text = self._ocr_image(frame).strip()
                except OcrError as error:
                    raise OcrError(f"Página {number}: {error}") from error
                finally:
                    frame.close()
                pages[number] = PageText(
                    page_number=number, text=text, extraction_method="tesseract",
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                )
                self._checkpoint(key, document, pages)
                self._notify(number, document.page_count, pages[number].extraction_method)

    def _ocr_pixmap(self, pixmap: pymupdf.Pixmap) -> str:
        with Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples) as image:
            return self._ocr_image(image)

    def _ensure_tesseract(self) -> None:
        if self._tesseract_ready:
            return
        try:
            import pytesseract
        except ImportError as error:
            raise OcrError("Instale as dependências Python de OCR pelo requirements.txt.") from error
        requested = self.settings.ocr_languages.split("+")
        if not all(re.fullmatch(r"[A-Za-z0-9_-]+", language) for language in requested):
            raise OcrError("OCR_LANGUAGES deve listar idiomas Tesseract separados por +.")
        configured = os.getenv("TESSERACT_CMD", "").strip().strip('"')
        if configured:
            executable = shutil.which(configured)
            if not executable and Path(configured).is_file():
                executable = configured
        else:
            executable = shutil.which("tesseract")
        if not executable and not configured and os.name == "nt":
            for root in (os.getenv("ProgramFiles"), os.getenv("ProgramFiles(x86)")):
                if root:
                    candidate = Path(root) / "Tesseract-OCR" / "tesseract.exe"
                    if candidate.is_file():
                        executable = str(candidate)
                        break
        if not executable:
            raise OcrError(
                "Tesseract não encontrado. Instale o mecanismo local e coloque-o no PATH "
                "ou defina TESSERACT_CMD com o caminho do executável."
            )
        pytesseract.pytesseract.tesseract_cmd = str(executable)
        try:
            available = set(pytesseract.get_languages(config=""))
        except (OSError, RuntimeError) as error:
            raise OcrError("Não foi possível executar o Tesseract; confira PATH/TESSERACT_CMD.") from error
        missing = sorted(set(requested) - available)
        if missing:
            raise OcrError(
                "Idiomas Tesseract ausentes: " + ", ".join(missing)
                + ". Instale os dados desses idiomas ou ajuste OCR_LANGUAGES."
            )
        self._tesseract_ready = True

    def _ocr_image(self, image: Image.Image) -> str:
        self._ensure_tesseract()
        import pytesseract
        try:
            return pytesseract.image_to_string(
                image, lang=self.settings.ocr_languages, timeout=self.settings.ocr_timeout_seconds,
            )
        except RuntimeError as error:
            if "timeout" in str(error).lower():
                raise OcrError("O OCR excedeu o tempo limite configurado para a página.") from error
            raise OcrError("O Tesseract falhou ao reconhecer o texto da página.") from error
        except OSError as error:
            raise OcrError("Não foi possível executar o Tesseract local.") from error
