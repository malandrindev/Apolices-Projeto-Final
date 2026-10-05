"""Workspace projections: metadata, compatibility, matrix, review and ordered pairs."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.agents.extraction import Phase2Report
from src.agents.ocr import PageText, ProcessedDocument
from src.schemas.comparison import (
    ComparisonCitation, DifferenceClass, PolicyComparison, PolicyDifference,
)
from src.schemas.policy import NOT_FOUND, FieldEvidence, PolicyExtraction
from src.workspace import (
    FIELD_GROUPS, FIELD_LABELS, DocumentMetadata, assess_compatibility, build_matrix,
    compare_reference, executive_metrics, infer_metadata, review_key,
)


def processed(text: str = "", source_name: str = "document.pdf", *, mixed=False) -> ProcessedDocument:
    pages = [PageText(page_number=1, text=text, extraction_method="native")]
    if mixed:
        pages.append(PageText(page_number=2, text="Additional page", extraction_method="tesseract"))
    return ProcessedDocument(
        source_name=source_name, sha256="a" * 64, size_bytes=100,
        media_type="application/pdf", pages=pages,
        processed_at="2026-10-03T00:00:00Z", cache_key="local-test",
    )


def evidence(value: str, excerpt: str | None = None) -> FieldEvidence:
    return FieldEvidence(valor=value, pagina=1, trecho_origem=excerpt or value, confianca=0.9)


def report(letter: str, policy: PolicyExtraction | None = None) -> Phase2Report:
    return Phase2Report(source_name=f"{letter}.pdf", sha256=letter * 64,
                        clause_count=1, policy=policy or PolicyExtraction())


def difference(name: str, classification: DifferenceClass) -> PolicyDifference:
    return PolicyDifference(
        field_name=name, label=FIELD_LABELS[name], value_a="Reference", value_b="Candidate",
        classification=classification, justification="Test evidence contrast.",
        citation_a=ComparisonCitation(source_name="A.pdf", page_number=1, excerpt="Reference"),
        citation_b=ComparisonCitation(source_name="B.pdf", page_number=2, excerpt="Candidate"),
    )


def comparison(a: Phase2Report, b: Phase2Report, items=()) -> PolicyComparison:
    return PolicyComparison(
        source_a=a.source_name, document_id_a=a.sha256,
        source_b=b.source_name, document_id_b=b.sha256,
        compared_at="2026-10-03T00:00:00Z", differences=list(items),
        executive_summary="Local comparison.",
    )


class NoRequestsGateway:
    provider = "offline-workspace-test"

    def __init__(self):
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        raise AssertionError("These objective cases must not call a provider.")


class WorkspaceTests(unittest.TestCase):
    def test_groups_cover_each_existing_field_once(self) -> None:
        fields = [name for names in FIELD_GROUPS.values() for name in names]
        self.assertEqual(set(fields), set(PolicyExtraction.model_fields))
        self.assertEqual(len(fields), 27)
        self.assertEqual(len(set(fields)), 27)
        self.assertEqual(set(FIELD_LABELS), set(fields))
        self.assertEqual(set(FIELD_GROUPS), {"Coberturas", "Exclusões", "Limites", "Cláusulas"})

    def test_filename_does_not_become_documentary_metadata(self) -> None:
        doc = processed("Generic text only.", "Chubb_D&O_capital_aberto_2025-12-16.pdf")
        metadata = infer_metadata(doc)
        self.assertIsNone(metadata.insurer)
        self.assertIsNone(metadata.product_name)
        self.assertIsNone(metadata.effective_date)
        self.assertIsNone(metadata.company_type)
        self.assertIsNone(metadata.branch)
        self.assertEqual(metadata.page_count, 1)
        self.assertEqual(metadata.extraction_method, "native")
        self.assertEqual(infer_metadata(None), DocumentMetadata())

    def test_explicit_frontmatter_keeps_policy_period_separate_from_version(self) -> None:
        doc = processed(
            "Condições Gerais\n"
            "Produto: Seguro RC D&O Capital aberto\n"
            "Seguradora: Companhia Exemplo\n"
            "Processo SUSEP: 15414.900831/2017-45\n"
            "Versão: 16/12/2025\n"
            "Vigência: 01/01/2026 a 31/12/2026\n"
            "Moeda: BRL\n", mixed=True,
        )
        metadata = infer_metadata(doc)
        self.assertEqual(metadata.insurer, "Companhia Exemplo")
        self.assertEqual(metadata.product_name, "Seguro RC D&O Capital aberto")
        self.assertEqual(metadata.susep_process, "15414.900831/2017-45")
        self.assertEqual(metadata.effective_date, "2025-12-16")
        self.assertEqual(metadata.period, "01/01/2026 a 31/12/2026")
        self.assertEqual(metadata.document_type, "Condições gerais")
        self.assertEqual(metadata.company_type, "Capital aberto")
        self.assertEqual(metadata.currency, "BRL")
        self.assertEqual(metadata.branch, "D&O")
        self.assertEqual(metadata.extraction_method, "mixed")
        self.assertEqual(metadata.evidence["effective_date"]["page_number"], 1)
        self.assertEqual(metadata.evidence["effective_date"]["origin"], "document")

    def test_ambiguous_process_is_unknown_even_when_catalog_selects_one(self) -> None:
        doc = processed(
            "Processo SUSEP: 15414.900831/2017-45\n"
            "Processo SUSEP: 15414.004037/2007-59\n"
        )
        metadata = infer_metadata(doc, catalog={"susep_process": "15414.900831/2017-45"})
        self.assertIsNone(metadata.susep_process)
        self.assertIn("susep_process", metadata.conflicts)

    def test_catalog_is_editorial_fallback_and_does_not_invent_effective_date(self) -> None:
        metadata = infer_metadata(processed("Unknown front matter."), catalog={
            "id": "public-product", "insurer": "Companhia Exemplo",
            "product_name": "Seguro D&O Capital Fechado", "product_scope": "capital_fechado",
            "document_type": "condicoes_gerais", "effective_date": None, "susep_process": None,
        })
        self.assertEqual(metadata.insurer, "Companhia Exemplo")
        self.assertEqual(metadata.company_type, "Capital fechado")
        self.assertEqual(metadata.branch, "D&O")
        self.assertEqual(metadata.document_type, "Condições gerais")
        self.assertIsNone(metadata.effective_date)
        self.assertIsNone(metadata.susep_process)
        self.assertEqual(metadata.evidence["insurer"]["origin"], "catalog")

    def test_verified_extraction_can_fill_insurer_and_period(self) -> None:
        doc = processed("Seguradora: Companhia Exemplo\nInício: 01/01/2026\nFim: 31/12/2026\n")
        extraction = report("a", PolicyExtraction(
            seguradora=evidence("Companhia Exemplo", "Seguradora: Companhia Exemplo"),
            vigencia_inicio=evidence("01/01/2026", "Início: 01/01/2026"),
            vigencia_fim=evidence("31/12/2026", "Fim: 31/12/2026"),
        ))
        metadata = infer_metadata(doc, extraction)
        self.assertEqual(metadata.insurer, "Companhia Exemplo")
        self.assertEqual(metadata.period, "01/01/2026 a 31/12/2026")
        self.assertIsNone(metadata.effective_date)
        self.assertEqual(metadata.evidence["insurer"]["origin"], "extraction")
        with self.assertRaises(ValueError):
            infer_metadata(doc, report("b"))

    def test_insurer_conflict_is_visible_without_catalog_overwriting_document(self) -> None:
        metadata = infer_metadata(processed("Seguradora: Empresa Fonte\n"), catalog={"insurer": "Empresa Catálogo"})
        self.assertEqual(metadata.insurer, "Empresa Fonte")
        self.assertIn("insurer", metadata.conflicts)

    def test_compatibility_requires_multiple_known_signals_for_high(self) -> None:
        unknown = DocumentMetadata()
        self.assertEqual(assess_compatibility(unknown, unknown).status, "Não determinada")
        sparse = DocumentMetadata(currency="BRL", document_type="Condições gerais")
        self.assertEqual(assess_compatibility(sparse, sparse).status, "Não determinada")
        complete = DocumentMetadata(
            branch="D&O", company_type="Capital aberto", currency="BRL",
            document_type="Condições gerais", product_name="Seguro D&O",
            effective_date="2025-12-16",
        )
        self.assertEqual(assess_compatibility(complete, replace(complete, effective_date="2026-01-01")).status, "Alta")
        partial = DocumentMetadata(branch="D&O", currency="BRL", document_type="Condições gerais")
        self.assertEqual(assess_compatibility(partial, partial).status, "Média")

    def test_known_compatibility_conflicts_are_explained(self) -> None:
        reference = DocumentMetadata(
            branch="D&O", company_type="Capital aberto", currency="BRL",
            document_type="Condições gerais", effective_date="2025-12-16",
        )
        for changed in (
            replace(reference, branch="Cyber"),
            replace(reference, company_type="Capital fechado"),
            replace(reference, currency="USD"),
            replace(reference, document_type="Proposta"),
            replace(reference, effective_date="2017-01-01"),
        ):
            with self.subTest(changed=changed):
                result = assess_compatibility(reference, changed)
                self.assertEqual(result.status, "Baixa")
                self.assertTrue(result.reasons)

    def test_matrix_distinguishes_missing_equal_uncompared_and_other_reference(self) -> None:
        a = report("a", PolicyExtraction(side_a=evidence("Defesa")))
        b = report("b", PolicyExtraction(side_a=evidence("Defesa")))
        c = report("c", PolicyExtraction(side_a=evidence("Outro texto")))
        wrong_reference = comparison(c, b)
        ready = comparison(a, b)
        key = review_key(a.sha256, b.sha256, "side_a")
        rows = {row["field_name"]: row for row in build_matrix(
            a, [b, c], [wrong_reference, ready], {key: {"status": "Confirmado", "note": "Conferido na página."}},
        )}
        self.assertEqual(rows["side_a"]["candidates"][b.sha256]["classification"], "igual")
        self.assertIsNone(rows["side_a"]["candidates"][c.sha256]["classification"])
        self.assertEqual(rows["side_a"]["candidates"][b.sha256]["review_status"], "Confirmado")
        self.assertEqual(rows["side_a"]["candidates"][b.sha256]["review_note"], "Conferido na página.")
        self.assertEqual(rows["side_b"]["candidates"][b.sha256]["classification"], "nao_localizado_em_ambos")
        self.assertEqual(rows["side_a"]["reference_evidence"]["page_number"], 1)
        self.assertEqual(len(rows), 27)

    def test_reference_orchestration_supports_five_documents_and_reuses_cache(self) -> None:
        docs = []
        for letter, limit in (("a", 8), ("b", 12), ("c", 10), ("d", 9), ("e", 20)):
            value = f"R$ {limit}.000.000,00"
            docs.append(report(letter, PolicyExtraction(limite_maximo_garantia=evidence(value))))
        reference, candidates = docs[2], [docs[0], docs[1], docs[3], docs[4]]
        gateway = NoRequestsGateway()
        progress = []
        with tempfile.TemporaryDirectory() as temp_root:
            first = compare_reference(reference, candidates, gateway, "test", Path(temp_root),
                                      on_progress=lambda *args: progress.append(args))
            second = compare_reference(reference, candidates, gateway, "test", Path(temp_root))
        self.assertEqual(len(first), 4)
        self.assertTrue(all(item.document_id_a == reference.sha256 for item in first))
        self.assertEqual([item.document_id_b for item in first], [item.sha256 for item in candidates])
        self.assertEqual([item.model_dump() for item in first], [item.model_dump() for item in second])
        self.assertEqual(progress, [(index, 4, candidate.sha256) for index, candidate in enumerate(candidates, 1)])
        self.assertEqual(gateway.calls, 0)
        rows = build_matrix(reference, candidates, first)
        self.assertEqual(len(rows[0]["candidates"]), 4)
        with self.assertRaises(ValueError):
            compare_reference(reference, [reference], gateway, "test", None)
        with self.assertRaises(ValueError):
            compare_reference(reference, [candidates[0], candidates[0]], gateway, "test", None)

    def test_metrics_translate_reference_direction_without_global_ranking(self) -> None:
        a, b = report("a"), report("b")
        items = [
            difference("side_a", DifferenceClass.MORE_FAVORABLE_B),
            difference("retencao_franquia", DifferenceClass.MORE_FAVORABLE_A),
            difference("exclusoes", DifferenceClass.NOT_COMPARABLE),
            difference("premio", DifferenceClass.MISSING_ON_ONE),
            difference("vigencia_inicio", DifferenceClass.MORE_FAVORABLE_B),
        ]
        result = comparison(a, b, items)
        reviews = {
            review_key(a.sha256, b.sha256, "side_a"): {"status": "Confirmado"},
            review_key(a.sha256, b.sha256, "retencao_franquia"): {"status": "Corrigido", "note": "Anotação humana."},
            review_key(a.sha256, b.sha256, "exclusoes"): "Requer análise",
        }
        metrics = executive_metrics(result, reviews)
        self.assertEqual(metrics, {"differences": 5, "favorable_candidate": 1,
                                  "restrictive_candidate": 1, "non_comparable": 2,
                                  "review_needed": 3, "reviewed": 2})
        self.assertEqual(result.differences[1].value_b, "Candidate")
        self.assertNotIn("score", metrics)
        self.assertNotIn("winner", metrics)
        self.assertNotEqual(review_key(a.sha256, b.sha256, "side_a"),
                            review_key(b.sha256, a.sha256, "side_a"))
        self.assertNotEqual(review_key(a.sha256, b.sha256, "side_a", "v1"),
                            review_key(a.sha256, b.sha256, "side_a", "v2"))


if __name__ == "__main__":
    unittest.main()
