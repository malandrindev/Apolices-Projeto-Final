"""Contratos de comparação entre duas apólices e suas citações."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.policy import NOT_FOUND


class DifferenceClass(StrEnum):
    EQUAL = "igual"
    MORE_FAVORABLE_A = "mais_favoravel_A"
    MORE_FAVORABLE_B = "mais_favoravel_B"
    NOT_COMPARABLE = "diferente_nao_comparavel"
    MISSING_ON_ONE = "ausente_em_um"


class ComparisonCitation(BaseModel):
    """Origem documental do campo; ausência explícita não inventa uma página."""

    model_config = ConfigDict(extra="forbid")

    source_name: str
    page_number: int | None = Field(default=None, ge=1)
    excerpt: str = NOT_FOUND


class PolicyDifference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    label: str
    value_a: str
    value_b: str
    classification: DifferenceClass
    justification: str
    citation_a: ComparisonCitation
    citation_b: ComparisonCitation


class SemanticFieldPair(BaseModel):
    """Trechos mínimos enviados ao modelo para comparação semântica de um campo."""

    model_config = ConfigDict(extra="forbid")

    field_name: str
    label: str
    value_a: str
    value_b: str
    excerpt_a: str
    excerpt_b: str
    page_a: int
    page_b: int


class SemanticDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    classification: DifferenceClass
    justification: str = Field(min_length=1, max_length=600)


class SemanticDecisionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparisons: list[SemanticDecision]


class PolicyComparison(BaseModel):
    """Resultado completo para duas fontes e diferenças auditáveis."""

    model_config = ConfigDict(extra="forbid")

    source_a: str
    document_id_a: str
    source_b: str
    document_id_b: str
    compared_at: str
    differences: list[PolicyDifference]
    executive_summary: str = Field(min_length=1)
    risk_highlights: list[str] = Field(default_factory=list)
    source_references: dict[str, str] = Field(default_factory=dict)
