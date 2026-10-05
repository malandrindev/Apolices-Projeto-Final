"""Repositório SQLite versionado para apólices estruturadas e cláusulas de origem."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from src.agents.extraction import Phase2Report
from src.schemas.clause import ClauseChunk
from src.schemas.storage import EvidenceChunk

SCHEMA_VERSION = 1


class RepositoryError(RuntimeError):
    """Falha local de persistência com mensagem segura para exibição."""


class SqliteRepository:
    """Persistência local sem armazenar credenciais ou abrir uma porta de rede."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        try:
            with self._connection() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise RepositoryError(
                        "O banco local foi criado por uma versão mais nova da aplicação."
                    )
                if version == 0:
                    connection.executescript(
                        """
                        BEGIN IMMEDIATE;
                        CREATE TABLE policies (
                            document_id TEXT PRIMARY KEY,
                            source_name TEXT NOT NULL,
                            report_json TEXT NOT NULL,
                            stored_at TEXT NOT NULL
                        );

                        CREATE TABLE clauses (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            document_id TEXT NOT NULL,
                            clause_id TEXT NOT NULL,
                            title TEXT NOT NULL,
                            category TEXT NOT NULL,
                            page_start INTEGER NOT NULL CHECK (page_start >= 1),
                            page_end INTEGER NOT NULL CHECK (page_end >= page_start),
                            text TEXT NOT NULL,
                            UNIQUE (document_id, clause_id),
                            FOREIGN KEY (document_id) REFERENCES policies(document_id)
                                ON DELETE CASCADE
                        );

                        CREATE TABLE evidence_chunks (
                            chunk_id TEXT PRIMARY KEY,
                            document_id TEXT NOT NULL,
                            clause_id TEXT NOT NULL,
                            title TEXT NOT NULL,
                            category TEXT NOT NULL,
                            page_number INTEGER NOT NULL CHECK (page_number >= 1),
                            chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
                            text TEXT NOT NULL,
                            FOREIGN KEY (document_id, clause_id)
                                REFERENCES clauses(document_id, clause_id)
                                ON DELETE CASCADE
                        );

                        CREATE INDEX idx_clauses_document_category
                            ON clauses(document_id, category);
                        CREATE INDEX idx_evidence_document_page
                            ON evidence_chunks(document_id, page_number);
                        PRAGMA user_version = 1;
                        COMMIT;
                        """
                    )
        except RepositoryError:
            raise
        except sqlite3.Error as error:
            raise RepositoryError("Não foi possível inicializar o banco SQLite local.") from error

    def save_document(
        self,
        report: Phase2Report,
        clauses: list[ClauseChunk],
        chunks: list[EvidenceChunk],
    ) -> None:
        """Grava relatório e segmentos em uma transação idempotente por SHA-256."""
        if any(chunk.document_id != report.sha256 for chunk in chunks):
            raise RepositoryError("Há trechos vetoriais associados a outro documento.")

        now = datetime.now(UTC).isoformat()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO policies(document_id, source_name, report_json, stored_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(document_id) DO UPDATE SET
                        source_name = excluded.source_name,
                        report_json = excluded.report_json,
                        stored_at = excluded.stored_at
                    """,
                    (
                        report.sha256,
                        report.source_name,
                        report.model_dump_json(),
                        now,
                    ),
                )
                connection.execute("DELETE FROM clauses WHERE document_id = ?", (report.sha256,))
                connection.executemany(
                    """
                    INSERT INTO clauses(
                        document_id, clause_id, title, category, page_start, page_end, text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            report.sha256,
                            clause.clause_id,
                            clause.title,
                            clause.category.value,
                            clause.page_start,
                            clause.page_end,
                            clause.text,
                        )
                        for clause in clauses
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO evidence_chunks(
                        chunk_id, document_id, clause_id, title, category,
                        page_number, chunk_index, text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chunk.chunk_id,
                            chunk.document_id,
                            chunk.clause_id,
                            chunk.title,
                            chunk.category,
                            chunk.page_number,
                            chunk.chunk_index,
                            chunk.text,
                        )
                        for chunk in chunks
                    ],
                )
        except RepositoryError:
            raise
        except sqlite3.Error as error:
            raise RepositoryError("Não foi possível salvar o documento no SQLite local.") from error

    def get_document(self, document_id: str) -> Phase2Report | None:
        """Recupera o relatório Pydantic por hash integral do arquivo."""
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT report_json FROM policies WHERE document_id = ?",
                    (document_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise RepositoryError("Não foi possível consultar o banco SQLite local.") from error
        if row is None:
            return None
        try:
            return Phase2Report.model_validate_json(row["report_json"])
        except ValueError as error:
            raise RepositoryError("O relatório armazenado não passou pela validação do schema.") from error

    def get_evidence_chunks(self, document_id: str) -> list[EvidenceChunk]:
        """Lista os trechos indexáveis de um documento, ordenados por página."""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT chunk_id, document_id, clause_id, title, category,
                           page_number, chunk_index, text
                    FROM evidence_chunks
                    WHERE document_id = ?
                    ORDER BY page_number, clause_id, chunk_index
                    """,
                    (document_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError("Não foi possível consultar os trechos no SQLite local.") from error
        return [EvidenceChunk.model_validate(dict(row)) for row in rows]

    def list_documents(self) -> list[dict[str, str]]:
        """Retorna apenas metadados e hashes, sem texto nem JSON de apólice."""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT document_id, source_name, stored_at FROM policies ORDER BY stored_at DESC"
                ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError("Não foi possível listar os documentos locais.") from error
        return [dict(row) for row in rows]
