"""Meaningful offline contracts for complete, deterministic local retrieval."""
import hashlib
import json
from unittest import TestCase
from unittest.mock import patch

from src.agents.ocr import PageText, ProcessedDocument
from src.agents.segmentation import SegmentationAgent
from src.retrieval.local import CRITICAL_FIELDS, FIELD_GROUPS, LocalRetrievalIndex
from src.schemas.clause import ClauseCategory, ClauseChunk, SourcePage
from src.schemas.policy import PolicyExtraction


def fixture(texts):
    document = ProcessedDocument(source_name="synthetic.pdf", sha256=hashlib.sha256("\n".join(texts).encode()).hexdigest(), size_bytes=100,
        media_type="application/pdf", pages=[PageText(page_number=i + 1, text=text, extraction_method="native") for i, text in enumerate(texts)],
        processed_at="2026-10-04T00:00:00+00:00", cache_key="local-test")
    return document, SegmentationAgent(gateway=None).segment(document)


def single_chunks(texts):
    document, _ = fixture(texts)
    return document, [ClauseChunk(clause_id=f"c{i}", title="Trecho", category=ClauseCategory.UNKNOWN,
        page_start=i, page_end=i, text=text, source_pages=[SourcePage(page_number=i, text=text)]) for i, text in enumerate(texts, 1)]


class LocalRetrievalTests(TestCase):
    def test_seven_groups_cover_exact_schema_once(self):
        fields = [field for group in FIELD_GROUPS.values() for field in group]
        self.assertEqual(len(FIELD_GROUPS), 7)
        self.assertEqual(len(fields), 27)
        self.assertEqual(len(set(fields)), 27)
        self.assertEqual(set(fields), set(PolicyExtraction.model_fields))
        self.assertTrue(CRITICAL_FIELDS <= set(fields))

    def test_retains_original_pages_and_chunks_even_when_plan_capped(self):
        doc, chunks = single_chunks(["Franquia indicada no contrato.", "Franquia segundo contrato.", "Texto sem dados relevantes."])
        index = LocalRetrievalIndex.build(doc, chunks)
        plan = index.plan("LIMITS", max_candidates=1)
        self.assertEqual(index.pages, tuple(doc.pages))
        self.assertEqual(index.chunks, tuple(chunks))
        self.assertIs(index.chunks[0], chunks[0])
        self.assertEqual(len(plan.candidates), 1)
        self.assertTrue(plan.field_diagnostics["retencao_franquia"].overflow)
        self.assertTrue(index.summary()["full_local_corpus_retained"])

    def test_heading_routing_and_exact_provenance(self):
        doc, chunks = fixture(["AMBITO GEOGRAFICO\nA cobertura territorial abrange o Brasil."])
        plan = LocalRetrievalIndex.build(doc, chunks).plan("SCOPE")
        candidate = next(c for c in plan.candidates if "territorialidade" in c.fields)
        self.assertIn("heading", candidate.methods)
        self.assertIs(candidate.chunk, chunks[0])
        self.assertEqual(candidate.source_pages[0].page_number, 1)
        self.assertIn(candidate.source_pages[0].text, doc.pages[0].text)

    def test_synonym_quotes_linebreak_and_letter_boundaries(self):
        doc, chunks = single_chunks(["Cobertura adicional e acessoria.", "Cobertura \u201cB\u201d\nReembolso a sociedade.", "Cobertura \u201cA\u201d\nIndenizacao ao segurado."])
        index = LocalRetrievalIndex.build(doc, chunks)
        plan = index.plan("CORE_COVERAGES")
        b = next(c for c in plan.candidates if c.chunk_id == "c2")
        a = next(c for c in plan.candidates if c.chunk_id == "c3")
        self.assertIn("side_b", b.fields)
        self.assertIn("side_a", a.fields)
        self.assertIn("cobertura b", b.matched_terms)
        self.assertFalse(any("side_a" in c.fields for c in plan.candidates if c.chunk_id == "c1"))

    def test_bm25_scores_without_exact_multiword_phrase(self):
        doc, chunks = single_chunks(["Territorio brasileiro e regras de ambito para atos no exterior."])
        candidate = LocalRetrievalIndex.build(doc, chunks).plan("SCOPE").candidates[0]
        self.assertIn("bm25", candidate.methods)
        self.assertGreater(candidate.score, 0)

    def test_critical_rare_field_gets_own_quota(self):
        doc, chunks = single_chunks([f"Limite maximo de garantia e LMG clausula{i}." for i in range(30)] + ["Sublimites especificos desta cobertura."])
        plan = LocalRetrievalIndex.build(doc, chunks).plan("LIMITS", per_field_limit=1)
        self.assertTrue(any(c.page_start == 31 and "sublimites" in c.fields for c in plan.candidates))
        self.assertGreaterEqual(plan.field_diagnostics["limite_maximo_garantia"].selected_count, 4)

    def test_candidate_is_deduplicated_across_methods_and_fields(self):
        doc, chunks = fixture(["FORO E AMBITO GEOGRAFICO\nForo do segurado e territorio brasileiro."])
        plan = LocalRetrievalIndex.build(doc, chunks).plan("SCOPE")
        self.assertEqual(len({c.chunk_id for c in plan.candidates}), len(plan.candidates))
        self.assertTrue(any(set(c.fields) == {"jurisdicao_lei", "territorialidade"} for c in plan.candidates))

    def test_neighbor_expansion_preserves_context_and_is_selective(self):
        doc, chunks = single_chunks(["Introducao da clausula.", "Territorialidade definida abaixo.", "Continua em todos os paises.", "Foro competente no Brasil.", "Outro assunto."])
        index = LocalRetrievalIndex.build(doc, chunks)
        initial = index.plan("SCOPE")
        expanded = index.expand(initial, fields=["territorialidade"], stage=2)
        self.assertTrue({c.chunk_id for c in initial.candidates} <= {c.chunk_id for c in expanded.candidates})
        self.assertTrue(any(c.page_start == 3 and "page_neighbor" in c.methods for c in expanded.candidates))
        self.assertEqual(expanded.field_diagnostics["jurisdicao_lei"], initial.field_diagnostics["jurisdicao_lei"])
        full = index.expand(expanded, fields=["territorialidade"], stage=4)
        self.assertTrue({c.chunk_id for c in expanded.candidates} <= {c.chunk_id for c in full.candidates})

    def test_configured_expanded_quota_persists(self):
        doc, chunks = single_chunks([f"Foro competente no local{i}." for i in range(20)])
        index = LocalRetrievalIndex.build(doc, chunks)
        expanded = index.expand(index.plan("SCOPE"), fields=["jurisdicao_lei"], stage=1, per_field_limit=6)
        self.assertEqual(expanded.field_diagnostics["jurisdicao_lei"].selected_count, 6)
        self.assertEqual(expanded.expanded_per_field_limit, 6)

    def test_missing_initial_candidates_are_not_retrieved(self):
        doc, chunks = single_chunks(["Apenas texto administrativo diverso."])
        plan = LocalRetrievalIndex.build(doc, chunks).plan("LIMITS")
        diagnostic = plan.field_diagnostics["sublimites"]
        self.assertEqual(diagnostic.status, "NOT_RETRIEVED")
        self.assertFalse(diagnostic.full_search)

    def test_not_found_only_after_full_specific_search(self):
        doc, chunks = single_chunks(["Apenas texto administrativo diverso."])
        index = LocalRetrievalIndex.build(doc, chunks)
        plan = index.expand(index.plan("LIMITS"), fields=["sublimites"], stage=4)
        diagnostic = plan.field_diagnostics["sublimites"]
        self.assertEqual(diagnostic.status, "NOT_FOUND")
        self.assertTrue(diagnostic.full_search)
        self.assertEqual(diagnostic.searched_pages, (1,))
        self.assertEqual(diagnostic.searched_chunks, 1)
        self.assertEqual(plan.field_diagnostics["retencao_franquia"].status, "NOT_RETRIEVED")

    def test_empty_ocr_is_not_contractual_not_found(self):
        doc, chunks = fixture([""])
        diagnostic = LocalRetrievalIndex.build(doc, chunks).plan("LIMITS", stage=4).field_diagnostics["sublimites"]
        self.assertEqual(diagnostic.status, "NOT_RETRIEVED")
        self.assertEqual(diagnostic.no_hit_reason, "empty_extracted_text")

    def test_page_hit_outside_incomplete_chunks_is_not_found_proof(self):
        doc, chunks = single_chunks(["Introducao apenas.", "Sublimites indicados neste anexo."])
        diagnostic = LocalRetrievalIndex.build(doc, chunks[:1]).plan("LIMITS", stage=4).field_diagnostics["sublimites"]
        self.assertEqual(diagnostic.status, "NOT_RETRIEVED")
        self.assertFalse(diagnostic.full_search)
        self.assertTrue(diagnostic.limited)

    def test_phrase_split_between_fragments_is_diagnosed(self):
        doc, _ = fixture(["Side C"])
        chunks = [ClauseChunk(clause_id="left", title="Trecho", category=ClauseCategory.UNKNOWN, page_start=1, page_end=1,
            text="Side ", source_pages=[SourcePage(page_number=1, text="Side ")]),
            ClauseChunk(clause_id="right", title="Trecho", category=ClauseCategory.UNKNOWN, page_start=1, page_end=1,
            text="C", source_pages=[SourcePage(page_number=1, text="C")])]
        diagnostic = LocalRetrievalIndex.build(doc, chunks).plan("CORE_COVERAGES", stage=4).field_diagnostics["side_c"]
        self.assertFalse(diagnostic.full_search)
        self.assertTrue(diagnostic.limited)

    def test_invalid_provenance_or_duplicate_ids_rejected(self):
        doc, chunks = single_chunks(["Foro real."])
        altered = chunks[0].model_copy(update={"text": "Texto inventado."})
        with self.assertRaises(ValueError):LocalRetrievalIndex.build(doc, [altered])
        with self.assertRaises(ValueError):LocalRetrievalIndex.build(doc, chunks + chunks)

    def test_repeatable_and_no_provider_or_network_needed(self):
        doc, chunks = fixture(["EXCLUSOES\nAtos dolosos e poluicao.", "FORO\nDomicilio do segurado."])
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            index = LocalRetrievalIndex.build(doc, chunks)
            first = json.dumps(index.plan("EXCLUSIONS").summary(), sort_keys=True)
            second = json.dumps(LocalRetrievalIndex.build(doc, chunks).plan("EXCLUSIONS").summary(), sort_keys=True)
        self.assertEqual(first, second)

    def test_large_section_expansion_is_explicitly_bounded(self):
        doc, chunks = single_chunks(["Franquia definida." for _ in range(12)])
        diagnostic = LocalRetrievalIndex.build(doc, chunks).plan("LIMITS", stage=3).field_diagnostics["retencao_franquia"]
        self.assertTrue(diagnostic.overflow)
        self.assertTrue(diagnostic.limited)
        self.assertFalse(diagnostic.full_search)

    def test_invalid_limits_fields_and_foreign_plans_rejected(self):
        doc, chunks = single_chunks(["Foro real."])
        index = LocalRetrievalIndex.build(doc, chunks)
        for kwargs in [{"per_field_limit":0}, {"stage":5}, {"max_candidates":0}]:
            with self.assertRaises(ValueError):index.plan("SCOPE", **kwargs)
        with self.assertRaises(ValueError):index.expand(index.plan("SCOPE"), fields=["premio"])
        other_doc, other_chunks = single_chunks(["Fonte distinta."])
        with self.assertRaises(ValueError):index.expand(LocalRetrievalIndex.build(other_doc, other_chunks).plan("SCOPE"))

    def test_cover_company_name_preserved_without_generic_keyword(self):
        doc, chunks = single_chunks(["NORTHSTAR\n15414.123456/2025-00", "Seguradora assume as obrigacoes previstas."])
        plan = LocalRetrievalIndex.build(doc, chunks).plan("IDENTIFICATION")
        cover = next(c for c in plan.candidates if c.page_start == 1)
        self.assertIn("seguradora", cover.fields)
        self.assertIn("document_cover", cover.methods)
        self.assertIs(cover.chunk, chunks[0])

    def test_company_in_contract_introduction_found_by_generic_fallback(self):
        doc, chunks = single_chunks(["Introducao neutra.", "Este contrato de seguro e emitido por Northstar."])
        index = LocalRetrievalIndex.build(doc, chunks)
        initial = index.plan("IDENTIFICATION")
        self.assertFalse(any(c.page_start == 2 and "seguradora" in c.fields for c in initial.candidates))
        expanded = index.expand(initial, fields=["seguradora"], stage=4)
        self.assertTrue(any(c.page_start == 2 and "seguradora" in c.fields and "fallback" in c.methods for c in expanded.candidates))

    def test_selective_fields_can_progress_independently(self):
        doc, chunks = single_chunks(["Seguradora e premio definidos."])
        index = LocalRetrievalIndex.build(doc, chunks)
        insurer = index.expand(index.plan("IDENTIFICATION"), fields=["seguradora"], stage=4)
        premium = index.expand(insurer, fields=["premio"], stage=1)
        self.assertEqual(premium.stage, 4)
        self.assertEqual(premium.field_diagnostics["seguradora"].stage, 4)
        self.assertEqual(premium.field_diagnostics["premio"].stage, 1)
        with self.assertRaises(ValueError):index.expand(premium, fields=["seguradora"], stage=3)
