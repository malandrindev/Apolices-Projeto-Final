"""Contratos da resposta RAG e de suas citações rastreáveis."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.policy import NOT_FOUND


class RagModelAnswer(BaseModel):
    """Forma JSON esperada da resposta do LLM antes de anexar fontes locais."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    cited_chunk_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_citations_or_abstention(self) -> RagModelAnswer:
        if not self.cited_chunk_ids and self.answer.strip() != NOT_FOUND:
            raise ValueError("A resposta precisa citar trechos ou retornar nao_localizado.")
        if len(self.cited_chunk_ids) != len(set(self.cited_chunk_ids)):
            raise ValueError("A lista de IDs de citação não pode conter duplicatas.")
        return self


class RagCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_name: str
    page_number: int = Field(ge=1)
    title: str


class RagResponse(BaseModel):
    """Resposta da consulta com fontes derivadas dos metadados recuperados."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    citations: list[RagCitation] = Field(default_factory=list)
    retrieved_count: int = Field(ge=0)
