"""Cache persistente de embeddings, sem baixar modelos nem acessar APIs."""
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import StorageSettings
from src.schemas.storage import EvidenceChunk
from src.storage.vector_store import ChromaVectorStore, VectorStoreError
from tests.test_storage_rag import FakeChromaClient, FakeCollection, FakeEncoder


class CountingEncoder(FakeEncoder):
    def __init__(self, model_name: str = FakeEncoder.model_name) -> None:
        self.model_name = model_name
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return super().encode(texts)


class VectorCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.settings = StorageSettings(
            sqlite_path=self.root / "policies.sqlite3", chroma_path=self.root / "chroma",
            chroma_collection="cache_test", embedding_model=FakeEncoder.model_name,
            embedding_device="cpu", chunk_size_chars=400, chunk_overlap_chars=50, rag_top_k=3,
        )
        self.encoder = CountingEncoder()
        self.collection = FakeCollection()
        self.store = ChromaVectorStore(
            self.settings, encoder=self.encoder, client=FakeChromaClient(self.collection),
        )
        self.chunks = [self.chunk(number, f"Diagnostico independente {number}.") for number in range(4)]

    def tearDown(self) -> None:
        self.directory.cleanup()

    @staticmethod
    def chunk(number: int, text: str) -> EvidenceChunk:
        return EvidenceChunk(
            chunk_id=f"diagnostic:clause-{number}:p1:c0", document_id="diagnostic",
            clause_id=f"clause-{number}", title="Diagnostico", category="outras",
            page_number=1, chunk_index=0, text=text,
        )

    def test_identical_reindex_and_new_instance_reuse_persistent_vectors(self) -> None:
        self.store.upsert(self.chunks, source_name="synthetic-diagnostic")
        before = {key: item["embedding"] for key, item in self.collection.items.items()}
        self.store.upsert(self.chunks, source_name="synthetic-diagnostic")
        second_encoder = CountingEncoder()
        second = ChromaVectorStore(
            self.settings, encoder=second_encoder, client=FakeChromaClient(self.collection),
        )
        second.upsert(self.chunks, source_name="synthetic-diagnostic")
        self.assertEqual(len(self.encoder.calls), 1)
        self.assertEqual(len(self.encoder.calls[0]), 4)
        self.assertEqual(second_encoder.calls, [])
        self.assertEqual(before, {key: item["embedding"] for key, item in self.collection.items.items()})
        for path in self.store.embedding_cache.directory.glob("*.json"):
            payload = path.read_text(encoding="utf-8")
            self.assertNotIn("Diagnostico independente", payload)

    def test_changed_text_recomputes_only_changed_vector(self) -> None:
        self.store.upsert(self.chunks, source_name="synthetic-diagnostic")
        changed = [*self.chunks]
        changed[2] = changed[2].model_copy(update={"text": "Diagnostico atualizado."})
        self.store.upsert(changed, source_name="synthetic-diagnostic")
        self.assertEqual(self.encoder.calls[1], ["Diagnostico atualizado."])
        self.assertEqual(set(self.collection.items), {chunk.chunk_id for chunk in changed})

    def test_model_identity_separates_cache(self) -> None:
        self.store.upsert(self.chunks, source_name="synthetic-diagnostic")
        other_encoder = CountingEncoder("fake/another-model")
        other_collection = FakeCollection()
        other_collection.metadata["embedding_model"] = other_encoder.model_name
        other = ChromaVectorStore(
            self.settings, encoder=other_encoder, client=FakeChromaClient(other_collection),
        )
        other.upsert(self.chunks, source_name="synthetic-diagnostic")
        self.assertEqual(len(other_encoder.calls), 1)
        self.assertEqual(len(list(self.store.embedding_cache.directory.glob("*.json"))), 8)

    def test_invalid_dimensions_nonfinite_and_wrong_hash_cache_are_rebuilt(self) -> None:
        self.store.upsert(self.chunks[:1], source_name="synthetic-diagnostic")
        path = next(self.store.embedding_cache.directory.glob("*.json"))
        invalid = [
            {"dimensions": 999},
            {"vector": [math.nan, 1, 2]},
            {"text_sha256": "f" * 64},
        ]
        for update in invalid:
            with self.subTest(update=list(update)):
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload.update(update)
                path.write_text(json.dumps(payload), encoding="utf-8")
                count = len(self.encoder.calls)
                self.store.upsert(self.chunks[:1], source_name="synthetic-diagnostic")
                self.assertEqual(len(self.encoder.calls), count + 1)
                repaired = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(repaired["dimensions"], len(repaired["vector"]))
                self.assertTrue(all(math.isfinite(value) for value in repaired["vector"]))

    def test_duplicate_texts_encode_once_and_preserve_independent_ids(self) -> None:
        duplicated = [self.chunk(1, "Mesmo diagnostico."), self.chunk(2, "Mesmo diagnostico.")]
        self.store.upsert(duplicated, source_name="synthetic-diagnostic")
        self.assertEqual(self.encoder.calls, [["Mesmo diagnostico."]])
        self.assertEqual(len(self.collection.items), 2)
        self.assertEqual(self.collection.items[duplicated[0].chunk_id]["embedding"], self.collection.items[duplicated[1].chunk_id]["embedding"])

    def test_bad_encoder_vectors_are_rejected_before_persistence(self) -> None:
        for vector in ([], [float("inf"), 1], [float("nan"), 1]):
            with self.subTest(vector_size=len(vector)):
                with patch.object(self.encoder, "encode", return_value=[vector]):
                    with self.assertRaises(VectorStoreError):
                        self.store.upsert(self.chunks[:1], source_name="synthetic-diagnostic")
        self.assertEqual(self.collection.items, {})
        self.assertEqual(list(self.store.embedding_cache.directory.glob("*.json")), [])

    def test_query_cache_can_be_disabled(self) -> None:
        self.store.upsert(self.chunks, source_name="synthetic-diagnostic")
        self.store.search("consulta repetida", document_id="diagnostic")
        self.store.search("consulta repetida", document_id="diagnostic")
        self.assertEqual(len(self.encoder.calls), 2)
        uncached = ChromaVectorStore(
            self.settings, encoder=self.encoder, client=FakeChromaClient(self.collection), cache_queries=False,
        )
        uncached.search("consulta repetida", document_id="diagnostic")
        uncached.search("consulta repetida", document_id="diagnostic")
        self.assertEqual(len(self.encoder.calls), 4)
        self.assertEqual(self.collection.query_where, {"document_id": "diagnostic"})

    def test_chroma_specific_backend_errors_become_safe_vector_errors(self) -> None:
        from chromadb.errors import InvalidArgumentError
        self.store.upsert(self.chunks[:1], source_name="synthetic-diagnostic")
        operations = [
            (self.collection, "upsert", lambda: self.store.upsert(self.chunks[:1], source_name="synthetic-diagnostic")),
            (self.collection, "query", lambda: self.store.search("consulta de diagnostico")),
            (self.collection, "delete", lambda: self.store.delete_document("diagnostic")),
        ]
        new_client = FakeChromaClient(self.collection)
        new_store = ChromaVectorStore(self.settings, encoder=self.encoder, client=new_client)
        operations.append((new_client, "get_or_create_collection", lambda: new_store.search("consulta de diagnostico")))
        for target, method, operation in operations:
            with self.subTest(method=method):
                with patch.object(target, method, side_effect=InvalidArgumentError("diagnostic content must not escape")):
                    with self.assertRaises(VectorStoreError) as caught:
                        operation()
                self.assertNotIn("diagnostic content", str(caught.exception))

    def test_failed_upsert_preserves_old_ids_and_retry_reuses_computed_vector(self) -> None:
        self.store.upsert(self.chunks[:1], source_name="synthetic-diagnostic")
        replacement = [self.chunk(7, "Diagnostico substituto.")]
        with patch.object(self.collection, "upsert", side_effect=RuntimeError("simulated failure")):
            with self.assertRaises(VectorStoreError):
                self.store.upsert(replacement, source_name="synthetic-diagnostic")
        self.assertEqual(set(self.collection.items), {self.chunks[0].chunk_id})
        self.store.upsert(replacement, source_name="synthetic-diagnostic")
        self.assertEqual(len(self.encoder.calls), 2)
        self.assertEqual(set(self.collection.items), {replacement[0].chunk_id})


if __name__ == "__main__":
    unittest.main()
