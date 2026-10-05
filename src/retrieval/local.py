"""Deterministic local evidence retrieval. The complete source corpus is retained.

No provider, embeddings, network, model downloads or persistent cache are used.
Retrieval candidates are source material, not verified contractual conclusions.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Sequence

from src.agents.ocr import PageText, ProcessedDocument
from src.schemas.clause import ClauseChunk, SourcePage
from src.retrieval.groups import FIELD_GROUPS

RETRIEVAL_VERSION = "local-bm25-fields-v1"
CRITICAL_FIELDS = frozenset(("limite_maximo_garantia", "sublimites", "retencao_franquia", "side_a", "side_b", "side_c", "custos_defesa", "data_retroativa", "territorialidade", "jurisdicao_lei", "exclusoes"))
# Each field has its own quota. Common headings cannot displace a rare field.
_ALIASES: dict[str, tuple[str, ...]] = {
    "seguradora": ("seguradora", "companhia de seguros", "seguros", "susep", "processo susep"),
    "numero_apolice": ("numero da apolice", "numero apolice", "apolice numero", "apolice n"),
    "tomador_segurado": ("tomador", "segurado", "sociedade", "contratante"),
    "vigencia_inicio": ("inicio de vigencia", "inicio da vigencia", "vigencia", "periodo de vigencia"),
    "vigencia_fim": ("fim de vigencia", "termino de vigencia", "vigencia", "periodo de vigencia"),
    "moeda": ("moeda", "real brasileiro", "reais", "brl", "usd", "eur", "dolares"),
    "premio": ("premio", "pagamento do premio", "premio total"),
    "limite_maximo_garantia": ("limite maximo de garantia", "limite maximo de responsabilidade", "limite de responsabilidade", "lmg", "lmi", "limite agregado"),
    "sublimites": ("sublimite", "sublimites", "sub limite", "sub limites"),
    "retencao_franquia": ("franquia", "retencao", "participacao obrigatoria", "pos"),
    "side_a": ("side a", "cobertura a", "indenizacao ou reembolso ao segurado", "perdas nao indenizaveis", "nao indenizaveis"),
    "side_b": ("side b", "cobertura b", "reembolso a sociedade", "reembolso a empresa", "indenizacao a sociedade"),
    "side_c": ("side c", "cobertura c", "valores mobiliarios", "mercado de capitais", "mercados de capitais", "reclamacoes contra a sociedade"),
    "custos_defesa": ("custos de defesa", "custo de defesa", "despesas de defesa", "honorarios advocaticios"),
    "controle_defesa": ("controle da defesa", "controle de defesa", "conducao da defesa", "defesa", "advogados"),
    "consentimento_acordo": ("consentimento", "acordo", "anuencia", "autorizacao previa"),
    "rateio": ("rateio", "alocacao", "perdas cobertas e nao cobertas"),
    "base_cobertura": ("base de reclamacoes", "base de reclamacao", "claims made", "notificacao", "fato gerador"),
    "data_retroativa": ("data retroativa", "data limite de retroatividade", "retroatividade", "periodo de retroatividade"),
    "periodo_estendido_notificacao": ("prazo complementar", "prazo suplementar", "periodo complementar", "periodo suplementar", "prazo adicional"),
    "prazo_aviso_sinistro": ("aviso de sinistro", "notificacao de reclamacao", "comunicacao de sinistro", "reclamacao e caracterizacao do sinistro", "aviso", "notificacao"),
    "jurisdicao_lei": ("jurisdicao", "foro", "legislacao aplicavel", "lei aplicavel", "domicilio do segurado"),
    "territorialidade": ("territorialidade", "ambito geografico", "territorio", "qualquer lugar", "mundial"),
    "exclusoes": ("exclusoes", "riscos excluidos", "excluido", "excluidos", "atos dolosos", "poluicao", "nao cobre"),
    "extensoes_cobertura": ("extensoes de cobertura", "extensao de cobertura", "cobertura adicional", "coberturas adicionais", "condicao particular", "condicoes particulares"),
    "cancelamento_renovacao": ("cancelamento", "renovacao", "rescisao", "nao renovacao"),
    "definicoes_relevantes": ("definicoes", "glossario", "conceitos", "definicao"),
}
_FALLBACK: dict[str, tuple[str, ...]] = {
    "seguradora": ("contrato de seguro", "disposicoes preliminares", "apresentacao"),
    "side_a": ("responsabilidade dos administradores", "nao reembolsaveis", "indenizacao ao segurado"),
    "side_b": ("reembolsar a sociedade", "reembolsar a empresa", "ressarcimento a sociedade"),
    "side_c": ("titulos e valores", "securities", "companhias abertas", "entidade segurada"),
    "custos_defesa": ("assistencia juridica", "custeio de defesa", "custas judiciais", "defesa do segurado"),
    "controle_defesa": ("escolha de advogado", "nomeacao de advogado", "assumir a defesa"),
    "retencao_franquia": ("participacoes obrigatorias", "dedutivel", "participacao do segurado"),
    "sublimites": ("limite adicional", "limites adicionais", "limite por cobertura", "limites por cobertura"),
    "territorialidade": ("cobertura territorial", "fora do brasil", "exterior", "todo o mundo"),
    "jurisdicao_lei": ("tribunais", "arbitragem", "legislacao brasileira", "foro competente"),
    "data_retroativa": ("anterior a vigencia", "antes da vigencia", "data retro", "retroativa"),
    "exclusoes": ("nao se aplica", "nao sera responsavel", "nao estao cobertas", "exclusao"),
    "numero_apolice": ("apolice",),
    "vigencia_inicio": ("inicio", "vigente", "comercializados"),
    "vigencia_fim": ("termino", "vencimento", "encerramento"),
}
_STOP = frozenset("a o os as um uma de do da dos das e ou para por no na nos nas ao aos com em que se esta este seu sua suas seus".split())


def _normalise(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def _phrase(needle: str, haystack: str) -> bool:
    return f" {needle} " in f" {haystack} "


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: str
    chunk: ClauseChunk
    score: float
    methods: tuple[str, ...]
    matched_terms: tuple[str, ...]
    fields: tuple[str, ...]

    @property
    def id(self) -> str:
        return self.chunk_id

    @property
    def text(self) -> str:
        return self.chunk.text

    @property
    def source_pages(self) -> list[SourcePage]:
        return self.chunk.source_pages

    @property
    def page_start(self) -> int:
        return self.chunk.page_start

    @property
    def page_end(self) -> int:
        return self.chunk.page_end


@dataclass(frozen=True, slots=True)
class FieldRetrievalDiagnostics:
    candidate_count: int
    selected_count: int
    pages: tuple[int, ...]
    methods: tuple[str, ...]
    full_search: bool
    searched_pages: tuple[int, ...]
    searched_chunks: int
    limited: bool
    overflow: bool
    no_hit_reason: str | None
    stage: int

    @property
    def status(self) -> str:
        if self.selected_count:
            return "CANDIDATES"  # FOUND belongs to deterministic evidence validation.
        return "NOT_FOUND" if self.full_search else "NOT_RETRIEVED"

    def to_dict(self) -> dict[str, Any]:
        return {name: list(value) if isinstance(value, tuple) else value for name, value in ((key, getattr(self, key)) for key in self.__dataclass_fields__)} | {"status": self.status}


@dataclass(frozen=True, slots=True)
class RetrievalGroupPlan:
    group_id: str
    fields: tuple[str, ...]
    candidates: tuple[RetrievalCandidate, ...]
    field_diagnostics: dict[str, FieldRetrievalDiagnostics]
    stage: int
    document_sha256: str
    per_field_limit: int = 2
    max_candidates: int | None = None
    expanded_per_field_limit: int | None = None

    def summary(self) -> dict[str, Any]:
        return {"group_id": self.group_id, "fields": list(self.fields), "stage": self.stage,
                "document_sha256": self.document_sha256, "candidate_chunks": len(self.candidates),
                "candidate_ids": [c.chunk_id for c in self.candidates],
                "candidate_pages": sorted({p.page_number for c in self.candidates for p in c.source_pages}),
                "field_diagnostics": {key: value.to_dict() for key, value in self.field_diagnostics.items()}}

    def to_dict(self) -> dict[str, Any]:
        return self.summary()


class LocalRetrievalIndex:
    """Complete corpus plus a small lexical index; selected plans never mutate it."""

    def __init__(self, document: ProcessedDocument, chunks: Sequence[ClauseChunk]) -> None:
        self.document_sha256 = document.sha256
        self.pages = tuple(document.pages)
        self.chunks = tuple(chunks)
        self._by_id = {chunk.clause_id: chunk for chunk in self.chunks}
        if len(self._by_id) != len(self.chunks):
            raise ValueError("Clause IDs must be unique within one document.")
        original_pages = {page.page_number: page.text for page in self.pages}
        for chunk in self.chunks:
            if chunk.text != "".join(page.text for page in chunk.source_pages):
                raise ValueError("Chunk text must match its ordered source page fragments.")
            for page in chunk.source_pages:
                if page.page_number not in original_pages or page.text not in original_pages[page.page_number]:
                    raise ValueError("Chunk provenance must be contained in the original page.")
            actual = [page.page_number for page in chunk.source_pages]
            if actual != sorted(actual) or chunk.page_start != min(actual) or chunk.page_end != max(actual):
                raise ValueError("Chunk page bounds must match its source pages.")
        self._text = {c.clause_id: _normalise(c.text) for c in self.chunks}
        self._title = {c.clause_id: _normalise(c.title) for c in self.chunks}
        self._tokens = {key: Counter(value.split()) for key, value in self._text.items()}
        self._length = {key: sum(value.values()) for key, value in self._tokens.items()}
        self._average_length = sum(self._length.values()) / max(1, len(self.chunks)) or 1.0
        self._document_frequency = Counter(token for counts in self._tokens.values() for token in counts)
        self._repeated_titles = {title for title, count in Counter(self._title.values()).items() if count >= max(3, len(self.pages) // 2)}
        self._sections = self._build_sections()
        self._page_text = {p.page_number: _normalise(p.text) for p in self.pages}
        self._fragment_text = {(c.clause_id, p.page_number): _normalise(p.text) for c in self.chunks for p in c.source_pages}

    @classmethod
    def build(cls, processed: ProcessedDocument, chunks: Sequence[ClauseChunk]) -> LocalRetrievalIndex:
        return cls(processed, chunks)

    def summary(self) -> dict[str, Any]:
        return {"retrieval_version": RETRIEVAL_VERSION, "document_sha256": self.document_sha256,
                "pages": len(self.pages), "chunks": len(self.chunks), "full_local_corpus_retained": True,
                "local_text_chars": sum(len(page.text) for page in self.pages)}

    def _build_sections(self) -> dict[str, int]:
        sections: dict[str, int] = {}
        current = 0
        for chunk in self.chunks:
            title = chunk.title.strip()
            letters = [c for c in title if c.isalpha()]
            major = bool(re.match(r"^\d{1,2}[.)]?\s+\D", title)) or (len(letters) >= 8 and all(c.isupper() for c in letters))
            if major and self._title[chunk.clause_id] not in self._repeated_titles:
                current += 1
            sections[chunk.clause_id] = current
        return sections

    def _bm25(self, chunk_id: str, terms: set[str]) -> float:
        counts = self._tokens[chunk_id]
        length = self._length[chunk_id]
        score = 0.0
        for term in sorted(terms):
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            df = self._document_frequency[term]
            idf = math.log1p((len(self.chunks) - df + 0.5) / (df + 0.5))
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * length / self._average_length)
            score += idf * frequency * 2.5 / denominator
        return score

    def _rank_field(self, field: str, stage: int) -> list[RetrievalCandidate]:
        aliases = _ALIASES[field] + (_FALLBACK.get(field, ()) if stage >= 4 else ())
        query_terms = {word for alias in aliases for word in alias.split() if word not in _STOP}
        ranked: list[RetrievalCandidate] = []
        for chunk in self.chunks:
            key = chunk.clause_id
            text, title = self._text[key], self._title[key]
            hits = tuple(alias for alias in aliases if _phrase(alias, text))
            headings = tuple(alias for alias in aliases if _phrase(alias, title))
            overlap = query_terms & self._tokens[key].keys()
            bm25 = self._bm25(key, query_terms)
            # BM25 can suggest an incomplete synonym, but never matches on a
            # single common component of a multiword domain concept.
            bm25_hit = len(overlap) >= min(2, len(query_terms)) and bm25 > 0
            if not hits and not headings and not bm25_hit:
                continue
            methods: list[str] = []
            if headings and title not in self._repeated_titles:
                methods.append("heading")
            if hits:
                methods.append("keyword")
            if bm25_hit:
                methods.append("bm25")
            if stage >= 4:
                methods.append("fallback")
            score = bm25 + 4 * len(hits) + 8 * len(headings)
            if title in self._repeated_titles:
                score *= 0.2
            if re.search(r"\.{3,}\s*\d+", chunk.text):
                score *= 0.25  # TOC remains available; body evidence ranks higher.
            score += 0.01 / (1 + chunk.page_start)
            ranked.append(RetrievalCandidate(key, chunk, round(score, 8), tuple(methods), tuple(dict.fromkeys(hits + headings)), (field,)))
        return sorted(ranked, key=lambda c: (-c.score, c.page_start, c.page_end, c.chunk_id))

    @staticmethod
    def _positive_integer(value: int, name: str) -> None:
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer.")

    def _field_selection(self, field: str, stage: int, limit: int, expanded_limit: int | None = None) -> tuple[list[RetrievalCandidate], FieldRetrievalDiagnostics]:
        ranked = self._rank_field(field, stage)
        quota = max(limit, 4 if field in CRITICAL_FIELDS else limit)
        quota = max(expanded_limit, 4 if field in CRITICAL_FIELDS else expanded_limit) if stage >= 1 and expanded_limit is not None else quota * (2 if stage >= 1 else 1)
        selected = ranked if stage >= 4 else ranked[:quota]
        expanded = {c.chunk_id: c for c in selected}
        if field == "seguradora":
            # Cover metadata often consists of a bare company name and a
            # process number, with no generic insurance keyword in that chunk.
            for chunk in self.chunks:
                if any(page.page_number == 1 for page in chunk.source_pages):
                    existing = expanded.get(chunk.clause_id)
                    if existing:
                        expanded[chunk.clause_id] = replace(existing, methods=tuple(dict.fromkeys(existing.methods + ("document_cover",))))
                    else:
                        expanded[chunk.clause_id] = RetrievalCandidate(chunk.clause_id, chunk, 0.0, ("document_cover",), (), (field,))
        section_overflow = False
        if stage in (2, 3):
            seed_pages = {p.page_number for c in selected for p in c.source_pages}
            nearby = {p for number in seed_pages for p in (number - 1, number, number + 1) if 1 <= p <= len(self.pages)}
            section_ids = {self._sections[c.chunk_id] for c in selected}
            allowed_sections: set[int] = set()
            if stage >= 3:
                for section in section_ids:
                    pages = {p.page_number for c in self.chunks if self._sections[c.clause_id] == section for p in c.source_pages}
                    if len(pages) <= 8:
                        allowed_sections.add(section)
                    else:
                        section_overflow = True
            for chunk in self.chunks:
                methods = []
                if any(p.page_number in nearby for p in chunk.source_pages):
                    methods.append("page_neighbor")
                if stage >= 3 and self._sections[chunk.clause_id] in allowed_sections:
                    methods.append("section")
                if methods:
                    existing = expanded.get(chunk.clause_id)
                    if existing:
                        expanded[chunk.clause_id] = replace(existing, methods=tuple(dict.fromkeys(existing.methods + tuple(methods))))
                    else:
                        expanded[chunk.clause_id] = RetrievalCandidate(chunk.clause_id, chunk, 0.0, tuple(methods), (), (field,))
        selected = list(expanded.values())
        hit_ids = {c.chunk_id for c in ranked}
        selected_ids = set(expanded)
        exhausted = stage >= 4
        has_text = any(page.text.strip() for page in self.pages)
        # Check full original pages too. If callers passed incomplete chunks,
        # absence from that subset can never become a confirmed NOT_FOUND.
        aliases = _ALIASES[field] + _FALLBACK.get(field, ())
        page_hits = {p.page_number for p in self.pages if any(_phrase(alias, self._page_text[p.page_number]) for alias in aliases)} if exhausted else set()
        orphan_hits = {number for number in page_hits if any(_phrase(alias, self._page_text[number]) and not any(_phrase(alias, self._fragment_text[(c.chunk_id, page.page_number)]) for c in ranked for page in c.source_pages if page.page_number == number) for alias in aliases)}
        full_search = exhausted and has_text and not orphan_hits
        reason = None
        if not selected:
            reason = "no_lexical_evidence_after_full_field_scan" if full_search else "field_strategy_not_exhausted"
            if not has_text:
                reason = "empty_extracted_text"
            elif orphan_hits:
                reason = "source_matches_outside_supplied_chunks"
        diagnostics = FieldRetrievalDiagnostics(
            candidate_count=len(hit_ids | selected_ids), selected_count=len(selected),
            pages=tuple(sorted({p.page_number for c in selected for p in c.source_pages})),
            methods=tuple(sorted({m for c in selected for m in c.methods})),
            full_search=full_search, searched_pages=tuple(p.page_number for p in self.pages),
            searched_chunks=len(self.chunks), limited=bool(hit_ids - selected_ids) or bool(orphan_hits) or section_overflow,
            overflow=section_overflow, no_hit_reason=reason, stage=stage,
        )
        return selected, diagnostics

    def _assemble(self, group_id: str, selections: dict[str, list[RetrievalCandidate]], diagnostics: dict[str, FieldRetrievalDiagnostics], stage: int, limit: int, cap: int | None) -> RetrievalGroupPlan:
        merged: dict[str, RetrievalCandidate] = {}
        for field in FIELD_GROUPS[group_id]:
            for candidate in selections[field]:
                existing = merged.get(candidate.chunk_id)
                if existing:
                    merged[candidate.chunk_id] = replace(existing, score=max(existing.score, candidate.score),
                        methods=tuple(dict.fromkeys(existing.methods + candidate.methods)),
                        matched_terms=tuple(dict.fromkeys(existing.matched_terms + candidate.matched_terms)),
                        fields=tuple(dict.fromkeys(existing.fields + (field,))))
                else:
                    merged[candidate.chunk_id] = candidate
        # Explicit caller cap is allocated round-robin across fields, not by
        # one global popularity score. Omitted candidates are always reported.
        if cap is not None and len(merged) > cap:
            keep: set[str] = set()
            depth = 0
            while len(keep) < cap:
                progressed = False
                for field in FIELD_GROUPS[group_id]:
                    if depth < len(selections[field]):
                        keep.add(selections[field][depth].chunk_id)
                        progressed = True
                        if len(keep) == cap:
                            break
                if not progressed:
                    break
                depth += 1
            merged = {key: candidate for key, candidate in merged.items() if key in keep}
            for field, diagnostic in diagnostics.items():
                original_ids = {c.chunk_id for c in selections[field]}
                kept = [c for key, c in merged.items() if key in original_ids]
                omitted = bool(original_ids - keep)
                diagnostics[field] = replace(diagnostic, selected_count=len(kept),
                    pages=tuple(sorted({p.page_number for c in kept for p in c.source_pages})),
                    methods=tuple(sorted({m for c in kept for m in c.methods})),
                    limited=diagnostic.limited or omitted, overflow=diagnostic.overflow or omitted,
                    full_search=diagnostic.full_search and not omitted,
                    no_hit_reason="explicit_candidate_cap" if not kept and omitted else diagnostic.no_hit_reason)
        candidates = tuple(sorted(merged.values(), key=lambda c: (c.page_start, c.page_end, c.chunk_id)))
        return RetrievalGroupPlan(group_id, FIELD_GROUPS[group_id], candidates, diagnostics, stage, self.document_sha256, limit, cap)

    def plan(self, group_id: str, *, per_field_limit: int = 2, max_candidates: int | None = None, stage: int = 0) -> RetrievalGroupPlan:
        group_id = group_id.upper()
        if group_id not in FIELD_GROUPS:
            raise ValueError("Unknown field group.")
        self._positive_integer(per_field_limit, "per_field_limit")
        if max_candidates is not None:
            self._positive_integer(max_candidates, "max_candidates")
        if type(stage) is not int or not 0 <= stage <= 4:
            raise ValueError("Retrieval stage must be between 0 and 4.")
        selections: dict[str, list[RetrievalCandidate]] = {}
        diagnostics: dict[str, FieldRetrievalDiagnostics] = {}
        for field in FIELD_GROUPS[group_id]:
            selections[field], diagnostics[field] = self._field_selection(field, stage, per_field_limit)
        return self._assemble(group_id, selections, diagnostics, stage, per_field_limit, max_candidates)

    def expand(self, plan: RetrievalGroupPlan, *, fields: Sequence[str] | None = None, stage: int | None = None, per_field_limit: int | None = None) -> RetrievalGroupPlan:
        if plan.document_sha256 != self.document_sha256:
            raise ValueError("Plan belongs to a different source document.")
        selected_fields = tuple(dict.fromkeys(fields if fields is not None else plan.fields))
        if any(field not in plan.fields for field in selected_fields):
            raise ValueError("Expansion field does not belong to this group.")
        target = min(4, plan.stage + 1) if stage is None else stage
        if type(target) is not int or not 0 <= target <= 4 or any(plan.field_diagnostics[field].stage > target for field in selected_fields):
            raise ValueError("Expansion cannot move a field backwards or exceed stage 4.")
        expanded_limit = per_field_limit if per_field_limit is not None else plan.expanded_per_field_limit
        if expanded_limit is not None:
            self._positive_integer(expanded_limit, "per_field_limit")
        selections: dict[str, list[RetrievalCandidate]] = {}
        diagnostics = dict(plan.field_diagnostics)
        for field in plan.fields:
            if field in selected_fields:
                selections[field], diagnostics[field] = self._field_selection(field, target, plan.per_field_limit, expanded_limit)
                new_ids = {c.chunk_id for c in selections[field]}
                # Expansion is monotone: adjacent evidence selected earlier is
                # retained even when the final lexical scan changes ranking.
                selections[field].extend(replace(c, fields=(field,)) for c in plan.candidates if field in c.fields and c.chunk_id not in new_ids)
                diagnostic = diagnostics[field]
                diagnostics[field] = replace(diagnostic, selected_count=len(selections[field]), candidate_count=max(diagnostic.candidate_count, len(selections[field])),
                    pages=tuple(sorted({p.page_number for c in selections[field] for p in c.source_pages})),
                    methods=tuple(sorted({m for c in selections[field] for m in c.methods})))
            else:
                selections[field] = [replace(c, fields=(field,)) for c in plan.candidates if field in c.fields]
        return replace(self._assemble(plan.group_id, selections, diagnostics, max(plan.stage, target), plan.per_field_limit, plan.max_candidates), expanded_per_field_limit=expanded_limit)
