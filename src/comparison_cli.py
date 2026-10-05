"""CLI para comparar duas apólices já estruturadas e exportar os relatórios."""

from __future__ import annotations

import argparse
import sys

from src.agents.comparison import ComparisonAgent, ComparisonError
from src.agents.report import ComparisonReportAgent, ReportError
from src.config import ConfigurationError, get_settings, get_storage_settings, get_extraction_routing
from src.llm.providers import get_gateway
from src.storage.sqlite_repo import RepositoryError, SqliteRepository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compara duas apólices SQLite; valores numéricos são determinísticos."
    )
    parser.add_argument("document_id_a", help="SHA-256 integral da apólice A")
    parser.add_argument("document_id_b", help="SHA-256 integral da apólice B")
    arguments = parser.parse_args()

    try:
        storage_settings = get_storage_settings()
        repository = SqliteRepository(storage_settings.sqlite_path)
        report_a = repository.get_document(arguments.document_id_a)
        report_b = repository.get_document(arguments.document_id_b)
        if report_a is None or report_b is None:
            raise ComparisonError(
                "Uma das apólices não foi encontrada no SQLite. Execute o pipeline --structure primeiro."
            )
        llm_settings = get_settings()
        comparison = ComparisonAgent(
            gateway=get_gateway(llm_settings),
            model_strong=get_extraction_routing(llm_settings).comparison_model or llm_settings.model_strong,
            processed_dir=storage_settings.sqlite_path.parent,
        ).compare(report_a, report_b)
        artifacts = ComparisonReportAgent().generate(
            comparison,
            storage_settings.sqlite_path.parent,
        )
    except (
        ComparisonError,
        ConfigurationError,
        ReportError,
        RepositoryError,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1

    print(comparison.executive_summary)
    print(f"Markdown: {artifacts.markdown_path}")
    print(f"PDF: {artifacts.pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
