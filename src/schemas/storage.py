"""Contratos normalizados para persistência relacional e busca vetorial."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvidenceChunk(BaseModel):
    """Subchunk de uma cláusula, limitado a uma página para citações precisas."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    clause_id: str = Field(min_length=1)
    title: str
    category: str
    page_number: int = Field(ge=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)


class RetrievedChunk(BaseModel):
    """Trecho recuperado com metadados para citação visível na resposta RAG."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    document_id: str
    source_name: str
    clause_id: str
    title: str
    category: str
    page_number: int = Field(ge=1)
    text: str
    distance: float = Field(ge=0)
