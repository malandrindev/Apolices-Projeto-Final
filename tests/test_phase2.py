"""Testes offline de segmentação, proveniência e estruturação por cláusula."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agents.extraction import ExtractionAgent, Phase2Pipeline
from src.agents.ocr import PageText, ProcessedDocument
from src.agents.segmentation import SegmentationAgent
from src.schemas.clause import ClauseCategory
from src.schemas.policy import NOT_FOUND, FieldEvidence, PolicyExtraction


class FakeGateway:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Resposta simulada inesperada.")
        return self.responses.pop(0)


class Phase2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.processed_dir = Path(self.temp_dir.name)
        self.document = ProcessedDocument(
            source_name="policy-test.pdf",
            sha256="a" * 64,
            size_bytes=123,
            media_type="application/pdf",
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1. COBERTURAS\n"
                        "A Seguradora Exemplo garante Side A para custos de defesa.\n"
                        "2. EXCLUSÕES\n"
                        "Ficam excluídos os atos dolosos."
                    ),
                    extraction_method="native",
                ),
                PageText(
                    page_number=2,
                    text="3. VIGÊNCIA\nA vigência inicia em 1 de janeiro de 2026.",
                    extraction_method="native",
                ),
            ],
            processed_at="2026-09-30T00:00:00Z",
            cache_key="ocr-test",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_segments_titles_and_preserves_pages_and_source_text(self) -> None:
        clauses = SegmentationAgent(max_chunk_chars=200).segment(self.document)

        self.assertEqual(len(clauses), 3)
        self.assertEqual(clauses[0].category, ClauseCategory.COVERAGE)
        self.assertEqual(clauses[1].category, ClauseCategory.EXCLUSIONS)
        self.assertEqual(clauses[2].page_start, 2)
        self.assertIn("A Seguradora Exemplo", clauses[0].source_pages[0].text)
        self.assertTrue(all(clause.text for clause in clauses))

    def test_classifies_ambiguous_heading_with_fast_model_only(self) -> None:
        gateway = FakeGateway(
            ['{"clauses":[{"clause_id":"heading-1","category":"definicoes"}]}']
        )
        document = self.document.model_copy(
            update={"pages": [self.document.pages[0].model_copy(update={"text": "ANEXO ALFA\nSide A"})]}
        )

        clauses = SegmentationAgent(gateway=gateway, model_fast="fast-test").segment(document)

        self.assertEqual(clauses[0].category, ClauseCategory.DEFINITIONS)
        self.assertEqual(gateway.calls[0]["model"], "fast-test")
        self.assertEqual(gateway.calls[0]["response_format"], {"type": "json_object"})

    def test_invalid_json_is_retried_once_then_policy_is_validated(self) -> None:
        clause = SegmentationAgent().segment(self.document)[0]
        valid_policy = PolicyExtraction(
            seguradora=FieldEvidence(
                valor="Seguradora Exemplo",
                pagina=1,
                trecho_origem="A Seguradora Exemplo garante Side A",
                confianca=0.93,
            ),
            side_a=FieldEvidence(
                valor="Cobertura Side A para custos de defesa",
                pagina=1,
                trecho_origem="garante Side A para custos de defesa",
                confianca=0.89,
            ),
        )
        gateway = FakeGateway(["{ inválido", valid_policy.model_dump_json()])
        agent = ExtractionAgent(
            gateway=gateway,
            model_strong="strong-test",
            processed_dir=self.processed_dir,
        )

        result = agent.extract([clause], file_sha256=self.document.sha256)

        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(result.policy.seguradora.valor, "Seguradora Exemplo")
        self.assertEqual(result.policy.seguradora.pagina, 1)
        self.assertEqual(result.policy.side_a.valor, "Cobertura Side A para custos de defesa")
        self.assertEqual(result.policy.side_b.valor, NOT_FOUND)
        self.assertEqual(result.issues, [])

    def test_invalid_citation_is_retried_and_not_accepted(self) -> None:
        clause = SegmentationAgent().segment(self.document)[0]
        invalid = PolicyExtraction(
            seguradora=FieldEvidence(
                valor="Seguradora inventada",
                pagina=1,
                trecho_origem="texto inexistente na cláusula",
                confianca=0.99,
            )
        ).model_dump_json()
        gateway = FakeGateway([invalid, invalid])
        result = ExtractionAgent(
            gateway=gateway,
            model_strong="strong-test",
            processed_dir=self.processed_dir,
        ).extract([clause], file_sha256=self.document.sha256)

        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(result.policy.seguradora.valor, NOT_FOUND)
        self.assertEqual(result.issues[0].code, "structured_output_invalid_after_retry")

    def test_phase_pipeline_uses_one_extraction_call_per_clause_and_reuses_cache(self) -> None:
        clauses = SegmentationAgent().segment(self.document)
        responses = [PolicyExtraction().model_dump_json() for _ in clauses]
        first_gateway = FakeGateway(responses.copy())
        pipeline = Phase2Pipeline(
            gateway=first_gateway,
            model_fast="fast-test",
            model_strong="strong-test",
            processed_dir=self.processed_dir,
        )
        result_clauses, result = pipeline.run(self.document)
        self.assertEqual(len(result_clauses), 3)
        self.assertEqual(result.policy.seguradora.valor, NOT_FOUND)
        self.assertEqual(len(first_gateway.calls), 3)

        cached_gateway = FakeGateway([])
        cached = Phase2Pipeline(
            gateway=cached_gateway,
            model_fast="fast-test",
            model_strong="strong-test",
            processed_dir=self.processed_dir,
        ).run(self.document)[1]
        self.assertEqual(cached.cache_hits, 3)
        self.assertEqual(cached_gateway.calls, [])

    def test_policy_json_contains_citation_fields_for_each_value(self) -> None:
        policy = PolicyExtraction()
        payload = json.loads(policy.model_dump_json())

        self.assertEqual(payload["limite_maximo_garantia"]["valor"], NOT_FOUND)
        self.assertIn("pagina", payload["limite_maximo_garantia"])
        self.assertIn("trecho_origem", payload["limite_maximo_garantia"])
        self.assertIn("confianca", payload["limite_maximo_garantia"])


if __name__ == "__main__":
    unittest.main()
