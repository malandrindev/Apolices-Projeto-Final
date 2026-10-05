"""Testes da comparação determinística/semântica e exportação de relatórios."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.agents.comparison import ComparisonAgent
from src.agents.extraction import Phase2Report
from src.agents.report import ComparisonReportAgent
from src.schemas.comparison import DifferenceClass
from src.schemas.policy import FieldEvidence, PolicyExtraction


class FakeGateway:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Resposta simulada inesperada.")
        return self.responses.pop(0)


def evidence(value: str, page: int, excerpt: str, confidence: float = 0.9) -> FieldEvidence:
    return FieldEvidence(
        valor=value,
        pagina=page,
        trecho_origem=excerpt,
        confianca=confidence,
    )


def report(
    *,
    name: str,
    file_hash: str,
    policy: PolicyExtraction,
) -> Phase2Report:
    return Phase2Report(
        source_name=name,
        sha256=file_hash * 64,
        clause_count=2,
        policy=policy,
    )


class ComparisonAndReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.processed_dir = Path(self.temp_dir.name)
        self.report_a = report(
            name="Apólice A pública.pdf",
            file_hash="a",
            policy=PolicyExtraction(
                moeda=evidence("BRL", 1, "Moeda: BRL"),
                limite_maximo_garantia=evidence("R$ 10.000.000,00", 2, "LMG de R$ 10.000.000,00"),
                retencao_franquia=evidence("R$ 100.000,00", 3, "Franquia: R$ 100.000,00"),
                vigencia_inicio=evidence("01/01/2026", 1, "Vigência: 01/01/2026"),
                side_a=evidence("Side A com custos de defesa", 4, "Side A com custos de defesa"),
                exclusoes=evidence("Exclusão por dolo", 8, "Exclui-se dolo comprovado"),
            ),
        )
        self.report_b = report(
            name="Apólice B pública.pdf",
            file_hash="b",
            policy=PolicyExtraction(
                moeda=evidence("BRL", 1, "Moeda: BRL"),
                limite_maximo_garantia=evidence("R$ 8.000.000,00", 2, "LMG de R$ 8.000.000,00"),
                retencao_franquia=evidence("R$ 200.000,00", 3, "Franquia: R$ 200.000,00"),
                vigencia_inicio=evidence("2026-02-01", 1, "Vigência: 2026-02-01"),
                side_a=evidence("Side A sem custo de defesa", 5, "Side A sem custo de defesa"),
                exclusoes=evidence(
                    "Exclusão por atos dolosos",
                    9,
                    "Exclusão: atos dolosos comprovados",
                ),
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_numeric_and_date_fields_are_deterministic_and_cited(self) -> None:
        gateway = FakeGateway(
            [
                json.dumps(
                    {
                        "comparisons": [
                            {
                                "field_name": "side_a",
                                "classification": "diferente_nao_comparavel",
                                "justification": "Os textos têm escopos diferentes.",
                            },
                            {
                                "field_name": "exclusoes",
                                "classification": "diferente_nao_comparavel",
                                "justification": "As exclusões não são equivalentes.",
                            },
                        ]
                    }
                )
            ]
        )
        comparison = ComparisonAgent(
            gateway=gateway,
            model_strong="strong-test",
        ).compare(self.report_a, self.report_b)
        by_field = {item.field_name: item for item in comparison.differences}

        self.assertEqual(
            by_field["limite_maximo_garantia"].classification,
            DifferenceClass.MORE_FAVORABLE_A,
        )
        self.assertEqual(
            by_field["retencao_franquia"].classification,
            DifferenceClass.MORE_FAVORABLE_A,
        )
        self.assertEqual(
            by_field["vigencia_inicio"].classification,
            DifferenceClass.NOT_COMPARABLE,
        )
        self.assertEqual(by_field["limite_maximo_garantia"].citation_a.page_number, 2)
        self.assertEqual(
            by_field["limite_maximo_garantia"].citation_b.source_name,
            self.report_b.source_name,
        )
        self.assertEqual(len(gateway.calls), 1)
        self.assertIn("diferenças", comparison.executive_summary)

    def test_semantic_fields_use_one_batch_and_carry_both_sources(self) -> None:
        response = {
            "comparisons": [
                {
                    "field_name": "side_a",
                    "classification": "mais_favoravel_A",
                    "justification": "A prevê custos de defesa para Side A; B exclui esses custos.",
                },
                {
                    "field_name": "exclusoes",
                    "classification": "diferente_nao_comparavel",
                    "justification": "As exclusões apresentadas não têm escopo equivalente.",
                },
            ]
        }
        gateway = FakeGateway([json.dumps(response, ensure_ascii=False)])
        comparison = ComparisonAgent(
            gateway=gateway,
            model_strong="strong-test",
        ).compare(self.report_a, self.report_b)
        by_field = {item.field_name: item for item in comparison.differences}

        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(gateway.calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(by_field["side_a"].classification, DifferenceClass.MORE_FAVORABLE_A)
        self.assertEqual(by_field["side_a"].citation_a.page_number, 4)
        self.assertEqual(by_field["side_a"].citation_b.page_number, 5)
        self.assertEqual(by_field["exclusoes"].citation_a.source_name, self.report_a.source_name)
        self.assertEqual(by_field["exclusoes"].citation_b.page_number, 9)

    def test_invalid_semantic_output_falls_back_to_non_comparable(self) -> None:
        gateway = FakeGateway(["{ inválido", "{ ainda inválido"])
        comparison = ComparisonAgent(
            gateway=gateway,
            model_strong="strong-test",
        ).compare(self.report_a, self.report_b)

        side_a = next(item for item in comparison.differences if item.field_name == "side_a")
        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(side_a.classification, DifferenceClass.NOT_COMPARABLE)
        self.assertIn("não pôde ser avaliada", side_a.justification)

    def test_different_currency_is_never_compared_as_more_favorable(self) -> None:
        policy_a = PolicyExtraction(
            limite_maximo_garantia=evidence("USD 10,000,000", 1, "limit USD 10,000,000")
        )
        policy_b = PolicyExtraction(
            limite_maximo_garantia=evidence("R$ 8.000.000,00", 2, "limite R$ 8.000.000,00")
        )
        comparison = ComparisonAgent(
            gateway=FakeGateway([]),
            model_strong="strong-test",
        ).compare(
            report(name="A.pdf", file_hash="c", policy=policy_a),
            report(name="B.pdf", file_hash="d", policy=policy_b),
        )

        self.assertEqual(
            comparison.differences[0].classification,
            DifferenceClass.NOT_COMPARABLE,
        )
        self.assertIn("moedas diferem", comparison.differences[0].justification)

    def test_unknown_currency_and_missing_field_are_not_overstated(self) -> None:
        policy_a = PolicyExtraction(
            limite_maximo_garantia=evidence("10.000.000", 1, "Limite: 10.000.000")
        )
        policy_b = PolicyExtraction(
            premio=evidence("R$ 20.000,00", 2, "Prêmio: R$ 20.000,00")
        )
        comparison = ComparisonAgent(
            gateway=FakeGateway([]),
            model_strong="strong-test",
        ).compare(
            report(name="A.pdf", file_hash="e", policy=policy_a),
            report(name="B.pdf", file_hash="f", policy=policy_b),
        )
        by_field = {item.field_name: item for item in comparison.differences}

        self.assertEqual(
            by_field["limite_maximo_garantia"].classification,
            DifferenceClass.MISSING_ON_ONE,
        )
        self.assertEqual(
            by_field["premio"].classification,
            DifferenceClass.MISSING_ON_ONE,
        )

        unknown_currency = ComparisonAgent(
            gateway=FakeGateway([]),
            model_strong="strong-test",
        ).compare(
            report(
                name="C.pdf",
                file_hash="g",
                policy=PolicyExtraction(
                    limite_maximo_garantia=evidence("10.000.000 unidades", 1, "10.000.000 unidades")
                ),
            ),
            report(
                name="D.pdf",
                file_hash="h",
                policy=PolicyExtraction(
                    limite_maximo_garantia=evidence("8.000.000 unidades", 1, "8.000.000 unidades")
                ),
            ),
        )
        self.assertEqual(
            unknown_currency.differences[0].classification,
            DifferenceClass.NOT_COMPARABLE,
        )

    def test_comparison_cache_avoids_second_semantic_request(self) -> None:
        gateway = FakeGateway(
            [
                json.dumps(
                    {
                        "comparisons": [
                            {
                                "field_name": "side_a",
                                "classification": "diferente_nao_comparavel",
                                "justification": "Os textos diferem sem direção clara de vantagem.",
                            },
                            {
                                "field_name": "exclusoes",
                                "classification": "diferente_nao_comparavel",
                                "justification": "O escopo não é diretamente comparável.",
                            },
                        ]
                    }
                )
            ]
        )
        agent = ComparisonAgent(
            gateway=gateway,
            model_strong="strong-test",
            processed_dir=self.processed_dir,
        )
        first = agent.compare(self.report_a, self.report_b)
        second = agent.compare(self.report_a, self.report_b)

        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(first.model_dump(), second.model_dump())

    def test_markdown_and_pdf_report_include_citations_and_summary(self) -> None:
        comparison = ComparisonAgent(
            gateway=FakeGateway(
                [
                    json.dumps(
                        {
                            "comparisons": [
                                {
                                    "field_name": "side_a",
                                    "classification": "mais_favoravel_A",
                                    "justification": "A inclui defesa expressamente.",
                                },
                                {
                                    "field_name": "exclusoes",
                                    "classification": "diferente_nao_comparavel",
                                    "justification": "Os excertos não permitem ordenar o escopo.",
                                },
                            ]
                        }
                    )
                ]
            ),
            model_strong="strong-test",
        ).compare(self.report_a, self.report_b)

        artifacts = ComparisonReportAgent().generate(comparison, self.processed_dir)
        markdown = artifacts.markdown_path.read_text(encoding="utf-8")
        pdf = Path(artifacts.pdf_path).read_bytes()

        self.assertIn("Resumo executivo", markdown)
        self.assertIn("Apólice A pública.pdf, p. 4", markdown)
        self.assertIn("Apólice B pública.pdf, p. 5", markdown)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(artifacts.json_path.exists())


if __name__ == "__main__":
    unittest.main()
