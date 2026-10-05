"""Canonical extraction groups with stable legacy and routed cache contracts.

The routed groups partition the same fields more narrowly. A legacy group can
resolve to several routed groups; aliases must never silently drop its fields.
"""
from __future__ import annotations

FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "IDENTIFICATION": ("seguradora", "numero_apolice", "tomador_segurado", "vigencia_inicio", "vigencia_fim", "moeda", "premio"),
    "LIMITS": ("limite_maximo_garantia", "sublimites", "retencao_franquia"),
    "CORE_COVERAGES": ("side_a", "side_b", "side_c", "custos_defesa", "controle_defesa", "consentimento_acordo", "rateio"),
    "TEMPORAL": ("base_cobertura", "data_retroativa", "periodo_estendido_notificacao", "prazo_aviso_sinistro"),
    "SCOPE": ("jurisdicao_lei", "territorialidade"),
    "EXCLUSIONS": ("exclusoes",),
    "EXTENSIONS_DEFINITIONS": ("extensoes_cobertura", "cancelamento_renovacao", "definicoes_relevantes"),
}

SEMANTIC_FIELD_GROUPS = {
    "LIMITS_SUBLIMITS": ("limite_maximo_garantia", "sublimites"),
    "RETENTIONS_DEDUCTIBLES": ("retencao_franquia",),
    "SIDE_COVERAGES": ("side_a", "side_b", "side_c"),
    "DEFENSE_COSTS": ("custos_defesa", "controle_defesa"),
    "TERRITORY": ("territorialidade",),
    "JURISDICTION": ("jurisdicao_lei",),
    "EXCLUSIONS": ("exclusoes",),
    "TEMPORAL": ("base_cobertura", "data_retroativa", "periodo_estendido_notificacao",
                 "prazo_aviso_sinistro"),
    "IDENTIFICATION": ("seguradora", "numero_apolice", "tomador_segurado",
                       "vigencia_inicio", "vigencia_fim", "moeda", "premio"),
    "CONSENT": ("consentimento_acordo",),
    "ALLOCATION": ("rateio",),
    "EXTENSIONS": ("extensoes_cobertura",),
    "CANCELLATION_RENEWAL": ("cancelamento_renovacao",),
    "DEFINITIONS": ("definicoes_relevantes",),
}

# Accept the shorter coverage label without dropping core-coverage fields.
GROUP_ID_ALIASES = {"COVERAGES": "CORE_COVERAGES"}


def field_groups(*, routed: bool) -> dict[str, tuple[str, ...]]:
    """Return the stable registry for the selected extraction contract."""
    return SEMANTIC_FIELD_GROUPS if routed else FIELD_GROUPS


def resolve_group_ids(group_id: str, *, routed: bool) -> tuple[str, ...]:
    """Resolve aliases and splits by their fields, retaining destination order.

    Identifiers already in the requested contract remain unchanged. Cross-contract
    translation can produce several identifiers, e.g. LIMITS includes retention.
    Unknown identifiers fail explicitly rather than selecting unrelated fields.
    """
    canonical = GROUP_ID_ALIASES.get(group_id, group_id)
    destination = field_groups(routed=routed)
    if canonical in destination:
        return (canonical,)
    source = field_groups(routed=not routed)
    if canonical not in source:
        raise ValueError("Unknown extraction field group.")
    requested = set(source[canonical])
    return tuple(name for name, fields in destination.items()
                 if requested.intersection(fields))


LEGACY_TO_ROUTED_GROUPS = {
    name: resolve_group_ids(name, routed=True) for name in FIELD_GROUPS
}
ROUTED_TO_LEGACY_GROUPS = {
    name: resolve_group_ids(name, routed=False) for name in SEMANTIC_FIELD_GROUPS
}
