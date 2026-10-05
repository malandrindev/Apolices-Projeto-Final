"""Integração real local; somente o mecanismo OCR e o provedor remoto são isolados."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.smoke_end_to_end import (
    MAX_LOGICAL_CALLS, SYNTHETIC_B_TEXT, BudgetGateway, SmokeError, run_smoke,
)
from src.agents.ocr import OcrAgent
from src.schemas.comparison import PolicyComparison
from src.storage.sqlite_repo import SqliteRepository


class EndToEndTests(unittest.TestCase):
    def test_two_documents_persist_compare_export_and_resume_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            output = Path(temp_root)
            messages = []
            with patch.object(OcrAgent, "_ocr_image", return_value=SYNTHETIC_B_TEXT) as engine:
                with patch("scripts.smoke_end_to_end.get_gateway") as remote_factory:
                    with patch("scripts.smoke_end_to_end.get_settings") as live_settings:
                        diagnostics = run_smoke(output, emit=messages.append)
            engine.assert_called_once()
            remote_factory.assert_not_called()
            live_settings.assert_not_called()
            self.assertEqual(diagnostics["status"], "PASS")
            self.assertEqual(diagnostics["mode"], "offline_simulated")
            self.assertFalse(diagnostics["generative_ai_verified"])
            self.assertEqual(diagnostics["provider"], "offline-synthetic-smoke")
            self.assertGreaterEqual(diagnostics["elapsed_total_seconds"], 0)
            self.assertEqual(diagnostics["gateway_events"], [])
            self.assertEqual(diagnostics["logical_calls"], 3)
            self.assertEqual(diagnostics["stages"], ["extraction", "extraction", "comparison"])
            self.assertEqual(diagnostics["http_attempts"], 0)
            self.assertEqual(diagnostics["resume_cache_hits"], 2)
            self.assertEqual(diagnostics["resume_new_calls"], 0)
            self.assertEqual(diagnostics["documents"][0]["extraction_methods"], ["native"])
            self.assertEqual(diagnostics["documents"][1]["extraction_methods"], ["tesseract"])
            self.assertEqual(len(SqliteRepository(Path(diagnostics["sqlite_path"])).list_documents()), 2)
            for document in diagnostics["documents"]:
                self.assertEqual(document["clauses"], 1)
                self.assertTrue(Path(document["report_path"]).is_file())
            artifacts = diagnostics["artifacts"]
            markdown = Path(artifacts["markdown"]).read_text(encoding="utf-8")
            self.assertIn("sintetica", markdown)
            self.assertIn("p. 1", markdown)
            self.assertIn("não constitui parecer jurídico", markdown)
            self.assertTrue(Path(artifacts["pdf"]).read_bytes().startswith(b"%PDF-"))
            comparison = PolicyComparison.model_validate_json(
                Path(artifacts["json"]).read_text(encoding="utf-8"),
            )
            by_field = {item.field_name: item for item in comparison.differences}
            self.assertEqual(by_field["limite_maximo_garantia"].classification.value, "mais_favoravel_A")
            self.assertEqual(by_field["side_a"].classification.value, "mais_favoravel_A")
            self.assertEqual(by_field["vigencia_inicio"].classification.value, "diferente_nao_comparavel")
            saved = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], diagnostics["status"])
            # Antes de qualquer chamada, somente o orçamento e metadados foram emitidos.
            initial = json.loads(messages[0])
            self.assertEqual(initial["maximum_logical_calls_including_repair"], 6)
            self.assertEqual(initial["maximum_http_attempts"], 0)
            self.assertNotIn("R$", "".join(messages))
            self.assertNotIn("Inclui custos", "".join(messages))


    def test_gateway_diagnostics_copy_only_safe_event_metadata(self) -> None:
        class MetadataGateway:
            provider = "unit-test"
            events = [{
                "provider": "unit-test", "model": "model", "stage": "extraction",
                "duration_ms": 25, "input_tokens": 10, "output_tokens": 4,
                "attempt": 1, "cache_hit": False, "status": "completed", "error_kind": "",
                "prompt": "document content", "raw_response": "provider response",
            }]

        gateway = BudgetGateway(MetadataGateway())
        self.assertEqual(gateway.events[0]["duration_ms"], 25)
        self.assertEqual(gateway.events[0]["input_tokens"], 10)
        self.assertNotIn("prompt", gateway.events[0])
        self.assertNotIn("raw_response", gateway.events[0])
        self.assertEqual(len(gateway.events[0]), 10)
        self.assertIn("prompt", MetadataGateway.events[0])

    def test_budget_stops_before_a_seventh_provider_request(self) -> None:
        class CountingGateway:
            provider = "unit-test"

            def __init__(self):
                self.calls = 0

            @property
            def usage(self):
                return {"calls": self.calls}

            def complete(self, **kwargs):
                self.calls += 1
                return "{}"

        provider = CountingGateway()
        gateway = BudgetGateway(provider)
        for _ in range(MAX_LOGICAL_CALLS):
            gateway.complete(agent="extraction")
        with self.assertRaises(SmokeError):
            gateway.complete(agent="extraction")
        self.assertEqual(provider.calls, 6)
        self.assertEqual(gateway.logical_calls, 6)


if __name__ == "__main__":
    unittest.main()
