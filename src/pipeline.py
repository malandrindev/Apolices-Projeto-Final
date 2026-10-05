"""Orquestra ingestão e extração local sem depender de credenciais de LLM."""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Any

from src.agents.extraction import Phase2Pipeline, Phase2Report
from src.agents.ingestion import IngestedDocument, IngestionAgent, IngestionError
from src.agents.ocr import OcrAgent, OcrError, ProcessedDocument
from src.config import (
    ConfigurationError,
    IngestionSettings,
    Settings,
    StorageSettings,
    get_ingestion_settings,
    get_extraction_optimization_settings,
    get_extraction_routing,
    get_settings,
    get_storage_settings,
)
from src.llm.providers import get_gateway
from src.storage.cache import JsonCache, make_cache_key
from src.storage.document_store import DocumentStore


def process_document(
    source: str | Path,
    *,
    settings: IngestionSettings | None = None,
    ocr_progress_callback: Callable | None = None,
) -> tuple[IngestedDocument, ProcessedDocument]:
    """Valida, calcula hash, deduplica via cache e grava o texto página a página."""
    effective_settings = settings or get_ingestion_settings()
    ingested = IngestionAgent(effective_settings).ingest(source)
    processed = OcrAgent(effective_settings, progress_callback=ocr_progress_callback).extract(ingested)
    return ingested, processed


def process_and_structure_document(
    source: str | Path,
    *,
    ingestion_settings: IngestionSettings | None = None,
    llm_settings: Settings | None = None,
    gateway: Any | None = None,
    progress_callback: Callable | None = None,
    ocr_progress_callback: Callable | None = None,
) -> tuple[IngestedDocument, ProcessedDocument, Phase2Report, Path, list]:
    """Executa OCR e estruturação por cláusula; persiste JSON sem texto integral."""
    effective_ingestion = ingestion_settings or get_ingestion_settings()
    effective_llm = llm_settings or get_settings()
    ingested, processed = process_document(source, settings=effective_ingestion, ocr_progress_callback=ocr_progress_callback)
    model_extraction = effective_llm.model_fast if effective_llm.llm_provider == "openai" else effective_llm.model_strong
    optimization = get_extraction_optimization_settings()
    routing = get_extraction_routing(effective_llm)
    effective_gateway = gateway or get_gateway(effective_llm)
    pipeline = Phase2Pipeline(
        gateway=effective_gateway,
        model_fast=effective_llm.model_fast,
        model_strong=model_extraction,
        model_repair=effective_llm.model_intermediate if effective_llm.llm_provider == "openai" else None,
        processed_dir=effective_ingestion.processed_dir,
        optimization=optimization,
        routing=routing,
    )
    clauses, extraction = pipeline.run(processed, progress_callback=progress_callback)
    report = Phase2Report(
        source_name=ingested.source_name,
        sha256=ingested.sha256,
        clause_count=len(clauses),
        policy=extraction.policy,
        issues=extraction.issues,
        field_status=extraction.field_status,
        retrieval_diagnostics=extraction.retrieval_diagnostics,
    )
    cache_key = make_cache_key(
        file_sha256=ingested.sha256,
        pipeline_version="policy-report-grouped-v1" if extraction.retrieval_diagnostics else "policy-report-v1",
        options={
            "provider": effective_llm.llm_provider,
            "model_extraction": model_extraction,
            "prompt_version": "extraction-v2",
            **({"routing_policy": effective_gateway.policy.routing_signature} if hasattr(effective_gateway, "complete_routed") else {}),
            "model_fast": effective_llm.model_fast,
            "model_strong": effective_llm.model_strong,
            **({"optimization": asdict(optimization), "routing": asdict(routing)} if extraction.retrieval_diagnostics else {}),
        },
    )
    report_path = JsonCache(effective_ingestion.processed_dir).save(
        cache_key,
        report.model_dump(mode="json"),
    )
    # SQLite stores structured data even when the optional vector index is unused.
    from src.storage.sqlite_repo import SqliteRepository
    from src.storage.vector_store import ChromaVectorStore
    storage = get_storage_settings()
    chunks = ChromaVectorStore.split_clauses(clauses, document_id=report.sha256, chunk_size_chars=storage.chunk_size_chars, chunk_overlap_chars=storage.chunk_overlap_chars)
    SqliteRepository(effective_ingestion.processed_dir / "policies.sqlite3").save_document(report, clauses, chunks)
    return ingested, processed, report, report_path, clauses


def index_phase2_document(
    report: Phase2Report,
    clauses: list,
    *,
    storage_settings: StorageSettings | None = None,
) -> int:
    """Persiste apólice em SQLite e indexa cláusulas no Chroma local."""
    return DocumentStore(storage_settings or get_storage_settings()).store(report, clauses)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida documentos; --structure chama o provider configurado e --index inclui SQLite/Chroma."
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="um ou mais PDFs/imagens para processar",
    )
    parser.add_argument(
        "--structure",
        action="store_true",
        help="também segmenta e estrutura os documentos com o provider configurado",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="estrutura com o provider configurado e persiste no SQLite/Chroma local com embeddings locais",
    )
    arguments = parser.parse_args()

    try:
        settings = get_ingestion_settings()
        for source in arguments.files:
            if arguments.structure or arguments.index:
                document, result, report, report_path, clauses = process_and_structure_document(
                    source,
                    ingestion_settings=settings,
                )
                indexed = (
                    index_phase2_document(report, clauses) if arguments.index else None
                )
                print(
                    f"Processado e estruturado: {document.source_name}; "
                    f"sha256={document.sha256}; páginas={len(result.pages)}; "
                    f"cláusulas={report.clause_count}; issues={len(report.issues)}; "
                    f"indexados={indexed if indexed is not None else 'não solicitado'}; "
                    f"resultado={report_path}"
                )
            else:
                document, result = process_document(source, settings=settings)
                cache_path = settings.processed_dir / f"{result.cache_key}.json"
                print(
                    f"Processado: {document.source_name}; sha256={document.sha256}; "
                    f"páginas={len(result.pages)}; cache={'sim' if result.cache_hit else 'não'}; "
                    f"resultado={cache_path}"
                )
    except (ConfigurationError, IngestionError, OcrError, RuntimeError, ValueError, OSError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
