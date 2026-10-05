"""Generate factual local academic PDF and editable pitch from stored evidence.
No API calls, .env reads, email, publication or invented team identification.
Supply QA totals from a completed test run. Preview PDF reproduces the vector
slide layout; it is not an Office screenshot. EVIDENCE_BUNDLE.json preserves
the historical synthetic validation snapshot, not a new API execution. Missing
local diagnostics load the whole bundle atomically; records are never mixed.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from typing import Any
import pymupdf as fitz
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Projeto_Final_Artefatos"
NAVY, TEAL, PALE, INK, GRAY, ORANGE, WHITE = "17324D", "007C83", "F1F6F8", "263B4C", "5B6F7E", "B76016", "FFFFFF"
FONT_REG, FONT_BOLD = Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")
REG = fitz.Font(fontfile=str(FONT_REG)) if FONT_REG.exists() else fitz.Font("helv")
BOLD = fitz.Font(fontfile=str(FONT_BOLD)) if FONT_BOLD.exists() else fitz.Font("hebo")
ORIGIN = "https://github.com/leo-vilelela/desafio_final_insurminds_comparacao_entre_apolices"


def pdf_text(value: str) -> str:
    # Simple Latin fonts preserve searchable spaces/hyphens and Portuguese accents.
    # Arial's composite CMap aliases space/semicolon/hyphen to unrelated Unicode.
    return value.translate(str.maketrans({"“": '"', "”": '"', "’": "'", "‘": "'", "—": "-", "–": "-", "•": "|"}))


def rgb(value: str) -> tuple[float, float, float]:
    return tuple(int(value[i:i+2], 16)/255 for i in (0, 2, 4))


def wrap(text: str, width: float, size: float, bold: bool = False) -> list[str]:
    font = BOLD if bold else REG
    result = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if font.text_length(candidate, fontsize=size) <= width:
                line = candidate
                continue
            if line:
                result.append(line)
                line = ""
            for character in word:
                if font.text_length(line+character, fontsize=size) > width and line:
                    result.append(line)
                    line = ""
                line += character
        result.append(line)
    return result


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_evidence(bundle_path: Path | None = None) -> dict[str, Any]:
    """Read complete local evidence, or one historical bundle; never mix sources."""
    if bundle_path is not None:
        return _validate_evidence(read_json(bundle_path))
    try:
        return _read_local_evidence()
    except FileNotFoundError:
        return _validate_evidence(read_json(ROOT / "Projeto_Final_Artefatos/EVIDENCE_BUNDLE.json"))


def _validate_evidence(result: dict[str, Any]) -> dict[str, Any]:
    for provider in ("openai", "groq"):
        diag = result[provider]["diagnostic"]
        if not (diag.get("status") == "PASS" and diag.get("mode") == "live" and diag.get("synthetic_data") is True and diag.get("generative_ai_verified") is True):
            raise ValueError(f"{provider}: expected passing historical live synthetic evidence")
        comparison = result[provider]["comparison"]
        documents = {item["source_name"]: item["sha256"] for item in diag["documents"]}
        for side in ("a", "b"):
            if documents.get(comparison[f"source_{side}"]) != comparison[f"document_id_{side}"]:
                raise ValueError(f"{provider}: comparison and diagnostic source mismatch")
    for name in ("ocr", "rag_first", "rag_cached"):
        if name not in result:
            raise ValueError(f"Historical evidence missing: {name}")
    return result


def _read_local_evidence() -> dict[str, Any]:
    result = {}
    for provider in ("openai", "groq"):
        directory = ROOT / "data/processed" / f"e2e_smoke_{provider}"
        diag = read_json(directory / "diagnostics.json")
        if not (diag.get("status") == "PASS" and diag.get("mode") == "live" and diag.get("synthetic_data") is True and diag.get("generative_ai_verified") is True):
            raise ValueError(f"{provider}: expected passing live synthetic evidence")
        artifact = Path(diag["artifacts"]["json"])
        result[provider] = {"diagnostic": diag, "comparison": read_json(artifact if artifact.is_absolute() else ROOT / artifact)}
    result["ocr"] = read_json(ROOT / "data/processed/ocr_validation/ocr_validation_report.json")
    for name, stamp in (("rag_first", "run_20261003T225302"), ("rag_cached", "run_20261003T225512")):
        result[name] = read_json(ROOT / "data/processed/rag_benchmark" / stamp / "rag_benchmark_report.json")
    return _validate_evidence(result)


def phase(report: dict[str, Any], name: str) -> dict[str, Any]:
    for worker in report["workers"]:
        for item in worker.get("phases", []):
            if item["phase"] == name:
                if item["status"] != "PASS":
                    raise ValueError(f"Phase failed: {name}")
                return item
    raise KeyError(name)


class ReportPDF:
    def __init__(self) -> None:
        self.doc = fitz.open()
        self.page = None
        self.y = 0.0
        self.toc = []

    def new(self, title: str | None = None) -> None:
        self.page = self.doc.new_page(width=595.3, height=841.9)
        self.page.insert_font(fontname="Body", fontbuffer=REG.buffer, set_simple=True)
        self.page.insert_font(fontname="Strong", fontbuffer=BOLD.buffer, set_simple=True)
        self.page.draw_rect(fitz.Rect(0, 0, 595.3, 9), color=rgb(TEAL), fill=rgb(TEAL))
        self.at(42, 30, "INSURMINDS  |  PROJETO FINAL D&O", 9, True, NAVY)
        self.at(42, 44, "Relatório técnico local • revisão humana pendente • 03/10/2026", 8, False, GRAY)
        self.y = 70
        if title:
            self.toc.append([1, title, len(self.doc)])
            self.heading(title)

    def at(self, x: float, y: float, text: str, size: float = 10, bold: bool = False, color: str = INK) -> None:
        self.page.insert_text((x, y), pdf_text(text), fontsize=size, fontname="Strong" if bold else "Body", color=rgb(color))

    def ensure(self, height: float) -> None:
        if self.y + height > 788:
            self.new()

    def heading(self, text: str, size: float = 23) -> None:
        lines = wrap(text, 511, size, True)
        self.ensure(len(lines)*(size+4)+18)
        for line in lines:
            self.at(42, self.y+size, line, size, True, NAVY)
            self.y += size+4
        self.y += 16

    def paragraph(self, text: str, size: float = 10.5) -> None:
        lines = wrap(text, 511, size)
        self.ensure(len(lines)*size*1.42+10)
        top = self.y
        for line in lines:
            self.at(42, self.y+size, line, size)
            self.y += size*1.42
        for match in re.finditer(r"https?://[^\s]+", text):
            self.page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(42, top, 553, self.y), "uri": match.group(0).rstrip(".,;")})
        self.y += 10

    def table(self, headers: list[str], rows: list[list[str]], widths: list[float] | None = None, size: float = 9.1) -> None:
        widths = widths or [511/len(headers)]*len(headers)
        for number, row in enumerate([headers]+rows):
            lines = [wrap(str(cell), width-14, size, number == 0) for cell, width in zip(row, widths)]
            height = max(map(len, lines))*size*1.36+16
            self.ensure(height)
            x = 42.0
            for index, width in enumerate(widths):
                self.page.draw_rect(fitz.Rect(x, self.y, x+width, self.y+height), fill=rgb(NAVY if number == 0 else (PALE if number % 2 else WHITE)), color=rgb("D5E1E6"), width=0.5)
                for linenum, line in enumerate(lines[index]):
                    self.at(x+7, self.y+10+size+linenum*size*1.36, line, size, number == 0, WHITE if number == 0 else INK)
                x += width
            self.y += height
        self.y += 14

    def diagram(self) -> None:
        self.ensure(200)
        items = ["1  Entrada PDF / imagem", "2  OCR local + páginas", "3  Segmentação + GenAI", "4  Pydantic + JSON / SQLite", "5  Comparação + evidências", "6  Streamlit + exportações"]
        for index, label in enumerate(items):
            x, y = 42+(index % 2)*260, self.y+(index // 2)*54
            self.page.draw_rect(fitz.Rect(x, y, x+251, y+43), fill=rgb(PALE), color=rgb(TEAL), width=1)
            self.at(x+11, y+26, label, 10.4, True, NAVY)
        self.y += 178
        self.paragraph("Ramo opcional: cláusulas -> embeddings locais -> Chroma -> RagAgent. A comparação principal funciona sem carregar esse ramo.", 10)

    def finish(self, path: Path) -> None:
        for index, page in enumerate(self.doc):
            page.draw_line((42, 805), (553, 805), color=rgb("D5E1E6"), width=0.6)
            page.insert_text((42, 820), "MVP acadêmico | fontes e limites explicitados | não constitui envio", fontname="Body", fontsize=8, color=rgb(GRAY))
            page.insert_text((505, 820), f"{index+1} / {len(self.doc)}", fontname="Body", fontsize=8, color=rgb(GRAY))
        self.doc.set_toc(self.toc)
        self.doc.set_metadata({"title": "InsurMinds — Relatório Técnico do Projeto Final", "subject": "MVP D&O, evidências sintéticas e preparação local para revisão", "creator": "scripts/build_delivery_documents.py"})
        self.doc.save(path, garbage=4, deflate=True)


def build_report(evidence: dict[str, Any], args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    qa_text = (f"{args.test_count} testes pytest, {args.subtest_count} subtestes e execução em {args.test_seconds:g} s; pip check passou. Contagem informada da validação consolidada de 03/10/2026, não recalculada por este gerador. Imports src: 30 (25 módulos + 5 pacotes).") if args.test_count else "Consulte a validação consolidada em docs/VALIDATION_REPORT.md; contagens não fornecidas ao gerador."
    blocks = []
    def page(title): blocks.append({"type": "page", "title": title})
    def p(text): blocks.append({"type": "paragraph", "text": text})
    def table(headers, rows, widths=None, size=9.1): blocks.append({"type": "table", "headers": headers, "rows": rows, "widths": widths, "size": size})

    page("Plataforma Inteligente para Análise e Comparação de Apólices D&O")
    p("RELATÓRIO TÉCNICO • Projeto Final I2A2 / InsurMinds • snapshot factual de 03/10/2026")
    p("Protótipo Python para receber PDF ou imagem, extrair campos de seguro D&O com página e trecho de origem, comparar duas fontes e apresentar diferenças na interface local. Combina extração local de texto, IA Generativa, validação estruturada e persistência.")
    p("Situação deste material: preparação local para revisão humana. Não comprova envio, publicação do repositório final, aprovação acadêmica ou conclusão de todas as pendências de entrega.")
    table(["Identificação", "Situação"], [["Nome do grupo", "Pendente de confirmação pelos participantes"], ["Representante e integrantes", "Pendente; não foram inventados nomes ou contatos"], ["MVP original", "Leo — GitHub leo-vilelela — autoria Git leo_vilela"], ["Licença do código", "MIT; arquivo LICENSE preservado"], ["Origem do projeto", ORIGIN]], [151, 360])
    p("A estabilização preservou o projeto original, contratos e testes, o provedor Groq, schemas e persistência. Requisitos acadêmicos seguem Desafios1.pdf, fornecido em capturas pelo usuário: páginas editoriais 4–6 e 20–25. Este gerador não utilizou uma cópia local do PDF oficial.")
    p("Testes integrados com modelos reais usaram exclusivamente documentos sintéticos educacionais. Chubb e AIG são fontes públicas registradas pelo projeto; não foram processadas nos smokes apresentados.")

    page("1. Problema, escopo e requisitos mínimos")
    p("Apólices D&O apresentam coberturas, exclusões, limites, retenções, vigências e condições em linguagem técnica. A plataforma reduz o trabalho de localizar informações e confrontar fontes, mantendo evidência para revisão. O objetivo acadêmico é um MVP funcional e fundamentado; funcionamento tem prioridade sobre complexidade.")
    table(["Requisito mínimo oficial", "Implementação e evidência"], [["Ler PDF ou imagem", "IngestionAgent + OcrAgent; smoke real com PDF nativo A e PNG B reconhecido por Tesseract"], ["Extrair automaticamente informações relevantes", "SegmentationAgent + ExtractionAgent; duas extrações reais por provedor"], ["Estruturar dados organizados", "PolicyExtraction: 27 campos FieldEvidence; Pydantic, JSON e SQLite"], ["Comparar pelo menos duas apólices", "ComparisonAgent compara duas fontes; demonstrado com documentos sintéticos D&O"], ["Apresentar principais diferenças", "Streamlit, tabela comparativa e relatório JSON/Markdown/PDF com citações"], ["Usar pelo menos um modelo GenAI", "Chamadas reais OpenAI e Groq; generative_ai_verified=true"], ["Disponibilizar interface demonstrável", "interface/app.py; AppTest e servidor HTTP 200 validados; demonstração visual é artefato separado"]], [177, 334])
    p("Rastreabilidade: docs/REQUIREMENTS_TRACEABILITY.md. Cada PASS tem escopo de evidência; aprovação da banca e qualidade em toda apólice de mercado não são deduzidas desses testes.")
    p("OCR em nuvem, frameworks de agentes, bancos adicionais, alta disponibilidade e interfaces sofisticadas são exemplos ou recomendações, não novas obrigações. RAG permanece opcional.")

    page("2. Arquitetura da solução")
    blocks.append({"type": "diagram"})
    p("src/pipeline.py coordena o processamento. Componentes têm contratos explícitos e responsabilidades delimitadas. UI e CLI usam a mesma lógica; estado em data/processed/ permite retomada. A camada de provedor desacopla chamadas externas de agentes e validações.")
    table(["Camada", "Local no código", "Responsabilidade"], [["Apresentação", "interface/app.py", "Uploads, provedor escolhido, progresso, diferenças, evidências e downloads"], ["Orquestração", "src/pipeline.py", "Ingestão -> OCR -> segmentação -> extração -> SQLite / comparação / índice opcional"], ["Agentes", "src/agents/", "Operações de domínio com schemas tipados e tratamento de erros"], ["GenAI", "src/llm/providers.py e src/llm/", "Adaptadores OpenAI/Groq, retry limitado e eventos seguros"], ["Persistência", "src/storage/", "Cache JSON atômico, SQLite e Chroma opcional"], ["Contratos", "src/schemas/ e schemas de agentes", "Valores, evidências e categorias comparativas"]], [90, 155, 266], 8.8)
    p("Os agentes são componentes especializados do pipeline Python, com responsabilidades delimitadas. A divisão modular atende à recomendação acadêmica sem introduzir framework somente por ter sido citado.")

    page("3. Tecnologias utilizadas")
    table(["Tecnologia", "Uso implementado", "Referência"], [["Python 3.11 / Pydantic", "Contratos, validação, CLI e agentes", "src/config.py; schemas"], ["PyMuPDF", "Texto nativo PDF, rasterização e exportação PDF", "API primária [R4]"], ["Tesseract 5.4.0 / pytesseract", "OCR local por página, por+eng, timeout e executável configurável", "Distribuição/idiomas [R5–R7]"], ["OpenAI SDK / Responses API", "JSON estrito, modelo por tarefa e store=false", "API primária [R2–R3]"], ["Groq SDK", "Provedor original preservado; modelos configuráveis", "API primária [R8]"], ["Streamlit", "Interface local para duas fontes", "interface/app.py; testes da UI"], ["SQLite / JSON", "Dados estruturados, evidências e retomada", "src/storage/"], ["Chroma / SentenceTransformers", "Índice opcional, CPU, 384 dimensões", "vector_store.py; [R9]"], ["pytest", "Testes com doubles e integração local", "tests/"], ["python-pptx 1.0.2", "Pitch editável; ferramenta de entrega", "requirements-artifacts.txt"]], [127, 248, 136], 9)
    p("Versões exatas estão nos requirements e diagnósticos. Tesseract 5.4.0 foi validado nesta máquina; não é uma afirmação de versão mais recente. O setup portátil verifica hashes, extrai em data/processed/tooling/, não executa instalador e não escreve configuração do sistema.")
    p("Credenciais ficam no ambiente e fora dos artefatos/Git; o gerador não as lê. Ferramentas de entrega ficam separadas das dependências necessárias à aplicação.")

    page("4. Agentes desenvolvidos")
    table(["Agente", "Função", "Evidência / contrato"], [["IngestionAgent", "Tipo/magic bytes, tamanho, integridade, PDF protegido, SHA-256", "ingestion.py; test_ingestion_ocr.py e test_ocr_hardening.py"], ["OcrAgent", "Texto nativo, Tesseract por página, cache e progresso", "ocr.py; PageText / ProcessedDocument"], ["SegmentationAgent", "Títulos heurísticos; IA rápida somente se ambíguos", "segmentation.py; chunks <=4000 caracteres"], ["ExtractionAgent", "Campos com evidência, validação, uma correção de saída inválida", "extraction.py; FieldEvidence / PolicyExtraction"], ["ComparisonAgent", "Regras objetivas e GenAI semântica em lotes limitados", "comparison.py; citações A/B"], ["ComparisonReportAgent", "Diferenças, justificativas, citações e fontes", "report.py; JSON / Markdown / PDF"], ["RagAgent (opcional)", "Consulta trechos e responde com citações; abstém-se sem fontes", "rag.py; resposta GenAI live RAG ainda não demonstrada"]], [119, 201, 191], 9)
    p("Arquivos acima estão em src/agents/. Phase2Pipeline orquestra segmentação/extração. Doubles testam contratos; os smokes live distinguem execução real de modelos da simulação offline.")

    page("5. Fluxo completo e formato das informações")
    table(["Etapa", "Entrada -> saída / decisão"], [["1. Recepção", "Arquivo validado -> IngestedDocument; rejeição de tipo, limite, corrupção e proteção"], ["2. Extração textual", "PDF nativo se suficiente; senão rasterização e OCR -> PageText com página, método e duração"], ["3. Organização", "Texto paginado -> cláusulas limitadas, identificadas e associadas às páginas"], ["4. GenAI", "Cláusula -> campos/evidências no schema; cache por documento/opções evita repetir"], ["5. Validação", "Pydantic, página, excerto, data, moeda e número; uma correção quando necessária"], ["6. Persistência", "JSON e SQLite conservam extração e retomada; dados ausentes não são inventados"], ["7. Comparação", "Duas extrações -> categorias, justificativas e citações; regras locais antes da semântica"], ["8. Apresentação", "Diferenças na interface; exportações JSON/Markdown/PDF persistem"], ["9. Consulta opcional", "--index gera embeddings; --ask recupera fontes e usa RagAgent, fora do caminho principal da UI"]], [100, 411])
    p("Cada campo usa FieldEvidence(valor, pagina, trecho_origem, confianca). Campo não localizado recebe valor 'nao_localizado', página/trecho ausentes e confiança zero. Isso significa ausência de evidência na extração, não prova de inexistência contratual.")
    p("Os 27 campos abrangem identificação, vigências, moeda, LMG, sublimites, retenção/franquia, prêmio, Side A/B/C, custos de defesa, extensões, base de cobertura, retroatividade, período estendido, exclusões, aviso de sinistro, defesa, acordo, rateio, jurisdição, territorialidade, cancelamento/renovação e definições.")

    page("6. Justificativa das decisões arquiteturais")
    table(["Decisão", "Motivo e consequência"], [["OCR local primeiro", "PDF textual dispensa chamada de visão; extração por página e dependência externa controladas"], ["Schema com evidências", "Resultados rastreáveis; detecção de JSON/citação inválidos; confiança sem calibração estatística"], ["GenAI + checagens locais", "Modelo interpreta; regras verificam estrutura/consistência. Citação literal não prova formalmente toda inferência"], ["Provedor selecionável", "Groq preservado, OpenAI sob contrato comum; sem troca automática de conta/provedor"], ["Modelos por tarefa", "OpenAI Luna extrai/classifica, Terra corrige uma saída, Sol compara; Groq usa configuração original"], ["Cache e retomada", "SHA-256 + versões/opções/modelos; gravação atômica e retomada por página/cláusula"], ["SQLite + JSON", "Persistência local simples, auditável e suficiente ao MVP"], ["RAG opcional/lazy", "Funcionalidade original preservada sem impor encoder ao caminho principal"], ["Retries limitados", "Até 3 tentativas configuráveis; quota/crédito e espera longa não causam loop indefinido"]], [145, 366], 9)
    p("OpenAI usa Structured Outputs na Responses API e store=false. Essa configuração da requisição não estabelece garantia sobre todas as políticas de retenção do provedor. Eventos locais registram provedor/modelo/etapa/duração/tokens/tentativa/status, sem chave, prompt ou texto integral.")
    p("Vantagem numérica exige montante único não negativo, moeda identificada igual e ausência de conflito. Moeda desconhecida/diferente, percentuais sem base, múltiplos montantes ou datas isoladas não ordenam vantagem. Maior LMG ou menor retenção/prêmio não elege a melhor apólice como um todo.")
    p("Terra é rota implementada e testada para correção; os smokes bem-sucedidos não precisaram usá-la. Não há evidência live de ganho de qualidade causado por essa rota.")

    page("7. Resultados integrados com IA Generativa real")
    p("Fontes sintéticas educacionais: A é PDF textual; B é PNG rasterizado reconhecido por OCR local. Cada uma contém uma página e uma cláusula simples. Os smokes validam integração técnica, sem estimar precisão em condicionais de mercado ou documentos extensos.")
    rows = []
    for provider in ("openai", "groq"):
        diag = evidence[provider]["diagnostic"]
        usage = diag["usage"]
        rows.append([provider.upper(), str(diag["logical_calls"]), str(diag["http_attempts"]), f"{usage['prompt_tokens']} / {usage['completion_tokens']}", f"{diag['resume_cache_hits']} / {diag['resume_new_calls']}"])
    table(["Provedor", "Chamadas lógicas", "HTTP", "Tokens entrada / saída", "Cache hits / novas chamadas"], rows, [88, 80, 44, 152, 147])
    p("Primeira passagem: duas extrações e uma comparação semântica. Retomada: duas extrações em cache, zero novas chamadas. Registros não incluem duração total; nenhuma latência foi inventada. Tokens agregados não permitem inferir fatura ou custo por modelo sem distribuição correspondente e cobrança real. Cobrança real observada pelo usuário no Cost Dashboard: aproximadamente US$ 0,00703. Volume OpenAI histórico: E2E 3 HTTP, 3.589 tokens de entrada/1.980 de saída; ping 1 HTTP, 12/5 tokens. A auditoria estática não identificou tools, function calling ou service_tier explícito; Structured Outputs usa text.format. Inscrição, elegibilidade e projeto InsurMinds-I2A2 foram informados pelo usuário. A aplicação da franquia de complimentary daily tokens continua sob investigação; não foi possível reconciliar a cobrança com tokens agregados. Nenhuma nova inferência ou alteração de parâmetros foi feita na PHASE A. Ver docs/OPENAI_BILLING_AUDIT.md.")
    by_field = {item["field_name"]: item for item in evidence["openai"]["comparison"]["differences"]}
    selected = [by_field[name] for name in ("limite_maximo_garantia", "retencao_franquia", "side_a")]
    table(["Campo sintético", "A", "B", "Resultado pontual"], [[item["label"], item["value_a"], item["value_b"], item["classification"]] for item in selected], [124, 128, 128, 131], 8.9)
    item = by_field["limite_maximo_garantia"]
    p(f"A, página {item['citation_a']['page_number']}: “{item['citation_a']['excerpt']}”. B, página {item['citation_b']['page_number']}: “{item['citation_b']['excerpt']}”. Favorecimento restrito ao limite nominal em BRL; não conclui superioridade contratual global.")
    p("Evidências: data/processed/e2e_smoke_openai/diagnostics.json e data/processed/e2e_smoke_groq/diagnostics.json, com relatórios vinculados. São fontes sintéticas, não documentos de clientes nem amostras Chubb/AIG.")

    page("8. Testes, OCR e demonstração")
    p(qa_text)
    p("Cobertura inclui ingestão/OCR, schemas, evidência de página/trecho, validação financeira, comparação, persistência, provedores/falhas, retomada, cache vetorial e interface. Testes offline não consomem API. Diagnósticos live explicitam uso real de GenAI.")
    table(["Diagnóstico OCR sintético", "Método", "Tempo observado", "Resultado"], [[Path(item["input_path"]).name, item["method"], f"{item['duration_ms']:.3f} ms", "Texto esperado reconhecido" if item["recognized_expected_text"] else "Falhou"] for item in evidence["ocr"]["results"]], [207, 87, 94, 123])
    p("Tesseract 5.4.0.20240606, por+eng, PDF digitalizado a 200 DPI. Diagnósticos de uma página e texto curto não são benchmark de apólices completas. Fonte: data/processed/ocr_validation/ocr_validation_report.json.")
    p("Demonstração adicional em navegador Edge real: dois uploads, três downloads (JSON verificado), zero exceções de página e APIs bloqueadas por guarda durante reexecução em cache. Vídeo local InsurMinds_Projeto_Final.mp4: 165,44 s (2 min 45,44 s), com narração pt-BR, dentro do limite oficial de 5 minutos; decodificação integral FFmpeg passou. A gravação demonstra a aplicação e a retomada, não uma nova chamada ao modelo. Conferência humana segue pendente.")
    table(["Ação", "Comando / referência"], [["Instalação", "README.md; pip install -r requirements-dev.txt no Python da .venv"], ["Suíte", ".venv/Scripts/python.exe -m pytest -q"], ["Interface", ".venv/Scripts/python.exe -m streamlit run interface/app.py"], ["OCR portátil", "scripts/setup_tesseract.ps1; configurar TESSERACT_CMD sem substituir .env"], ["Validação consolidada", "docs/VALIDATION_REPORT.md; diagnósticos vinculados"], ["Benchmark RAG sem LLM", ".venv/Scripts/python.exe scripts/benchmark_rag.py --help"]], [150, 361], 9)

    page("9. RAG opcional: medições e recursos locais")
    first, cached = evidence["rag_first"], evidence["rag_cached"]
    def sec(report, key): return f"{phase(report, key)['wall_seconds']:.4f} s"
    table(["Fase medida", "Primeiro uso", "Modelo já baixado"], [["Import SentenceTransformers (processo separado)", sec(first, "import_sentence_transformers"), sec(cached, "import_sentence_transformers")], ["Download / carga do modelo", sec(first, "first_model_download_and_load"), sec(cached, "first_model_download_and_load")], ["Encode de quatro chunks", sec(first, "encode_four_chunks"), sec(cached, "encode_four_chunks")], ["Reindexação com cache vetorial", sec(first, "index_four_chunks_repeat_cached"), sec(cached, "index_four_chunks_repeat_cached")], ["Consulta com cache", sec(first, "query_repeat_cached"), sec(cached, "query_repeat_cached")]], [269, 121, 121], 9)
    peak = max(item["rss_mb_after"] for worker in cached["workers"] for item in worker.get("phases", []) if item.get("rss_mb_after") is not None)
    p(f"Windows, Python 3.11, CPU, 12 CPUs lógicas; paraphrase-multilingual-MiniLM-L12-v2, 384 dimensões, quatro chunks sintéticos e zero chamadas LLM. O processo do fluxo chegou a {peak:.1f} MiB de RSS. Importação e carga podem ocorrer em processos diferentes; não se somam esses tempos como latência de usuário.")
    p("Primeira medição: aproximadamente 457,5 MiB de arquivos de modelo. Fases limitadas externamente a 60 s passaram. Não se extrapola para apólices extensas ou demora de 30 minutos. Método detalhado: docs/RAG_PERFORMANCE.md.")
    p("Cache JSON por hash de modelo/texto/versão/opções rejeita dimensão incorreta e valores não finitos. Reindexação idêntica reutiliza vetores e preserva IDs/remoção de entradas antigas. Nova instância pode reindexar só com cache sem carregar encoder.")
    p("HF_HOME foi isolado no benchmark; a aplicação deve apontar explicitamente ao mesmo cache para reutilizar download. Outro processo com diretório padrão pode baixar novamente. Este gerador não altera .env. Consulta vetorial real foi medida, mas resposta RAG GenAI live ainda não foi demonstrada e mantém escopo parcial na matriz.")

    page("10. Limitações conhecidas")
    table(["Limitação", "Implicação"], [["Live sintético e pequeno", "Sem avaliação rotulada de precisão em apólices completas de seguradoras"], ["OCR dependente do documento", "Ruído, inclinação, tabelas, fontes pequenas e layout degradam texto; por/eng validados"], ["Interpretação semântica", "Página/trecho válidos não garantem toda paráfrase ou relação de cobertura"], ["Confiança não calibrada", "Não é probabilidade estatística de acerto"], ["Campo ausente", "nao_localizado requer revisão da fonte; não prova falta de cobertura"], ["Comparabilidade financeira", "Moeda, unidade, percentuais, sublimites e escopo precisam equivalência; conflitos não favorecem"], ["Modelo externo", "Disponibilidade, quota, latência e custos variam; US$ 0,00703 observados pelo usuário; incentivo sob investigação"], ["Persistência local", "JSON/SQLite/caches sem criptografia ou controle corporativo; operação exige cuidado com arquivos"], ["RAG opcional", "Carga exige memória; comparação principal não exige encoder"], ["Entrega", "Identificação, revisão dos artefatos e publicação autorizada pendentes; geração local não é envio"]], [162, 349], 9)
    p("O protótipo apoia leitura e revisão técnica. Resultados exigem conferência humana da íntegra, anexos e condições aplicáveis. A abrangência comprovada não é ampliada por um diagnóstico simples sem erros.")

    page("11. Evolução futura e fechamento da entrega")
    table(["Proposta futura", "Como medir utilidade antes de ampliar complexidade"], [["Corpus público rotulado", "Avaliar campos, evidência, abstinência e diferenças em versões/escopos equivalentes; citar origem e uso permitido"], ["Revisão assistida", "Confirmação/correção humana e registro da alteração"], ["Qualidade OCR", "Scans variados, rotação, tabelas e multipágina"], ["Comparabilidade ampliada", "Escopo, unidades, moedas, bases percentuais, sublimites e conflitos com testes de domínio"], ["Correção Terra avaliada", "Casos rotulados de recuperação; custo/latência por tarefa"], ["Métricas úteis", "Qualidade, falhas, duração, tokens, memória e cobrança real sem texto sensível"], ["RAG validado", "Recuperação e resposta citada GenAI live antes de ampliar papel no fluxo principal"]], [171, 340], 9)
    p("São propostas, não funcionalidades entregues nem novos requisitos mínimos. Prioridade: funcionamento, identificação correta, revisão e entrega.")
    p("Oficial: PDF técnico, GitHub público, ZIP do código e artefatos, InsurMinds_Projeto_Final.pptx, InsurMinds_Projeto_Final.mp4 <=5 minutos e pasta Projeto_Final_Artefatos/. Checklist: docs/DELIVERY_CHECKLIST.md. Vídeo local real foi gerado; ZIP e acesso público devem ser conferidos no fechamento. Este gerador não publica nem envia e-mail.")
    p("Prazo registrado: 06/10/2026 às 23h59, sujeito a comunicação institucional de alteração; fuso não explicitado na fonte. Identificação do grupo e revisão humana permanecem pendentes. Nenhum artefato local significa submissão realizada.")

    page("12. Fontes e referências utilizadas")
    p("[R1] I2A2 / InsurMinds. Desafios1.pdf; cabeçalho 15/07/2026; autor indicado: Celso Azevedo. Fonte oficial fornecida em capturas pelo usuário. Informações gerais pp. editoriais 4–6; Projeto Final pp. 20–25. Sem URL público fornecido; nenhum link foi inventado.")
    references = [
        ("R2", "OpenAI — Responses API", "https://developers.openai.com/api/docs/guides/migrate-to-responses", "Adaptador Responses; execução comprovada pelo diagnóstico local"),
        ("R3", "OpenAI — Structured Outputs", "https://developers.openai.com/api/docs/guides/structured-outputs", "JSON estruturado; validação local continua necessária"),
        ("R4", "PyMuPDF — Page API", "https://pymupdf.readthedocs.io/en/latest/page.html", "Texto e rasterização"),
        ("R5", "UB Mannheim — Tesseract Windows", "https://github.com/UB-Mannheim/tesseract/wiki", "Distribuição validada, versão fixada 5.4.0.20240606"),
        ("R6", "WinGet — manifesto Tesseract", "https://github.com/microsoft/winget-pkgs/blob/master/manifests/u/UB-Mannheim/TesseractOCR/5.4.0.20240606/UB-Mannheim.TesseractOCR.installer.yaml", "Hash do pacote portátil"),
        ("R7", "Tesseract — tessdata_fast", "https://github.com/tesseract-ocr/tessdata_fast/tree/87416418657359cb625c412a48b6e1d6d41c29bd", "Idiomas locais por/eng"),
        ("R8", "Groq — modelos", "https://console.groq.com/docs/models", "Provedor original; modelos configuráveis"),
        ("R9", "SentenceTransformers — revisão do modelo", "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/tree/e8f8c211226b894fcb81acc59f3b34ba3efd5f42", "Encoder de 384 dimensões medido"),
        ("R10", "MVP original — Leo / leo-vilelela", ORIGIN, "Histórico/licença preservados; não identifica grupo atual"),
    ]
    for code, title, url, purpose in references:
        p(f"[{code}] {title}. {url}\nUso: {purpose}. Referências de tecnologia consultadas em 03/10/2026.")

    page("13. Fontes públicas e delimitação dos dados")
    p("data/samples/SOURCES.md registra estas fontes e download em 30/09/2026 no projeto original. Não se afirma leitura integral nem submissão desses documentos aos provedores nesta validação. São condições gerais com anos/escopos diferentes; não necessariamente incluem prêmio, franquia ou limite de apólice emitida.")
    p("[R11] Chubb — RC D&O Capital Aberto, SUSEP 15414.900831/2017-45, versão a partir de 16/12/2025. https://www.chubb.com/content/dam/chubb-sites/chubb-com/br-pt/condicoes-gerais/diretores-e-administradores/capital-aberto-processo-susep-15414-900831-2017-45-versao-a-partir-de-16-12-2025.pdf")
    p("[R12] AIG — D&O Capital Fechado, condições de 2017. https://www.aig.com.br/content/dam/aig/lac/brazil/documents/brochure/2017-cc-deo-553-capital-fechado.pdf")
    p("Fontes públicas registradas não são automaticamente comparáveis em todos os campos. Registro de procedência evita confundir condições gerais com dados individuais. PDFs públicos de amostra não são apresentados como novas criações do grupo.")
    table(["Tipo de fonte", "Uso efetivamente evidenciado"], [["Documento oficial I2A2", "Requisitos/recomendações/entrega nas pp. citadas [R1]"], ["Código e docs locais", "Arquitetura, schemas, testes, matriz e limites"], ["Documentação primária", "APIs e tecnologias [R2–R9]"], ["Chubb / AIG registradas", "Catálogo público; não processadas nos smokes citados [R11–R12]"], ["Fontes sintéticas A/B", "Extração/comparação live OpenAI e Groq"], ["Diagnósticos OCR / RAG", "Medições reais em textos/chunks sintéticos; sem apólices privadas"]], [155, 356])
    p("Evidências locais adicionais: docs/VALIDATION_REPORT.md; docs/RAG_PERFORMANCE.md; scripts/benchmark_rag.py; tests/test_vector_cache.py; tests/test_ocr_hardening.py; tests/ de provedores/comparação/interface; docs/REQUIREMENTS_TRACEABILITY.md. URLs opcionais/catálogo também podem ser citados em relatórios comparativos, sem transformar arquivo local em fonte pública inexistente.")

    md = ["# InsurMinds — Relatório Técnico do Projeto Final", "", "Preparação local • revisão humana pendente • snapshot 03/10/2026", ""]
    for block in blocks:
        if block["type"] == "page": md += ["## " + block["title"], ""]
        elif block["type"] == "paragraph": md += [block["text"], ""]
        elif block["type"] == "diagram": md += ["```mermaid", "flowchart LR", "    A[PDF / imagem] --> B[OCR local e páginas]", "    B --> C[Segmentação e GenAI]", "    C --> D[Pydantic, JSON e SQLite]", "    D --> E[Comparação com evidências]", "    E --> F[Streamlit e exportações]", "    C -. opcional .-> G[Embeddings, Chroma e RagAgent]", "```", ""]
        elif block["type"] == "table":
            safe = lambda value: str(value).replace("|", "\\|").replace("\n", "<br>")
            md += ["| " + " | ".join(map(safe, block["headers"])) + " |", "| " + " | ".join("---" for _ in block["headers"]) + " |"]
            md += ["| " + " | ".join(map(safe, row)) + " |" for row in block["rows"]]
            md += [""]
    return blocks, "\n".join(md)

class Deck:
    """Editable vector slides and matching PDF previews share one layout."""
    def __init__(self):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(13.333333), Inches(7.5)
        self.pdf = fitz.open()
        self.slide = self.page = None
        self.pages = 0

    def rect(self, x, y, w, h, fill):
        shape = self.slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Pt(x), Pt(y), Pt(w), Pt(h))
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(fill)
        shape.line.fill.background()
        self.page.draw_rect(fitz.Rect(x, y, x+w, y+h), fill=rgb(fill), color=rgb(fill), width=0)

    def text(self, x, y, w, h, value, size=20, bold=False, color=INK):
        lines = wrap(value, w-4, size, bold)
        line_height = size*1.2
        if len(lines)*line_height > h-3:
            raise ValueError(f"Slide {self.pages}: text overflow {value[:60]!r}")
        box = self.slide.shapes.add_textbox(Pt(x), Pt(y), Pt(w), Pt(h))
        frame = box.text_frame
        frame.clear()
        frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = Pt(0)
        frame.word_wrap = False
        for number, line in enumerate(lines):
            paragraph = frame.paragraphs[0] if number == 0 else frame.add_paragraph()
            paragraph.text = line
            paragraph.font.name = "Arial"
            paragraph.font.size = Pt(size)
            paragraph.font.bold = bold
            paragraph.font.color.rgb = RGBColor.from_string(color)
            paragraph.space_before = paragraph.space_after = Pt(0)
            paragraph.line_spacing = Pt(line_height)
            self.page.insert_text((x, y+size+number*line_height), pdf_text(line), fontsize=size, fontname="Strong" if bold else "Body", color=rgb(color))

    def new(self, title):
        self.pages += 1
        self.slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        self.page = self.pdf.new_page(width=960, height=540)
        self.page.insert_font(fontname="Body", fontbuffer=REG.buffer, set_simple=True)
        self.page.insert_font(fontname="Strong", fontbuffer=BOLD.buffer, set_simple=True)
        self.rect(0, 0, 960, 540, WHITE)
        self.rect(0, 0, 960, 8, TEAL)
        self.text(44, 28, 840, 18, "INSURMINDS  /  PROJETO FINAL D&O", 11, True, TEAL)
        self.text(44, 64, 873, 83, title, 31, True, NAVY)
        self.rect(44, 501, 872, 1, "D5E1E6")
        self.text(44, 514, 790, 16, "Preparação local • revisão humana pendente • evidências de 03/10/2026", 9, False, GRAY)
        self.text(891, 514, 25, 16, str(self.pages), 9, True, TEAL)

    def card(self, x, y, w, h, heading, body, size=18):
        self.rect(x, y, w, h, PALE)
        self.rect(x, y, 5, h, TEAL)
        self.text(x+17, y+15, w-33, 52, heading, 21, True, NAVY)
        self.text(x+17, y+74, w-33, h-85, body, size)


def build_deck(evidence, args, preview):
    deck = Deck()
    deck.new("Comparar apólices D&O\ncom evidências")
    deck.text(44, 173, 550, 135, "PDF ou imagem -> dados estruturados -> diferenças citadas", 26)
    deck.rect(650, 172, 266, 207, NAVY)
    deck.text(673, 193, 215, 80, "MVP funcional\nPython + GenAI", 24, True, WHITE)
    deck.text(673, 294, 215, 64, "Interface local\nPDF / JSON / Markdown", 17, False, WHITE)
    deck.text(44, 342, 558, 76, "Grupo, representante e integrantes: identificação pendente. Autoria original do MVP: Leo / leo-vilelela.", 18, False, GRAY)
    deck.text(44, 443, 870, 30, "Projeto Final I2A2 / InsurMinds • código original preservado • licença MIT", 16, True, TEAL)

    deck.new("O problema: informação dispersa\ne difícil de confrontar")
    deck.card(44, 176, 279, 250, "Documentos longos", "Cláusulas, coberturas, exclusões, franquias e limites em linguagem técnica.")
    deck.card(340, 176, 279, 250, "Fontes diferentes", "PDF textual e páginas digitalizadas exigem caminhos de extração distintos.")
    deck.card(637, 176, 279, 250, "Revisão com contexto", "Página e trecho permitem conferir diferenças sem confiar apenas no resumo.")
    deck.text(44, 451, 872, 31, "Objetivo: apoiar revisão, com funcionamento prioritário sobre complexidade.", 19, True, TEAL)

    deck.new("Um fluxo local, modular e demonstrável")
    labels = [("1  Recepção", "PDF / imagem\nValidação + SHA-256"), ("2  Texto", "PyMuPDF nativo\nTesseract por página"), ("3  GenAI", "Cláusulas + 27 campos\nPágina e excerto"), ("4  Validação", "Pydantic + regras\nJSON / SQLite"), ("5  Comparação", "Regras numéricas\nSemântica citada"), ("6  Resultados", "Streamlit local\nPDF / JSON / MD")]
    for index, (title, body) in enumerate(labels):
        deck.card(44+(index % 3)*296, 160+(index // 3)*145, 279, 128, title, body, 16)
    deck.text(44, 461, 873, 27, "Ramo opcional: embeddings locais -> Chroma -> RagAgent.", 18, True, TEAL)

    deck.new("IA interpreta; contratos e regras\nverificam a saída")
    deck.card(44, 172, 422, 264, "IA Generativa", "Extração por cláusula; títulos ambíguos; comparação semântica em lotes limitados.\nOpenAI e Groq selecionáveis.", 20)
    deck.card(494, 172, 422, 264, "Verificação local", "Schema, página, excerto, valores, moeda, datas e conflitos.\nAusente: nao_localizado.\nUma correção de saída inválida.", 20)
    deck.text(44, 452, 874, 36, "OpenAI: Luna extrai, Terra corrige, Sol compara. Terra não foi necessária nos smokes.", 16, True, TEAL)

    deck.new("Integração real validada em duas\nfontes sintéticas")
    deck.text(44, 164, 872, 55, "A: PDF nativo  |  B: PNG com OCR  |  1 página e 1 cláusula por documento", 18, False, GRAY)
    deck.card(44, 221, 423, 163, "OpenAI: PASS", "3 chamadas HTTP reais\n2 extrações + 1 comparação\nRetomada: 0 novas chamadas", 18)
    deck.card(494, 221, 422, 163, "Groq: PASS", "3 chamadas HTTP reais\n2 extrações + 1 comparação\nRetomada: 0 novas chamadas", 18)
    deck.text(44, 403, 871, 52, "Exemplo observado: LMG A R$ 10 mi / B R$ 8 mi; franquia A R$ 100 mil / B R$ 200 mil.", 20, True, NAVY)
    deck.text(44, 466, 872, 24, "Vantagem pontual; testes não elegem melhor apólice nem medem precisão de mercado.", 13, False, ORANGE)

    deck.new("Cada diferença mantém\na evidência das duas fontes")
    fields = {item["field_name"]: item for item in evidence["openai"]["comparison"]["differences"]}
    limit = fields["limite_maximo_garantia"]
    deck.card(44, 173, 423, 190, "Fonte A • página 1", "“"+limit["citation_a"]["excerpt"]+"”\nPDF textual sintético", 21)
    deck.card(494, 173, 422, 190, "Fonte B • página 1", "“"+limit["citation_b"]["excerpt"]+"”\nPNG sintético com OCR", 21)
    deck.text(44, 388, 872, 55, "Maior limite nominal favorece A neste campo, em BRL. Revisão humana do escopo continua necessária.", 20, True, NAVY)
    deck.text(44, 458, 872, 32, "Citação não prova toda interpretação; nao_localizado não significa ausência de cobertura.", 14, False, ORANGE)

    deck.new("Evidências de engenharia\ne recursos medidos")
    count = f"{args.test_count} testes + {args.subtest_count} subtestes" if args.test_count else "Suíte consolidada no estado atual"
    deck.card(44, 172, 279, 221, "Qualidade local", count+"\npip check: PASS\nCache e falhas cobertos", 19)
    deck.card(340, 172, 279, 221, "OCR real local", "PDF nativo, PNG e PDF digitalizado.\nTesseract por+eng\n200 DPI no scan", 19)
    deck.card(637, 172, 279, 221, "RAG opcional", "4 chunks, CPU, 0 LLM.\nModelo inicial: 25,56 s\nRSS ~1192 MiB", 18)
    deck.text(44, 414, 872, 65, "Medições sintéticas pequenas: não estimam tempo, custo ou precisão de apólices completas. Reutilizar download exige o mesmo cache configurado.", 18, False, GRAY)

    deck.new("Limitações conhecidas\ne evolução com evidência")
    deck.card(44, 172, 422, 281, "Limites atuais", "Live com casos sintéticos simples.\nOCR varia com layout/qualidade.\nConfiança não calibrada.\nCobrança real não apurada.\nRAG GenAI live pendente.", 19)
    deck.card(494, 172, 422, 281, "Próximas avaliações", "Corpus público rotulado e citado.\nRevisão humana dos campos.\nScans e tabelas multipágina.\nEscopo, moedas e percentuais.\nQualidade, latência e custo real.", 19)
    deck.text(44, 470, 872, 22, "Evoluções propostas: não são novas obrigações nem capacidades já demonstradas.", 14, True, TEAL)

    deck.new("Entrega preparada para\nconferência e revisão humana")
    deck.card(44, 172, 422, 226, "Materiais locais", "Relatório PDF e fonte MD.\nPitch editável de 9 slides.\nVídeo real: 2 min 45,44 s.\nZIP: conferir fechamento.", 19)
    deck.card(494, 172, 422, 226, "Conferência final", "Confirmar grupo e integrantes.\nRevisar os artefatos.\nAutorizar repositório público.\nVídeo em cache, APIs bloqueadas.", 19)
    deck.text(44, 416, 872, 46, "Fonte oficial: Desafios1.pdf, pp. 20–25. Prazo: 06/10/2026, 23h59; fuso não explicitado.", 17, False, GRAY)
    deck.text(44, 469, 872, 20, "Fontes e referências no relatório • sem e-mail ou publicação por este gerador", 12, True, TEAL)

    path = OUT / "InsurMinds_Projeto_Final.pptx"
    deck.prs.core_properties.title = "InsurMinds — Projeto Final D&O"
    deck.prs.core_properties.subject = "Pitch local para revisão; dados sintéticos explicitados"
    deck.prs.core_properties.author = "Identificação do grupo pendente"
    deck.prs.save(path)
    deck.pdf.save(preview / "pitch_layout_preview.pdf", deflate=True)
    for index, page in enumerate(deck.pdf):
        page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2)).save(preview / f"slide_{index+1:02d}.png")
    return {"slides": len(deck.prs.slides), "path": str(path.relative_to(ROOT)), "preview_note": "Vector PDF layout reproduction, not Office rendering"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-bundle", type=Path, help="Read one historical synthetic evidence snapshot explicitly; never calls an API")
    parser.add_argument("--test-count", type=int)
    parser.add_argument("--subtest-count", type=int, default=0)
    parser.add_argument("--test-seconds", type=float, default=0)
    parser.add_argument("--preview-dir", type=Path, default=ROOT / "data/processed/delivery_preview")
    args = parser.parse_args()
    if args.test_count is not None and (args.test_count < 1 or args.subtest_count < 0 or args.test_seconds <= 0):
        parser.error("QA snapshot requires positive tests/duration and nonnegative subtests")
    OUT.mkdir(exist_ok=True)
    args.preview_dir.mkdir(parents=True, exist_ok=True)
    evidence = read_evidence(args.evidence_bundle)
    blocks, markdown = build_report(evidence, args)
    (OUT / "InsurMinds_Relatorio_Tecnico.md").write_text(markdown, encoding="utf-8")
    report = ReportPDF()
    for block in blocks:
        if block["type"] == "page": report.new(block["title"])
        elif block["type"] == "paragraph": report.paragraph(block["text"])
        elif block["type"] == "diagram": report.diagram()
        elif block["type"] == "table": report.table(block["headers"], block["rows"], block["widths"], block["size"])
    report.finish(OUT / "InsurMinds_Relatorio_Tecnico.pdf")
    for index, page in enumerate(report.doc):
        page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2)).save(args.preview_dir / f"report_{index+1:02d}.png")
    pitch = build_deck(evidence, args, args.preview_dir)
    summary = {"status": "BUILT_FOR_HUMAN_REVIEW", "api_calls_by_builder": 0, "report_pages": len(report.doc), "report_searchable": True, "pitch": pitch, "qa_snapshot": {"test_count": args.test_count, "subtest_count": args.subtest_count, "seconds": args.test_seconds}, "team_identification": "PENDING", "publication": "NOT_EXECUTED", "email": "NOT_SENT"}
    (args.preview_dir / "build_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
