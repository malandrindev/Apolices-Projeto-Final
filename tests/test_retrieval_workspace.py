"""Offline propagation of retrieval uncertainty through storage and the UI."""
from tempfile import TemporaryDirectory
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from src.agents.extraction import Phase2Report
from src.schemas.policy import FieldEvidence, PolicyExtraction, NOT_FOUND
from src.schemas.retrieval import FieldStatus
from src.schemas.comparison import DifferenceClass, PolicyComparison, PolicyDifference, ComparisonCitation
from src.storage.sqlite_repo import SqliteRepository
from src.workspace import build_matrix
from tests.test_interface import fixture_documents, ui_patches, button, selectbox

def report(letter, status, value=NOT_FOUND):
    policy = PolicyExtraction()
    if value != NOT_FOUND:
        policy.side_a = FieldEvidence(valor=value, pagina=1, trecho_origem=value, confianca=.9)
    return Phase2Report(source_name=letter+".pdf", sha256=letter*64, clause_count=0,
        policy=policy, field_status={"side_a": status},
        retrieval_diagnostics={"strategy":"optimized", "full_local_corpus_retained":True})

@pytest.mark.parametrize("status", [FieldStatus.NOT_RETRIEVED, FieldStatus.AMBIGUOUS])
@pytest.mark.parametrize("value", [NOT_FOUND, "Conditional wording"])
def test_uncertainty_overrides_missing_or_advantage_in_matrix(status, value):
    a,b=report("a",status,value),report("b",FieldStatus.FOUND,"Covered")
    fake_difference=PolicyDifference(field_name="side_a", label="Side A",
        value_a=value,value_b="Covered",classification=DifferenceClass.MORE_FAVORABLE_B,
        justification="An old comparison result must not order an inconclusive field.",
        citation_a=ComparisonCitation(source_name=a.source_name,page_number=None,excerpt=NOT_FOUND),
        citation_b=ComparisonCitation(source_name=b.source_name,page_number=1,excerpt="Covered"))
    comparison=PolicyComparison(source_a=a.source_name,source_b=b.source_name,document_id_a=a.sha256,
        document_id_b=b.sha256,compared_at="2026-10-04T00:00:00Z",differences=[fake_difference],executive_summary="Test")
    row=next(r for r in build_matrix(a,[b],[comparison]) if r["field_name"]=="side_a")
    assert row["reference_status"]==status.value
    assert row["candidates"][b.sha256]["classification"]==DifferenceClass.NOT_COMPARABLE.value
    assert row["reference_evidence"]["field_status"]==status.value

def test_both_missing_with_incomplete_retrieval_is_not_both_absent():
    a,b=report("a",FieldStatus.NOT_RETRIEVED),report("b",FieldStatus.NOT_FOUND)
    row=next(r for r in build_matrix(a,[b],[]) if r["field_name"]=="side_a")
    assert row["candidates"][b.sha256]["classification"]==DifferenceClass.NOT_COMPARABLE.value

def test_sqlite_roundtrip_preserves_status_and_diagnostics(tmp_path):
    a=report("a",FieldStatus.NOT_RETRIEVED)
    repo=SqliteRepository(tmp_path/"policies.sqlite3")
    repo.save_document(a,[],[])
    assert repo.get_document(a.sha256)==a
    legacy=a.model_dump(mode="json")
    legacy.pop("field_status");legacy.pop("retrieval_diagnostics")
    parsed=Phase2Report.model_validate(legacy)
    assert parsed.field_status=={} and parsed.retrieval_diagnostics=={}

def test_ui_shows_inconclusive_state_and_keeps_review_offline():
    documents,processed,reports=fixture_documents(2)
    for source in documents:
        reports[source.sha256].field_status["side_a"]=FieldStatus.NOT_RETRIEVED
        reports[source.sha256].retrieval_diagnostics={"strategy":"optimized","full_local_corpus_retained":True}
    calls=[]
    with TemporaryDirectory() as directory,ui_patches(directory,processed,reports,calls)[0]:
        app=AppTest.from_file("interface/app.py").run(timeout=20)
        app.session_state["workspace_docs"]=documents
        app.session_state["workspace_reference"]=documents[0].sha256
        app.run()
        button(app,"analyze").click().run(timeout=20)
        selectbox(app,"detail_field").set_value("side_a").run()
        assert not app.exception
        assert any("Não localizado com segurança" in i.value for i in app.markdown)
        assert any("não demonstra ausência contratual" in i.value for i in app.info)
        before=len(calls)
        key=f"{documents[0].sha256}:{documents[1].sha256}:side_a"
        button(app,f"review_{key}").click().run()
        assert not app.exception and len(calls)==before
