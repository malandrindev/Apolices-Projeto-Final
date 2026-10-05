"""Local projections and reference-versus-candidate orchestration for the workspace.

Metadata and compatibility are cautious presentation hints. They neither alter extracted
facts nor establish legal/regulatory equivalence. Rendering and review use no inference.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.agents._evidence import currency as parse_currency, fold, normalize, parsed_date
from src.agents.comparison import ComparisonAgent, LABELS
from src.agents.extraction import Phase2Report
from src.agents.ocr import ProcessedDocument
from src.schemas.comparison import DifferenceClass, PolicyComparison
from src.schemas.policy import NOT_FOUND, FieldEvidence, PolicyExtraction
from src.schemas.retrieval import FieldStatus

FIELD_LABELS = dict(LABELS)
FIELD_GROUPS = {
    "Coberturas": ("side_a", "side_b", "side_c", "custos_defesa",
                  "extensoes_cobertura", "base_cobertura"),
    "Exclusões": ("exclusoes",),
    "Limites": ("moeda", "limite_maximo_garantia", "sublimites", "retencao_franquia", "premio"),
    "Cláusulas": tuple(name for name in PolicyExtraction.model_fields if name not in {
        "side_a", "side_b", "side_c", "custos_defesa", "extensoes_cobertura",
        "base_cobertura", "exclusoes", "moeda", "limite_maximo_garantia",
        "sublimites", "retencao_franquia", "premio",
    }),
}
_FIELD_GROUP = {name: group for group, names in FIELD_GROUPS.items() for name in names}
REVIEW_STATUSES = ("Não revisado", "Confirmado", "Corrigido", "Requer análise")
CLASSIFICATION_LABELS = {
    DifferenceClass.EQUAL.value: "Sem diferença identificada",
    DifferenceClass.MORE_FAVORABLE_A.value: "Mais favorável à referência neste item",
    DifferenceClass.MORE_FAVORABLE_B.value: "Mais favorável ao candidato neste item",
    DifferenceClass.NOT_COMPARABLE.value: "Sem ordenação segura",
    DifferenceClass.MISSING_ON_ONE.value: "Não localizado em um documento",
    "nao_localizado_em_ambos": "Não localizado em ambos",
    None: "Ainda não comparado",
}
_NO_ADVANTAGE_FIELDS = {"seguradora", "numero_apolice", "tomador_segurado",
                        "moeda", "vigencia_inicio", "vigencia_fim", "data_retroativa"}


@dataclass(frozen=True)
class DocumentMetadata:
    insurer: str | None = None
    product_name: str | None = None
    susep_process: str | None = None
    effective_date: str | None = None
    period: str | None = None
    currency: str | None = None
    branch: str | None = None
    company_type: str | None = None
    document_type: str | None = None
    page_count: int | None = None
    extraction_method: str | None = None
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Compatibility:
    status: str
    reasons: tuple[str, ...]


def _doc_type(value: str) -> str:
    aliases = {"condicoes gerais": "Condições gerais", "condicoes contratuais": "Condições gerais",
               "condicoes_gerais": "Condições gerais", "apolice": "Apólice",
               "apolice emitida": "Apólice", "proposta": "Proposta", "endosso": "Endosso"}
    return aliases.get(fold(value), value.strip())


def _company_type(value: str) -> str | None:
    value = fold(value).replace("_", " ")
    if "capital aberto" in value and "capital fechado" not in value:
        return "Capital aberto"
    if "capital fechado" in value and "capital aberto" not in value:
        return "Capital fechado"
    return None


def _branch(value: str) -> str | None:
    if re.search(r"\bd\s*&\s*o\b|directors.{0,8}officers|diretores e administradores|"
                 r"responsabilidade civil.{0,35}administradores", fold(value)):
        return "D&O"
    return None


def infer_metadata(
    processed: ProcessedDocument | None,
    report: Phase2Report | None = None,
    catalog: Mapping[str, Any] | None = None,
) -> DocumentMetadata:
    """Recognize explicit front matter and preserve editorial/contractual provenance."""
    if processed is not None and report is not None and processed.sha256 != report.sha256:
        raise ValueError("Metadados e extração pertencem a documentos diferentes.")
    values: dict[str, Any] = {}
    evidence: dict[str, dict[str, Any]] = {}
    conflicts: list[str] = []
    front: list[tuple[int, str]] = []
    if processed is not None:
        for page in processed.pages[:3]:
            for line in page.text[:5000].splitlines()[:100]:
                if line.strip():
                    front.append((page.page_number, line.strip()))
        values["page_count"] = len(processed.pages)
        methods = {page.extraction_method for page in processed.pages}
        values["extraction_method"] = next(iter(methods)) if len(methods) == 1 else "mixed"

    candidates: dict[str, list[tuple[str, int, str]]] = {}

    def add(name: str, value: str | None, page: int, excerpt: str) -> None:
        if value and value.strip() and len(value) <= 250:
            candidates.setdefault(name, []).append((value.strip(), page, excerpt))

    dates = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})"
    for page, line in front:
        folded = fold(line)
        for name, label in (
            ("insurer", r"(?:raz[aã]o social da )?seguradora"),
            ("product_name", r"(?:nome do )?produto"),
            ("branch", r"ramo"),
        ):
            match = re.match(r"(?i)^" + label + r"\s*[:\-]\s*(.+)$", line)
            if match:
                value = match.group(1).strip()
                add(name, _branch(value) or value if name == "branch" else value, page, line)
        if len(line) < 200 and re.match(r"(?i)^(?:seguro|seguro de|responsabilidade civil|D&O)\b", line):
            if _branch(line):
                add("product_name", line, page, line)
        if _branch(line):
            add("branch", "D&O", page, line)
        if any(token in folded for token in ("produto", "seguro", "empresas", "tipo de empresa")):
            add("company_type", _company_type(line), page, line)
        if "susep" in folded or "processo" in folded:
            for match in re.finditer(r"\b(\d{5})[./-](\d{6})[/.-](\d{4})[-./](\d{2})\b", line):
                add("susep_process", f"{match[1]}.{match[2]}/{match[3]}-{match[4]}", page, line)
        match = re.search(r"(?i)(?:vers[aã]o(?: vigente)?\s*[:\-]?|vigente a partir de|"
                          r"condi[cç][oõ]es v[aá]lidas a partir de|aplic[aá]vel a partir de)\s*" + dates, line)
        if match and (version_date := parsed_date(match.group(1))):
            add("effective_date", version_date.isoformat(), page, line)
        match = re.match(r"(?i)^vig[eê]ncia\s*[:\-]?\s*(.+)$", line)
        if match and len(re.findall(dates, match[1])) >= 2:
            add("period", match[1].strip(), page, line)
        if re.match(r"(?i)^moeda\s*[:\-]", line):
            add("currency", parse_currency(line), page, line)
        if len(line) <= 120:
            if re.match(r"^(?:\d+[.)]\s*)?condicoes (?:gerais|contratuais)(?:\s|$)", folded):
                add("document_type", "Condições gerais", page, line)
            elif re.match(r"^(?:\d+[.)]\s*)?apolice(?: de seguro| emitida| d&o)?(?:\s*[:#]|$)", folded):
                add("document_type", "Apólice", page, line)
            elif re.match(r"^(?:\d+[.)]\s*)?proposta(?: de seguro)?(?:\s|$)", folded):
                add("document_type", "Proposta", page, line)
            elif re.match(r"^(?:\d+[.)]\s*)?endosso(?:\s|$)", folded):
                add("document_type", "Endosso", page, line)
    for name, possibilities in candidates.items():
        distinct = {normalize(value) for value, _, _ in possibilities}
        if len(distinct) == 1:
            value, page, excerpt = possibilities[0]
            values[name] = value
            evidence[name] = {"origin": "document", "page_number": page, "excerpt": excerpt}
        else:
            conflicts.append(name)
            values[name] = None

    if report is not None:
        verified = {}
        for policy_name, metadata_name in (("seguradora", "insurer"), ("moeda", "currency"),
                                          ("vigencia_inicio", "period_start"),
                                          ("vigencia_fim", "period_end")):
            item = getattr(report.policy, policy_name)
            if item.valor == NOT_FOUND:
                continue
            if processed is not None:
                pages = {page.page_number: page.text for page in processed.pages}
                if normalize(item.trecho_origem) not in normalize(pages.get(item.pagina, "")):
                    conflicts.append(metadata_name)
                    continue
            verified[metadata_name] = item
        for name in ("insurer", "currency"):
            if name in verified:
                item = verified[name]
                value = parse_currency(item.valor) if name == "currency" else item.valor
                if value is not None:
                    if values.get(name) and normalize(values[name]) != normalize(value):
                        conflicts.append(name)
                    values[name] = value
                    evidence[name] = {"origin": "extraction", "page_number": item.pagina,
                                      "excerpt": item.trecho_origem}
        start, end = verified.get("period_start"), verified.get("period_end")
        if start or end:
            values["period"] = (
                f"{start.valor} a {end.valor}" if start and end
                else f"Início: {start.valor}" if start else f"Fim: {end.valor}"
            )
            evidence["period"] = {"origin": "extraction", "sources": [
                {"page_number": item.pagina, "excerpt": item.trecho_origem}
                for item in (start, end) if item is not None
            ]}
    editorial = dict(catalog or {})
    if not editorial.get("company_type") and editorial.get("product_scope"):
        editorial["company_type"] = _company_type(str(editorial["product_scope"]))
    if not editorial.get("branch") and editorial.get("product_name"):
        editorial["branch"] = _branch(str(editorial["product_name"]))
    for name in ("insurer", "product_name", "susep_process", "effective_date",
                 "branch", "company_type", "document_type"):
        value = editorial.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if name == "document_type":
            value = _doc_type(value)
        elif name == "company_type":
            value = _company_type(value) or value
        elif name == "branch":
            value = _branch(value) or value
        if values.get(name) is not None:
            if normalize(values[name]) != normalize(value):
                conflicts.append(name)
            continue
        # Ambiguous documentary information is not silently resolved by a catalog.
        if name in conflicts:
            continue
        values[name] = value
        evidence[name] = {"origin": "catalog", "url": editorial.get("source_url") or editorial.get("url"),
                          "catalog_id": editorial.get("id")}
    return DocumentMetadata(**values, evidence=evidence, conflicts=tuple(sorted(set(conflicts))))


def assess_compatibility(reference: DocumentMetadata, candidate: DocumentMetadata) -> Compatibility:
    """Compare only known attributes; missing evidence never creates a high score."""
    reasons: list[str] = []
    matches, paired = 0, 0
    hard_conflict = False
    soft_conflict = False
    labels = {"branch": "Ramo", "company_type": "Tipo de empresa", "currency": "Moeda",
              "document_type": "Natureza documental", "product_name": "Produto", "period": "Vigência"}
    for name, label in labels.items():
        a, b = getattr(reference, name), getattr(candidate, name)
        if not a or not b:
            continue
        paired += 1
        if normalize(a) == normalize(b):
            matches += 1
            reasons.append(f"{label} identificado coincide.")
        else:
            reasons.append(f"{label} identificado difere; confira a adequação da comparação.")
            if name in {"branch", "company_type", "currency", "document_type"}:
                hard_conflict = True
            else:
                soft_conflict = True
    version_a = parsed_date(reference.effective_date) if reference.effective_date else None
    version_b = parsed_date(candidate.effective_date) if candidate.effective_date else None
    temporal_known = False
    if version_a and version_b:
        paired += 1
        temporal_known = True
        days = abs((version_a - version_b).days)
        if days <= 366:
            matches += 1
            reasons.append("Versões documentais identificadas estão a até um ano de distância.")
        elif days > 3 * 366:
            hard_conflict = True
            reasons.append("Versões documentais identificadas estão a mais de três anos de distância.")
        else:
            soft_conflict = True
            reasons.append("Versões documentais identificadas estão entre um e três anos de distância.")
    elif reference.period and candidate.period:
        temporal_known = normalize(reference.period) == normalize(candidate.period)
    if reference.conflicts or candidate.conflicts:
        soft_conflict = True
        reasons.append("Há metadados conflitantes; confirme a identidade documental.")
    unknown = [label for name, label in labels.items()
               if not getattr(reference, name) or not getattr(candidate, name)]
    if not version_a or not version_b:
        unknown.append("Versão documental")
    if unknown:
        reasons.append("Dados não identificados em um ou ambos: " + ", ".join(unknown) + ".")
    if hard_conflict:
        status = "Baixa"
    elif paired >= 5 and matches >= 5 and temporal_known and not soft_conflict:
        status = "Alta"
    elif paired >= 3 and matches >= 2:
        status = "Média"
    else:
        status = "Não determinada"
        reasons.insert(0, "Compatibilidade ainda não determinada: poucos sinais conhecidos.")
    return Compatibility(status, tuple(reasons))


def review_key(reference_sha: str, candidate_sha: str, field_name: str,
               evidence_hash: str | None = None) -> str:
    key = f"{reference_sha}:{candidate_sha}:{field_name}"
    return f"{key}:{evidence_hash}" if evidence_hash else key


def _review(reviews: Mapping[str, Any] | None, key: str) -> tuple[str, str]:
    value = (reviews or {}).get(key)
    if isinstance(value, str):
        return (value if value in REVIEW_STATUSES else REVIEW_STATUSES[0]), ""
    if isinstance(value, Mapping):
        status = value.get("status", value.get("review_status"))
        note = value.get("note", value.get("comment", ""))
        return (status if status in REVIEW_STATUSES else REVIEW_STATUSES[0]), str(note or "")
    return REVIEW_STATUSES[0], ""


def _evidence(report: Phase2Report, item: FieldEvidence, status: FieldStatus | None = None) -> dict[str, Any]:
    return {"source_name": report.source_name, "page_number": item.pagina,
            "excerpt": item.trecho_origem, "confidence": item.confianca,
            "field_status": status.value if status else None}


def build_matrix(
    reference: Phase2Report, candidates: Sequence[Phase2Report],
    comparisons: Sequence[PolicyComparison], reviews: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    results = {item.document_id_b: item for item in comparisons
               if item.document_id_a == reference.sha256}
    rows = []
    for name in PolicyExtraction.model_fields:
        reference_field = getattr(reference.policy, name)
        reference_status = reference.field_status.get(name)
        row = {"field_name": name, "label": FIELD_LABELS[name], "group": _FIELD_GROUP[name],
               "reference_value": reference_field.valor,
               "reference_status": reference_status.value if reference_status else None,
               "reference_evidence": _evidence(reference, reference_field, reference_status), "candidates": {}}
        for candidate in candidates:
            candidate_field = getattr(candidate.policy, name)
            candidate_status = candidate.field_status.get(name)
            result = results.get(candidate.sha256)
            difference = next((item for item in result.differences if item.field_name == name), None) if result else None
            both_missing = reference_field.valor == candidate_field.valor == NOT_FOUND
            uncertain = {FieldStatus.NOT_RETRIEVED, FieldStatus.AMBIGUOUS, FieldStatus.TECHNICAL_UNAVAILABLE}
            if FieldStatus.TECHNICAL_UNAVAILABLE in {reference_status, candidate_status}:
                classification = DifferenceClass.NOT_COMPARABLE.value
                justification = "Processamento tecnicamente indisponivel neste campo; evidencias anteriores foram preservadas para revisao, sem concluir equivalencia ou vantagem."
            elif reference_status in uncertain or candidate_status in uncertain:
                classification = DifferenceClass.NOT_COMPARABLE.value
                justification = "Busca inconclusiva ou evidência ambígua neste campo. Revise as fontes; este resultado não demonstra ausência de cobertura nem permite ordenar as condições."
            elif difference:
                classification, justification = difference.classification.value, difference.justification
            elif both_missing:
                classification = "nao_localizado_em_ambos"
                justification = "Informação não localizada em ambas as fontes; isso não prova ausência de cobertura."
            elif result is not None and reference_field.valor != NOT_FOUND and candidate_field.valor != NOT_FOUND:
                classification = DifferenceClass.EQUAL.value
                justification = "Não foi registrada diferença neste campo pelo comparador."
            else:
                classification = None
                justification = "Este campo ainda não possui uma comparação concluída."
            key = review_key(reference.sha256, candidate.sha256, name)
            status, note = _review(reviews, key)
            row["candidates"][candidate.sha256] = {
                "source_name": candidate.source_name, "value": candidate_field.valor,
                "field_status": candidate_status.value if candidate_status else None,
                "classification": classification, "classification_label": CLASSIFICATION_LABELS[classification],
                "justification": justification, "evidence": _evidence(candidate, candidate_field, candidate_status),
                "review_key": key, "review_status": status, "review_note": note,
            }
        rows.append(row)
    return rows


def executive_metrics(comparison: PolicyComparison, reviews: Mapping[str, Any] | None = None) -> dict[str, int]:
    metrics = {"differences": len(comparison.differences), "favorable_candidate": 0,
               "restrictive_candidate": 0, "non_comparable": 0, "review_needed": 0, "reviewed": 0}
    for item in comparison.differences:
        if item.field_name not in _NO_ADVANTAGE_FIELDS:
            metrics["favorable_candidate"] += item.classification == DifferenceClass.MORE_FAVORABLE_B
            metrics["restrictive_candidate"] += item.classification == DifferenceClass.MORE_FAVORABLE_A
        metrics["non_comparable"] += item.classification in {DifferenceClass.NOT_COMPARABLE, DifferenceClass.MISSING_ON_ONE}
        status, _ = _review(reviews, review_key(comparison.document_id_a, comparison.document_id_b, item.field_name))
        metrics["review_needed"] += status in {"Não revisado", "Requer análise"}
        metrics["reviewed"] += status in {"Confirmado", "Corrigido"}
    return metrics


def compare_reference(
    reference: Phase2Report, candidates: Sequence[Phase2Report], gateway: Any,
    model_strong: str, processed_dir: Path | None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[PolicyComparison]:
    """Run ordered existing pairwise comparisons; no document re-extraction."""
    identities = [candidate.sha256 for candidate in candidates]
    if reference.sha256 in identities or len(set(identities)) != len(identities):
        raise ValueError("Referência e candidatos precisam ter documentos distintos.")
    agent = ComparisonAgent(gateway=gateway, model_strong=model_strong, processed_dir=processed_dir)
    results = []
    for completed, candidate in enumerate(candidates, 1):
        results.append(agent.compare(reference, candidate))
        if on_progress:
            on_progress(completed, len(candidates), candidate.sha256)
    return results


def load_validated_session(directory: Path) -> dict[str, Any]:
    """Read the fixed local fast-track package only after all three review gates PASS.

    This reader constructs no SDK, downloads no sources and never modifies pilot records.
    Every viewable report is bound to the reviewed result and source digest.
    """
    from src.sources import SourceDocument
    base = Path(directory).resolve()

    def local_path(relative: Any) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("Sessão validada contém caminho local inválido.")
        path = (base / relative).resolve()
        if path == base or base not in path.parents:
            raise ValueError("Sessão validada contém caminho fora do diretório permitido.")
        return path

    def read_json(relative: Any) -> dict[str, Any]:
        value = json.loads(local_path(relative).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Registro de validação inválido.")
        return value

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    index = read_json("validated_ui_session.json")
    if index.get("schema_version") != 1 or index.get("quality_status") != "PASS":
        raise ValueError("Sessão ainda não aprovada para validação manual.")
    stage_records = index.get("stages", {})
    reviewed = {}
    reviews = {}
    for stage in ("berkley", "axa", "comparison"):
        record = stage_records.get(stage, {})
        run_id = record.get("run_id", "")
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Identificação do run validado inválida.")
        result_path = f"runs/{run_id}/result.json"
        result = read_json(result_path)
        review = read_json(f"runs/{run_id}/review.json")
        if (review.get("status") != "PASS" or review.get("stage") != stage
                or review.get("run_id") != run_id
                or review.get("result_sha256") != digest(local_path(result_path))
                or result.get("run_id") != run_id or result.get("stage") != stage):
            raise ValueError("Resultado não corresponde à revisão aprovada.")
        reviewed[stage] = result
        reviews[stage] = review

    sources, reports, retrieval, runs = [], {}, {}, []
    for item in index.get("documents", []):
        stage = item.get("stage")
        if stage not in {"berkley", "axa"} or stage not in reviewed:
            raise ValueError("Documento sem gate aprovado.")
        result = reviewed[stage]
        data = local_path(item.get("pdf_path")).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha != item.get("sha256") or sha != result.get("document_sha256"):
            raise ValueError("Hash do documento difere da fonte validada.")
        filename = item.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("Nome da fonte validada inválido.")
        report = Phase2Report.model_validate(read_json(item.get("report_path")))
        if (report.sha256 != sha or report.source_name != filename
                or report.model_dump(mode="json") != result.get("report")
                or set(report.field_status) != set(PolicyExtraction.model_fields)):
            raise ValueError("Report não corresponde aos 27 estados e resultado validados.")
        if reviews[stage].get("conservative_field_overrides"):
            from src.agents.semantic_completeness import apply_reviewed_conservative_statuses
            projected_path = f"runs/{stage_records[stage]['run_id']}/reviewed_report.json"
            projected = apply_reviewed_conservative_statuses(report.model_dump(mode="json"), reviews[stage])
            if (reviews[stage].get("reviewed_report_sha256") != digest(local_path(projected_path))
                    or read_json(projected_path) != projected):
                raise ValueError("Projeção conservadora não corresponde à revisão aprovada.")
            report = Phase2Report.model_validate(projected)
        from src.agents.report import ComparisonReportAgent
        supplied = ComparisonReportAgent.validate_source_urls({filename: item.get("source_url", "")})
        sources.append(SourceDocument(filename=filename, data=data, sha256=sha,
            origin="public_url", source_url=supplied.get(filename, ""),
            access_date=item.get("access_date", ""), cache_hit=True,
            metadata={"validated_session": True}))
        reports[sha] = report
        retrieval[sha] = report.retrieval_diagnostics
    if len(sources) != 2 or len(reports) != 2:
        raise ValueError("Sessão validada deve conter duas fontes distintas.")
    reference = index.get("reference_sha")
    if reference not in reports:
        raise ValueError("Referência da sessão não corresponde às fontes.")
    comparisons = []
    for item in index.get("comparisons", []):
        comparison = PolicyComparison.model_validate(read_json(item.get("path")))
        if (comparison.model_dump(mode="json") != reviewed["comparison"].get("comparison")
                or comparison.document_id_a != reference
                or comparison.document_id_b not in reports
                or comparison.document_id_a == comparison.document_id_b):
            raise ValueError("Comparação não corresponde ao resultado aprovado.")
        comparisons.append(comparison)
    if len(comparisons) != 1:
        raise ValueError("Sessão validada deve possuir uma comparação aprovada.")
    for item in index.get("runs", []):
        if item.get("stage") not in reviewed or item.get("run_id") != reviewed[item["stage"]]["run_id"]:
            raise ValueError("Telemetria não corresponde ao run aprovado.")
        telemetry = read_json(item.get("telemetry_path"))
        if telemetry.get("run_id") != item["run_id"]:
            raise ValueError("Identidade da telemetria inválida.")
        runs.append({"run_id": item["run_id"], "label": item.get("label", item["stage"]),
                     "stage": item["stage"], "events": telemetry.get("events", []),
                     "metrics": reviewed[item["stage"]].get("metrics", {}),
                     "retrieval": retrieval.get(reviewed[item["stage"]].get("document_sha256"), {})})
    return {"documents": sources, "reports": reports, "comparisons": comparisons,
            "reference_sha": reference, "runs": runs,
            "processing_tier_evidence": index.get("processing_tier_evidence", {}),
            "quality_status": "PASS"}
