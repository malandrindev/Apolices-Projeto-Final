"""Testes locais de SQLite, chunking, Chroma adapter e citações RAG."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.agents.extraction import Phase2Report
from src.agents.ocr import PageText, ProcessedDocument
from src.agents.rag import RagAgent
from src.agents.segmentation import SegmentationAgent
from src.config import StorageSettings
from src.schemas.clause import ClauseCategory, ClauseChunk, SourcePage
from src.schemas.policy import FieldEvidence, PolicyExtraction
from src.schemas.storage import EvidenceChunk, RetrievedChunk
from src.storage.document_store import DocumentStore
from src.storage.sqlite_repo import SqliteRepository
from src.storage.vector_store import ChromaVectorStore


class FakeEncoder:
    model_name = "fake/multilingual-test"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text) % 7), 1.0, 0.5] for text in texts]


class FakeCollection:
    def __init__(self) -> None:
        self.metadata = {
            "hnsw:space": "cosine",
            "embedding_model": FakeEncoder.model_name,
        }
        self.items: dict[str, dict[str, Any]] = {}
        self.query_where: dict[str, str] | None = None

    def count(self) -> int:
        return len(self.items)

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for chunk_id, embedding, document, metadata in zip(
            ids, embeddings, documents, metadatas, strict=True
        ):
            self.items[chunk_id] = {
                "embedding": embedding,
                "document": document,
                "metadata": metadata,
            }

    def query(self, **kwargs: Any) -> dict[str, list[list[Any]]]:
        self.query_where = kwargs.get("where")
        matching = list(self.items.items())
        if self.query_where:
            matching = [
                item
                for item in matching
                if item[1]["metadata"].get("document_id")
                == self.query_where["document_id"]
            ]
        if not matching:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        chunk_id, item = matching[0]
        del chunk_id
        return {
            "documents": [[item["document"]]],
            "metadatas": [[item["metadata"]]],
            "distances": [[0.12]],
        }

    def get(
        self,
        *,
        where: dict[str, str] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, list[str]]:
        del include
        matching_ids = [
            chunk_id
            for chunk_id, item in self.items.items()
            if not where
            or item["metadata"].get("document_id") == where.get("document_id")
        ]
        return {"ids": matching_ids}

    def delete(
        self,
        *,
        where: dict[str, str] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        self.items = {
            chunk_id: item
            for chunk_id, item in self.items.items()
            if not (
                (where and item["metadata"].get("document_id") == where.get("document_id"))
                or (ids and chunk_id in ids)
            )
        }


class FakeChromaClient:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection

    def get_or_create_collection(self, *, name: str, metadata: dict[str, str]) -> FakeCollection:
        del name, metadata
        return self.collection


class FakeRagVectorStore:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.query: str | None = None
        self.document_id: str | None = None

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        document_id: str | None = None,
    ) -> list[RetrievedChunk]:
        self.query = query
        self.document_id = document_id
        return self.chunks[:top_k] if top_k else self.chunks


class FakeGateway:
    def __init__(self, response: str | list[str]) -> None:
        self.responses = [response] if isinstance(response, str) else list(response)
        self.messages: list[dict[str, str]] = []
        self.calls = 0

    def complete(self, **kwargs: Any) -> str:
        self.messages = kwargs["messages"]
        response = self.responses[self.calls]
        self.calls += 1
        return response


class StorageAndRagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = StorageSettings(
            sqlite_path=self.root / "policies.sqlite3",
            chroma_path=self.root / "chroma",
            chroma_collection="test_collection",
            embedding_model=FakeEncoder.model_name,
            embedding_device="cpu",
            chunk_size_chars=400,
            chunk_overlap_chars=50,
            rag_top_k=3,
        )
        self.document = ProcessedDocument(
            source_name="sample-public-policy.pdf",
            sha256="b" * 64,
            size_bytes=456,
            media_type="application/pdf",
            pages=[
                PageText(
                    page_number=1,
                    text="1. EXCLUSÕES\nExclui-se fraude comprovada no trecho da apólice.",
                    extraction_method="native",
                )
            ],
            processed_at="2026-09-30T00:00:00Z",
            cache_key="ocr-fixture",
        )
        self.clauses = SegmentationAgent().segment(self.document)
        self.report = Phase2Report(
            source_name=self.document.source_name,
            sha256=self.document.sha256,
            clause_count=len(self.clauses),
            policy=PolicyExtraction(
                exclusoes=FieldEvidence(
                    valor="Fraude comprovada",
                    pagina=1,
                    trecho_origem="fraude comprovada",
                    confianca=0.8,
                )
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sqlite_repository_persists_policy_and_page_chunks_idempotently(self) -> None:
        chunks = ChromaVectorStore.split_clauses(
            self.clauses,
            document_id=self.report.sha256,
            chunk_size_chars=self.settings.chunk_size_chars,
            chunk_overlap_chars=self.settings.chunk_overlap_chars,
        )
        repository = SqliteRepository(self.settings.sqlite_path)
        repository.save_document(self.report, self.clauses, chunks)
        repository.save_document(self.report, self.clauses, chunks)

        stored = repository.get_document(self.report.sha256)
        stored_chunks = repository.get_evidence_chunks(self.report.sha256)

        self.assertEqual(stored.policy.exclusoes.valor, "Fraude comprovada")
        self.assertEqual(len(stored_chunks), len(chunks))
        self.assertEqual(repository.list_documents()[0]["source_name"], self.report.source_name)

    def test_sqlite_initial_schema_migration_rolls_back_on_partial_failure(self) -> None:
        import sqlite3
        from contextlib import closing

        database_path = self.root / "broken_migration.sqlite3"
        with closing(sqlite3.connect(database_path)) as connection, connection:
            connection.execute("CREATE TABLE policies (placeholder TEXT)")

        with self.assertRaisesRegex(RuntimeError, "inicializar"):
            SqliteRepository(database_path)

        with closing(sqlite3.connect(database_path)) as connection, connection:
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]

        self.assertEqual(table_names, {"policies"})
        self.assertEqual(user_version, 0)

    def test_chunks_preserve_page_and_have_bounded_overlap(self) -> None:
        text = " ".join(f"palavra{index}" for index in range(180))
        clause = ClauseChunk(
            clause_id="clause-0001",
            title="Condições gerais",
            category=ClauseCategory.OTHER,
            page_start=7,
            page_end=7,
            text=text,
            source_pages=[SourcePage(page_number=7, text=text)],
        )
        chunks = ChromaVectorStore.split_clauses(
            [clause],
            document_id=self.report.sha256,
            chunk_size_chars=400,
            chunk_overlap_chars=50,
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.page_number == 7 for chunk in chunks))
        self.assertTrue(all(len(chunk.text) <= 400 for chunk in chunks))
        self.assertTrue(all(chunk.document_id == self.report.sha256 for chunk in chunks))

    def test_chroma_adapter_uses_local_embeddings_and_metadata_filter(self) -> None:
        collection = FakeCollection()
        vector_store = ChromaVectorStore(
            self.settings,
            encoder=FakeEncoder(),
            client=FakeChromaClient(collection),
        )
        chunks = ChromaVectorStore.split_clauses(
            self.clauses,
            document_id=self.report.sha256,
            chunk_size_chars=self.settings.chunk_size_chars,
            chunk_overlap_chars=self.settings.chunk_overlap_chars,
        )
        stored_count = vector_store.upsert(chunks, source_name=self.report.source_name)
        results = vector_store.search("fraude coberta?", document_id=self.report.sha256)

        self.assertEqual(stored_count, len(chunks))
        self.assertEqual(results[0].source_name, self.report.source_name)
        self.assertEqual(results[0].page_number, 1)
        self.assertEqual(collection.query_where, {"document_id": self.report.sha256})

    def test_reindex_removes_stale_chunks_after_successful_upsert(self) -> None:
        collection = FakeCollection()
        vector_store = ChromaVectorStore(
            self.settings,
            encoder=FakeEncoder(),
            client=FakeChromaClient(collection),
        )
        old_chunk = EvidenceChunk(
            chunk_id=f"{self.report.sha256}:old-clause:p1:c0",
            document_id=self.report.sha256,
            clause_id="old-clause",
            title="Antiga seção",
            category="outras",
            page_number=1,
            chunk_index=0,
            text="Texto que não existe na nova versão.",
        )
        new_chunk = EvidenceChunk(
            chunk_id=f"{self.report.sha256}:new-clause:p2:c0",
            document_id=self.report.sha256,
            clause_id="new-clause",
            title="Seção atualizada",
            category="outras",
            page_number=2,
            chunk_index=0,
            text="Texto atualizado da cláusula atual.",
        )

        vector_store.upsert([old_chunk], source_name=self.report.source_name)
        vector_store.upsert([new_chunk], source_name=self.report.source_name)

        self.assertEqual(set(collection.items), {new_chunk.chunk_id})

    def test_document_store_saves_sqlite_and_vector_index(self) -> None:
        collection = FakeCollection()
        vector_store = ChromaVectorStore(
            self.settings,
            encoder=FakeEncoder(),
            client=FakeChromaClient(collection),
        )
        store = DocumentStore(
            self.settings,
            repository=SqliteRepository(self.settings.sqlite_path),
            vector_store=vector_store,
        )

        count = store.store(self.report, self.clauses)

        self.assertGreaterEqual(count, 1)
        self.assertIsNotNone(store.get_document(self.report.sha256))
        self.assertEqual(len(store.get_chunks(self.report.sha256)), count)

    def test_rag_response_citations_are_derived_from_retrieved_metadata(self) -> None:
        retrieved = RetrievedChunk(
            chunk_id=f"{self.report.sha256}:clause-0001:p1:c0",
            document_id=self.report.sha256,
            source_name=self.report.source_name,
            clause_id="clause-0001",
            title="Exclusões",
            category="exclusoes",
            page_number=1,
            text="Exclui-se fraude comprovada no trecho da apólice.",
            distance=0.12,
        )
        gateway = FakeGateway(
            '{"answer":"A cláusula exclui fraude comprovada.",'
            f'"cited_chunk_ids":["{retrieved.chunk_id}"]}}'
        )
        vector_store = FakeRagVectorStore([retrieved])
        response = RagAgent(
            gateway=gateway,
            model_strong="strong-test",
            vector_store=vector_store,
        ).answer("A apólice cobre fraude?", document_id=self.report.sha256)

        self.assertEqual(response.answer, "A cláusula exclui fraude comprovada.")
        self.assertEqual(response.citations[0].source_name, self.report.source_name)
        self.assertEqual(response.citations[0].page_number, 1)
        self.assertEqual(vector_store.document_id, self.report.sha256)
        self.assertIn(retrieved.text, gateway.messages[1]["content"])
        self.assertNotIn(retrieved.source_name, gateway.messages[1]["content"])

    def test_rag_abstains_without_retrieval_and_does_not_call_llm(self) -> None:
        gateway = FakeGateway('{"answer":"nao_localizado","cited_chunk_ids":[]}')
        response = RagAgent(
            gateway=gateway,
            model_strong="strong-test",
            vector_store=FakeRagVectorStore([]),
        ).answer("Existe extensão para ex-administradores?")

        self.assertEqual(response.answer, "nao_localizado")
        self.assertEqual(response.citations, [])
        self.assertEqual(gateway.messages, [])

    def test_rag_rejects_unknown_citation_and_abstains_after_one_retry(self) -> None:
        retrieved = RetrievedChunk(
            chunk_id=f"{self.report.sha256}:clause-0001:p1:c0",
            document_id=self.report.sha256,
            source_name=self.report.source_name,
            clause_id="clause-0001",
            title="Exclusões",
            category="exclusoes",
            page_number=1,
            text="Exclui-se fraude comprovada no trecho da apólice.",
            distance=0.12,
        )
        gateway = FakeGateway(
            [
                '{"answer":"A apólice cobre fraude.","cited_chunk_ids":["inventado"]}',
                '{"answer":"A apólice cobre fraude.","cited_chunk_ids":["inventado"]}',
            ]
        )
        response = RagAgent(
            gateway=gateway,
            model_strong="strong-test",
            vector_store=FakeRagVectorStore([retrieved]),
        ).answer("A apólice cobre fraude?")

        self.assertEqual(gateway.calls, 2)
        self.assertEqual(response.answer, "nao_localizado")
        self.assertEqual(response.citations, [])


if __name__ == "__main__":
    unittest.main()
