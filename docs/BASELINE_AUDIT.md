# Baseline audit â€” 2026-10-03

- Upstream: https://github.com/leo-vilelela/desafio_final_insurminds_comparacao_entre_apolices
- Commit original: `02802f6cca9a54064c0425a4882301595e6d18ff` â€” Primeiro commit.
- Remoto local: origin, upstream; branch `feature/i2a2-mvp-hardening`, sem tracking. Nenhum push/PR.
- Pasta inicialmente vazia; histÃ³rico original preservado via init/fetch/switch.
- Windows 10 build 19045, PowerShell, Python 3.11.0, pip global 25.2, Git 2.52.0; sem WSL ativo.
- DiferenÃ§a de proprietÃ¡rio no sandbox: Git usa `-c safe.directory=C:/dev/I2A2/Apolices-Projeto-Final-I2A2` por comando, sem configuraÃ§Ã£o global.
- Shell e apply_patch no sandbox falham apÃ³s inicializaÃ§Ã£o com helper_unknown_error/setup refresh ou Failed to write file. Escalonamento revisado funciona; operaÃ§Ãµes limitadas ao trabalho local autorizado.
- Virtualenv .venv criado. DependÃªncias diretas originais resolvendo; wheel torch Windows 241,4 MB (pip embarcado baixa wheels em dry-run).
- InstalaÃ§Ã£o global: Streamlit 1.54.0/pytest 9.1.1; ausentes groq/dotenv/pydantic/PyMuPDF/pytesseract/Chroma/Sentence Transformers/OpenAI.
- Tesseract nÃ£o localizado no PATH nem Program Files. OPENAI_API_KEY e GROQ_API_KEY ausentes (somente presenÃ§a booleana verificada). NÃ£o Ã© evidÃªncia de chave invÃ¡lida.
- 34 arquivos versionados; 25 Python; 6 arquivos de testes, 29 mÃ©todos test_ (README anuncia 28).
- AUSENTES: src/agents/{ingestion,ocr,segmentation,extraction,comparison,report,rag}.py e main.py.
- Imports quebrados em pipeline, CLIs, SQLite, DocumentStore, interface e quatro arquivos de testes.
- ZIP Copilot: 26 entradas Markdown, nenhum .py; nÃ£o recupera backend. Sem extraÃ§Ã£o/execuÃ§Ã£o.
- Scan de todos arquivos e conteÃºdo descomprimido ZIP: nenhum padrÃ£o conhecido de credencial. NÃ£o equivale a auditoria exaustiva. .env ignorado e nÃ£o tracked; template vazio criado localmente.
- Arquitetura preservÃ¡vel: Streamlit, Pydantic, PyMuPDF, JSON cache atÃ´mico, gateway Groq, SQLite/RAG lazy.
- UI: gateways separados por etapa/documento, resultados perdidos em reruns de downloads, spinner sem progresso.
- Groq: nÃºmero de tentativas limitado; espera Retry-After sem teto; quota/crÃ©dito nÃ£o diferenciados.
- RAG: reindex calcula todos embeddings; downloads/modelo CPU ainda nÃ£o medidos. RAG nÃ£o Ã© mÃ­nimo oficial; nÃ£o removido.
- ProveniÃªncia no schema: 27 campos, pÃ¡gina/trecho/confianÃ§a; confianÃ§a fornecida por LLM nÃ£o calibrada.

EvidÃªncia executada antes dos reparos:

- `python -m unittest discover -s tests -v`: 6 erros de importaÃ§Ã£o; nenhum teste de negÃ³cio executado.
- `python -m pytest --collect-only -q`: 6 erros de coleta.
- `python -m streamlit --version`: 1.54.0 global; startup aplicaÃ§Ã£o pendente.
- `python -m venv .venv`: concluÃ­do.
- `.venv/Scripts/python -m pip install --dry-run -r requirements.txt`: em andamento.

Edital: capturas Desafios1.pdf fornecidas pelo usuÃ¡rio, pÃ¡ginas editoriais 4â€“6 e 20â€“25; sem alegar PDF local. Matriz/checklist em preparaÃ§Ã£o; prazo oficial 06/10/2026 23h59.

DecisÃ£o: restaurar mÃ³dulos ausentes pelos contratos e testes existentes, preservar arquitetura, avanÃ§ar GATE 1. GenAI real aguarda chave local; testes offline independentes.
