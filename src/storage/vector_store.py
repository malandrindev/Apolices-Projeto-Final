"""ChromaDB persistente com vetores calculados localmente por Sentence Transformers."""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.config import StorageSettings
from src.schemas.clause import ClauseChunk
from src.schemas.storage import EvidenceChunk, RetrievedChunk
from src.storage.cache import JsonCache, make_cache_key

logger = logging.getLogger(__name__)


class VectorStoreError(RuntimeError):
    """Erro seguro de persistência ou busca semântica local."""


def _is_chroma_error(error: Exception) -> bool:
    """Identifica erros do backend sem importar Chroma no startup do adaptador."""
    return any(
        kind.__module__ == "chromadb.errors" and kind.__name__ == "ChromaError"
        for kind in type(error).__mro__
    )


class EmbeddingCacheEntry(BaseModel):
    """Um vetor validado; guarda hash do texto, nunca o texto original."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal["embedding-cache-v1"] = "embedding-cache-v1"
    model_name: str = Field(min_length=1)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimensions: int = Field(ge=1)
    vector: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dimensions(self) -> EmbeddingCacheEntry:
        if len(self.vector) != self.dimensions:
            raise ValueError("Dimensões do vetor não correspondem aos metadados.")
        return self


class EmbeddingEncoder(Protocol):
    model_name: str

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEncoder:
    """Carregamento tardio de um modelo SBERT local/configurável, sempre em CPU por padrão."""

    def __init__(self, model_name: str, device: str = "cpu") -> None:
        self.model_name = model_name
        self.device = device
        self._model: Any | None = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name, device=self.device)
            except (ImportError, OSError, RuntimeError, ValueError) as error:
                raise VectorStoreError(
                    "Não foi possível carregar o modelo local de embeddings. "
                    "Verifique a instalação e o acesso ao modelo configurado."
                ) from error
        try:
            vectors = self._model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            encoded = vectors.tolist()
            if not all(math.isfinite(float(value)) for vector in encoded for value in vector):
                raise ValueError("embedding não finito")
            return [[float(value) for value in vector] for vector in encoded]
        except (RuntimeError, ValueError, TypeError) as error:
            raise VectorStoreError("Falha ao calcular embeddings locais.") from error


class ChromaVectorStore:
    """Índice persistente de chunks; não inicia servidor HTTP nem usa embeddings remotos."""

    def __init__(
        self,
        settings: StorageSettings,
        *,
        encoder: EmbeddingEncoder | None = None,
        client: Any | None = None,
        cache_queries: bool = True,
    ) -> None:
        self.settings = settings
        self.encoder = encoder or SentenceTransformerEncoder(
            settings.embedding_model,
            settings.embedding_device,
        )
        self._client = client
        self._collection: Any | None = None
        self.cache_queries = cache_queries
        self.embedding_cache = JsonCache(settings.chroma_path.parent / "embedding_cache")

    def _encode_cached(self, texts: list[str]) -> list[list[float]]:
        """Codifica apenas textos ausentes no cache, inclusive duplicatas de um lote."""
        if not texts:
            return []
        keys: list[str] = []
        hashes: dict[str, str] = {}
        missing: dict[str, str] = {}
        vectors: dict[str, list[float]] = {}
        for text in texts:
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            key = make_cache_key(
                file_sha256=text_hash,
                pipeline_version="embedding-cache-v1",
                options={"model_name": self.encoder.model_name, "normalize_embeddings": True},
            )
            keys.append(key)
            hashes[key] = text_hash
            if key in vectors or key in missing:
                continue
            cached = self.embedding_cache.load(key)
            if cached:
                try:
                    entry = EmbeddingCacheEntry.model_validate(cached)
                    if entry.model_name == self.encoder.model_name and entry.text_sha256 == text_hash:
                        vectors[key] = entry.vector
                        continue
                except ValidationError:
                    pass
            missing[key] = text
        if missing:
            encoded = self.encoder.encode(list(missing.values()))
            if len(encoded) != len(missing):
                raise VectorStoreError("O modelo local retornou embeddings em quantidade inválida.")
            for key, vector in zip(missing, encoded, strict=True):
                try:
                    entry = EmbeddingCacheEntry(
                        model_name=self.encoder.model_name, text_sha256=hashes[key],
                        dimensions=len(vector), vector=vector,
                    )
                except (ValidationError, TypeError) as error:
                    raise VectorStoreError("O modelo local retornou um vetor vazio ou inválido.") from error
                vectors[key] = entry.vector
                try:
                    self.embedding_cache.save(key, entry.model_dump(mode="json"))
                except OSError:
                    # Cache é otimização; a persistência principal continua disponível.
                    logger.warning("Não foi possível gravar uma entrada do cache local de embeddings.")
        result = [vectors[key] for key in keys]
        if len({len(vector) for vector in result}) != 1:
            raise VectorStoreError("O modelo/cache local retornou dimensões inconsistentes.")
        return result

    @staticmethod
    def split_clauses(
        clauses: list[ClauseChunk],
        *,
        document_id: str,
        chunk_size_chars: int,
        chunk_overlap_chars: int,
    ) -> list[EvidenceChunk]:
        """Divide cada cláusula por página; chunks médios com 150 chars de overlap por padrão."""
        chunks: list[EvidenceChunk] = []
        for clause in clauses:
            for source_page in clause.source_pages:
                page_chunks = _split_text(
                    source_page.text,
                    chunk_size_chars=chunk_size_chars,
                    overlap_chars=chunk_overlap_chars,
                )
                for chunk_index, text in enumerate(page_chunks):
                    chunks.append(
                        EvidenceChunk(
                            chunk_id=(
                                f"{document_id}:{clause.clause_id}:"
                                f"p{source_page.page_number}:c{chunk_index}"
                            ),
                            document_id=document_id,
                            clause_id=clause.clause_id,
                            title=clause.title,
                            category=clause.category.value,
                            page_number=source_page.page_number,
                            chunk_index=chunk_index,
                            text=text,
                        )
                    )
        return chunks

    def _get_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        try:
            if self._client is None:
                import chromadb

                self.settings.chroma_path.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(path=str(self.settings.chroma_path))
            collection = self._client.get_or_create_collection(
                name=self.settings.chroma_collection,
                metadata={
                    "hnsw:space": "cosine",
                    "embedding_model": self.encoder.model_name,
                },
            )
            existing_model = (collection.metadata or {}).get("embedding_model")
            if existing_model and existing_model != self.encoder.model_name:
                raise VectorStoreError(
                    "A coleção Chroma usa outro modelo de embeddings; escolha outra coleção "
                    "ou recrie o índice vetorial."
                )
            self._collection = collection
            return collection
        except VectorStoreError:
            raise
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            raise VectorStoreError("Não foi possível abrir o índice Chroma local.") from error
        except Exception as error:
            if _is_chroma_error(error):
                raise VectorStoreError('Não foi possível abrir o índice Chroma local.') from error
            raise

    def upsert(self, chunks: list[EvidenceChunk], *, source_name: str) -> int:
        """Indexa chunks e poda IDs antigos só após gravar o conjunto atual."""
        if not chunks:
            return 0
        if any(chunk.document_id != chunks[0].document_id for chunk in chunks):
            raise VectorStoreError("Uma operação de indexação deve conter apenas um documento.")
        try:
            embeddings = self._encode_cached([chunk.text for chunk in chunks])
            if len(embeddings) != len(chunks) or not embeddings[0]:
                raise VectorStoreError("O modelo local retornou embeddings em quantidade inválida.")
            dimensions = len(embeddings[0])
            if any(len(vector) != dimensions for vector in embeddings):
                raise VectorStoreError("O modelo local retornou dimensões inconsistentes.")
            collection = self._get_collection()
            collection.upsert(
                ids=[chunk.chunk_id for chunk in chunks],
                embeddings=embeddings,
                documents=[chunk.text for chunk in chunks],
                metadatas=[
                    {
                        "document_id": chunk.document_id,
                        "source_name": source_name,
                        "clause_id": chunk.clause_id,
                        "title": chunk.title,
                        "category": chunk.category,
                        "page_number": chunk.page_number,
                        "chunk_index": chunk.chunk_index,
                        "embedding_model": self.encoder.model_name,
                    }
                    for chunk in chunks
                ],
            )
            expected_ids = {chunk.chunk_id for chunk in chunks}
            existing = collection.get(
                where={"document_id": chunks[0].document_id},
                include=[],
            )
            stale_ids = set(existing.get("ids", [])) - expected_ids
            if stale_ids:
                collection.delete(ids=sorted(stale_ids))
            logger.info(
                "Chunks vetoriais indexados; document_id=%s; chunks=%d; modelo=%s",
                chunks[0].document_id,
                len(chunks),
                self.encoder.model_name,
            )
            return len(chunks)
        except VectorStoreError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            raise VectorStoreError("Não foi possível indexar os chunks no Chroma local.") from error
        except Exception as error:
            if _is_chroma_error(error):
                raise VectorStoreError('Não foi possível indexar os chunks no Chroma local.') from error
            raise

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        document_id: str | None = None,
    ) -> list[RetrievedChunk]:
        """Busca vetorial local; quando informado, filtra por hash da apólice."""
        normalized_query = query.strip()
        if not normalized_query:
            raise VectorStoreError("A pergunta não pode ficar vazia.")
        if top_k is not None and top_k < 1:
            raise VectorStoreError("top_k deve ser um inteiro positivo.")
        try:
            collection = self._get_collection()
            count = collection.count()
            if count == 0:
                return []
            query_embedding = (
                self._encode_cached([normalized_query])
                if self.cache_queries
                else self.encoder.encode([normalized_query])
            )
            if not query_embedding or not query_embedding[0]:
                raise VectorStoreError("O embedding local da pergunta ficou vazio.")
            where = {"document_id": document_id} if document_id else None
            result = collection.query(
                query_embeddings=query_embedding,
                n_results=min(top_k or self.settings.rag_top_k, count),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            documents = _first_result(result.get("documents"))
            metadatas = _first_result(result.get("metadatas"))
            distances = _first_result(result.get("distances"))
            retrieved: list[RetrievedChunk] = []
            for text, metadata, distance in zip(documents, metadatas, distances, strict=True):
                retrieved.append(
                    RetrievedChunk(
                        chunk_id=f"{metadata['document_id']}:{metadata['clause_id']}:"
                        f"p{metadata['page_number']}:c{metadata['chunk_index']}",
                        document_id=metadata["document_id"],
                        source_name=metadata["source_name"],
                        clause_id=metadata["clause_id"],
                        title=metadata["title"],
                        category=metadata["category"],
                        page_number=int(metadata["page_number"]),
                        text=text,
                        distance=float(distance),
                    )
                )
            return retrieved
        except VectorStoreError:
            raise
        except (KeyError, OSError, RuntimeError, ValueError, TypeError) as error:
            raise VectorStoreError("A busca semântica local falhou.") from error
        except Exception as error:
            if _is_chroma_error(error):
                raise VectorStoreError('A busca semântica local falhou.') from error
            raise

    def delete_document(self, document_id: str) -> None:
        """Remove vetores de documento que deixou de produzir chunks indexáveis."""
        try:
            self._get_collection().delete(where={"document_id": document_id})
        except (OSError, RuntimeError, ValueError) as error:
            raise VectorStoreError("Não foi possível remover o índice vetorial do documento.") from error
        except Exception as error:
            if _is_chroma_error(error):
                raise VectorStoreError('Não foi possível remover o índice vetorial do documento.') from error
            raise


def _split_text(text: str, *, chunk_size_chars: int, overlap_chars: int) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size_chars, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        value = normalized[start:end].strip()
        if value:
            chunks.append(value)
        if end >= len(normalized):
            break
        start = max(end - overlap_chars, start + 1)
        while start < len(normalized) and normalized[start].isspace():
            start += 1
    return chunks


def _first_result(values: list[list[Any]] | None) -> list[Any]:
    if not values:
        return []
    return values[0] or []
