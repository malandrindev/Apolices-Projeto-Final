"""Offline UI regression for a dynamic document workspace and persistent reviews."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
import pytest
from streamlit.testing.v1 import AppTest

from src.agents.comparison import ComparisonAgent
from src.agents.extraction import Phase2Report
from src.agents.ocr import PageText, ProcessedDocument
from src.agents.report import ComparisonReportAgent
from src.config import IngestionSettings, Settings
from src.schemas.policy import FieldEvidence, PolicyExtraction
from src.sources import SourceDocument, SourceError, UploadSource
from src.workspace import infer_metadata


def button(app, key):
    return next(item for item in app.button if item.key == key)


def selectbox(app, key):
    return next(item for item in app.selectbox if item.key == key)


def test_startup_missing_uploads_and_provider_change():
    app = AppTest.from_file("interface/app.py").run(timeout=20)
    assert not app.exception and app.title[0].value == "INSURMINDS"
    button(app, "analyze").click().run()
    assert not app.exception and "pelo menos dois documentos" in app.error[0].value
    selectbox(app, "provider").set_value("groq").run()
    assert not app.exception and selectbox(app, "provider").value == "groq"


def test_report_downloads_survive_rerun():
    with TemporaryDirectory() as temporary:
        a = Phase2Report(source_name="synthetic-a.pdf", sha256="a" * 64, clause_count=0, policy=PolicyExtraction())
        b = Phase2Report(source_name="synthetic-b.pdf", sha256="b" * 64, clause_count=0, policy=PolicyExtraction())
        comparison = ComparisonAgent(gateway=None, model_strong="unused").compare(a, b)
        artifacts = ComparisonReportAgent().generate(comparison, Path(temporary))
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        app.session_state["comparison_result"] = {"comparison": comparison, "artifacts": artifacts, "stats": {"calls": 0}, "issues": 0}
        app.run()
        assert not app.exception and len(app.get("download_button")) == 3
        app.run()
        assert not app.exception and len(app.get("download_button")) == 3


def fixture_documents(count):
    documents, processed, reports = [], {}, {}
    for index in range(count):
        amount = 10000000 - index * 1000000
        insurer = f"Seguradora Exemplo {index + 1}"
        text = f"Seguradora: {insurer}\nProduto: Seguro D&O\nCONDIÇÕES GERAIS\nMoeda: BRL\nLimite: BRL {amount}\n"
        pdf = pymupdf.open()
        pdf.new_page().insert_text((40, 50), text)
        data = pdf.tobytes()
        pdf.close()
        source = UploadSource().resolve(f"documento-{index + 1}.pdf", data)
        documents.append(source)
        processed[source.sha256] = ProcessedDocument(
            source_name=source.filename, sha256=source.sha256, size_bytes=len(data),
            media_type="application/pdf", pages=[PageText(page_number=1, text=text, extraction_method="native")],
            processed_at="2026-10-03T12:00:00Z", cache_key=source.sha256, cache_hit=True,
        )
        def evidence(value, excerpt):
            return FieldEvidence(valor=value, pagina=1, trecho_origem=excerpt, confianca=.9)
        reports[source.sha256] = Phase2Report(
            source_name=source.filename, sha256=source.sha256, clause_count=1,
            policy=PolicyExtraction(
                seguradora=evidence(insurer, f"Seguradora: {insurer}"),
                moeda=evidence("BRL", "Moeda: BRL"),
                limite_maximo_garantia=evidence(f"BRL {amount}", f"Limite: BRL {amount}"),
            ),
        )
    return documents, processed, reports


class OfflineGateway:
    provider = "openai"
    def __init__(self):
        self.events = []
        self.calls = 0

    @property
    def usage(self):
        return {"calls": self.calls, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": None}

    def complete(self, **kwargs):
        self.calls += 1
        return "{}"


def ui_patches(directory, processed, reports, structured_calls, *, fail=False):
    settings = Settings(groq_api_key="dummy", openai_api_key="dummy", llm_provider="openai",
                        model_fast="offline-fast", model_strong="offline-strong", model_vision="unused",
                        temperature=0, max_tokens=100, timeout_seconds=1, max_retries=1)
    ingestion = IngestionSettings(30, "por+eng", 20, 5, 200, Path(directory))
    gateway = OfflineGateway()

    def local(path, **kwargs):
        import hashlib
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        document = processed[digest]
        callback = kwargs.get("ocr_progress_callback")
        if callback:
            callback(1, 1, "native")
        return SimpleNamespace(sha256=digest), document

    def structured(path, **kwargs):
        info, document = local(path, **kwargs)
        structured_calls.append(document.sha256)
        if fail:
            raise RuntimeError("Quota esgotada. Confira Usage/limites do projeto.")
        kwargs["gateway"].complete(model="offline-fast", agent="extraction", messages=[])
        kwargs["progress_callback"](1, 1, "clause-1", True)
        report = reports[document.sha256].model_copy(update={"source_name": Path(path).name})
        return info, document, report, Path(directory) / "unused.json", [SimpleNamespace()]

    stack = ExitStack()
    stack.enter_context(patch("src.config.get_settings", side_effect=lambda **kwargs: replace(settings, llm_provider=kwargs.get("provider", "openai"))))
    stack.enter_context(patch("src.config.get_ingestion_settings", return_value=ingestion))
    stack.enter_context(patch("src.llm.providers.get_gateway", return_value=gateway))
    stack.enter_context(patch("src.pipeline.process_document", side_effect=local))
    stack.enter_context(patch("src.pipeline.process_and_structure_document", side_effect=structured))
    return stack, gateway


@pytest.mark.parametrize("count", [2, 3, 5])
def test_reference_workspace_analysis_review_and_exports_without_reinference(count):
    documents, processed, reports = fixture_documents(count)
    calls = []
    with TemporaryDirectory() as directory, ui_patches(directory, processed, reports, calls)[0]:
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        app.session_state["workspace_docs"] = documents
        app.session_state["workspace_reference"] = documents[0].sha256
        app.run()
        assert not app.exception
        assert calls == []
        assert not any(item.key == "prepare" for item in app.button)
        assert not any(item.key == "consent" for item in app.checkbox)
        button(app, "analyze").click().run(timeout=20)
        assert len(app.session_state["prepared_docs"]) == count
        assert not app.exception
        result = app.session_state["workspace_result"]
        assert len(result["comparisons"]) == count - 1
        assert set(calls) == {source.sha256 for source in documents}
        assert len(calls) == count
        assert all(item.document_id_a == documents[0].sha256 for item in result["comparisons"])
        result_tabs = [item.label for item in app.tabs][-6:]
        assert result_tabs == ["Resumo", "Coberturas", "Limites & Franquias", "Exclusões", "Cláusulas", "Evidências"]
        assert len(app.get("download_button")) == 2 + 3 * (count - 1) + count
        selectbox(app, "detail_field").set_value("limite_maximo_garantia").run()
        assert not app.exception
        candidate_sha = documents[1].sha256
        key = f"{documents[0].sha256}:{candidate_sha}:limite_maximo_garantia"
        button(app, f"review_{key}").click().run()
        assert not app.exception
        assert app.session_state["workspace_reviews"][key]["status"] == "Requer análise"
        button(app, f"confirm_{key}").click().run()
        assert app.session_state["workspace_reviews"][key]["status"] == "Confirmado"
        app.run()
        assert not app.exception and len(calls) == count
        assert len(app.get("download_button")) == 2 + 3 * (count - 1) + count
        button(app, f"reference_{documents[1].sha256}").click().run()
        assert not app.exception and app.session_state["workspace_result"] is None
        assert app.session_state["workspace_reviews"] == {}
        assert len(calls) == count


def test_public_url_and_catalog_addition_deduplicate_limit_and_safe_errors():
    documents, processed, reports = fixture_documents(6)
    calls = []
    with TemporaryDirectory() as directory, ui_patches(directory, processed, reports, calls)[0]:
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        source = replace(documents[0], origin="public_url", source_url="https://example.com/documento.pdf", access_date="2026-10-03")
        next(item for item in app.text_input if item.key == "public_url").set_value("https://example.com/documento.pdf").run()
        with patch("src.sources.PublicURLSource.resolve", return_value=source):
            button(app, "add_url").click().run()
            assert not app.exception and len(app.session_state["workspace_docs"]) == 1
            button(app, "add_url").click().run()
            assert not app.exception and len(app.session_state["workspace_docs"]) == 1
            assert any("duplicata" in item.value for item in app.warning)
        with patch("src.sources.PublicCatalogSource.resolve", return_value=replace(documents[1], origin="catalog", metadata={"trusted_catalog": True, "insurer": "Catálogo Exemplo"})):
            button(app, "add_catalog").click().run()
            assert not app.exception and len(app.session_state["workspace_docs"]) == 2
        app.session_state["workspace_docs"] = documents[:5]
        app.run()
        with patch("src.sources.PublicURLSource.resolve", return_value=documents[5]):
            button(app, "add_url").click().run()
            assert not app.exception and len(app.session_state["workspace_docs"]) == 5
            assert any("máximo cinco" in item.value for item in app.warning)
        with patch("src.sources.PublicURLSource.resolve", side_effect=SourceError("URL pública recusada; use upload.")):
            button(app, "add_url").click().run()
            assert not app.exception and "URL pública recusada" in app.error[0].value
        assert calls == []


def test_extraction_failure_keeps_local_preparation_and_shows_no_result():
    documents, processed, reports = fixture_documents(2)
    calls = []
    with TemporaryDirectory() as directory, ui_patches(directory, processed, reports, calls, fail=True)[0]:
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        app.session_state["workspace_docs"] = documents
        app.session_state["workspace_reference"] = documents[0].sha256
        app.session_state["prepared_docs"] = {source.sha256: {"processed": processed[source.sha256],
            "metadata": infer_metadata(processed[source.sha256]), "clause_count": 1} for source in documents}
        app.run()
        button(app, "analyze").click().run()
        assert not app.exception and "Quota esgotada" in app.error[0].value
        assert app.session_state["workspace_result"] is None
        assert len(app.session_state["prepared_docs"]) == 2
        assert len(calls) == 1
        assert selectbox(app, "provider").value == "openai"

def app_definition(name):
    # Exercise the small adapter/export boundary without executing a Streamlit app.
    import ast
    import csv
    import io
    tree = ast.parse(Path("interface/app.py").read_text(encoding="utf-8"))
    node = next(item for item in tree.body if getattr(item, "name", None) == name)
    namespace = {"csv": csv, "io": io}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "interface/app.py", "exec"), namespace)
    return namespace[name]


def test_budget_wrapper_preserves_provider_cache_identity_and_bounds_calls():
    from src.agents._evidence import provider_identity
    bounded = app_definition("BoundedLogicalGateway")
    openai = OfflineGateway()
    groq = OfflineGateway()
    groq.provider = "groq"
    wrapper = bounded(openai, 2)
    assert provider_identity(wrapper) == provider_identity(openai)
    assert provider_identity(bounded(groq, 2)) == provider_identity(groq)
    assert provider_identity(wrapper) != provider_identity(bounded(groq, 2))
    assert wrapper.usage is not None and wrapper.events is openai.events
    wrapper.complete(model="offline", messages=[], agent="test")
    wrapper.complete(model="offline", messages=[], agent="test")
    with pytest.raises(RuntimeError, match="Limite de opera"):
        wrapper.complete(model="offline", messages=[], agent="test")
    assert openai.calls == wrapper.logical_calls == 2


def test_matrix_csv_neutralizes_formulas_in_values_and_document_headers():
    import csv
    import io
    export = app_definition("csv_export")
    payload = export([{"=documento": "=SUM(1,2)", "Item": "normal", "Outro": "  @formula"}])
    row = next(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    assert row["'=documento"] == "'=SUM(1,2)"
    assert row["Item"] == "normal"
    assert row["Outro"] == "'  @formula"


def test_public_source_filename_collision_preserves_each_origin_url():
    documents, processed, reports = fixture_documents(3)
    sources = [
        replace(documents[0], filename="base.pdf", origin="public_url",
                source_url="https://example.com/first.pdf"),
        replace(documents[1], filename=f"base-{documents[2].sha256[:12]}.pdf",
                origin="public_url", source_url="https://example.com/second.pdf"),
        replace(documents[2], filename="base.pdf", origin="public_url",
                source_url="https://example.com/third.pdf"),
    ]
    calls = []
    with TemporaryDirectory() as directory, ui_patches(directory, processed, reports, calls)[0]:
        app = AppTest.from_file("interface/app.py").run(timeout=20)
        app.session_state["workspace_docs"] = sources[:2]
        app.session_state["workspace_reference"] = sources[0].sha256
        app.run()
        next(item for item in app.text_input if item.key == "public_url").set_value(sources[2].source_url).run()
        with patch("src.sources.PublicURLSource.resolve", return_value=sources[2]):
            button(app, "add_url").click().run()
        assert not app.exception
        stored = app.session_state["workspace_docs"]
        assert len(stored) == 3
        assert len({source.filename for source in stored}) == 3
        origins_by_name = {source.filename: source.source_url for source in stored}
        expected_urls = {source.sha256: source.source_url for source in sources}
        assert len(origins_by_name) == 3
        for source in stored:
            assert origins_by_name[source.filename] == expected_urls[source.sha256]
        assert stored[0].filename == sources[0].filename
        assert stored[1].filename == sources[1].filename
        assert calls == []
