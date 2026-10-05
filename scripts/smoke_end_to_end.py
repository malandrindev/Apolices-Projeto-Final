"""Smoke com dois documentos sintéticos; OCR real e provedor simulado por padrão.

Execute: python scripts/smoke_end_to_end.py
Somente --live permite chamadas ao provedor configurado. O modo offline não comprova IA
Generativa real; ele comprova integração, OCR, evidências, persistência e relatórios.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pymupdf

from src.agents._evidence import amounts, normalize
from src.agents.comparison import ComparisonAgent
from src.agents.report import ComparisonReportAgent
from src.config import Settings, get_ingestion_settings, get_settings
from src.llm.providers import get_gateway
from src.pipeline import process_and_structure_document
from src.schemas.comparison import DifferenceClass, PolicyComparison
from src.schemas.policy import NOT_FOUND, FieldEvidence, PolicyExtraction
from src.storage.sqlite_repo import SqliteRepository

MAX_LOGICAL_CALLS = 6
MAX_HTTP_ATTEMPTS = 18
SYNTHETIC_A_TEXT = (
    "1. COBERTURAS\n"
    "Documento SINTETICO A - somente demonstracao educacional.\n"
    "Seguradora: Companhia Exemplo A\n"
    "Moeda: BRL\n"
    "Limite: R$ 10.000.000,00\n"
    "Franquia: R$ 100.000,00\n"
    "Vigencia inicio: 01/01/2026\n"
    "Side A: Inclui custos de defesa.\n"
)
SYNTHETIC_B_TEXT = (
    "1. COBERTURAS\n"
    "Documento SINTETICO B - somente demonstracao educacional.\n"
    "Seguradora: Companhia Exemplo B\n"
    "Moeda: BRL\n"
    "Limite: R$ 8.000.000,00\n"
    "Franquia: R$ 200.000,00\n"
    "Vigencia inicio: 01/02/2026\n"
    "Side A: Exclui custos de defesa.\n"
)


class SmokeError(RuntimeError):
    """Uma etapa real do smoke não produziu a evidência esperada."""


class OfflineSyntheticGateway:
    """Respostas determinísticas somente para os dois documentos deste smoke."""

    provider = "offline-synthetic-smoke"

    @property
    def usage(self) -> dict[str, int]:
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def complete(self, **kwargs: Any) -> str:
        payload = json.loads(kwargs["messages"][1]["content"])
        if kwargs["agent"] == "extraction":
            policy = PolicyExtraction()
            for field, label in (
                ("seguradora", "Seguradora"), ("moeda", "Moeda"),
                ("limite_maximo_garantia", "Limite"), ("retencao_franquia", "Franquia"),
                ("vigencia_inicio", "Vigencia inicio"), ("side_a", "Side A"),
            ):
                for page in payload["pages"]:
                    match = re.search(r"(?im)^\s*" + re.escape(label) + r":\s*([^\r\n]+)",
                                      page["text"])
                    if match:
                        setattr(policy, field, FieldEvidence(
                            valor=match.group(1).strip(), pagina=page["page_number"],
                            trecho_origem=match.group(0).strip(), confianca=1,
                        ))
                        break
            return policy.model_dump_json()
        if kwargs["agent"] == "comparison":
            return json.dumps({"comparisons": [{
                "field_name": pair["field_name"],
                "classification": (
                    DifferenceClass.MORE_FAVORABLE_A.value
                    if pair["field_name"] == "side_a"
                    and "Inclui custos de defesa" in pair["excerpt_a"]
                    and "Exclui custos de defesa" in pair["excerpt_b"]
                    else DifferenceClass.NOT_COMPARABLE.value
                ),
                "justification": (
                    "No exemplo sintetico, A inclui custos de defesa e B os exclui."
                    if pair["field_name"] == "side_a"
                    else "Os excertos sinteticos nao estabelecem vantagem."
                ),
            } for pair in payload]}, ensure_ascii=False)
        raise SmokeError("Stage inesperado: os documentos do smoke precisam de apenas uma clausula cada.")


class BudgetGateway:
    """Impede exceder orçamento antes de encaminhar uma nova chamada ao provedor."""

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway
        self.provider = gateway.provider
        self.logical_calls = 0
        self.stages: list[str] = []

    @property
    def usage(self) -> dict[str, int]:
        return dict(self.gateway.usage)

    @property
    def events(self) -> list[dict[str, Any]]:
        allowed = {"provider", "model", "stage", "duration_ms", "input_tokens",
                   "output_tokens", "attempt", "cache_hit", "status", "error_kind"}
        # Copy only the metadata contract: never prompts, documents or response bodies.
        return [{key: value for key, value in event.items()
                 if key in allowed and isinstance(value, (str, int, float, bool))}
                for event in getattr(self.gateway, "events", []) if isinstance(event, dict)]

    def complete(self, **kwargs: Any) -> str:
        if self.logical_calls >= MAX_LOGICAL_CALLS:
            raise SmokeError("Orcamento de seis chamadas logicas excedido; smoke interrompido.")
        self.logical_calls += 1
        self.stages.append(kwargs["agent"])
        return self.gateway.complete(**kwargs)


def offline_settings() -> Settings:
    return Settings(
        groq_api_key="", openai_api_key="", llm_provider="openai",
        model_fast="offline-extraction", model_intermediate="offline-repair",
        model_strong="offline-comparison", model_vision="offline-unused",
        temperature=0, max_tokens=4500, timeout_seconds=30, max_retries=3,
    )


def create_synthetic_documents(directory: Path) -> tuple[Path, Path]:
    """Cria PDF textual A e imagem PNG B sem qualquer apólice real ou dados pessoais."""
    directory.mkdir(parents=True, exist_ok=True)
    text_path = directory / "sintetica_A_textual.pdf"
    image_path = directory / "sintetica_B_imagem.png"
    for text, path, raster in (
        (SYNTHETIC_A_TEXT, text_path, False),
        (SYNTHETIC_B_TEXT, image_path, True),
    ):
        with pymupdf.open() as document:
            page = document.new_page(width=595, height=842)
            page.insert_text((50, 65), text, fontsize=14, fontname="helv", lineheight=1.7)
            document.set_metadata({"title": "DOCUMENTO SINTETICO - SMOKE EDUCACIONAL"})
            if raster:
                page.get_pixmap(dpi=220, colorspace=pymupdf.csRGB, alpha=False).save(str(path))
            else:
                document.save(path)
    return text_path, image_path


def _verify_report_evidence(report: Any, processed: Any) -> None:
    sources = {page.page_number: normalize(page.text) for page in processed.pages}
    required = {"seguradora", "moeda", "limite_maximo_garantia",
                "retencao_franquia", "vigencia_inicio", "side_a"}
    for field_name in PolicyExtraction.model_fields:
        item = getattr(report.policy, field_name)
        if item.valor == NOT_FOUND:
            if field_name in required:
                raise SmokeError(f"Campo obrigatorio para o smoke nao extraido: {field_name}.")
            continue
        if normalize(item.trecho_origem) not in sources.get(item.pagina, ""):
            raise SmokeError(f"Evidencia nao corresponde a pagina: {field_name}.")
    if report.issues:
        raise SmokeError("O smoke produziu alertas de validacao; revise diagnostics e fonte sintetica.")


def run_smoke(output_dir: Path | None = None, *, live: bool = False,
              emit: Callable[[str], None] = print) -> dict[str, Any]:
    started = time.perf_counter()
    output = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "processed" / "e2e_smoke"
    output.mkdir(parents=True, exist_ok=True)
    llm = get_settings() if live else offline_settings()
    if not 1 <= llm.max_retries <= 3:
        raise SmokeError("O smoke requer no maximo tres tentativas HTTP por chamada.")
    mode = "live" if live else "offline_simulated"
    # Orçamento aparece antes da construção/chamada do gateway. Apenas metadados.
    emit(json.dumps({
        "mode": mode, "synthetic_documents": 2, "clauses_per_document": 1,
        "expected_logical_calls_without_repair": 3,
        "maximum_logical_calls_including_repair": MAX_LOGICAL_CALLS,
        "maximum_http_attempts": MAX_HTTP_ATTEMPTS if live else 0,
        "extraction_model": llm.model_fast if llm.llm_provider == "openai" else llm.model_strong,
        "comparison_model": llm.model_strong, "output_directory": str(output),
    }, ensure_ascii=True))
    gateway = BudgetGateway(get_gateway(llm) if live else OfflineSyntheticGateway())
    ingestion = replace(get_ingestion_settings(), processed_dir=output, min_native_chars=20)
    document_paths = create_synthetic_documents(output / "synthetic_inputs")
    reports, processed_documents, report_paths = [], [], []
    first_progress: list[tuple[int, int, str, bool]] = []
    ocr_progress: list[tuple[int, int, str]] = []
    for path in document_paths:
        _, processed, report, report_path, clauses = process_and_structure_document(
            path, ingestion_settings=ingestion, llm_settings=llm, gateway=gateway,
            progress_callback=lambda *args: first_progress.append(args),
            ocr_progress_callback=lambda *args: ocr_progress.append(args),
        )
        if len(clauses) != 1 or len(clauses[0].text) > 4000:
            raise SmokeError("O smoke exige exatamente uma clausula de ate 4000 caracteres por documento.")
        _verify_report_evidence(report, processed)
        reports.append(report)
        processed_documents.append(processed)
        report_paths.append(report_path)
    if processed_documents[0].pages[0].extraction_method != "native":
        raise SmokeError("O documento A deve demonstrar extracao textual nativa.")
    if processed_documents[1].pages[0].extraction_method != "tesseract":
        raise SmokeError("O documento B deve demonstrar OCR de imagem.")
    expected_limits = [Decimal("10000000"), Decimal("8000000")]
    expected_retentions = [Decimal("100000"), Decimal("200000")]
    for index, report in enumerate(reports):
        if amounts(report.policy.limite_maximo_garantia.valor) != [expected_limits[index]]:
            raise SmokeError("O limite extraido difere do documento sintetico original.")
        if amounts(report.policy.retencao_franquia.valor) != [expected_retentions[index]]:
            raise SmokeError("A franquia extraida difere do documento sintetico original.")
    comparator = ComparisonAgent(
        gateway=gateway, model_strong=llm.model_strong, processed_dir=output,
    )
    comparison = comparator.compare(reports[0], reports[1])
    by_field = {difference.field_name: difference for difference in comparison.differences}
    for field_name in ("limite_maximo_garantia", "retencao_franquia", "side_a"):
        if by_field[field_name].classification != DifferenceClass.MORE_FAVORABLE_A:
            raise SmokeError(f"O exemplo sintetico nao demonstrou diferenca esperada em {field_name}.")
        if not by_field[field_name].citation_a.page_number or not by_field[field_name].citation_b.page_number:
            raise SmokeError("Comparacao sem citacoes das duas fontes.")
    if by_field["vigencia_inicio"].classification != DifferenceClass.NOT_COMPARABLE:
        raise SmokeError("Uma data diferente nao deve implicar vantagem.")
    artifacts = ComparisonReportAgent().generate(comparison, output / "comparison_reports")
    PolicyComparison.model_validate_json(artifacts.json_path.read_text(encoding="utf-8"))
    with pymupdf.open(artifacts.pdf_path) as pdf:
        if pdf.page_count < 1 or not any(page.get_text().strip() for page in pdf):
            raise SmokeError("PDF comparativo vazio.")
    repository = SqliteRepository(output / "policies.sqlite3")
    for report in reports:
        stored = repository.get_document(report.sha256)
        if stored is None or stored.policy.model_dump() != report.policy.model_dump():
            raise SmokeError("A persistencia SQLite nao preservou os campos estruturados.")
        if not repository.get_evidence_chunks(report.sha256):
            raise SmokeError("A persistencia SQLite nao preservou as evidencias por pagina.")
    calls_before_resume = gateway.logical_calls
    resume_progress: list[tuple[int, int, str, bool]] = []
    resume_reports = []
    for path in document_paths:
        _, processed, report, _, _ = process_and_structure_document(
            path, ingestion_settings=ingestion, llm_settings=llm, gateway=gateway,
            progress_callback=lambda *args: resume_progress.append(args),
        )
        if not processed.cache_hit:
            raise SmokeError("A segunda passagem nao reutilizou o cache OCR.")
        resume_reports.append(report)
    repeated = comparator.compare(resume_reports[0], resume_reports[1])
    if repeated.model_dump() != comparison.model_dump() or gateway.logical_calls != calls_before_resume:
        raise SmokeError("A segunda passagem realizou chamadas novas ou alterou a comparacao em cache.")
    if len(resume_progress) != 2 or not all(event[3] for event in resume_progress):
        raise SmokeError("A segunda passagem nao reutilizou ambas as clausulas estruturadas.")
    usage = gateway.usage
    if usage.get("calls", 0) > MAX_HTTP_ATTEMPTS:
        raise SmokeError("O provedor excedeu o orcamento HTTP configurado.")
    diagnostics = {
        "status": "PASS", "mode": mode, "provider": gateway.provider,
        "elapsed_total_seconds": round(time.perf_counter() - started, 3),
        "gateway_events": gateway.events, "synthetic_data": True,
        "generative_ai_verified": live and gateway.logical_calls > 0,
        "limitations": (
            [] if live else ["Provedor deterministico simulado; nao comprova inferencia de IA Generativa real."]
        ),
        "documents": [{
            "source_name": report.source_name, "sha256": report.sha256,
            "pages": len(processed.pages), "clauses": report.clause_count,
            "extraction_methods": [page.extraction_method for page in processed.pages],
            "report_path": str(report_path),
        } for report, processed, report_path in zip(reports, processed_documents, report_paths, strict=True)],
        "logical_calls": gateway.logical_calls, "stages": gateway.stages,
        "http_attempts": usage.get("calls", 0), "usage": usage,
        "resume_cache_hits": sum(event[3] for event in resume_progress),
        "resume_new_calls": gateway.logical_calls - calls_before_resume,
        "sqlite_path": str(repository.database_path),
        "artifacts": {
            "markdown": str(artifacts.markdown_path), "pdf": str(artifacts.pdf_path),
            "json": str(artifacts.json_path),
        },
        "first_pass_ocr_progress": ocr_progress,
    }
    diagnostics_path = output / "diagnostics.json"
    diagnostics_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(json.dumps({
        "status": diagnostics["status"], "mode": mode, "provider": gateway.provider,
        "elapsed_total_seconds": diagnostics["elapsed_total_seconds"],
        "logical_calls": gateway.logical_calls, "http_attempts": diagnostics["http_attempts"],
        "resume_cache_hits": diagnostics["resume_cache_hits"],
        "diagnostics_path": str(diagnostics_path),
        "generative_ai_verified": diagnostics["generative_ai_verified"],
    }, ensure_ascii=True))
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="usa o provedor real configurado e consome API; por padrao nao usa rede")
    parser.add_argument("--output-dir", type=Path, help="diretorio local para diagnosticos e artefatos")
    arguments = parser.parse_args()
    try:
        run_smoke(arguments.output_dir, live=arguments.live)
    except (RuntimeError, ValueError, OSError) as error:
        print(f"Smoke nao concluido: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
