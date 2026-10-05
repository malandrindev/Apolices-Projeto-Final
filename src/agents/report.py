"""Relatórios comparativos reais em Markdown, JSON e PDF paginado por PyMuPDF."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

from src.schemas.comparison import ComparisonCitation, PolicyComparison
from src.schemas.policy import NOT_FOUND


class ReportError(RuntimeError):
    """Falha local na geração de um artefato comparativo."""


@dataclass(frozen=True)
class ReportArtifacts:
    markdown_path: Path
    pdf_path: Path
    json_path: Path


def _plain(value: str) -> str:
    return " ".join(value.replace("\x00", "").split())


def _markdown(value: str) -> str:
    value = re.sub(r"([\\*_{}\[\]<>#|])", r"\\\1", _plain(value))
    return value.replace(chr(96), "\\" + chr(96))


def _citation(citation: ComparisonCitation) -> str:
    if citation.page_number is None:
        return f"{citation.source_name}: {NOT_FOUND} (sem página inferida)"
    return f"{citation.source_name}, p. {citation.page_number}"


def _atomic_text(path: Path, content: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ComparisonReportAgent:
    def __init__(self, *, sources_path: Path | None = None) -> None:
        self.sources_path = sources_path or (
            Path(__file__).resolve().parents[2] / "data" / "samples" / "SOURCES.md"
        )


    def _registry_references(self, comparison: PolicyComparison) -> dict[str, str]:
        try:
            registry = self.sources_path.read_text(encoding="utf-8")
        except OSError:
            return {}
        references = {}
        for name in {comparison.source_a, comparison.source_b}:
            for line in registry.splitlines():
                if chr(96) + name + chr(96) not in line:
                    continue
                match = re.search(r"\]\((https?://[^)\s]+)\)", line)
                if match:
                    references[name] = match.group(1)
                    break
        return references


    @staticmethod
    def validate_source_urls(supplied: Mapping[str, str] | None) -> dict[str, str]:
        """Valida referências antes de inferência sem acessar as URLs nem expor valores."""
        validated = {}
        secret_parameters = {
            "token", "access_token", "api_key", "key", "secret", "signature", "sig",
            "password", "authorization", "credential", "x_amz_signature", "x_amz_credential",
        }
        for name, value in (supplied or {}).items():
            if not isinstance(name, str) or not name.strip() or not isinstance(value, str):
                raise ReportError("Referencia publica deve associar um arquivo a uma URL HTTP(S).")
            value = value.strip()
            if not value:
                continue
            try:
                parsed = urlsplit(value)
                valid = (
                    0 < len(value) <= 2048
                    and not any(char.isspace() or ord(char) < 32 for char in value)
                    and parsed.scheme.lower() in {"http", "https"}
                    and bool(parsed.hostname)
                    and parsed.username is None and parsed.password is None
                )
                parsed.port  # Valida porta malformada sem realizar conexao.
            except ValueError:
                valid = False
            if not valid:
                raise ReportError("Referencia publica exige URL HTTP(S) absoluta sem credenciais.")
            query_names = {key.lower().replace("-", "_") for key, _ in parse_qsl(parsed.query)}
            if query_names & secret_parameters:
                raise ReportError("Referencia publica contem parametro reservado para credencial/assinatura.")
            validated[name] = value
        return validated

    def _validated_references(
        self, comparison: PolicyComparison, supplied: Mapping[str, str] | None,
    ) -> dict[str, str]:
        references = self._registry_references(comparison)
        references.update(comparison.source_references)
        allowed = {comparison.source_a, comparison.source_b}
        if any(name not in allowed for name in (supplied or {})):
            raise ReportError("Referencia publica deve corresponder ao nome exato de uma das duas fontes.")
        references.update(self.validate_source_urls(supplied))
        if any(name not in allowed for name in references):
            raise ReportError("Referencia publica deve corresponder ao nome exato de uma das duas fontes.")
        return self.validate_source_urls(references)

    def _public_sources(self, comparison: PolicyComparison) -> list[str]:
        if not comparison.source_references:
            return []
        lines = [
            "## Fontes públicas utilizadas", "",
            "Referências dos documentos processados, informadas pelo usuário ou pelo catálogo "
            "local data/samples/SOURCES.md.", "",
            "| Arquivo processado | Fonte pública |", "|---|---|",
        ]
        for name, url in sorted(comparison.source_references.items()):
            destination = quote(url, safe=":/?#@!$&'+,;=%")
            lines.append(f"| {_markdown(name)} | [Fonte pública]({destination}) |")
        try:
            registry = self.sources_path.read_text(encoding="utf-8")
            registered = self._registry_references(comparison)
            if any(registered.get(name) == url for name, url in comparison.source_references.items()):
                provenance = next((line for line in registry.splitlines()
                                   if line.startswith("Documentos ")), "")
                if provenance:
                    lines.extend(["", provenance])
        except OSError:
            pass
        lines.append("")
        return lines

    def _markdown_report(self, comparison: PolicyComparison) -> str:
        lines = [
            "# Comparação de apólices D&O", "",
            f"Apólice A: {_markdown(comparison.source_a)}",
            f"Apólice B: {_markdown(comparison.source_b)}",
            f"Gerado em: {_markdown(comparison.compared_at)}", "",
            "## Resumo executivo", "", _markdown(comparison.executive_summary), "",
            "Esta análise apoia a revisão humana e não constitui parecer jurídico. "
            "Os resultados refletem os campos e excertos extraídos; informações ausentes "
            "não comprovam ausência de cobertura. Revise os documentos completos.", "",
            "## Diferenças e evidências", "",
        ]
        if not comparison.differences:
            lines.extend(["Nenhuma diferença identificada nos campos localizados.", ""])
        for item in comparison.differences:
            lines.extend([
                f"### {_markdown(item.label)}", "",
                f"- Apólice A: {_markdown(item.value_a)}",
                f"- Apólice B: {_markdown(item.value_b)}",
                f"- Classificação: {item.classification.value}",
                f"- Justificativa: {_markdown(item.justification)}", "",
                f"Fonte A: {_markdown(_citation(item.citation_a))}",
                f"> {_markdown(item.citation_a.excerpt)}", "",
                f"Fonte B: {_markdown(_citation(item.citation_b))}",
                f"> {_markdown(item.citation_b.excerpt)}", "",
            ])
        if comparison.risk_highlights:
            lines.extend(["## Limitações e pontos de revisão", ""])
            lines.extend(f"- {_markdown(value)}" for value in comparison.risk_highlights)
            lines.append("")
        lines.extend(self._public_sources(comparison))
        lines.extend([
            "## Identificação das fontes processadas", "",
            f"- SHA-256 A: {comparison.document_id_a}",
            f"- SHA-256 B: {comparison.document_id_b}", "",
        ])
        return "\n".join(lines)

    @staticmethod
    def _write_pdf(markdown: str, path: Path) -> None:
        import pymupdf as fitz

        font = fitz.Font("helv")
        width = 505
        lines = []
        for paragraph in markdown.splitlines():
            paragraph = re.sub(r"^#{1,3} ", "", paragraph)
            paragraph = paragraph.replace("\\", "").replace("\x00", "")
            if not paragraph:
                lines.append("")
                continue
            # Divide palavras largas também: nenhuma evidência é cortada na margem.
            line = ""
            for word in paragraph.split():
                candidate = f"{line} {word}".strip()
                if font.text_length(candidate, fontsize=10) <= width:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                    line = ""
                while font.text_length(word, fontsize=10) > width:
                    end = 1
                    while end < len(word) and font.text_length(word[:end + 1], fontsize=10) <= width:
                        end += 1
                    lines.append(word[:end])
                    word = word[end:]
                line = word
            if line:
                lines.append(line)
        temporary = None
        document = fitz.open()
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".pdf", delete=False) as stream:
                temporary = Path(stream.name)
            lines_per_page = 49
            for index in range(0, max(1, len(lines)), lines_per_page):
                page = document.new_page(width=595, height=842)
                page.insert_text((45, 45), "Comparação D&O | apoio à revisão humana",
                                 fontsize=10, fontname="helv")
                for offset, line in enumerate(lines[index:index + lines_per_page]):
                    page.insert_text((45, 75 + offset * 14), line, fontsize=10, fontname="helv")
                page.insert_text((45, 807), f"Página {index // lines_per_page + 1}",
                                 fontsize=9, fontname="helv")
            document.set_metadata({"title": "Comparação de apólices D&O",
                                   "subject": "Análise com evidências; não constitui parecer jurídico"})
            document.save(str(temporary), garbage=4, deflate=True)
            os.replace(temporary, path)
        finally:
            document.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def generate(self, comparison: PolicyComparison, output_dir: Path, *,
                 source_urls: Mapping[str, str] | None = None) -> ReportArtifacts:
        comparison = comparison.model_copy(update={
            "source_references": self._validated_references(comparison, source_urls),
        }, deep=True)
        output_dir = Path(output_dir)
        digest = hashlib.sha256(comparison.model_dump_json().encode("utf-8")).hexdigest()[:20]
        artifacts = ReportArtifacts(
            markdown_path=output_dir / f"comparacao_{digest}.md",
            pdf_path=output_dir / f"comparacao_{digest}.pdf",
            json_path=output_dir / f"comparacao_{digest}.json",
        )
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            markdown = self._markdown_report(comparison)
            self._write_pdf(markdown, artifacts.pdf_path)
            _atomic_text(artifacts.markdown_path, markdown)
            _atomic_text(artifacts.json_path, json.dumps(
                comparison.model_dump(mode="json"), ensure_ascii=False, indent=2,
            ))
        except (OSError, RuntimeError, ValueError, ImportError) as error:
            raise ReportError("Não foi possível gerar os relatórios locais em Markdown/PDF/JSON.") from error
        return artifacts
