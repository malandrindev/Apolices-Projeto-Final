"""Regressões relevantes: citações, retomada, custo, comparação e PDF completo."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import fitz

from src.agents.comparison import ComparisonAgent
from src.agents.extraction import ExtractionAgent, ExtractionIssue, Phase2Pipeline, Phase2Report
from src.agents.ocr import PageText, ProcessedDocument
from src.agents.rag import RagAgent
from src.agents.report import ComparisonReportAgent, ReportError
from src.agents.segmentation import SegmentationAgent
from src.schemas.comparison import DifferenceClass, PolicyComparison
from src.schemas.policy import NOT_FOUND, FieldEvidence, PolicyExtraction
from src.schemas.storage import RetrievedChunk


class Gateway:
    provider = "test-local"

    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def document(text: str) -> ProcessedDocument:
    return ProcessedDocument(
        source_name="teste.pdf", sha256="c" * 64, size_bytes=123,
        media_type="application/pdf",
        pages=[PageText(page_number=1, text=text, extraction_method="native")],
        processed_at="2026-10-03T00:00:00Z", cache_key="ocr-test",
    )


def evidence(value: str, excerpt: str) -> FieldEvidence:
    return FieldEvidence(valor=value, pagina=1, trecho_origem=excerpt, confianca=0.9)


def report(policy: PolicyExtraction, side: str = "A", name: str | None = None) -> Phase2Report:
    return Phase2Report(source_name=name or f"{side}.pdf", sha256=side.lower() * 64,
                        clause_count=1, policy=policy)


class PolicyHardeningTests(unittest.TestCase):
    def test_segmentation_bounds_chunks_and_retains_all_text(self) -> None:
        text = "1. COBERTURAS\n" + ("Cobertura para despesas de defesa. " * 400)
        gateway = Gateway([])
        clauses = SegmentationAgent(gateway=gateway, max_chunk_chars=1000).segment(document(text))
        self.assertGreater(len(clauses), 1)
        self.assertTrue(all(len(clause.text) <= 1000 for clause in clauses))
        self.assertEqual("".join(clause.text for clause in clauses), text)
        self.assertTrue(all(page.text in text for clause in clauses for page in clause.source_pages))
        self.assertEqual(gateway.calls, [])

    def test_real_quote_cannot_support_invented_amount(self) -> None:
        clause = SegmentationAgent().segment(document("1. LIMITES\nLimite R$ 1.000.000,00."))[0]
        invalid = PolicyExtraction(limite_maximo_garantia=evidence(
            "R$ 9.000.000,00", "Limite R$ 1.000.000,00",
        )).model_dump_json()
        gateway = Gateway([invalid, invalid])
        result = ExtractionAgent(gateway=gateway, model_strong="fast").extract(
            [clause], file_sha256="c" * 64,
        )
        self.assertEqual(result.policy.limite_maximo_garantia.valor, NOT_FOUND)
        self.assertEqual(len(gateway.calls), 2)
        self.assertTrue(result.issues)

    def test_real_quote_cannot_support_invented_date(self) -> None:
        clause = SegmentationAgent().segment(document(
            "1. VIGÊNCIA\nVigência inicia em 1 de janeiro de 2026."
        ))[0]
        invalid = PolicyExtraction(vigencia_inicio=evidence(
            "01/02/2026", "inicia em 1 de janeiro de 2026",
        )).model_dump_json()
        result = ExtractionAgent(gateway=Gateway([invalid, invalid]), model_strong="fast").extract(
            [clause], file_sha256="c" * 64,
        )
        self.assertEqual(result.policy.vigencia_inicio.valor, NOT_FOUND)

    def test_citation_from_other_clause_on_same_page_is_rejected(self) -> None:
        clauses = SegmentationAgent().segment(document(
            "1. COBERTURAS\nCustos de defesa.\n2. LIMITES\nLimite R$ 1.000.000,00."
        ))
        invalid = PolicyExtraction(limite_maximo_garantia=evidence(
            "R$ 1.000.000,00", "Limite R$ 1.000.000,00",
        )).model_dump_json()
        result = ExtractionAgent(gateway=Gateway([invalid, invalid]), model_strong="fast").extract(
            [clauses[0]], file_sha256="c" * 64,
        )
        self.assertEqual(result.policy.limite_maximo_garantia.valor, NOT_FOUND)

    def test_api_failure_is_not_cached_and_resume_keeps_completed_clauses(self) -> None:
        doc = document("1. COBERTURAS\nDefesa.\n2. EXCLUSÕES\nDolo.")
        valid = PolicyExtraction().model_dump_json()
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            first = Gateway([valid, RuntimeError("quota")])
            with self.assertRaisesRegex(RuntimeError, "quota"):
                Phase2Pipeline(gateway=first, model_fast="fast", model_strong="strong",
                               processed_dir=root).run(doc)
            second = Gateway([valid])
            progress = []
            _, resumed = Phase2Pipeline(
                gateway=second, model_fast="fast", model_strong="strong", processed_dir=root,
            ).run(doc, progress_callback=lambda *args: progress.append(args))
            self.assertEqual(resumed.cache_hits, 1)
            self.assertEqual(len(second.calls), 1)
            self.assertEqual(progress, [(1, 2, "clause-0001", True),
                                        (2, 2, "clause-0002", False)])

    def test_repair_uses_intermediate_model_and_provider_separates_cache(self) -> None:
        clause = SegmentationAgent().segment(document("1. COBERTURAS\nCustos de defesa."))[0]
        valid = PolicyExtraction().model_dump_json()
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            gateway = Gateway(["{}", valid])
            # O schema aceita campos ausentes com defaults; um JSON sintaticamente inválido
            # é necessário para exigir correção.
            gateway.responses[0] = "{invalid"
            ExtractionAgent(gateway=gateway, model_strong="fast", model_repair="intermediate",
                            processed_dir=root).extract([clause], file_sha256="c" * 64)
            self.assertEqual([call["model"] for call in gateway.calls], ["fast", "intermediate"])
            other_provider = Gateway([valid])
            other_provider.provider = "other-provider"
            result = ExtractionAgent(
                gateway=other_provider, model_strong="fast", model_repair="intermediate",
                processed_dir=root,
            ).extract([clause], file_sha256="c" * 64)
            self.assertEqual(result.cache_hits, 0)
            self.assertEqual(len(other_provider.calls), 1)

    def test_objective_currency_conflict_never_infers_advantage(self) -> None:
        a = PolicyExtraction(moeda=evidence("BRL", "BRL"),
                             limite_maximo_garantia=evidence("USD 10,000,000", "USD 10,000,000"))
        b = PolicyExtraction(moeda=evidence("BRL", "BRL"),
                             limite_maximo_garantia=evidence("R$ 8.000.000,00", "R$ 8.000.000,00"))
        comparison = ComparisonAgent(gateway=Gateway([]), model_strong="strong").compare(report(a), report(b, "B"))
        self.assertEqual(comparison.differences[0].classification, DifferenceClass.NOT_COMPARABLE)
        self.assertIn("inconsistente", comparison.differences[0].justification)


    def test_conflicting_extraction_values_require_review_before_advantage(self) -> None:
        a = report(PolicyExtraction(limite_maximo_garantia=evidence(
            "R$ 10.000.000,00", "R$ 10.000.000,00",
        )))
        a.issues = [ExtractionIssue(
            code="multiple_evidence_candidates", field_name="limite_maximo_garantia",
            message="Dois limites extraídos.", clause_id="clause-2",
        )]
        b = report(PolicyExtraction(limite_maximo_garantia=evidence(
            "R$ 8.000.000,00", "R$ 8.000.000,00",
        )), "B")
        comparison = ComparisonAgent(gateway=Gateway([]), model_strong="strong").compare(a, b)
        self.assertEqual(comparison.differences[0].classification, DifferenceClass.NOT_COMPARABLE)
        self.assertIn("conflitantes", comparison.differences[0].justification)

    def test_quote_currency_conflict_never_infers_advantage(self) -> None:
        a = report(PolicyExtraction(limite_maximo_garantia=evidence(
            "USD 10,000,000", "Limite BRL 10.000.000",
        )))
        b = report(PolicyExtraction(limite_maximo_garantia=evidence(
            "USD 8,000,000", "USD 8,000,000",
        )), "B")
        comparison = ComparisonAgent(gateway=Gateway([]), model_strong="strong").compare(a, b)
        self.assertEqual(comparison.differences[0].classification, DifferenceClass.NOT_COMPARABLE)
        self.assertIn("evidência", comparison.differences[0].justification)

    def test_objective_percentages_require_same_basis_before_advantage(self) -> None:
        a = PolicyExtraction(retencao_franquia=evidence("5%", "Franquia 5%"))
        b = PolicyExtraction(retencao_franquia=evidence("10%", "Franquia 10%"))
        gateway = Gateway([])
        comparison = ComparisonAgent(gateway=gateway, model_strong="strong").compare(report(a), report(b, "B"))
        self.assertEqual(comparison.differences[0].classification, DifferenceClass.NOT_COMPARABLE)
        self.assertEqual(gateway.calls, [])

    def test_semantic_invalid_result_is_not_cached_as_success(self) -> None:
        a = report(PolicyExtraction(side_a=evidence("Inclui defesa", "Inclui defesa")))
        b = report(PolicyExtraction(side_a=evidence("Não inclui defesa", "Não inclui defesa")), "B")
        valid = json.dumps({"comparisons": [{
            "field_name": "side_a", "classification": "mais_favoravel_A",
            "justification": "A inclui defesa e B não inclui defesa.",
        }]})
        with tempfile.TemporaryDirectory() as temp_root:
            gateway = Gateway(["{invalid", "{invalid", valid])
            agent = ComparisonAgent(gateway=gateway, model_strong="strong", processed_dir=Path(temp_root))
            first = agent.compare(a, b)
            second = agent.compare(a, b)
            self.assertEqual(first.differences[0].classification, DifferenceClass.NOT_COMPARABLE)
            self.assertEqual(second.differences[0].classification, DifferenceClass.MORE_FAVORABLE_A)
            self.assertEqual(len(gateway.calls), 3)

    def test_rag_locally_rejects_chunks_outside_requested_document(self) -> None:
        chunk = RetrievedChunk(chunk_id="other:1", document_id="other", source_name="other.pdf",
                               clause_id="1", title="Exclusões", category="exclusoes",
                               page_number=1, text="Outra apólice.", distance=0.1)

        class IgnoringAdapter:
            def search(self, *args, **kwargs):
                return [chunk]

        gateway = Gateway([])
        response = RagAgent(gateway=gateway, model_strong="strong", vector_store=IgnoringAdapter()).answer(
            "Há exclusões?", document_id="requested",
        )
        self.assertEqual(response.answer, NOT_FOUND)
        self.assertEqual(gateway.calls, [])


    def test_optional_source_urls_are_preserved_without_mutating_comparison(self) -> None:
        a = report(PolicyExtraction(limite_maximo_garantia=evidence(
            "R$ 10.000.000,00", "R$ 10.000.000,00",
        )))
        b = report(PolicyExtraction(), "B")
        comparison = ComparisonAgent(gateway=Gateway([]), model_strong="strong").compare(a, b)
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            agent = ComparisonReportAgent(sources_path=root / "absent_sources.md")
            references = {a.source_name: "https://example.org/policy.pdf?version=1"}
            artifacts = agent.generate(comparison, root, source_urls=references)
            restored = PolicyComparison.model_validate_json(
                artifacts.json_path.read_text(encoding="utf-8"),
            )
            self.assertEqual(restored.source_references, references)
            self.assertEqual(comparison.source_references, {})
            markdown = artifacts.markdown_path.read_text(encoding="utf-8")
            self.assertIn("https://example.org/policy.pdf?version=1", markdown)
            with fitz.open(artifacts.pdf_path) as pdf:
                rendered = "".join(page.get_text() for page in pdf)
                self.assertIn("https://example.org/policy.pdf?version=1", rendered)
            changed = agent.generate(
                comparison, root, source_urls={a.source_name: "https://example.org/policy.pdf?version=2"},
            )
            self.assertNotEqual(artifacts.json_path, changed.json_path)

    def test_public_source_validation_rejects_credentials_and_preserves_legitimate_query(self) -> None:
        good = ComparisonReportAgent.validate_source_urls({
            "A.pdf": "  https://example.org/policy.pdf?document=42&language=pt  ", "B.pdf": "",
        })
        self.assertEqual(good, {"A.pdf": "https://example.org/policy.pdf?document=42&language=pt"})
        for invalid in (
            "javascript:alert(1)", "file:///policy.pdf", "https://",
            "https://user:private@example.org/policy.pdf",
            "https://example.org/policy.pdf?token=private",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ReportError) as captured:
                    ComparisonReportAgent.validate_source_urls({"A.pdf": invalid})
                self.assertNotIn(invalid, str(captured.exception))
                self.assertNotIn("private", str(captured.exception))
        comparison = ComparisonAgent(gateway=Gateway([]), model_strong="strong").compare(
            report(PolicyExtraction()), report(PolicyExtraction(), "B"),
        )
        with tempfile.TemporaryDirectory() as temp_root:
            destination = Path(temp_root) / "artifacts"
            with self.assertRaises(ReportError):
                ComparisonReportAgent().generate(
                    comparison, destination,
                    source_urls={"wrong-document.pdf": "https://example.org/policy.pdf"},
                )
            self.assertFalse(destination.exists())

    def test_pdf_paginates_all_evidence_and_cites_selected_public_source(self) -> None:
        excerpt = "Longa evidência contratual. " * 350 + "TERMINAL_EVIDENCIA"
        a = report(PolicyExtraction(exclusoes=evidence(excerpt, excerpt)), name="public_a.pdf")
        b = report(PolicyExtraction(), "B")
        comparison = ComparisonAgent(gateway=Gateway([]), model_strong="strong").compare(a, b)
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            sources = root / "SOURCES.md"
            sources.write_text(
                "# Fontes\nDocumentos obtidos para demonstração.\n"
                "| " + chr(96) + "public_a.pdf" + chr(96)
                + " | [Fonte oficial](https://example.org/policy.pdf) | Condições gerais |",
                encoding="utf-8",
            )
            artifacts = ComparisonReportAgent(sources_path=sources).generate(comparison, root)
            markdown = artifacts.markdown_path.read_text(encoding="utf-8")
            self.assertIn("https://example.org/policy.pdf", markdown)
            restored = PolicyComparison.model_validate_json(
                artifacts.json_path.read_text(encoding="utf-8"),
            )
            self.assertEqual(restored.source_references,
                             {"public_a.pdf": "https://example.org/policy.pdf"})
            self.assertIn("não constitui parecer jurídico", markdown)
            self.assertIn("nao\\_localizado", markdown)
            with fitz.open(artifacts.pdf_path) as pdf:
                self.assertGreater(len(pdf), 1)
                text = "".join(page.get_text() for page in pdf)
                self.assertIn("TERMINAL_EVIDENCIA", text)
                self.assertIn("p. 1", text)
