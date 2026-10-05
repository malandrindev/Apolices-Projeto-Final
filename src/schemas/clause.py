"""Schemas de páginas de origem e cláusulas segmentadas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ClauseCategory(StrEnum):
    OBJECT = "objeto"
    DEFINITIONS = "definicoes"
    COVERAGE = "coberturas"
    LIMITS = "limites"
    EXCLUSIONS = "exclusoes"
    POLICY_PERIOD = "vigencia"
    CLAIMS = "sinistro"
    TERRITORY = "territorialidade"
    CANCELLATION = "cancelamento_renovacao"
    OTHER = "outras"
    UNKNOWN = "nao_classificada"


class SourcePage(BaseModel):
    """Página original preservada para conferir citações de extração."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    text: str


class ClauseChunk(BaseModel):
    """Trecho de uma seção, com identificação de origem e páginas envolvidas."""

    model_config = ConfigDict(extra="forbid")

    clause_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: ClauseCategory
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    text: str = Field(min_length=1)
    source_pages: list[SourcePage] = Field(min_length=1)


class ClauseClassification(BaseModel):
    """Classificação retornada pelo modelo rápido para títulos ambíguos."""

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    category: ClauseCategory


class ClauseClassificationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clauses: list[ClauseClassification]
