"""InsurMinds D&O comparison copilot; demo rendering uses no inference."""
from __future__ import annotations

import csv
from contextlib import nullcontext
import io
import json
import os
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.comparison import ComparisonAgent
from src.agents.report import ComparisonReportAgent
from src.agents.segmentation import SegmentationAgent
from src.config import ConfigurationError, get_ingestion_settings, get_settings, get_extraction_routing
from src.llm.providers import get_gateway
from src.llm.resilience import LLMClientError
from src.pipeline import process_and_structure_document, process_document
from src.schemas.policy import NOT_FOUND
from src.sources import PUBLIC_CATALOG, PublicCatalogSource, PublicURLSource, UploadSource
from src.workspace import FIELD_LABELS, assess_compatibility, build_matrix, executive_metrics, infer_metadata, load_validated_session
from src.llm.usage_reporting import BILLING_NOTE, aggregate_session, load_standard_rates, save_usage_runs, summarize_run
from src.workspace_recovery import find_unsafe_workspace_runs
from src.product_presentation import (RESULT_TABS, business_relevance, classification_text,
    comparison_counts, document_title, document_titles, important_differences, value_text as business_value_text)

st.set_page_config(page_title="InsurMinds | Revisão D&O", page_icon="📑", layout="wide")
st.markdown("""
<style>
.stApp { background: #f7f9fb; }
.block-container { max-width: 1440px; padding-top: 2.1rem; }
h1, h2, h3 { color: #183b4a; letter-spacing: -.02em; }
div[data-testid="stVerticalBlockBorderWrapper"] { background: white; border-radius: 12px; }
div[data-testid="stMetric"] { background: #eef4f6; padding: .75rem; border-radius: 8px; }
div[data-testid="stSidebar"] { background: #edf2f5; }
.stButton>button[kind="primary"] { background: #185c73; border-color: #185c73; }
</style>
""", unsafe_allow_html=True)


class BoundedLogicalGateway:
    """Bound logical operations without altering any provider request parameter."""
    def __init__(self, gateway, limit):
        self.gateway, self.limit, self.logical_calls = gateway, int(limit), 0

    @property
    def provider(self):
        return self.gateway.provider

    @property
    def usage(self):
        return self.gateway.usage

    @property
    def events(self):
        return self.gateway.events

    def complete(self, **kwargs):
        if self.logical_calls >= self.limit:
            raise RuntimeError("Limite de operações de IA desta execução atingido. O cache preserva o trabalho concluído; revise o limite antes de retomar.")
        self.logical_calls += 1
        return self.gateway.complete(**kwargs)


    def __getattr__(self, name):
        if name not in {"complete_routed", "policy", "control", "record_validation"}:
            raise AttributeError(name)
        delegated = getattr(self.gateway, name)
        if name != "complete_routed":
            return delegated

        def bounded_routed(*args, **kwargs):
            if self.logical_calls >= self.limit:
                raise LLMClientError("routing", "budget_exhausted")
            self.logical_calls += 1
            return delegated(*args, **kwargs)
        return bounded_routed


def initialize():
    defaults = {"workspace_docs": [], "prepared_docs": {}, "workspace_reference": "",
                "workspace_result": None, "workspace_reviews": {}, "workspace_epoch": 0,
                "workspace_usage_runs": {}, "workspace_demo": False, "demo_dataset": None}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def invalidate(reset_consent=True):
    st.session_state["workspace_result"] = None
    st.session_state["workspace_reviews"] = {}
    st.session_state.pop("comparison_result", None)
    if reset_consent:
        st.session_state["consent"] = False
    for key in ("detail_field", "detail_candidate"):
        st.session_state.pop(key, None)


def new_workspace():
    invalidate()
    st.session_state["workspace_docs"] = []
    st.session_state["prepared_docs"] = {}
    st.session_state["workspace_reference"] = ""
    st.session_state["workspace_epoch"] += 1
    st.session_state["workspace_demo"] = False
    st.session_state["demo_dataset"] = None
    for key in list(st.session_state):
        if key.startswith(("note_", "pdf_page_", "reference_select")):
            st.session_state.pop(key, None)


def add_documents(documents):
    current = list(st.session_state["workspace_docs"])
    added, notices = 0, []
    for source in documents:
        if any(item.sha256 == source.sha256 for item in current):
            notices.append(f"{source.filename}: documento já incluído; a duplicata foi ignorada.")
            continue
        if len(current) >= 5:
            notices.append("O workspace aceita no máximo cinco documentos. Remova um documento para adicionar outro.")
            continue
        if any(item.filename == source.filename for item in current):
            path = Path(source.filename)
            filename = f"{path.stem}-{source.sha256[:12]}{path.suffix}"
            suffix = 2
            while any(item.filename == filename for item in current):
                filename = f"{path.stem}-{source.sha256[:12]}-{suffix}{path.suffix}"
                suffix += 1
            source = replace(source, filename=filename)
        current.append(source)
        added += 1
    if added:
        invalidate()
        st.session_state["workspace_demo"] = False
        st.session_state["demo_dataset"] = None
        st.session_state["workspace_docs"] = current
        if not st.session_state["workspace_reference"]:
            st.session_state["workspace_reference"] = current[0].sha256
    return added, notices


def source_title(source, metadata=None):
    if metadata is None:
        metadata = st.session_state["prepared_docs"].get(source.sha256, {}).get("metadata")
    return document_title(source, metadata)


def value_text(value, status=None):
    return business_value_text(value, status)


def workspace_signature(documents, reference):
    return (reference, tuple((item.sha256, item.filename, item.source_url) for item in documents))


def safely_show_error(stage, error):
    if isinstance(error, KeyError):
        message = "Uma etapa de análise não pôde ser concluída com segurança. O trabalho local foi preservado."
    else:
        message = "Falha de acesso ao arquivo. Confira permissões e espaço local." if isinstance(error, OSError) else str(error)
    st.error(f"Não foi possível concluir {stage}. {message}")


def change_reference():
    selected = st.session_state.get("reference_select")
    if selected and selected != st.session_state["workspace_reference"]:
        st.session_state["workspace_reference"] = selected
        invalidate()


def document_cards(documents):
    prepared = st.session_state["prepared_docs"]
    is_demo = st.session_state.get("workspace_demo", False)
    result = st.session_state.get("workspace_result")
    reports = result.get("reports", {}) if result else {}
    for start in range(0, len(documents), 3):
        columns = st.columns(min(3, len(documents) - start))
        for column, source in zip(columns, documents[start:start + 3]):
            with column, st.container(border=True):
                metadata = prepared.get(source.sha256, {}).get("metadata")
                is_reference = source.sha256 == st.session_state["workspace_reference"]
                st.caption("REFERÊNCIA" if is_reference else "DOCUMENTO PARA COMPARAR")
                st.subheader(source_title(source, metadata))
                st.caption(source.filename)
                if is_demo:
                    st.caption("Especificação fictícia + excertos reais · sem validade contratual")
                elif metadata and metadata.document_type:
                    st.caption(metadata.document_type)
                report = reports.get(source.sha256)
                if report:
                    for label, name in (("Tomador", "tomador_segurado"), ("Número", "numero_apolice")):
                        field = getattr(report.policy, name)
                        if field.valor != NOT_FOUND:
                            st.caption(f"{label}: {value_text(field.valor, report.field_status.get(name))}")
                    if metadata and metadata.period:
                        st.caption("Vigência: " + metadata.period)
                with st.expander("Informações do documento", expanded=False):
                    if metadata:
                        for label, value in (("Processo SUSEP", metadata.susep_process),
                                             ("Versão", metadata.effective_date), ("Páginas", metadata.page_count)):
                            if value:
                                st.caption(f"{label}: {value}")
                        if metadata.conflicts:
                            st.info("Há informações que requerem confirmação no documento original.")
                    if source.source_url:
                        st.link_button("Abrir fonte pública ↗", source.source_url)
                        st.caption(f"Acesso: {source.access_date or 'URL informada pelo usuário'}")
                    if source.origin == "upload":
                        url = st.text_input("URL de origem (opcional)", value=source.source_url,
                                            key=f"origin_url_{source.sha256}", help="Informa uma citação; não baixa documentos.")
                        if st.button("Salvar origem", key=f"save_origin_{source.sha256}"):
                            try:
                                urls = ComparisonReportAgent.validate_source_urls({source.filename: url}) if url.strip() else {}
                                updated = replace(source, source_url=urls.get(source.filename, ""))
                                if updated.source_url != source.source_url:
                                    st.session_state["workspace_docs"] = [updated if item.sha256 == source.sha256 else item for item in documents]
                                    invalidate()
                                    st.rerun()
                                st.success("Origem validada.")
                            except (RuntimeError, ValueError) as error:
                                safely_show_error("origem", error)
                left, right = st.columns(2)
                if left.button("Usar como referência", key=f"reference_{source.sha256}", disabled=is_reference):
                    st.session_state["workspace_reference"] = source.sha256
                    st.session_state.pop("reference_select", None)
                    invalidate()
                    st.rerun()
                if right.button("Remover", key=f"remove_{source.sha256}"):
                    remaining = [item for item in documents if item.sha256 != source.sha256]
                    st.session_state["workspace_docs"] = remaining
                    st.session_state["prepared_docs"].pop(source.sha256, None)
                    st.session_state["workspace_demo"] = False
                    st.session_state["demo_dataset"] = None
                    if is_reference:
                        st.session_state["workspace_reference"] = remaining[0].sha256 if remaining else ""
                    st.session_state.pop("reference_select", None)
                    invalidate()
                    st.rerun()


def load_demo_workspace():
    from src.demo import load_demo_dataset
    loaded = load_demo_dataset()
    new_workspace()
    documents = loaded["sources"]
    st.session_state["workspace_docs"] = documents
    st.session_state["workspace_reference"] = loaded["reference_sha"]
    st.session_state["workspace_demo"] = True
    st.session_state["demo_dataset"] = loaded
    st.session_state["prepared_docs"] = {
        source.sha256: {"processed": loaded["processed"][source.sha256],
                       "metadata": infer_metadata(loaded["processed"][source.sha256], loaded["reports"][source.sha256]),
                       "clause_count": loaded["reports"][source.sha256].clause_count}
        for source in documents
    }


def build_demo_result(documents, reference, ingestion):
    """Use validated fixture facts only; never create a provider for the example."""
    from src.demo import demo_comparisons
    loaded = st.session_state.get("demo_dataset")
    if not loaded or {source.sha256 for source in documents} != {source.sha256 for source in loaded["sources"]}:
        raise ValueError("Recarregue os dois documentos do exemplo de demonstração.")
    comparisons = demo_comparisons(reference, loaded)
    reports = loaded["reports"]
    metadata = {source.sha256: st.session_state["prepared_docs"][source.sha256]["metadata"] for source in documents}
    artifacts = {
        comparison.document_id_b: ComparisonReportAgent().generate(
            comparison, ingestion.processed_dir / "demo_downloads", source_urls=comparison.source_references)
        for comparison in comparisons
    }
    st.session_state["workspace_result"] = {
        "signature": workspace_signature(documents, reference), "reference_sha": reference,
        "documents": list(documents), "reports": reports, "metadata": metadata,
        "comparisons": comparisons, "artifacts": artifacts, "issues": 0,
        "stats": {"demo": True, "usage_runs": [], "calls": 0, "cache_hits": 0, "duration_seconds": None},
    }
    st.success("Resultado de demonstração pré-processado. Nenhuma chamada de IA foi realizada.")


def prepare_documents(documents, ingestion, *, rerun=True):
    progress = st.progress(0.0, text="Lendo documentos...")
    status = st.status("Lendo documentos...", expanded=False)
    stage = "preparação"
    try:
        with tempfile.TemporaryDirectory(prefix="insurminds-preview-") as directory:
            for index, source in enumerate(documents):
                stage = f"{source.filename}: texto e páginas"
                folder = Path(directory) / str(index)
                folder.mkdir()
                path = folder / source.filename
                path.write_bytes(source.data)

                def on_page(page, total, method):
                    progress.progress((index + page / max(total, 1)) / len(documents),
                                      text=f"Lendo documentos... ({index + 1}/{len(documents)})")

                _, processed = process_document(path, settings=ingestion, ocr_progress_callback=on_page)
                clauses = SegmentationAgent(gateway=None).segment(processed)
                editorial = source.metadata if source.metadata.get("trusted_catalog") else None
                metadata = infer_metadata(processed, catalog=editorial)
                st.session_state["prepared_docs"][source.sha256] = {
                    "processed": processed, "metadata": metadata, "clause_count": len(clauses),
                }
                status.write(f"{source_title(source, metadata)}: documento lido.")
        status.update(label="Documentos lidos", state="complete", expanded=False)
        progress.progress(1.0, text="Identificando condições...")
        if rerun:
            st.rerun()
        return True
    except (OSError, RuntimeError, ValueError) as error:
        status.update(label="Preparação interrompida", state="error")
        safely_show_error(stage, error)
        return False


def analyze_documents(documents, reference, selected, ingestion, max_calls):
    if st.session_state.get("workspace_demo"):
        build_demo_result(documents, reference, ingestion)
        return
    existing = st.session_state.get("workspace_result")
    if existing and existing.get("signature") == workspace_signature(documents, reference):
        message = ("Resultados locais aprovados reutilizados; nenhuma nova chamada de IA."
                   if existing.get("stats", {}).get("restored_validated_session")
                   else "Resultado desta comparação reutilizado; nenhuma nova chamada de IA.")
        st.success(message)
        return
    stage = "configuração"
    started = time.perf_counter()
    status = None
    try:
        stage = "verificação do histórico"
        unsafe_runs = find_unsafe_workspace_runs(ingestion.processed_dir, {source.sha256 for source in documents})
        if unsafe_runs:
            st.error("Uma análise anterior destes documentos não foi concluída. A retomada pode repetir chamadas de IA e precisa ser revisada.")
            st.info("Use o exemplo de demonstração para validar offline. Nenhuma nova chamada de IA foi iniciada.")
            return
        stage = "configuração"
        settings = get_settings(provider=selected)
        source_urls = ComparisonReportAgent.validate_source_urls({
            item.filename: item.source_url for item in documents if item.source_url
        })
        invalidate(reset_consent=False)
        status = st.status("Identificando condições...", expanded=False)
        progress = st.progress(0.0, text="Documentos validados")
        business_event = st.empty()

        def on_event(event):
            business_event.caption("Identificando condições...")

        run_id = uuid.uuid4().hex
        routing_enabled = selected == "openai" and os.getenv("MODEL_ROUTING_ENABLED", "false").strip().lower() in {"true", "1", "yes"}
        options = {}
        if routing_enabled:
            from src.llm.model_routing import RequestControl
            control_path = ingestion.processed_dir / "workspace_usage" / (run_id + "-attempt-ledger.json")
            ledger_status = "ACTIVE"
            def persist_control(snapshot):
                control_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = control_path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"run_id": run_id, "workspace_status": ledger_status,
                    "workspace_documents": [source.sha256 for source in documents], **snapshot}, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(control_path)
            request_control = RequestControl(min(int(max_calls), 50), persist_callback=persist_control)
            options["request_control"] = request_control
        gateway = BoundedLogicalGateway(get_gateway(settings, event_callback=on_event, **options), max_calls)
        reports, metadata, cache_hits = {}, {}, 0
        usage_runs = []
        rates = load_standard_rates(ROOT / "docs" / "OPENAI_PRICES_2026-10-04.json")
        with tempfile.TemporaryDirectory(prefix="insurminds-review-") as directory:
            for index, source in enumerate(documents):
                stage = f"{source.filename}: extração de condições"
                event_start, document_started = len(gateway.events), time.perf_counter()
                folder = Path(directory) / str(index)
                folder.mkdir()
                path = folder / source.filename
                path.write_bytes(source.data)
                status.write(f"Analisando {source_title(source)}")

                def on_page(page, total, method):
                    progress.progress(min(.7 * (index + .2 * page / max(total, 1)) / len(documents), .7),
                                      text=f"Lendo documentos... ({index + 1}/{len(documents)})")

                def on_clause(completed, total, clause_id, hit):
                    nonlocal cache_hits
                    cache_hits += int(hit)
                    progress.progress(min(.7 * (index + .2 + .8 * completed / max(total, 1)) / len(documents), .7),
                                      text=f"Identificando condições... ({index + 1}/{len(documents)})")

                _, processed, report, _, clauses = process_and_structure_document(
                    path, ingestion_settings=ingestion, llm_settings=settings, gateway=gateway,
                    progress_callback=on_clause, ocr_progress_callback=on_page,
                )
                reports[source.sha256] = report
                usage_runs.append(summarize_run(run_id + ":" + source.sha256, source.filename,
                    gateway.events[event_start:], rates=rates, retrieval=report.retrieval_diagnostics,
                    duration_seconds=time.perf_counter() - document_started))
                cache_hits += int(processed.cache_hit)
                editorial = source.metadata if source.metadata.get("trusted_catalog") else None
                metadata[source.sha256] = infer_metadata(processed, report, editorial)
                st.session_state["prepared_docs"][source.sha256] = {
                    "processed": processed, "metadata": metadata[source.sha256], "clause_count": len(clauses),
                }
                status.write(f"Condições identificadas em {source_title(source, metadata[source.sha256])}.")
                if report.issues:
                    st.warning(f"{source.filename}: {len(report.issues)} aviso(s) de extração. Campos não localizados precisam de revisão.")
        candidates = [item for item in documents if item.sha256 != reference]
        comparisons, artifacts = [], {}
        agent = ComparisonAgent(gateway=gateway, model_strong=get_extraction_routing(settings).comparison_model or settings.model_strong, processed_dir=ingestion.processed_dir)
        for index, candidate in enumerate(candidates):
            stage = f"comparação: {candidate.filename}"
            progress.progress(.7 + .25 * index / len(candidates), text=f"Comparando... ({index + 1}/{len(candidates)})")
            event_start, comparison_started = len(gateway.events), time.perf_counter()
            comparison = agent.compare(reports[reference], reports[candidate.sha256])
            pair_urls = {name: url for name, url in source_urls.items() if name in {comparison.source_a, comparison.source_b}}
            comparison = comparison.model_copy(update={"source_references": pair_urls})
            comparisons.append(comparison)
            usage_runs.append(summarize_run(run_id + ":comparison:" + candidate.sha256,
                "Comparação · " + candidate.filename, gateway.events[event_start:], rates=rates,
                duration_seconds=time.perf_counter() - comparison_started))
            stage = "relatório comparativo"
            progress.progress(.95, text="Preparando evidências...")
            artifacts[candidate.sha256] = ComparisonReportAgent().generate(
                comparison, ingestion.processed_dir, source_urls=pair_urls,
            )
        register_usage_runs(usage_runs, ingestion.processed_dir)
        stats = {**gateway.usage, "cache_hits": cache_hits, "logical_calls": gateway.logical_calls,
                 "duration_seconds": round(time.perf_counter() - started, 2), "provider": selected,
                 "models": sorted({event.get("requested_model", event.get("model", "não informado")) for event in gateway.events}),
                 "events": gateway.events, "usage_runs": usage_runs}
        st.session_state["workspace_result"] = {
            "signature": workspace_signature(documents, reference), "reference_sha": reference,
            "reports": reports, "comparisons": comparisons, "artifacts": artifacts,
            "stats": stats, "issues": sum(len(report.issues) for report in reports.values()),
            "documents": list(documents), "metadata": metadata,
        }
        if "request_control" in locals():
            partial = request_control.blocked_reason or any(
                report.retrieval_diagnostics.get("partial") or report.retrieval_diagnostics.get("stop_reason")
                or "TECHNICAL_UNAVAILABLE" in report.field_status.values()
                for report in reports.values()
            )
            ledger_status = "INTERRUPTED" if partial else "COMPLETED"
            persist_control(request_control.snapshot())
        progress.progress(1.0, text="Comparação e relatórios disponíveis")
        status.update(label="Análise concluída · resultados prontos para revisão", state="complete", expanded=False)
        st.rerun()
    except (ConfigurationError, OSError, RuntimeError, ValueError, KeyError) as error:
        if "request_control" in locals():
            ledger_status = "INTERRUPTED"
            try:
                persist_control(request_control.snapshot())
            except (OSError, RuntimeError, ValueError, KeyError):
                pass  # The prior ACTIVE ledger still blocks an unsafe replay.
        if "gateway" in locals() and "run_id" in locals():
            try:
                interrupted = summarize_run(run_id + ":interrupted", "Execução interrompida · " + stage,
                                            gateway.events, rates=locals().get("rates", {}),
                                            duration_seconds=time.perf_counter() - started)
                register_usage_runs([interrupted], ingestion.processed_dir)
            except (OSError, RuntimeError, ValueError, KeyError):
                st.warning("Não foi possível salvar o resumo desta execução. O histórico de tentativas existente foi preservado.")
        if status is not None:
            status.update(label=f"Análise interrompida · {stage}", state="error", expanded=True)
        safely_show_error(stage, error)
        st.info("O trabalho local foi preservado. Use o exemplo de demonstração para validar sem novas chamadas de IA.")



def register_usage_runs(runs, directory):
    ledger = dict(st.session_state.get("workspace_usage_runs", {}))
    for run in runs:
        ledger[run["run_id"]] = run
    st.session_state["workspace_usage_runs"] = ledger
    save_usage_runs(Path(directory) / "workspace_usage", runs)


def display_number(value, decimals=0):
    if value is None:
        return "Não informado"
    return f"{value:,.{decimals}f}"


def render_usage_panel(runs):
    with st.expander("Uso da IA", expanded=False):
        st.caption(BILLING_NOTE)
        st.caption("Cached input é parte do input. Cache local reutiliza resultados e não é token em cache do provedor.")
        if not runs:
            st.info("Esta visualização não possui telemetria de run registrada.")
        for run in runs:
            st.subheader(run["label"])
            st.caption("Run: " + run["run_id"])
            columns = st.columns(3)
            for index, key, label in (
                (0, "requests", "Requests"), (1, "input_tokens", "Input tokens"),
                (2, "cached_input_tokens", "Cached input tokens"),
                (0, "uncached_input_tokens", "Uncached input tokens"),
                (1, "output_tokens", "Output tokens"), (2, "total_tokens", "Total tokens"),
            ):
                columns[index].metric(label, display_number(run.get(key)))
            st.dataframe([{"Modelo": model, "Requests": count,
                           "Input": run["tokens_by_model"][model]["input_tokens"],
                           "Cached input": run["tokens_by_model"][model]["cached_input_tokens"],
                           "Output": run["tokens_by_model"][model]["output_tokens"],
                           "Total": run["tokens_by_model"][model]["total_tokens"]}
                          for model, count in run.get("requests_by_model", {}).items()],
                         hide_index=True, use_container_width=True)
            st.write(f"Technical fallbacks: {run.get('technical_fallbacks', 0)} · "
                     f"Semantic escalations: {run.get('semantic_escalations', 0)} · "
                     f"Sol invocations: {run.get('sol_invocations', 0)}")
            st.write("Total runtime: " + display_number(run.get("duration_seconds"), 3) + " s · "
                     "LLM runtime: " + display_number(run.get("llm_runtime_seconds"), 3) + " s · "
                     "Median latency: " + display_number(run.get("median_latency_seconds"), 3) + " s · "
                     "p95 latency: " + display_number(run.get("p95_latency_seconds"), 3) + " s")
            st.write("Full corpus chunks: " + display_number(run.get("full_corpus_chunks")) + " · "
                     "Candidate chunks: " + display_number(run.get("candidate_chunks")) + " · "
                     "Context reduction: " + display_number(run.get("context_reduction_percent"), 2) + "%")
            st.caption("Candidate chunks mede candidatos únicos da busca local; não representa todos os trechos efetivamente enviados.")
            st.write("Standard-rate counterfactual cost (USD): " +
                     display_number(run.get("standard_rate_counterfactual_usd"), 8))
            st.write("Processing tier: " + run.get("processing_tier", "não reconciliado para este run"))
            if run.get("api_service_tiers"):
                st.caption("Service tier retornado pela API: " + ", ".join(run["api_service_tiers"]))
        session = aggregate_session(st.session_state.get("workspace_usage_runs", {}).values())
        st.subheader("SESSION TOTAL")
        st.dataframe([{"Requests": session["requests"], "Input": session["input_tokens"],
                       "Cached input": session["cached_input_tokens"], "Output": session["output_tokens"],
                       "Total tokens": session["total_tokens"], "Technical fallbacks": session["technical_fallbacks"],
                       "Semantic escalations": session["semantic_escalations"], "Sol invocations": session["sol_invocations"]}],
                     hide_index=True, use_container_width=True)
        st.write("Model distribution", session["requests_by_model"])
        st.caption("A sessão acumula runs distintos; downloads, filtros, revisão e recarregamento do mesmo run não adicionam requests.")


def restore_validated_session(ingestion):
    loaded = load_validated_session(ROOT / "data" / "processed" / "fasttrack_validation")
    documents = loaded["documents"]
    metadata, prepared = {}, {}
    with tempfile.TemporaryDirectory(prefix="insurminds-validated-") as directory:
        for index, source in enumerate(documents):
            folder = Path(directory) / str(index)
            folder.mkdir()
            path = folder / source.filename
            path.write_bytes(source.data)
            _, processed = process_document(path, settings=ingestion)
            report = loaded["reports"][source.sha256]
            metadata[source.sha256] = infer_metadata(processed, report)
            prepared[source.sha256] = {"processed": processed, "metadata": metadata[source.sha256],
                                      "clause_count": report.clause_count}
    artifacts = {}
    for comparison in loaded["comparisons"]:
        artifacts[comparison.document_id_b] = ComparisonReportAgent().generate(
            comparison, ingestion.processed_dir / "validated_session_downloads",
            source_urls=comparison.source_references)
    rates = load_standard_rates(ROOT / "docs" / "OPENAI_PRICES_2026-10-04.json")
    runs = [summarize_run(run["run_id"], run["label"], run["events"], rates=rates,
              retrieval=run["retrieval"],
              duration_seconds=run["metrics"].get("performance", {}).get(
                  "total_runtime_seconds", run["metrics"].get("duration_seconds", run["metrics"].get("wall_seconds"))),
              processing_tier_evidence=loaded["processing_tier_evidence"]) for run in loaded["runs"]]
    invalidate()
    reference = loaded["reference_sha"]
    st.session_state["workspace_demo"] = False
    st.session_state["demo_dataset"] = None
    st.session_state["workspace_docs"] = documents
    st.session_state["prepared_docs"] = prepared
    st.session_state["workspace_reference"] = reference
    register_usage_runs(runs, ingestion.processed_dir)
    session = aggregate_session(runs)
    st.session_state["workspace_result"] = {
        "signature": workspace_signature(documents, reference), "reference_sha": reference,
        "reports": loaded["reports"], "comparisons": loaded["comparisons"], "artifacts": artifacts,
        "stats": {"usage_runs": runs, "calls": session["requests"], "cache_hits": len(documents),
                  "duration_seconds": (sum(run["duration_seconds"] for run in runs)
                                       if all(run["duration_seconds"] is not None for run in runs) else None),
                  "restored_validated_session": True},
        "issues": sum(len(report.issues) for report in loaded["reports"].values()),
        "documents": documents, "metadata": metadata,
    }


def flat_rows(matrix, reference_title, candidates, titles):
    rows = []
    for row in matrix:
        output = {"Item": row["label"], f"Referência · {reference_title}": value_text(row["reference_value"], row.get("reference_status"))}
        classifications = []
        for candidate in candidates:
            cell = row["candidates"][candidate.sha256]
            title = titles[candidate.sha256]
            output[title] = value_text(cell["value"], cell.get("field_status"))
            classifications.append(f"{title}: {classification_text(row, cell)}")
        output["Conclusão"] = " | ".join(classifications)
        rows.append(output)
    return rows


def csv_export(rows):
    stream = io.StringIO(newline="")
    if rows:
        writer = csv.writer(stream)
        def safe_cell(value):
            text = str(value)
            return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")) else text
        headers = list(rows[0])
        writer.writerow([safe_cell(key) for key in headers])
        for row in rows:
            # Prevent spreadsheet formula execution in values and headers.
            writer.writerow([safe_cell(row[key]) for key in headers])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def render_evidence(evidence, source, *, key="evidence", field_name=None):
    st.caption(source_title(source))
    st.caption(source.filename)
    page = evidence.get("page_number")
    if not page or evidence.get("excerpt") in {None, "", NOT_FOUND}:
        st.info(value_text(NOT_FOUND, evidence.get("field_status")) + ". Confira a fonte; este estado não demonstra ausência contratual.")
        return
    st.write(f"**Página {page}**")
    st.text(evidence["excerpt"])
    dataset = st.session_state.get("demo_dataset")
    if dataset:
        mappings = dataset.get("page_mapping", {})
        mapping = mappings.get(source.sha256, {}) if isinstance(mappings, dict) else {}
        original = mapping.get(str(page), mapping.get(page)) if isinstance(mapping, dict) else None
        if original:
            if original.get("kind") == "synthetic":
                st.caption("Especificação sintética · dados fictícios · sem validade contratual.")
            else:
                original_page = original.get("original_source_page", original.get("source_page"))
                st.caption(f"Excerto de {original.get('source_file', source.filename)} · página original {original_page}")
    if source.source_url:
        url = source.source_url.split("#", 1)[0] + f"#page={page}"
        st.link_button(f"Abrir documento na página {page} ↗", url)
    elif source.filename.lower().endswith(".pdf"):
        page_key = f"pdf_page_{key}_{source.sha256}_{page}"
        if st.button(f"Abrir documento na página {page}", key=page_key):
            st.session_state[page_key + "_open"] = True
            st.rerun()
        if st.session_state.get(page_key + "_open"):
            import pymupdf
            with pymupdf.open(stream=source.data, filetype="pdf") as document:
                image = document[page - 1].get_pixmap(dpi=125).tobytes("png")
            st.image(image, caption=f"Página {page} de {source.filename}", use_container_width=True)
            st.download_button("Baixar documento com a evidência", source.data, source.filename,
                               "application/pdf", key=page_key + "_download")

    if dataset and field_name:
        supporting = dataset.get("ground_truth", {}).get(source.sha256, {}).get("fields", {}).get(field_name, {}).get("supporting_evidence", [])
        if supporting:
            support_open = any(name.startswith("pdf_page_" + key + "_support_") and name.endswith("_open") and value
                               for name, value in st.session_state.items())
            with st.expander("Trechos complementares", expanded=support_open):
                for index, citation in enumerate(supporting):
                    render_evidence({"page_number": citation["source_page"], "excerpt": citation["evidence_excerpt"]},
                                    source, key=key + "_support_" + str(index))


def render_pair(row, candidate, reference, titles, *, key):
    cell = row["candidates"][candidate.sha256]
    columns = st.columns(2)
    columns[0].caption("REFERÊNCIA · " + titles[reference.sha256])
    columns[0].write(value_text(row["reference_value"], row.get("reference_status")))
    columns[1].caption(titles[candidate.sha256])
    columns[1].write(value_text(cell["value"], cell.get("field_status")))
    st.write("**" + classification_text(row, cell) + "**")
    st.write(cell["justification"])
    st.caption("Por que isso importa: " + business_relevance(row))
    viewer_open = any(name.startswith("pdf_page_" + key + "_") and name.endswith("_open") and value
                      for name, value in st.session_state.items())
    with st.expander("Ver evidência · " + row["label"] + " · " + titles[candidate.sha256], expanded=viewer_open):
        evidence_columns = st.columns(2)
        with evidence_columns[0]:
            render_evidence(row["reference_evidence"], reference, key=key + "_reference", field_name=row["field_name"])
        with evidence_columns[1]:
            render_evidence(cell["evidence"], candidate, key=key + "_candidate", field_name=row["field_name"])


def detail_panel(matrix, candidates, documents, reference, titles):
    with st.expander("Revisão humana — opcional", expanded=False):
        st.caption("Confirmar registra a conferência; Precisa de revisão sinaliza o item; Corrigir guarda seu entendimento sem alterar a saída original.")
        fields = [row["field_name"] for row in matrix]
        if st.session_state.get("detail_field") not in fields:
            st.session_state["detail_field"] = fields[0]
        ids = [item.sha256 for item in candidates]
        if st.session_state.get("detail_candidate") not in ids:
            st.session_state["detail_candidate"] = ids[0]
        left, right = st.columns(2)
        field = left.selectbox("Item para revisão", fields, format_func=lambda name: FIELD_LABELS[name], key="detail_field")
        candidate_sha = right.selectbox("Documento para revisão", ids, format_func=lambda sha: titles[sha], key="detail_candidate")
        row = next(item for item in matrix if item["field_name"] == field)
        cell = row["candidates"][candidate_sha]
        candidate = next(item for item in candidates if item.sha256 == candidate_sha)
        render_pair(row, candidate, reference, titles, key="review_detail")
        st.caption("Registro: " + cell["review_status"])
        if cell["review_note"]:
            st.info("Anotação humana: " + cell["review_note"])
        key = cell["review_key"]
        note = st.text_area("Anotação ou correção", value=cell["review_note"], key=f"note_{key}",
                            help="Preserva o valor original e não recalcula a comparação.")
        confirm, review, correct = st.columns(3)
        state = None
        if confirm.button("Confirmar", key=f"confirm_{key}"):
            state = "Confirmado"
        if review.button("Precisa de revisão", key=f"review_{key}"):
            state = "Requer análise"
        if correct.button("Corrigir", key=f"correct_{key}"):
            if not note.strip():
                st.error("Descreva a correção na anotação antes de registrá-la.")
            else:
                state = "Corrigido"
        if state:
            st.session_state["workspace_reviews"][key] = {"status": state, "note": note.strip()}
            st.rerun()


def render_exports(result, matrix, reference, candidates, titles):
    st.subheader("Exportar relatório")
    for candidate in candidates:
        artifacts = result["artifacts"][candidate.sha256]
        st.download_button("Exportar relatório" if len(candidates) == 1 else "Exportar relatório · " + titles[candidate.sha256],
                           artifacts.pdf_path.read_bytes(), artifacts.pdf_path.name, "application/pdf",
                           key=f"download_{candidate.sha256}_PDF")
    with st.expander("Outros formatos", expanded=False):
        st.download_button("Baixar matriz CSV", csv_export(flat_rows(matrix, titles[reference.sha256], candidates, titles)),
                           "InsurMinds_Matriz.csv", "text/csv", key="matrix_csv")
        payload = {"reference_sha": reference.sha256, "candidate_shas": [item.sha256 for item in candidates],
                   "reviews": st.session_state["workspace_reviews"], "note": "Anotações humanas; valores e evidências originais preservados."}
        st.download_button("Baixar revisão JSON", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                           "InsurMinds_Revisao.json", "application/json", key="reviews_json")
        for candidate in candidates:
            artifacts = result["artifacts"][candidate.sha256]
            st.caption(titles[candidate.sha256])
            for column, path, label, mime in zip(st.columns(2),
                (artifacts.markdown_path, artifacts.json_path), ("Markdown", "JSON"), ("text/markdown", "application/json")):
                column.download_button("Baixar " + label, path.read_bytes(), path.name, mime,
                                       key=f"download_{candidate.sha256}_{label}")


def render_sources(result, titles):
    with st.expander("Fontes dos documentos", expanded=False):
        for source in result["documents"]:
            st.write(titles[source.sha256])
            st.caption(source.filename)
            if source.source_url:
                st.link_button("Origem pública · " + titles[source.sha256] + " ↗", source.source_url)
            st.caption("Captura pública: " + (source.access_date or "Não aplicável"))
            st.caption("SHA-256: " + source.sha256)
            st.download_button("Baixar documento-fonte", source.data, source.filename, key=f"original_{source.sha256}")
        st.caption("Condições gerais descrevem um produto; não equivalem a uma apólice individual emitida.")


def render_results(result):
    documents = result["documents"]
    reference_sha = result["reference_sha"]
    reference = next(item for item in documents if item.sha256 == reference_sha)
    candidates = [item for item in documents if item.sha256 != reference_sha]
    reports = result["reports"]
    matrix = build_matrix(reports[reference_sha], [reports[item.sha256] for item in candidates],
                          result["comparisons"], st.session_state["workspace_reviews"])
    titles = document_titles(documents, result["metadata"])
    st.divider()
    st.header("Resumo da comparação")
    st.caption("Referência: " + titles[reference_sha] + " · " + reference.filename)
    stats = result["stats"]
    if stats.get("demo"):
        st.info("Exemplo de demonstração — dados fictícios e wordings reais · sem validade contratual.")
        st.caption("Resultado de demonstração pré-processado. Nenhuma chamada de IA nesta sessão de demonstração.")
    elif stats.get("restored_validated_session"):
        st.caption("Resultados locais aprovados. Nenhuma nova chamada de IA nesta visualização.")
    if result["issues"]:
        st.info("Algumas condições requerem confirmação. Confira os itens sem conclusão segura.")
    tabs = st.tabs(list(RESULT_TABS))
    with tabs[0]:
        counts = comparison_counts(matrix)
        for column, label, value in zip(st.columns(4),
            ("Diferenças identificadas", "Pontos de atenção", "Condições equivalentes", "Sem conclusão segura"),
            (counts["differences"], counts["attention"], counts["equivalent"], counts["insufficient"])):
            column.metric(label, value)
        st.caption("Contagens por condição e comparação com a referência. Pontos de atenção são diferenças que merecem conferência; não há ranking global.")
        important = important_differences(matrix)
        st.subheader("Principais diferenças")
        if not important:
            st.info("Não há diferença relevante sustentada por evidência suficiente para destacar. Confira os itens sem conclusão segura.")
        for row in important[:5]:
            with st.container(border=True):
                st.subheader(row["label"])
                for candidate in candidates:
                    render_pair(row, candidate, reference, titles, key="summary_" + row["field_name"] + "_" + candidate.sha256)
        if len(important) > 5:
            st.caption("As demais diferenças estão nas abas por assunto.")
        with st.expander("Todas as condições comparadas", expanded=False):
            choice = st.radio("Mostrar", ["Todos", "Diferenças", "Sem conclusão segura"], horizontal=True, key="matrix_filter")
            from src.product_presentation import pair_state
            visible = matrix
            if choice == "Diferenças":
                visible = [row for row in matrix if any(pair_state(row, cell) == "different" for cell in row["candidates"].values())]
            elif choice == "Sem conclusão segura":
                visible = [row for row in matrix if any(pair_state(row, cell) == "insufficient" for cell in row["candidates"].values())]
            st.dataframe(flat_rows(visible, titles[reference_sha], candidates, titles), hide_index=True, use_container_width=True)
        render_exports(result, matrix, reference, candidates, titles)
    for tab, group in zip(tabs[1:5], ("Coberturas", "Limites", "Exclusões", "Cláusulas")):
        with tab:
            rows = [row for row in matrix if row["group"] == group]
            st.subheader("Limites & Franquias" if group == "Limites" else group)
            st.dataframe(flat_rows(rows, titles[reference_sha], candidates, titles), hide_index=True, use_container_width=True)
            with st.expander("Entender um item · " + group, expanded=False):
                field = st.selectbox("Condição", [row["field_name"] for row in rows],
                                     format_func=lambda name: FIELD_LABELS[name], key="group_field_" + group)
                row = next(row for row in rows if row["field_name"] == field)
                for candidate in candidates:
                    render_pair(row, candidate, reference, titles, key="group_" + group + "_" + candidate.sha256)
    with tabs[5]:
        st.subheader("Conferir evidências")
        fields = [row["field_name"] for row in matrix]
        field = st.selectbox("Item", fields, format_func=lambda name: FIELD_LABELS[name], key="evidence_field")
        row = next(row for row in matrix if row["field_name"] == field)
        st.caption("Informação não localizada não demonstra ausência contratual.")
        for candidate in candidates:
            render_pair(row, candidate, reference, titles, key="evidence_tab_" + candidate.sha256)
        render_sources(result, titles)
    detail_panel(matrix, candidates, documents, reference, titles)
    with st.expander("Detalhes técnicos", expanded=False):
        render_usage_panel(stats.get("usage_runs", []))
        if stats.get("demo"):
            st.caption("Demo offline: zero requests, zero tokens e nenhum modelo acionado para este resultado.")
        else:
            st.caption("Tempo total: " + display_number(stats.get("duration_seconds"), 1) + " s")
            st.caption("Reutilizações de cache local: " + str(stats.get("cache_hits", 0)))
        with st.expander("Diagnósticos de extração", expanded=False):
            for report in reports.values():
                if report.retrieval_diagnostics:
                    st.caption(report.source_name)
                    st.json({"field_status": report.model_dump(mode="json")["field_status"],
                             "retrieval": report.retrieval_diagnostics})
        with st.expander("Compatibilidade documental", expanded=False):
            for candidate in candidates:
                compatibility = assess_compatibility(result["metadata"][reference_sha], result["metadata"][candidate.sha256])
                st.write(titles[candidate.sha256] + ": " + compatibility.status)
                for reason in compatibility.reasons:
                    st.caption(reason)


def render_legacy(result):
    """Preserve already-created pair results and downloads across a UI migration."""
    st.subheader("Resumo executivo")
    st.write(result["comparison"].executive_summary)
    if result.get("issues"):
        st.warning("Resultado parcial: há cláusulas sem estruturação validada.")
    artifacts = result["artifacts"]
    for column, path, label, mime in zip(st.columns(3),
        (artifacts.markdown_path, artifacts.pdf_path, artifacts.json_path),
        ("Markdown", "PDF", "JSON"), ("text/markdown", "application/pdf", "application/json")):
        column.download_button(f"Baixar {label}", path.read_bytes(), path.name, mime, key=f"legacy_{label}")
    render_usage_panel(result.get("stats", {}).get("usage_runs", []))


initialize()
st.title("INSURMINDS")
st.header("Compare apólices D&O em minutos.")
st.write("Adicione de 2 a 5 documentos, escolha a referência e entenda o que mudou.")
st.button("Nova comparação", key="new_comparison", on_click=new_workspace)

try:
    initial = get_settings(require_api_key=False)
    ingestion = get_ingestion_settings()
    with st.sidebar:
        with st.expander("Configuração avançada", expanded=False):
            selected = st.selectbox("Provedor de IA", ["openai", "groq"],
                                   index=0 if initial.llm_provider == "openai" else 1, key="provider")
            preview = get_settings(require_api_key=False, provider=selected)
            st.caption("Routing de IA: conforme o perfil configurado. Modelos utilizados aparecem em Uso da IA.")
            max_calls = st.number_input("Limite de operações de IA", min_value=1, max_value=50, value=30, step=1, key="max_calls")
            routing_enabled = selected == "openai" and os.getenv("MODEL_ROUTING_ENABLED", "false").strip().lower() in {"true", "1", "yes"}
            if routing_enabled:
                st.caption(f"Perfil controlado: até {max_calls} tentativas HTTP; reservas persistidas e sem retry automático do mesmo modelo.")
            else:
                st.caption(f"Até {max_calls * preview.max_retries} tentativas HTTP, conforme retries configurados.")
            st.caption("Limites não estimam custo nem garantem gratuidade.")
            if st.button("Carregar sessão validada", key="load_validated_session"):
                try:
                    restore_validated_session(ingestion)
                    st.session_state.pop("reference_select", None)
                    st.rerun()
                except (OSError, RuntimeError, ValueError) as error:
                    safely_show_error("sessão validada local", error)
            st.caption("Reutiliza resultados locais aprovados, sem chamadas de IA.")
            if not (preview.openai_api_key if selected == "openai" else preview.groq_api_key):
                st.info("Credencial não configurada. O exemplo de demonstração e resultados aprovados funcionam offline.")
except ConfigurationError as error:
    st.error(str(error))
    st.stop()

intake_result = st.session_state.get("workspace_result")
intake_collapsed = bool(intake_result and intake_result.get("signature") ==
                        workspace_signature(st.session_state["workspace_docs"], st.session_state["workspace_reference"]))
with st.expander("Documentos desta comparação", expanded=False) if intake_collapsed else nullcontext():
    uploaded = st.file_uploader("Adicione de 2 a 5 PDFs ou imagens",
                               type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp"],
                               accept_multiple_files=True, key=f"uploads_{st.session_state['workspace_epoch']}")
    st.caption(f"Até {ingestion.max_document_mb} MB por documento.")
    if uploaded:
        try:
            adapter = UploadSource(ingestion.max_document_mb)
            sources = [adapter.resolve(item.name, item.getvalue()) for item in uploaded]
            unseen = [source for source in sources if all(item.sha256 != source.sha256 for item in st.session_state["workspace_docs"])]
            if unseen:
                _, notices = add_documents(unseen)
                for notice in notices:
                    st.warning(notice)
        except (OSError, RuntimeError, ValueError) as error:
            safely_show_error("recebimento dos documentos", error)

    if st.button("Usar exemplo de demonstração", key="use_demo"):
        try:
            load_demo_workspace()
            st.rerun()
        except (OSError, RuntimeError, ValueError) as error:
            safely_show_error("carregamento da demonstração", error)

    with st.expander("Outras formas de adicionar documentos", expanded=False):
        with st.expander("Catálogo público", expanded=False):
            catalog_id = st.selectbox("Documento público", [item["id"] for item in PUBLIC_CATALOG],
                format_func=lambda identity: next(f"{item['insurer']} · {item['product_name']}" for item in PUBLIC_CATALOG if item["id"] == identity),
                key="catalog_id")
            entry = next(item for item in PUBLIC_CATALOG if item["id"] == catalog_id)
            st.caption(entry.get("version_note", "Condições gerais de produto; não é apólice emitida."))
            refresh_catalog = st.checkbox("Atualizar captura do catálogo", key="refresh_catalog")
            if st.button("Adicionar do catálogo", key="add_catalog"):
                try:
                    source = PublicCatalogSource(PublicURLSource(ingestion.processed_dir, ingestion.max_document_mb)).resolve(catalog_id, refresh=refresh_catalog)
                    added, notices = add_documents([source])
                    if added:
                        st.success("Documento público incluído.")
                    for notice in notices:
                        st.warning(notice)
                except (OSError, RuntimeError, ValueError) as error:
                    safely_show_error("catálogo", error)
        with st.expander("URL pública", expanded=False):
            url = st.text_input("URL pública direta do PDF ou imagem", key="public_url",
                                placeholder="https://seguradora.com.br/condicoes-gerais.pdf")
            refresh_url = st.checkbox("Atualizar captura da URL", key="refresh_url")
            st.caption("Preserva a origem e reutiliza capturas locais. Páginas HTML não são documentos.")
            if st.button("Adicionar URL pública", key="add_url"):
                try:
                    source = PublicURLSource(ingestion.processed_dir, ingestion.max_document_mb).resolve(url, refresh=refresh_url)
                    added, notices = add_documents([source])
                    if added:
                        st.success("Documento público incluído.")
                    for notice in notices:
                        st.warning(notice)
                except (OSError, RuntimeError, ValueError) as error:
                    safely_show_error("download público", error)

    documents = st.session_state["workspace_docs"]
    if st.session_state.get("workspace_demo"):
        st.info("Exemplo de demonstração — dados fictícios e wordings reais · sem validade contratual.")
    if documents:
        identities = [source.sha256 for source in documents]
        if st.session_state["workspace_reference"] not in identities:
            st.session_state["workspace_reference"] = identities[0]
        if st.session_state.get("reference_select") not in identities:
            st.session_state["reference_select"] = st.session_state["workspace_reference"]
        reference_titles = document_titles(documents, {source.sha256: st.session_state["prepared_docs"].get(source.sha256, {}).get("metadata") for source in documents})
        st.selectbox("Documento de referência", identities,
                     format_func=lambda sha: reference_titles[sha],
                     key="reference_select", on_change=change_reference)
        document_cards(documents)
    else:
        st.caption("O primeiro documento será a referência. Você pode alterá-la antes de comparar.")

    reference = st.session_state["workspace_reference"]
    st.caption("A análise assistida usa trechos dos documentos e apoia sua conferência das condições.")
    if st.button("Comparar apólices", type="primary", key="analyze"):
        if len(documents) < 2:
            st.error("Adicione pelo menos dois documentos para comparar.")
        elif len(documents) > 5:
            st.error("Use de dois a cinco documentos para comparar.")
        else:
            prepared = st.session_state["prepared_docs"]
            fully_prepared = all(source.sha256 in prepared for source in documents)
            if fully_prepared or prepare_documents(documents, ingestion, rerun=False):
                analyze_documents(documents, reference, selected, ingestion, max_calls)
                if not intake_collapsed and st.session_state.get("workspace_result"):
                    st.rerun()

result = st.session_state.get("workspace_result")
if result and result["signature"] == workspace_signature(documents, reference):
    render_results(result)
elif st.session_state.get("comparison_result"):
    render_legacy(st.session_state["comparison_result"])
elif st.session_state.get("workspace_usage_runs"):
    with st.expander("Detalhes técnicos", expanded=False):
        render_usage_panel([])
