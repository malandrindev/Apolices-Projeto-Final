"""Casos de falha e retomada de OCR; nenhuma chamada externa ou engine é necessária."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PIL import Image

from src.agents.ingestion import IngestionAgent, IngestionError
from src.agents.ocr import OcrAgent, OcrError
from src.config import IngestionSettings


class OcrHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.settings = IngestionSettings(
            max_document_mb=2, ocr_languages="por+eng", min_native_chars=5,
            ocr_timeout_seconds=1, render_dpi=100, processed_dir=self.root / "processed",
        )
        self.ingestion = IngestionAgent(self.settings)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def pdf(self, name: str, texts: list[str]) -> Path:
        path = self.root / name
        with pymupdf.open() as document:
            for text in texts:
                page = document.new_page()
                if text:
                    page.insert_text((72, 72), text)
            document.save(path)
        return path

    def test_rejects_missing_empty_corrupt_and_password_protected_documents(self) -> None:
        empty = self.root / "empty.pdf"
        empty.touch()
        corrupt = self.root / "corrupt.pdf"
        corrupt.write_bytes(b"%PDF-1.7\nnot a real page tree")
        protected = self.root / "protected.pdf"
        with pymupdf.open() as document:
            document.new_page()
            document.save(
                protected, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                user_pw="temporary-test-only", owner_pw="temporary-test-owner",
            )
        for path in (self.root / "missing.pdf", empty, corrupt):
            with self.subTest(name=path.name), self.assertRaises(IngestionError):
                self.ingestion.ingest(path)
        with self.assertRaisesRegex(IngestionError, "protegido por senha"):
            self.ingestion.ingest(protected)

    def test_accepts_each_image_format_and_rejects_mismatched_image(self) -> None:
        formats = [(".png", "PNG"), (".jpg", "JPEG"), (".tif", "TIFF"), (".bmp", "BMP")]
        for suffix, format_name in formats:
            with self.subTest(format=format_name):
                path = self.root / ("document" + suffix)
                with Image.new("RGB", (32, 32), "white") as image:
                    image.save(path, format=format_name)
                document = self.ingestion.ingest(path)
                with patch.object(OcrAgent, "_ocr_image", return_value="Imagem reconhecida"):
                    result = OcrAgent(self.settings).extract(document)
                self.assertEqual(result.pages[0].extraction_method, "tesseract")
                self.assertEqual(document.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
        mismatch = self.root / "mismatch.jpg"
        mismatch.write_bytes((self.root / "document.png").read_bytes())
        with self.assertRaisesRegex(IngestionError, "assinatura"):
            self.ingestion.ingest(mismatch)

    def test_multipage_tiff_preserves_frame_numbers(self) -> None:
        path = self.root / "frames.tiff"
        with Image.new("RGB", (32, 32), "white") as first:
            with Image.new("RGB", (32, 32), "gray") as second:
                first.save(path, save_all=True, append_images=[second])
        document = self.ingestion.ingest(path)
        with patch.object(OcrAgent, "_ocr_image", side_effect=["Frame um", "Frame dois"]):
            result = OcrAgent(self.settings).extract(document)
        self.assertEqual(document.page_count, 2)
        self.assertEqual([page.page_number for page in result.pages], [1, 2])
        self.assertEqual([page.text for page in result.pages], ["Frame um", "Frame dois"])

    def test_mixed_pdf_uses_ocr_only_for_page_without_native_text(self) -> None:
        document = self.ingestion.ingest(self.pdf("mixed.pdf", ["Texto nativo suficiente", ""]))
        with patch.object(OcrAgent, "_ocr_pixmap", return_value="Texto OCR") as engine:
            result = OcrAgent(self.settings).extract(document)
        self.assertEqual(engine.call_count, 1)
        self.assertEqual([page.extraction_method for page in result.pages], ["native", "tesseract"])
        self.assertIsNone(result.pages[0].render_dpi)
        self.assertEqual(result.pages[1].render_dpi, 100)
        self.assertTrue(all(page.duration_ms >= 0 for page in result.pages))

    def test_failed_page_resumes_without_repeating_completed_ocr(self) -> None:
        document = self.ingestion.ingest(self.pdf("resume.pdf", ["", ""]))
        with patch.object(OcrAgent, "_ocr_pixmap", side_effect=["Primeira página", OcrError("timeout")]):
            with self.assertRaisesRegex(OcrError, "Página 2"):
                OcrAgent(self.settings).extract(document)
        progress_files = list((self.settings.processed_dir / "ocr_pages").glob("*.json"))
        self.assertEqual(len(progress_files), 1)
        progress = json.loads(progress_files[0].read_text(encoding="utf-8"))
        self.assertEqual(len(progress["pages"]), 1)
        with patch.object(OcrAgent, "_ocr_pixmap", return_value="Segunda página") as engine:
            result = OcrAgent(self.settings).extract(document)
        self.assertEqual(engine.call_count, 1)
        self.assertEqual([page.text for page in result.pages], ["Primeira página", "Segunda página"])
        self.assertFalse(result.cache_hit)
        self.assertFalse(progress_files[0].exists())

    def test_invalid_cache_is_rebuilt_and_settings_change_invalidates_cache(self) -> None:
        document = self.ingestion.ingest(self.pdf("native.pdf", ["Texto nativo para cache"] ))
        original = OcrAgent(self.settings).extract(document)
        path = self.settings.processed_dir / f"{original.cache_key}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["pages"] = []
        path.write_text(json.dumps(payload), encoding="utf-8")
        rebuilt = OcrAgent(self.settings).extract(document)
        self.assertFalse(rebuilt.cache_hit)
        self.assertEqual(rebuilt.pages[0].text, original.pages[0].text)
        changed = OcrAgent(replace(self.settings, render_dpi=150)).extract(document)
        self.assertNotEqual(original.cache_key, changed.cache_key)
        self.assertFalse(changed.cache_hit)

    def test_progress_callback_reports_pages_and_complete_cache(self) -> None:
        document = self.ingestion.ingest(self.pdf("progress.pdf", ["Primeira pagina nativa", "Segunda pagina nativa"]))
        events: list[tuple[int, int, str]] = []
        OcrAgent(self.settings, progress_callback=lambda *event: events.append(event)).extract(document)
        self.assertEqual(events, [(1, 2, "native"), (2, 2, "native")])
        events.clear()
        result = OcrAgent(self.settings, progress_callback=lambda *event: events.append(event)).extract(document)
        self.assertTrue(result.cache_hit)
        self.assertEqual(events, [(1, 2, "cache"), (2, 2, "cache")])

    def test_missing_engine_error_explains_path_and_environment_option(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("src.agents.ocr.shutil.which", return_value=None):
                with self.assertRaisesRegex(OcrError, "PATH.*TESSERACT_CMD"):
                    OcrAgent(self.settings)._ensure_tesseract()

    def test_missing_language_is_explicit_and_does_not_call_ocr(self) -> None:
        with patch("src.agents.ocr.shutil.which", return_value="mock-tesseract"):
            with patch("pytesseract.get_languages", return_value=["eng"]):
                with patch("pytesseract.image_to_string") as engine:
                    with Image.new("RGB", (32, 32)) as image:
                        with self.assertRaisesRegex(OcrError, "Idiomas Tesseract ausentes: por"):
                            OcrAgent(self.settings)._ocr_image(image)
                engine.assert_not_called()

    def test_timeout_is_sanitized_and_keeps_configured_limit(self) -> None:
        with patch.object(OcrAgent, "_ensure_tesseract"):
            with patch("pytesseract.image_to_string", side_effect=RuntimeError("Tesseract process timeout")) as engine:
                with Image.new("RGB", (32, 32)) as image:
                    with self.assertRaisesRegex(OcrError, "tempo limite"):
                        OcrAgent(self.settings)._ocr_image(image)
        self.assertEqual(engine.call_args.kwargs["timeout"], 1)
        self.assertEqual(engine.call_args.kwargs["lang"], "por+eng")


if __name__ == "__main__":
    unittest.main()
