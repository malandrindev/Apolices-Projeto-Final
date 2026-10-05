"""Schema mínimo D&O; cada valor factual exige página, trecho e confiança."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

NOT_FOUND = "nao_localizado"


def missing_evidence() -> FieldEvidence:
    return FieldEvidence()


class FieldEvidence(BaseModel):
    """Evidência de um campo, incluindo citação verificável no texto de origem."""

    model_config = ConfigDict(extra="forbid")

    valor: str = NOT_FOUND
    pagina: int | None = Field(default=None, ge=1)
    trecho_origem: str = NOT_FOUND
    confianca: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_evidence(self) -> FieldEvidence:
        if self.valor == NOT_FOUND:
            if self.pagina is not None or self.trecho_origem != NOT_FOUND:
                raise ValueError("Campos não localizados não devem incluir citação.")
            return self
        if self.pagina is None or not self.trecho_origem.strip() or self.confianca <= 0:
            raise ValueError(
                "Campo localizado exige página, trecho de origem e confiança positiva."
            )
        return self


class PolicyExtraction(BaseModel):
    """Informações essenciais de apólice D&O com proveniência por campo."""

    model_config = ConfigDict(extra="forbid")

    seguradora: FieldEvidence = Field(default_factory=missing_evidence)
    numero_apolice: FieldEvidence = Field(default_factory=missing_evidence)
    tomador_segurado: FieldEvidence = Field(default_factory=missing_evidence)
    vigencia_inicio: FieldEvidence = Field(default_factory=missing_evidence)
    vigencia_fim: FieldEvidence = Field(default_factory=missing_evidence)
    moeda: FieldEvidence = Field(default_factory=missing_evidence)
    limite_maximo_garantia: FieldEvidence = Field(default_factory=missing_evidence)
    sublimites: FieldEvidence = Field(default_factory=missing_evidence)
    retencao_franquia: FieldEvidence = Field(default_factory=missing_evidence)
    premio: FieldEvidence = Field(default_factory=missing_evidence)
    side_a: FieldEvidence = Field(default_factory=missing_evidence)
    side_b: FieldEvidence = Field(default_factory=missing_evidence)
    side_c: FieldEvidence = Field(default_factory=missing_evidence)
    custos_defesa: FieldEvidence = Field(default_factory=missing_evidence)
    extensoes_cobertura: FieldEvidence = Field(default_factory=missing_evidence)
    base_cobertura: FieldEvidence = Field(default_factory=missing_evidence)
    data_retroativa: FieldEvidence = Field(default_factory=missing_evidence)
    periodo_estendido_notificacao: FieldEvidence = Field(default_factory=missing_evidence)
    exclusoes: FieldEvidence = Field(default_factory=missing_evidence)
    prazo_aviso_sinistro: FieldEvidence = Field(default_factory=missing_evidence)
    controle_defesa: FieldEvidence = Field(default_factory=missing_evidence)
    consentimento_acordo: FieldEvidence = Field(default_factory=missing_evidence)
    rateio: FieldEvidence = Field(default_factory=missing_evidence)
    jurisdicao_lei: FieldEvidence = Field(default_factory=missing_evidence)
    territorialidade: FieldEvidence = Field(default_factory=missing_evidence)
    cancelamento_renovacao: FieldEvidence = Field(default_factory=missing_evidence)
    definicoes_relevantes: FieldEvidence = Field(default_factory=missing_evidence)
