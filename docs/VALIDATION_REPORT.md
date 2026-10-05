# Validação local — 03/10/2026

Checkpoint de código: `d1777a6`, branch `feature/i2a2-mvp-hardening`; origem `02802f6`. Alterações documentais e artefatos locais posteriores são registradas no WORKLOG. Este registro comprova o cenário observado, sem declarar a entrega acadêmica completa.

## Gates e causas verificadas

| Gate | Resultado | Evidência e limite |
|---|---|---|
| 0 — baseline | PASS: causas identificadas | Upstream sem src/agents e main.py; 6 erros de coleta/importação. ZIP original contém instruções Markdown, não agentes executáveis. docs/BASELINE_AUDIT.md |
| 1 — runtime | PASS Windows | Python3.11, dependências originais completas, OpenAI SDK; pip check sem conflitos; imports src PASS; Streamlit health200 |
| 2 — OCR | PASS sintético | Texto nativo, PNG e PDF rasterizado: reconhecimento esperado. Tesseract5.4.0 por+eng portátil com hashes verificados. scripts/setup_tesseract.ps1 |
| 3 — providers/resiliência | PASS | OpenAI/Groq conectividade e E2E reais; limites/retry/quota/JSON testados por mocks. Quota histórica e atraso30min não foram reproduzidos como incidentes atuais |
| 4 — estruturação | PASS sintético | Campos Pydantic, valores/trechos/páginas, SQLite e cache verificados nos smokes reais; reparo Terra coberto por mock, não foi necessário live |
| 5 — comparação | PASS sintético | LMG/franquia/Side A esperados; datas não ordenadas como vantagem; duas fontes citadas; MD/PDF/JSON legíveis |
| 6 — interface | PASS AppTest e startup | Dois testes: provedor, entrada ausente amigável, três downloads persistentes. Edge headless:2uploads/3downloads/JSON conferido/0exceções; reexecução em cache com API bloqueada |
| 7 — QA | PASS | pytest:85 testes e22subtestes em5.84s;29originais preservados;pipcheckPASS. Não repetir chamadas reais somente para confirmar cache |
| 8 — documentação/entrega | PARTIAL | Matriz72IDs/checklist/README existentes; equipe/publicação/revisão final pendentes; PDF14p/PPTX9slides/MP4165.44s/ZIP preparado e verificado |

A única modificação de teste original foi fechar explicitamente duas conexões SQLite com closing; a transação de sqlite3.connect não fecha o arquivo no Windows. Assertivas mantidas.

## Inferência real e custo

| Ensaio | Modelo | HTTP | Tokens entrada / saída | Resultado |
|---|---|---|---|---|
| Ping OpenAI neutro | gpt-5.6-luna | 1 | 12 / 5 | PASS,3.088s |
| Ping Groq neutro | openai/gpt-oss-20b | 1 | 77 / 36 | PASS,0.396s |
| E2E OpenAI | Luna extração;Sol comparação | 3 | 3589 / 1980 | PASS,zero retries |
| E2E Groq | openai/gpt-oss-120b | 3 | 2428 / 3530 | PASS,zero retries |

Os dois E2E receberam PDF nativo A e PNG sintético B com OCR real; uma cláusula por documento. Conferiram limites10M/8M, franquias100k/200k, Side A inclui/exclui defesa, evidências literais, armazenamento e relatórios. Ambos retomaram com2cláusulas em cache e0novas chamadas.

O orçamento prévio foi3chamadas esperadas, máximo6lógicas/18tentativas HTTP. Uso agregado não permite atribuir custo exato por modelo. PHASE A: o usuário informou cobrança real aproximada US$0.00703 e inscrição complimentary/projeto InsurMinds-I2A2; auditoria estática em OPENAI_BILLING_AUDIT, causa não determinada e sem novas inferências. Tempos de ping não estimam latência de uma apólice. Os smokes originais não gravaram duração total nem todos eventos por chamada; o script agora grava esses metadados para execuções futuras, sem repetir API para preencher lacunas.

Diagnósticos locais ignorados pelo Git: data/processed/provider_smoke.json; e2e_smoke_openai/diagnostics.json; e2e_smoke_groq/diagnostics.json.

## OCR e RAG

OCR sintético real: nativo1.45ms,PNG672.218ms,PDFdigitalizado897.507ms@200DPI; diagnóstico em data/processed/ocr_validation. Não é avaliação estatística de qualidade.

RAG: modelo e Chroma reais em quatro textos sintéticos; cold download/load25.5578s e479729245bytes; primeiro encode1.3458s; reindexcached0.0421s; novoencoder/cache0.0152s; RSSobservado1192MiB. docs/RAG_PERFORMANCE.md detalha método. Cache antigo recomputava embeddings de todos chunks; agora hashes/modelos/dimensões/valores finitos validados evitam esse custo. Chroma InvalidArgumentError normalizado sem revelar backend. RAG é opcional; não participa da comparação principal na UI.

## Arquivos e checkpoint

Commits:0a82cc0baseline;6bb3258agentes;7dfcdf5providers/UI;bd61be1cacheRAG;d1777a6proveniência/QA. Scanners staged passaram antes de cada commit, sem achados. Nenhum push,PR,publicação,mudança de visibilidade ou e-mail.

Vídeo real:Projeto_Final_Artefatos/InsurMinds_Projeto_Final.mp4,165.44s(2min45.44s),narração localpt-BR,decodificação completaPASS. A gravação reutiliza resultados live OpenAI;0chamadas novas. Evidência:data/processed/browser_qa/diagnostics.json.

PDF14p e PPTX9slides abertos e inspecionados;ZIP89entradas,CRC/extração/fallback/smokeofflinePASS. Dependências reutilizadas da.venv e Tesseract local; não é teste de instalação do zero. Manifestos emdata/processed/delivery_package. Próxima ação:identificação da equipe e revisão final, sem repetir inferência. Nomes da equipe dependem de informação humana. Publicação final depende de autorização específica.

## PHASE C — produto e UX — 04/10/2026

Baseline85/22 acima é histórica e permanece preservada. A494335f/B9674bc8 e Cc1ddb20 antecedem este checkpoint local de UX. Nova suíte consolidada: **156 testes + 27 subtestes PASS em 14.14s**; compileall src/interface/scripts/tests PASS. Os85casos existentes continuam representados; títulos/labels daUI acompanham a especificação. pytest.ini coleta somente tests/, preservando os backups extraídos que antes duplicavam testes.

Streamlit real no Edge, SDKs OpenAI/Groq bloqueados, gateway fake/cache: **3uploads,2comparações contra referência,7abas,3downloadsMD/PDF/JSON,0HTTPproviders,0pageerrors**. JSON baixado na execução atual e citações/diferenças esperadas conferidos; filtros/download/revisão/rerun não inferem. Evidências: data/processed/workspace_browser_qa/diagnostics.json,01_home.png,02_prepared.png,03_results.png,04_evidence.png,05_review.png. Script: scripts/check_workspace_browser.py. AppTest também exercita2/3/5documentos,referência/invalidação,URL/catalogmocks,max5,erroquota,exports e budget/cacheidentity.

Fontes públicas:40testesoffline incluem validaçãoPDF/imagem,DNS/IP/TLS/redirects,deadlineabsolutoheaders/corpo,tamanho/hash/cache e catálogoexigePDF. Workspace:12casos+5subtests para metadados/compatibilidade/matriz/métricas e pairwise; providers:28casosmock incluindo cached_tokens/IDs/tierseguros sem alterar requests; interface: 10 casos. Teste adicional reproduz colisão com filename já sufixado e verifica URLs por fonte; nomes agora são garantidos únicos. Cenário sintético e mocks não comprovam precisão em condições públicas reais.

Quatrohashes dosfinaisPDF/PPTX/MP4/ZIP conferidos iguais àbaselinea946882. Capturas C são para revisãovisual, sem regeneração de vídeo ou outros finais. D ainda pesquisa/preflight e aguarda autorização antesGenAI público; nenhum novo uso/custo real nestas fases.

## PHASE D — pesquisa e preflight local — 04/10/2026

PHASE C concluída em **890ab0b**; métricas acima preservadas. D não altera pipeline/UI ou refaz gates. **Nenhuma chamada OpenAI/Groq real nesta etapa.**

- Cinco PDFs oficiais: **456 páginas nativas**, SHA/páginas/3496 chunks reconferidos; chunks de até4000 caracteres. Retomada do estimador com **5/5 hits de cache OCR local**. Nenhuma validação semântica GenAI/contratação de cobertura comprovada.
- `scripts/preflight_public_validation.py` executado somente local e py_compile PASS. Não constrói cliente de inferência; segmentação gatewayNone e gateway de extração rejeita chamadas. Revisão independente confirmou guard de caminho, SHA, contagens e até2 lotes semânticos porpar.
- Pesquisa primária confirma contrato D&O público2.0.0/RC3.0.0 e JSON de metadados/eventualURL; disponibilidade operacional por participante não testada.
- Par Berkley×AXA:187 p/1100 chunks/40 lotes/até1142 operações sem reparos. Todos os pares frios excedem o limite UI30/100. Ajustar piloto/segmentação e revalidar orçamento antes de qualquer execução autorizada.
- Sompo recusada pelo TLS padrão deste ambiente; captura de pesquisa com CA certifi verificada. Allianz403 no downloader/Edge, sem PDF local ou chunks inventados.
- Custos são cenários, sem leitura de billing/incentivo ou desconto de cache presumido. PDFs/caches/manifestos permanecem locais e ignorados.
- Quatro hashes dos finais congelados reconferidos iguais ao a946882; localhost8501 health200. Nenhum artefato final regenerado/publicação/e-mail.

Consulte [preflight concreto](PUBLIC_VALIDATION_PREFLIGHT.md), [candidatos e hashes](PUBLIC_DOCUMENT_CANDIDATES.md), [pesquisa Open Insurance](OPEN_INSURANCE_RESEARCH.md) e [modelo de custo](PUBLIC_VALIDATION_COST_MODEL.md). Suíte156/27 de C permanece a validação consolidada mais recente; não foi repetida nesta etapa documental sem mudanças de produto. Scanner/diff do checkpoint D registrados no Git/WORKLOG. Aguardando autorização antes de GenAI pública.

Fechamento do checkpoint D: scanner staged **10 arquivos PASS**, zero ocorrências; git diff --cached --check PASS. Script local compilado e executado; conjunto público permanece sem inferência.

## 04/10/2026 — PHASE E1 offline e controle E0.1

Suíte consolidada: **292 testes + 27 subtestes PASS em 22,46 s**, todos offline. Golden set literal de 20 referências: 15 hits iniciais → 20 após fallback, zero misses finais, 13 âncoras críticas recuperadas desde o início. Corpus de 187 páginas/1100 chunks preservado. Os cenários de 42/12 operações guiadas, 207/216 conservadoras e 621/648 tentativas HTTP de stress são planejamento, sem prova de precisão GenAI pública. Arquitetura, estimativas, riscos e piloto: [PUBLIC_EXTRACTION_OPTIMIZATION.md](PUBLIC_EXTRACTION_OPTIMIZATION.md).

QA Edge com SDKs bloqueados PASS: três documentos, dois candidatos, sete abas, downloads, citações, revisão e estado NOT_RETRIEVED; zero HTTP de provedores e zero erros. A UI não mudou após o QA. E0.1 adicional explicitamente autorizada: apenas duas requests neutras Terra/Sol OK, 26 input/10 output totais, API tier default, zero retry; Dashboard pendente conforme [COMPLIMENTARY_MODEL_VALIDATION.md](COMPLIMENTARY_MODEL_VALIDATION.md). GenAI pública ainda não executada; entregáveis finais congelados.
