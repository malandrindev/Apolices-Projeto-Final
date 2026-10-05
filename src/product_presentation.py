"""Business-facing projections; stored facts and decisions remain untouched."""
from __future__ import annotations
import re
import unicodedata
from collections import Counter
from src.schemas.policy import NOT_FOUND

RESULT_TABS = ("Resumo", "Coberturas", "Limites & Franquias", "Exclusões", "Cláusulas", "Evidências")
PRIORITY_FIELDS = ("limite_maximo_garantia", "premio", "retencao_franquia", "custos_defesa",
                   "data_retroativa", "territorialidade", "periodo_estendido_notificacao",
                   "exclusoes", "side_a", "side_b", "side_c", "extensoes_cobertura")
UNCERTAIN = {"NOT_RETRIEVED", "AMBIGUOUS", "TECHNICAL_UNAVAILABLE", "NOT_FOUND"}
IDENTITY_FIELDS = {"seguradora", "numero_apolice", "tomador_segurado", "moeda",
                   "vigencia_inicio", "vigencia_fim"}
BUSINESS_RELEVANCE = {
    "limite_maximo_garantia": "Indica o teto financeiro, sujeito aos sublimites e à base de aplicação.",
    "premio": "Muda o custo da proposta; o preço sozinho não determina sua adequação.",
    "retencao_franquia": "Muda a parcela suportada pelo segurado quando a retenção é aplicável.",
    "custos_defesa": "Pode alterar o pagamento da defesa e o consumo do limite disponível.",
    "data_retroativa": "Delimita a data dos atos que podem ser considerados, junto às demais condições.",
    "territorialidade": "Delimita onde atos ou reclamações podem estar abrangidos, sujeito à jurisdição.",
    "periodo_estendido_notificacao": "Pode mudar o tempo para notificar reclamações após o término, conforme os requisitos.",
    "exclusoes": "Delimita situações excluídas e suas exceções; confira o alcance completo.",
    "side_a": "Trata da proteção da pessoa segurada quando a sociedade não a indeniza.",
    "side_b": "Trata do reembolso à sociedade quando ela indeniza a pessoa segurada.",
    "side_c": "A cobertura da própria sociedade depende do escopo expressamente previsto.",
}

def _fold(value):
    return "".join(c for c in unicodedata.normalize("NFKD", str(value)) if not unicodedata.combining(c)).lower()

def document_title(source, metadata=None):
    """Short names are presentation aliases, never replacements for provenance."""
    insurer = getattr(metadata, "insurer", None) if metadata is not None else None
    insurer = insurer or source.metadata.get("insurer") or ""
    candidate = _fold(insurer)
    for token, title in (("porto", "PORTO SEGURO"), ("allianz", "ALLIANZ"),
                         ("axa", "AXA SEGUROS"), ("berkley", "BERKLEY")):
        if re.search(r"\b" + token + r"\b", candidate):
            return title
    if insurer:
        return re.sub(r"\s+(?:Ap[oó]lice|Produto|Tomador)\s*:\s*$", "", insurer).strip()
    return source.filename

def document_titles(documents, metadata):
    titles = {source.sha256: document_title(source, metadata.get(source.sha256)) for source in documents}
    counts = Counter(titles.values())
    return {source.sha256: (titles[source.sha256] if counts[titles[source.sha256]] == 1
            else titles[source.sha256] + " · " + source.filename) for source in documents}

def value_text(value, status=None):
    if status == "TECHNICAL_UNAVAILABLE":
        return "Não foi possível analisar"
    if status == "NOT_RETRIEVED":
        return "Não localizado com segurança"
    if status == "AMBIGUOUS":
        return "Requer confirmação" if value == NOT_FOUND else str(value) + " · requer confirmação"
    if value == NOT_FOUND or status == "NOT_FOUND":
        return "Não localizado com segurança"
    return str(value)

def pair_state(row, cell):
    """Count evidence-supported findings; unknown facts never become differences."""
    for value, status, evidence in (
        (row["reference_value"], row.get("reference_status"), row["reference_evidence"]),
        (cell["value"], cell.get("field_status"), cell["evidence"]),
    ):
        if value == NOT_FOUND or status in UNCERTAIN or not evidence.get("page_number") or evidence.get("excerpt") in {None, "", NOT_FOUND}:
            return "insufficient"
    reason = _fold(cell.get("justification", ""))
    if any(phrase in reason for phrase in ("evidencia insuficiente", "evidencias insuficientes",
           "trecho extenso", "trechos extensos", "insufficient evidence", "nao permite concluir")):
        return "insufficient"
    classification = cell.get("classification")
    if classification == "igual":
        return "equivalent"
    if classification in {"mais_favoravel_A", "mais_favoravel_B", "diferente_nao_comparavel"} and row["reference_value"] != cell["value"]:
        return "different"
    return "insufficient"

def comparison_counts(matrix):
    counts = {"differences": 0, "attention": 0, "equivalent": 0, "insufficient": 0}
    for row in matrix:
        if row["field_name"] in IDENTITY_FIELDS:
            continue
        for cell in row["candidates"].values():
            kind = pair_state(row, cell)
            counts[{"different": "differences", "equivalent": "equivalent", "insufficient": "insufficient"}[kind]] += 1
            if kind == "different" and row["field_name"] not in IDENTITY_FIELDS:
                counts["attention"] += 1
    return counts

def important_differences(matrix):
    order = {name: index for index, name in enumerate(PRIORITY_FIELDS)}
    return sorted((row for row in matrix if row["field_name"] not in IDENTITY_FIELDS
                   and any(pair_state(row, cell) == "different" for cell in row["candidates"].values())),
                  key=lambda row: order.get(row["field_name"], len(order)))

def classification_text(row, cell):
    kind = pair_state(row, cell)
    if kind == "insufficient":
        return "Informação insuficiente"
    if kind == "equivalent":
        return "Equivalente neste item"
    if cell["classification"] == "mais_favoravel_A":
        return "Mais favorável à referência neste aspecto"
    if cell["classification"] == "mais_favoravel_B":
        return "Mais favorável ao candidato neste aspecto"
    return "Diferente sem direção segura"

def business_relevance(row):
    return BUSINESS_RELEVANCE.get(row["field_name"],
        "Esta condição pode mudar a aplicação da cobertura; confira definições, limites e exceções.")
