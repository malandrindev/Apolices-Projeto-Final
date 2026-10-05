"""Conservative semantic guards for source-grounded policy summaries.

These checks detect material omissions; they do not certify legal completeness.
All context is local and document independent. Failed FOUND stays reviewable.
"""
from __future__ import annotations

import re
import copy
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from src.agents._evidence import amounts, currency
from src.schemas.policy import NOT_FOUND, FieldEvidence, PolicyExtraction

SEMANTIC_COMPLETENESS_VERSION = "material-qualifiers-v2"
MONETARY = frozenset({"limite_maximo_garantia", "sublimites", "retencao_franquia", "premio"})
CRITICAL_SEMANTIC = frozenset({
    "limite_maximo_garantia", "sublimites", "retencao_franquia", "side_a", "side_b",
    "side_c", "custos_defesa", "data_retroativa", "territorialidade", "jurisdicao_lei",
    "exclusoes",
})


def fold(text: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                           if not unicodedata.combining(c)).split())


@dataclass(frozen=True)
class SemanticCheck:
    field: str
    reasons: tuple[str, ...]
    critical: bool

    @property
    def passed(self) -> bool:
        return not self.reasons

    def to_dict(self) -> dict:
        return {"status": "PASS" if self.passed else "SEMANTIC_ESCALATION_REQUIRED",
                "reasons": list(self.reasons), "critical": self.critical,
                "validator_version": SEMANTIC_COMPLETENESS_VERSION}


def validate_semantic_completeness(field: str, evidence: FieldEvidence,
                                   context: Iterable[str] = ()) -> SemanticCheck:
    """Check a located summary against its citation plus relevant local context.

    Context should include field-retrieved clauses and adjacent continuation,
    never another document. Missing/ambiguous source values remain safe states.
    """
    reasons: list[str] = []
    if evidence.valor == NOT_FOUND:
        return SemanticCheck(field, (), field in CRITICAL_SEMANTIC)
    value, quote = fold(evidence.valor), fold(evidence.trecho_origem)
    source = fold("\n".join([evidence.trecho_origem, *context]))

    def missing(code: str, present: str, represented: str | None = None) -> None:
        if re.search(present, source) and not re.search(represented or present, value):
            reasons.append(code)

    labels = {"premio", "premio total", "lmg", "lmi", "franquia", "retencao",
              "sublimite", "sublimites", "limite maximo de garantia", "custos de defesa",
              "territorialidade", "jurisdicao", "exclusoes", "definicoes"}
    if value.strip(" .:-") in labels:
        reasons.append("TOPIC_LABEL_NOT_INFORMATION")
    if field in MONETARY:
        if not amounts(evidence.valor):
            reasons.append("MONETARY_VALUE_NOT_ESTABLISHED")
        elif not currency(evidence.valor) and "%" not in value:
            reasons.append("MONETARY_UNIT_MISSING")
        if re.search(r"por reclamacao|por sinistro|agregado", quote):
            missing("MONETARY_SCOPE_OMITTED", r"por reclamacao|por sinistro|agregado")
        if field == "limite_maximo_garantia" and re.search(r"\bsublimite", quote) and not re.search(
                r"limite maximo|limite agregado|\blmg\b", quote):
            reasons.append("LMG_SUBLIMIT_CONFUSION")
        if field == "retencao_franquia":
            missing("DEDUCTIBLE_RETENTION_TYPE_OMITTED", r"\bfranquia\b|\bretencao\b")

    # Generic qualifiers are checked against the cited clause; unrelated
    # endorsements in broader retrieval do not impose their conditions here.
    complete_source = source
    source = quote
    # An operative restriction must survive the summary, not only its quote.
    if field in {"side_a", "side_b", "side_c", "custos_defesa", "extensoes_cobertura",
                 "consentimento_acordo", "territorialidade", "exclusoes",
                 "base_cobertura", "periodo_estendido_notificacao"}:
        missing("PRIOR_CONSENT_OMITTED", r"(?:previa.{0,45}(?:anuencia|autorizacao|consentimento)|"
                r"(?:anuencia|autorizacao|consentimento).{0,45}previ)",
                r"anuencia|autorizacao|consentimento")
        missing("CONTRACTING_CONDITION_OMITTED",
                r"se contratad|caso contratad|desde que.{0,70}contratad|expressamente.{0,35}especificacao",
                r"se contratad|caso contratad|condicion|especificacao|nao.{0,35}contratacao")
        missing("EXCEPTION_OMITTED", r"\bexceto\b|\bsalvo\b|\bexcecao\b|nao se aplica",
                r"\bexceto\b|\bsalvo\b|\bexcecao\b|nao se aplica|exclui|ressalv")
        missing("DEDUCTIBLE_QUALIFIER_OMITTED", r"observad[ao].{0,35}franquia",
                r"\bfranquia\b")
        missing("ELIGIBILITY_CONDITION_OMITTED", r"hipossuficiencia", r"hipossuficiencia")

    if field in {"side_a", "side_b", "side_c"}:
        missing("COVERED_SUBJECT_OMITTED", r"\bsociedade\b|\btomador\b|\bsegurado\b",
                r"\bsociedade\b|\btomador\b|\bsegurado\b|\badministrador")
        if re.search(r"se contratad|caso contratad|especificacao", source) and re.search(
                r"incondicional|automaticamente|sem restricao", value):
            reasons.append("CONDITIONAL_COVERAGE_GENERALIZED")

    source = complete_source
    if field == "consentimento_acordo":
        missing("CONSENT_DEDUCTIBLE_EXCEPTION_OMITTED",
                r"(?:dispensa|dispensad|nao.{0,25}necessari).{0,180}(?:consentimento|anuencia|franquia)|"
                r"(?:franquia|retencao).{0,180}(?:dispensa|dispensad)",
                r"dispensa|dispensad|exceto|excecao|ate.{0,30}franquia")
    if field == "custos_defesa":
        missing("DEFENSE_EXPENSES_RESTRICTION_OMITTED",
                r"nao inclui|nao compreende|nao abrang|exclu", r"nao inclui|nao compreende|nao abrang|exclu")

    if field == "territorialidade":
        if re.search(r"local indicado|ambito.{0,25}especificacao|definid.{0,25}especificacao", value):
            reasons.append("REMISSION_NOT_OPERATIVE_TERRITORY")
        missing("TERRITORY_OPERATIVE_SCOPE_OMITTED", r"todo o mundo|mundial|qualquer lugar",
                r"todo o mundo|mundial|qualquer lugar")
        missing("TERRITORY_COUNTRY_RESTRICTION_OMITTED", r"estados unidos|eua|canada|ira\b|cuba",
                r"estados unidos|eua|canada|ira\b|cuba|excecoes territoriais")
        if re.search(r"\bforo\b|jurisdicao", quote) and not re.search(r"territor|geografic|mundo", quote):
            reasons.append("TERRITORY_JURISDICTION_CONFUSION")
    if field == "jurisdicao_lei":
        missing("FORUM_COMPONENT_OMITTED", r"\bforo\b", r"\bforo\b|domicilio|tribunal")
        missing("LAW_COMPONENT_OMITTED", r"\blei\b|\bleis\b|legislacao", r"\blei\b|\bleis\b|legislacao")
        missing("FORUM_EXCEPTION_OMITTED", r"hipossuficiencia", r"hipossuficiencia")
    if field == "cancelamento_renovacao":
        missing("RENEWAL_COMPONENT_OMITTED", r"renovacao", r"renovacao")
        missing("CANCELLATION_COMPONENT_OMITTED", r"cancelamento|rescisao", r"cancelamento|rescisao")
        missing("RENEWAL_NOT_AUTOMATIC_OMITTED", r"renovacao.{0,120}nao.{0,30}automatic|nao.{0,30}renovacao automat",
                r"nao.{0,45}automatic|nao automat")
    if field == "exclusoes":
        missing("EXCLUSION_RECOGNITION_ALTERNATIVE_OMITTED", r"reconhec", r"reconhec")
        missing("EXCLUSION_CARVEBACK_OMITTED",
                r"nao se aplica|ressalvad|exceto|salvo", r"nao se aplica|ressalvad|exceto|salvo")
        if re.search(r"somente.{0,80}decisao|apenas.{0,80}decisao", value) and re.search(r"reconhec", source) and "reconhec" not in value:
            reasons.append("EXCLUSION_EXHAUSTIVE_GENERALIZATION")

    if field in {"data_retroativa", "vigencia_inicio", "vigencia_fim"}:
        if not re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", evidence.valor):
            reasons.append("TEMPORAL_VALUE_NOT_ESTABLISHED")
        if field == "data_retroativa" and re.search(r"prazo adicional|complementar|suplementar", quote) and "retroativ" not in quote:
            reasons.append("RETROACTIVITY_ADDITIONAL_PERIOD_CONFUSION")
    if field == "periodo_estendido_notificacao":
        missing("ADDITIONAL_PERIOD_MECHANISM_OMITTED",
                r"nao renovacao|nao renovad|cancelamento|encerramento|prorrogacao",
                r"nao renovacao|nao renovad|cancelamento|encerramento|prorrogacao")
        if re.search(r"especificacao", source) and not re.search(r"especificacao|duracao nao|nao localiz", value):
            reasons.append("ADDITIONAL_PERIOD_DURATION_CONDITION_OMITTED")
    if field == "base_cobertura":
        missing("COVERAGE_TRIGGER_REQUIREMENTS_OMITTED", r"requisitos|cumulativ",
                r"requisitos|cumulativ|condicoes")
    if (field == "definicoes_relevantes"
            and re.search(r"(?:conceito|definicao).{0,60}inclui", quote)
            and re.search(r"nao se incluem?.{0,60}(?:conceito|definicao)", quote)
            and not re.search(r"conflit|ambig|indetermin|nao se incluem", value)):
        reasons.append("CONTRADICTORY_DEFINITION_SCOPE")
    if field == "definicoes_relevantes" and re.search(r"principais termos|prevalec|glossario", value) and not re.search(
            r"\b(?:segurado|reclamacao|perdas|ato danoso|fato gerador)\b", value):
        reasons.append("GLOSSARY_INTRO_NOT_DEFINITIONS")
    if field == "extensoes_cobertura" and re.search(r"\bextensao\b", value) and not re.search(
            r"parcial|inventario|demais|exemplo|nao exaustiv|uma das", value):
        # A single endorsement is not a complete inventory of contract extensions.
        reasons.append("EXTENSIONS_INVENTORY_NOT_ESTABLISHED")

    # Mid-clause citations cannot silently establish a complete legal condition.
    if field in CRITICAL_SEMANTIC | {"base_cobertura", "periodo_estendido_notificacao"}:
        end = quote.rstrip(" \"'”")
        if end.endswith((",", ";", ":")) or re.search(r"\b(?:e|ou|da|do|que|qualquer|desde que|mediante|incluindo)$", end):
            reasons.append("SOURCE_CONTINUATION_UNRESOLVED")
    return SemanticCheck(field, tuple(dict.fromkeys(reasons)), field in CRITICAL_SEMANTIC)

def apply_reviewed_conservative_statuses(report: dict, review: dict) -> dict:
    """Project only independently reviewed FOUND -> AMBIGUOUS downgrades.

    The caller validates the immutable result/review digest before using this
    projection. Values, quotes, pages and raw provider artifacts never change.
    """
    projected = copy.deepcopy(report)
    overrides = review.get("conservative_field_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("Conservative review overrides must be a mapping.")
    for name, override in overrides.items():
        if (name not in PolicyExtraction.model_fields or not isinstance(override, dict)
                or override.get("status") != "AMBIGUOUS"
                or not isinstance(override.get("reason"), str)
                or not override["reason"].strip()
                or projected.get("field_status", {}).get(name) != "FOUND"):
            raise ValueError("Only reasoned FOUND to AMBIGUOUS downgrades are permitted.")
        projected["field_status"][name] = "AMBIGUOUS"
        diagnostics = projected.setdefault("retrieval_diagnostics", {})
        field = diagnostics.setdefault("field_diagnostics", {}).setdefault(name, {})
        field.update(status="AMBIGUOUS", reason=override["reason"])
        diagnostics.setdefault("independent_review_downgrades", {})[name] = {
            "original_status": "FOUND", **override}
    return projected
