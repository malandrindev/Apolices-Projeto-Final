"""Orquestra persistência estruturada SQLite e indexação vetorial Chroma."""

from __future__ import annotations

from src.agents.extraction import Phase2Report
from src.config import StorageSettings
from src.schemas.clause import ClauseChunk
from src.schemas.storage import EvidenceChunk, RetrievedChunk
from src.storage.sqlite_repo import SqliteRepository
from src.storage.vector_store import ChromaVectorStore, VectorStoreError


class DocumentStoreError(RuntimeError):
    """Falha de persistência/indexação do documento."""


class DocumentStore:
    """Mantém SQLite como fonte estruturada e Chroma como índice vetorial local."""

    def __init__(
        self,
        settings: StorageSettings,
        *,
        repository: SqliteRepository | None = None,
        vector_store: ChromaVectorStore | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository or SqliteRepository(settings.sqlite_path)
        self.vector_store = vector_store or ChromaVectorStore(settings)

    def store(
        self,
        report: Phase2Report,
        clauses: list[ClauseChunk],
    ) -> int:
        """Persiste os dados e cria embeddings locais; reprocessar é idempotente."""
        chunks = ChromaVectorStore.split_clauses(
            clauses,
            document_id=report.sha256,
            chunk_size_chars=self.settings.chunk_size_chars,
            chunk_overlap_chars=self.settings.chunk_overlap_chars,
        )
        self.repository.save_document(report, clauses, chunks)
        if not chunks:
            self.vector_store.delete_document(report.sha256)
            return 0
        try:
            return self.vector_store.upsert(chunks, source_name=report.source_name)
        except VectorStoreError as error:
            # O SQLite permanece íntegro; repetir a indexação com os mesmos IDs é seguro.
            raise DocumentStoreError(
                "A apólice foi salva no SQLite, mas não foi possível concluir o índice vetorial."
            ) from error

    def get_document(self, document_id: str) -> Phase2Report | None:
        return self.repository.get_document(document_id)

    def get_chunks(self, document_id: str) -> list[EvidenceChunk]:
        return self.repository.get_evidence_chunks(document_id)

    def search(
        self,
        query: str,
        *,
        document_id: str | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        return self.vector_store.search(query, document_id=document_id, top_k=top_k)
