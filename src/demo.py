"""Validated local demonstration fixtures. No provider, model, SDK or network is used."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

from src.agents.extraction import Phase2Report
from src.agents.ocr import PageText, ProcessedDocument
from src.schemas.comparison import ComparisonCitation, DifferenceClass, PolicyComparison, PolicyDifference
from src.schemas.policy import NOT_FOUND, FieldEvidence, PolicyExtraction
from src.schemas.retrieval import FieldStatus
from src.sources import SourceDocument

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = ROOT / "data" / "demo"
WARNINGS = ("DOCUMENTO DE DEMONSTRAÇÃO", "DADOS FICTÍCIOS", "SEM VALIDADE CONTRATUAL")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _normal(text):
    return " ".join(text.split())


def _asset(directory, name):
    path = directory / name
    if path.resolve().parent != directory.resolve() or Path(name).name != name:
        raise ValueError("Caminho inválido no dataset demo.")
    return path


def load_demo_dataset(directory=None, *, source_directory=None):
    """Check every local asset, original-page mapping and literal ground-truth citation."""
    directory = Path(directory) if directory is not None else DEFAULT_DIRECTORY
    source_directory = Path(source_directory) if source_directory is not None else ROOT / "data/demo_sources"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not manifest.get("demo") or manifest.get("provider_http_requests") != 0:
        raise ValueError("Manifesto de demonstração inválido.")
    for name, digest in manifest["assets"].items():
        if _digest(_asset(directory, name).read_bytes()) != digest:
            raise ValueError("Integridade do dataset demo inválida: " + name)
    sources, reports, processed, ground_truth, page_mapping, by_key = [], {}, {}, {}, {}, {}
    for entry in manifest["documents"]:
        raw = _asset(directory, entry["pdf"]).read_bytes()
        digest = _digest(raw)
        original_raw = _asset(source_directory, entry["source_file"]).read_bytes()
        if _digest(original_raw) != entry["source_sha256"]:
            raise ValueError("Documento-base da demo foi alterado.")
        gt = json.loads(_asset(directory, entry["ground_truth"]).read_text(encoding="utf-8"))
        mapping = json.loads(_asset(directory, entry["page_map"]).read_text(encoding="utf-8"))
        if gt["document_sha256"] != digest or gt["source_name"] != entry["pdf"] or set(gt["fields"]) != set(PolicyExtraction.model_fields):
            raise ValueError("Ground truth não corresponde aos 27 campos do documento.")
        if gt["original_source_sha256"] != entry["source_sha256"] or mapping["original_source_sha256"] != entry["source_sha256"]:
            raise ValueError("Proveniência original inconsistente.")
        with pymupdf.open(stream=raw, filetype="pdf") as demo, pymupdf.open(stream=original_raw, filetype="pdf") as original:
            if len(demo) != entry["pages"] or len(original) != entry["source_pages"]:
                raise ValueError("Contagem de páginas da demo inconsistente.")
            texts = [page.get_text() for page in demo]
            if [item["demo_page"] for item in mapping["pages"]] != list(range(1, len(demo) + 1)):
                raise ValueError("Mapa de páginas incompleto.")
            mapped = {item["demo_page"]: item for item in mapping["pages"]}
            for item in mapping["pages"]:
                text = texts[item["demo_page"] - 1]
                if item["kind"] == "synthetic":
                    if not all(warning in text for warning in WARNINGS):
                        raise ValueError("Página sintética sem avisos obrigatórios.")
                elif item["kind"] == "original_excerpt":
                    page = item["source_page"]
                    if not isinstance(page, int) or not 1 <= page <= len(original):
                        raise ValueError("Página original inválida.")
                    if item["source_file"] != entry["source_file"] or item["source_sha256"] != entry["source_sha256"]:
                        raise ValueError("Origem de excerto inconsistente.")
                    if _digest(text.encode("utf-8")) != item["text_sha256"] or text != original[page - 1].get_text():
                        raise ValueError("Texto jurídico original foi alterado.")
                else:
                    raise ValueError("Tipo de página desconhecido.")
            policy, states = {}, {}
            for name, field in gt["fields"].items():
                status = FieldStatus(field["status"])
                states[name] = status
                citations = [field, *field.get("supporting_evidence", [])]
                for citation in citations:
                    page, quote = citation["source_page"], citation["evidence_excerpt"]
                    if page is None:
                        if quote != NOT_FOUND:
                            raise ValueError("Citação sem página localizada.")
                        continue
                    if citation["source_document"] != entry["pdf"] or not 1 <= page <= len(texts):
                        raise ValueError("Citação atribuída a documento/página incorretos.")
                    if _normal(quote) not in _normal(texts[page - 1]):
                        raise ValueError("Trecho demo não é literal.")
                    origin = citation["origin"]
                    expected_type = "real_wording" if origin == "original_wording" else "synthetic_specification"
                    if citation.get("source_type") != expected_type:
                        raise ValueError("Origem real/sintética da citação não declarada.")
                    item = mapped[page]
                    if origin == "original_wording":
                        if (item["kind"] != "original_excerpt" or citation["original_source_document"] != entry["source_file"]
                                or citation["original_source_page"] != item["source_page"]):
                            raise ValueError("Mapeamento original da citação inválido.")
                    elif origin != "synthetic_specification" or item["kind"] != "synthetic":
                        raise ValueError("Citação sintética confundida com wording real.")
                value = field["expected_value"]
                policy[name] = FieldEvidence() if value == NOT_FOUND else FieldEvidence(
                    valor=value, pagina=field["source_page"], trecho_origem=field["evidence_excerpt"],
                    confianca=1.0 if field["source_type"] == "synthetic_specification" else .9)
            source = SourceDocument(filename=entry["pdf"], data=raw, sha256=digest, origin="demo",
                                    metadata={**gt["metadata"], "demo": True, "synthetic_specification": True})
            sources.append(source)
            by_key[entry["key"]] = digest
            ground_truth[digest] = gt
            page_mapping[digest] = {str(item["demo_page"]): item for item in mapping["pages"]}
            processed[digest] = ProcessedDocument(source_name=source.filename, sha256=digest, size_bytes=len(raw),
                media_type="application/pdf", pages=[PageText(page_number=i, text=text, extraction_method="native")
                    for i, text in enumerate(texts, 1)], processed_at="2026-10-04T00:00:00Z",
                cache_key="demo:" + digest, cache_hit=True)
            reports[digest] = Phase2Report(source_name=source.filename, sha256=digest, clause_count=len(gt["fields"]),
                policy=PolicyExtraction(**policy), field_status=states,
                retrieval_diagnostics={"strategy": "human_authored_demo_ground_truth", "demo": True,
                    "provider_http_requests": 0, "dataset_version": manifest["dataset_version"],
                    "ground_truth": gt["fields"], "page_mapping": mapping["pages"]})
    if len(sources) != 2 or manifest["default_reference"] not in by_key:
        raise ValueError("A demo requer Porto e Allianz, com referência válida.")
    comparison_truth = json.loads((directory / "ground_truth_comparison.json").read_text(encoding="utf-8"))
    if (not comparison_truth.get("no_global_ranking") or len(comparison_truth["fields"]) != 27
            or {row["field_name"] for row in comparison_truth["fields"]} != set(PolicyExtraction.model_fields)):
        raise ValueError("Comparação demo incompleta ou com ranking global.")
    if {comparison_truth["document_id_a"], comparison_truth["document_id_b"]} != set(reports):
        raise ValueError("Comparação demo atribuída aos documentos incorretos.")
    for row in comparison_truth["fields"]:
        name = row["field_name"]
        for side, document_id in (("a", comparison_truth["document_id_a"]), ("b", comparison_truth["document_id_b"])):
            field = ground_truth[document_id]["fields"][name]
            citation = row["source_evidence_" + side]
            if row["value_" + side] != field["expected_value"] or any(citation[k] != field[k]
                    for k in ("source_document", "source_page", "evidence_excerpt", "origin", "source_type")):
                raise ValueError("Comparação altera valor/evidência do ground truth.")
        classification = DifferenceClass(row["classification"])
        if classification in {DifferenceClass.MORE_FAVORABLE_A, DifferenceClass.MORE_FAVORABLE_B}:
            if name not in {"limite_maximo_garantia", "premio", "retencao_franquia"} or row["information_insufficient"]:
                raise ValueError("Direção contratual não sustentada na demo.")
            if any(reports[digest].field_status[name] != FieldStatus.FOUND for digest in reports):
                raise ValueError("Vantagem atribuída a estado inconclusivo.")
    return {"sources": sources, "reports": reports, "processed": processed, "ground_truth": ground_truth,
            "page_mapping": page_mapping, "reference_sha": by_key[manifest["default_reference"]],
            "comparison_truth": comparison_truth, "manifest": manifest, "runs": [], "demo": True}


def demo_comparisons(reference_sha, dataset=None):
    """Reverse the same explicit fixture if the user changes reference; no new inference."""
    loaded = dataset if dataset is not None else load_demo_dataset()
    truth = loaded["comparison_truth"]
    if reference_sha not in loaded["reports"]:
        raise ValueError("Referência fora do exemplo.")
    reverse = reference_sha == truth["document_id_b"]
    rows = []
    for row in truth["fields"]:
        classification = DifferenceClass(row["classification"])
        if reverse:
            classification = {DifferenceClass.MORE_FAVORABLE_A: DifferenceClass.MORE_FAVORABLE_B,
                              DifferenceClass.MORE_FAVORABLE_B: DifferenceClass.MORE_FAVORABLE_A}.get(classification, classification)
        sides = ("b", "a") if reverse else ("a", "b")
        citations = [row["source_evidence_" + side] for side in sides]
        rows.append(PolicyDifference(field_name=row["field_name"], label=row["label"],
            value_a=row["value_" + sides[0]], value_b=row["value_" + sides[1]],
            classification=classification, justification=row["business_explanation"],
            citation_a=ComparisonCitation(source_name=citations[0]["source_document"],
                page_number=citations[0]["source_page"], excerpt=citations[0]["evidence_excerpt"]),
            citation_b=ComparisonCitation(source_name=citations[1]["source_document"],
                page_number=citations[1]["source_page"], excerpt=citations[1]["evidence_excerpt"])))
    return [PolicyComparison(source_a=truth["source_b"] if reverse else truth["source_a"],
        document_id_a=truth["document_id_b"] if reverse else truth["document_id_a"],
        source_b=truth["source_a"] if reverse else truth["source_b"],
        document_id_b=truth["document_id_a"] if reverse else truth["document_id_b"],
        compared_at=truth["compared_at"], differences=rows, executive_summary=truth["executive_summary"],
        risk_highlights=truth["risk_highlights"])]
