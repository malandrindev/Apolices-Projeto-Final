"""Compact multi-field responses; the original 27-field policy stays unchanged."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from src.schemas.policy import NOT_FOUND, FieldEvidence

PolicyFieldName = Literal[
    "seguradora", "numero_apolice", "tomador_segurado", "vigencia_inicio",
    "vigencia_fim", "moeda", "limite_maximo_garantia", "sublimites",
    "retencao_franquia", "premio", "side_a", "side_b", "side_c",
    "custos_defesa", "extensoes_cobertura", "base_cobertura", "data_retroativa",
    "periodo_estendido_notificacao", "exclusoes", "prazo_aviso_sinistro",
    "controle_defesa", "consentimento_acordo", "rateio", "jurisdicao_lei",
    "territorialidade", "cancelamento_renovacao", "definicoes_relevantes",
]


class FieldStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    NOT_RETRIEVED = "NOT_RETRIEVED"
    AMBIGUOUS = "AMBIGUOUS"
    TECHNICAL_UNAVAILABLE = "TECHNICAL_UNAVAILABLE"


class GroupFieldExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_name: PolicyFieldName
    evidence: FieldEvidence = Field(default_factory=FieldEvidence)
    status: FieldStatus

    @model_validator(mode="after")
    def consistent_evidence(self):
        if self.status == FieldStatus.TECHNICAL_UNAVAILABLE:
            raise ValueError("Technical availability is a local execution state, not a model decision.")
        located = self.evidence.valor != NOT_FOUND
        if self.status == FieldStatus.FOUND and not located:
            raise ValueError("FOUND requires a cited value.")
        if self.status in {FieldStatus.NOT_FOUND, FieldStatus.NOT_RETRIEVED} and located:
            raise ValueError("Missing states cannot contain a located value.")
        return self


class GroupExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[GroupFieldExtraction]

    @classmethod
    def model_json_schema(cls, **kwargs):
        # Keep the E1 response/cache contract: the fifth state is assigned locally.
        schema = super().model_json_schema(**kwargs)
        schema["$defs"]["FieldStatus"]["enum"] = [
            "FOUND", "NOT_FOUND", "NOT_RETRIEVED", "AMBIGUOUS",
        ]
        return schema
