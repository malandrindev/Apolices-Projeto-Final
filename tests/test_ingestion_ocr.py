"""Testes de ingestão, extração nativa por página e cache usando PDFs temporários."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pymupdf

from src.agents.ingestion import IngestionError
from src.config import IngestionSettings
from src.pipeline import process_document


class IngestionAndOcrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = IngestionSettings(
            max_document_mb=2,
            ocr_languages="por+eng",
            min_native_chars=5,
            ocr_timeout_seconds=1,
            render_dpi=100,
            processed_dir=self.root / "processed",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_pdf(self, name: str, texts: list[str]) -> Path:
        path = self.root / name
        pdf = pymupdf.open()
        for text in texts:
            page = pdf.new_page()
            page.insert_text((72, 72), text)
        pdf.save(path)
        pdf.close()
        return path

    def test_processes_two_pdf_samples_by_page_and_caches_them(self) -> None:
        first = self.make_pdf("sample-a.pdf", ["Apólice de demonstração A", "Página 2"])
        second = self.make_pdf("sample-b.pdf", ["Apólice de demonstração B"])

        first_meta, first_result = process_document(first, settings=self.settings)
        second_meta, second_result = process_document(second, settings=self.settings)
        _, cached_result = process_document(first, settings=self.settings)
        duplicate = self.root / "sample-a-copy.pdf"
        duplicate.write_bytes(first.read_bytes())
        _, duplicate_result = process_document(duplicate, settings=self.settings)

        self.assertEqual(first_result.pages[0].page_number, 1)
        self.assertEqual(first_result.pages[0].extraction_method, "native")
        self.assertEqual(len(first_result.pages), 2)
        self.assertEqual(second_result.pages[0].text, "Apólice de demonstração B")
        self.assertNotEqual(first_meta.sha256, second_meta.sha256)
        self.assertFalse(first_result.cache_hit)
        self.assertTrue(cached_result.cache_hit)
        self.assertTrue(duplicate_result.cache_hit)
        self.assertEqual(duplicate_result.source_name, duplicate.name)
        self.assertTrue(self.settings.processed_dir.joinpath(f"{first_result.cache_key}.json").exists())

    def test_rejects_non_pdf_and_mismatched_pdf_signature(self) -> None:
        text_file = self.root / "contract.txt"
        text_file.write_text("not a supported document", encoding="utf-8")
        fake_pdf = self.root / "fake.pdf"
        fake_pdf.write_text("this is not a PDF", encoding="utf-8")

        with self.assertRaises(IngestionError):
            process_document(text_file, settings=self.settings)
        with self.assertRaisesRegex(IngestionError, "não parece ser um PDF válido"):
            process_document(fake_pdf, settings=self.settings)

    def test_ocr_fallback_keeps_page_number(self) -> None:
        scanned = self.make_pdf("scanned.pdf", [""])
        with patch("src.agents.ocr.OcrAgent._ocr_pixmap", return_value="Texto reconhecido"):
            _, result = process_document(scanned, settings=self.settings)

        self.assertEqual(result.pages[0].page_number, 1)
        self.assertEqual(result.pages[0].text, "Texto reconhecido")
        self.assertEqual(result.pages[0].extraction_method, "tesseract")

    def test_rejects_files_larger_than_configured_limit(self) -> None:
        path = self.make_pdf("large.pdf", ["Texto de teste suficientemente longo"])
        tiny_limit = replace(self.settings, max_document_mb=0)

        with self.assertRaisesRegex(IngestionError, "excede o limite"):
            process_document(path, settings=tiny_limit)


if __name__ == "__main__":
    unittest.main()
