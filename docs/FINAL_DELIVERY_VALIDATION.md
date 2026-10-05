# Validação final da entrega — InsurMinds

Fonte acadêmica: capturas de **Desafios1.pdf**, páginas editoriais4–6 e20–25, disponibilizadas pelo usuário. Este registro consolida a entrega atual; os gates anteriores e a matriz histórica são preservados. Data da release:05/10/2026. Produto aceito manualmente pelo usuário; código congelado em4506859 antes da documentação final. **Zero novas chamadas reais de providers nesta finalização.**

## Matriz da release

Os mínimos oficiais têm prioridade. OCR, imagem digitalizada e segurança estão desdobrados para rastrear a implementação e a entrega solicitada; boas práticas continuam recomendações. PASS exige evidência concreta. PENDING_ARTIFACT significa gate final ainda não concluído.

| Requisito | Implementação | Evidência | Status |
| --- | --- | --- | --- |
| 01 Leitura PDF | src/agents/ingestion.py; src/agents/ocr.py | tests/test_ingestion_ocr.py; E2E Allianz4p native | PASS |
| 02 Imagem/PDF digitalizado | IngestionAgent/OcrAgent; interface/app.py | E2E Porto4p somente imagens; leitura local | PASS |
| 03 OCR | OcrAgent + Tesseract local por/eng | FINAL_EVIDENCE_SUMMARY.json: tesseract4p; figura07 | PASS |
| 04 Extração automática | src/pipeline.py; src/agents/extraction.py; grouped_extraction.py | 37 requests reais históricas, reports27campos; suíte offline | PASS |
| 05 Dados estruturados | src/schemas/policy.py; src/storage/sqlite_repo.py | Schemas e JSON/SQLite; testes de integração/persistência | PASS |
| 06 Comparação de pelo menos2 documentos | ComparisonAgent; src/workspace.py; referência×candidatos | E2E Allianz×Porto; Edge demo/cache; testes2/3/5 documentos | PASS |
| 07 Diferenças principais | src/product_presentation.py; interface/app.py:render_results | E2E2diferenças/19insuficientes; figura04; Edge6abas | PASS |
| 08 Uso de IA Generativa | src/llm/providers.py; model_routing.py | Telemetria histórica37HTTP/37responses: Terra24/Sol11/Luna2 | PASS |
| 09 Interface demonstrável | interface/app.py, Streamlit | Edge real demo/cache PASS; expansão/página/revisão/export/details | PASS |
| 10 Componentes especializados/modulares (recomendado) | src/agents/; src/llm/; src/storage/; src/workspace.py | Relatório seções6–9; diagrama; imports10 PASS | PASS |
| 11 Tratamento de erros (recomendado) | IngestionError/OcrError/ComparisonError; workspace_recovery.py | tests/; erro LIMITS corrigido4506859; estados conservadores | PASS |
| 12 Credenciais ocultas (recomendado) | src/config.py; .env.example; .gitignore | Scanner149tracked+2valores privados comparados silenciosamente;0localizações | PASS |
| 13 Relatório técnico PDF | Projeto_Final_Artefatos/InsurMinds_Relatorio_Tecnico.pdf | Fonte docs/InsurMinds_Relatorio_Tecnico.md;25p/24seções/8figuras, QA integral PASS | PASS |
| 14 GitHub público final | https://github.com/malandrindev/Apolices-Projeto-Final, main | Identidade/public/defaultmain confirmados; backup remoto082da47; promoção pendente | PENDING_ARTIFACT |
| 15 ZIP de código e artefatos | InsurMinds_Projeto_Final.zip; scripts/package_final_release.py | 165entradas; CRC/hashes/extração limpa/10imports/demo/exports/AppTest PASS,0SDK/HTTP | PASS |
| 16 Pitch Deck nome exato | Projeto_Final_Artefatos/InsurMinds_Projeto_Final.pptx | 10 slides editáveis; PowerPoint1920×1080, inspeção integral PASS | PASS |
| 17 Vídeo nome exato | Projeto_Final_Artefatos/InsurMinds_Projeto_Final.mp4 | Gravação E2E real; COM/SEM e oficial: probe/decode PASS, mesmo bitstream visual | PASS |
| 18 Vídeo máximo5min | Build de vídeo261s a partir do master real | ffprobe261,0s/4min21s;1920×1080/30fps/H264; decode PASS | PASS |
| 19 Pasta Projeto_Final_Artefatos | Pasta exata com PDF/PPTX/MP4/figuras/roteiro | PDF/PPTX/MP4 oficiais e8figuras/roteiro presentes; inventário final | PASS |
| 20 README descrição | README.md: Descrição/Problema/Objetivo | Leitura final; corresponde ao MVP aceito | PASS |
| 21 README instalação | README.md: Pré-requisitos/Instalação/Configuração/Tesseract | Comandos reais; .env.example e scripts/setup_tesseract.ps1 | PASS |
| 22 README execução | README.md: Execução/Como utilizar/Demonstração | Streamlit interface/app.py; demo offline verificada Edge | PASS |
| 23 README tecnologias | README.md: Tecnologias utilizadas | requirements*.txt; imports/pipcheck PASS | PASS |
| 24 README integrantes | README.md: Integrantes | Vitor Ferreira; José Leonardo Alves Vilela; Wagner Assis. README oficial anterior082da47 | PASS |
| 25 README licença MIT | README.md: Licença | Referência ao LICENSE MIT; titularidade de documentos terceiros preservada | PASS |
| 26 LICENSE MIT | LICENSE na raiz | Texto MIT preservado; presente no snapshot e ZIP a conferir | PASS |
| 27 Entregáveis sem segredos | scripts/check_secrets.py; package_final_release.py; auditoria final | Scanner165tracked+2valores reais/PDF/PPTX/ZIP PASS; frames72master+17finais PASS | PASS |

## Evidências e limites

A auditoria atual registrou **601 testes e27subtestes PASS em69,88s**, compileall,10imports e pipcheck PASS. Os SDKs e transportes de IA foram bloqueados durante a suíte;0 construções SDK/0HTTP. Edge real validou demonstração, três operações fake, restauração da sessão histórica em cache, evidência/página, revisão, downloads e detalhes técnicos. **660 arquivos backend/runtime preservados**, sem reconstruir históricos.

A execução real previamente gravada utilizou **Allianz4p nativas e Porto4p Tesseract**, com37requests concluídas. Não corresponde à íntegra dos documentos-base de75/52p. Os27campos estruturados conservam estados ambíguos e não recuperados. Resumo de negócio:2diferenças,0equivalências e19itens com evidência insuficiente; não se declara ranking global. Demo offline é outro cenário: especificações fictícias e comparação pré-processada, sem validade contratual.

Evidência pública resumida: [FINAL_EVIDENCE_SUMMARY.json](../Projeto_Final_Artefatos/FINAL_EVIDENCE_SUMMARY.json). Diagnósticos completos são locais em `data/processed/final_release/audit/`, deliberadamente excluídos da publicação.

## Conteúdo obrigatório do relatório

A fonte editável tem24seções. Arquitetura(seção6), tecnologias(7), componentes/agentes(8), fluxo(9–17), decisões(21), limitações(20), evolução(22) e referências(24) atendem ao relatório técnico exigido. São8figuras atuais: upload/referência, processamento, resumo, diferenças, evidência/página, uso IA, OCR e arquitetura. A revisão visual confere todas as páginas e slides; plano não substitui QA.

Fontes públicas são citadas na seção24. Allianz tem URL oficial primária confirmada; Porto é descrita como CG fornecida pelo usuário, sem URL de origem inventada. As CG genéricas não contêm apólices emitidas ou dados de clientes. Os originais foram mantidos somente para validar os hashes e o mapeamento de59páginas reais da demo. Documentos/marcas de terceiros não são relicenciados sob MIT.

## Vídeos e empacotamento

Master OBS local preservado, excluído de Git/ZIP. Os dois candidatos usam o mesmo corte visual real; esperas aceleradas são identificadas. Narração usa somente voz local Windows. Seleção oficial, metadados, duração e decode serão registrados após QA. Alternativas ficam locais em `Projeto_Final_Artefatos/video_versions/`; roteiro Markdown é publicável.

ZIP limpo contém README/LICENSE/.env.example, requirements, src/interface/tests/scripts/docs, demo segura e PDF/PPTX/MP4. Exclui .env/.git/venv/cache/tooling/master/alternativas binárias/arquivos temporários. Inventário e hashes completos ficam no manifesto local da release.

## Publicação e entrega administrativa

Destino autorizado: **https://github.com/malandrindev/Apolices-Projeto-Final**, **main**, visibilidadePUBLIC. Clone separado `C:/dev/I2A2/Apolices-Projeto-Final-PUBLICATION`. Main anterior **082da47d8f034ed266603d8ec8b317113130d647**; backup **backup/pre-final-insurminds** já publicado e conferido no mesmo SHA. Nenhum forcepush ou exclusão do repositório. Promoção final e conferência remota pendentes neste registro de trabalho.

Prazo oficial: **06/10/2026 às23h59**, sem fuso especificado nas capturas. Envio pelo representante para **challenges@i2a2.academy**, assunto **InsurMinds – Projeto Final**, CC aos integrantes, identificação `Entrega do grupo: <nome real>`. Ver [DELIVERY_CHECKLIST.md](DELIVERY_CHECKLIST.md). Nome do grupo, representante e e-mails precisam de confirmação humana; nomes dos três integrantes constam do README oficial anterior. **Nenhum e-mail enviado.**

## QA documental concluído

Relatório **25páginas**,24seções e8figuras: renderização e inspeção de todas as páginasPASS; nenhum texto fora da página. PDF **2.448.013bytes**, SHA256 `9a7214c923d69701fb58bde440cbf1300f87bafc24ccb1b80d141ec0c9e2ff21`. Pitch **10slides editáveis**, renderização nativa PowerPoint1920×1080 e inspeção integralPASS; ajuste de caixa do slide3 concluído. PPTX **1.063.115bytes**, SHA256 `4f550adf2dc0ba0a1845a26b2257e5802e72359002e92bac7577922fe5423bc3`. Scanner textual/metadata sem padrões de credenciais. QA local: `data/processed/final_release/documents/visual_qa.json`. Backups anteriores preservados fora da entrega pública.

## Vídeos concluídos

**Candidato oficial:SEM_NARRACAO**. Ambos têm **261,0s(4min21s),1920×1080,30fps,H264**, e idêntico bitstream visual. COM tem **AAC48kHz**,14.560.310bytes,SHA256 `92fbd4546ea6b0778082b5a08b917419a09ca138b64709b51f405e8027f25540`; SEM e oficial têm **nenhuma faixa de áudio**,10.505.676bytes,SHA256 `d8753ad1bbb883e856ff8985b2d1761a337e7c0885c7de8a35bd039be2dcfc7f`. Decode dos trêsPASS, oficial abaixo95MB. Narração:Windows System.Speech, voz **Microsoft Daniel**,ptBR, inteiramente local, sem alegação de voz neural. Script sincronizado00:00–04:21 em video_versions/ROTEIRO_NARRACAO_PTBR.md. Conteúdo cobre problema/arquitetura/funcionamento/resultados. Espera acelerada identificada, revisão/evidência/OCR e limites visíveis. O master originalSHA2971a615... permanece intacto. Manifesto local:`data/processed/final_release/video_build/video_validation.json`.

Qualidade técnica da narraçãoPASS: sem clipping, pico-1,2dB, capítulos sincronizados e pausas de1,4–3,9s. **Qualidade perceptiva:PENDING_HUMAN_LISTENING; NARRATION_TTS_QUALITY_BLOCKER:YES** porque o canal deste ambiente não suporta escuta. Não há afirmação de que a voz é ruim, nem escuta fictícia. O candidatoSEM foi selecionado como oficial por satisfazer integralmente os quatro conteúdos exigidos sem essa pendência. A alternativaCOM permanece disponível para escolha após escuta do usuário; essa pendência não bloqueia a entrega oficial sem narração. Scanner local dos17quadros finaisPASS, nenhuma localização/falha de OCR.

## Empacotamento e smoke da extração limpa

O ZIP pré-publicação foi extraído em diretório novo, sem .env privado: **165entradas, CRC/caminhos/symlinks/duplicatas/hashes/arquivos obrigatóriosPASS**. Dez imports da cópia isolada passaram; demoPorto30p/Allianz33p,27campos, mapas/literais/SHA e referência invertidaPASS. ExportsPDF13p/JSON27campos/MD e jornadaAppTest cold→demo→comparar→6abas→8downloads→página/evidência→revisão→rerunPASS, originalpreservado. **0SDK/HTTP/gateway**. Logs locais:`data/processed/final_release/audit/zip_smoke.json` e `zip_smoke_child.json`. Código/demos/artefatos não mudaram após esse smoke; somente os registros de conclusão de gates são incorporados à documentação. Cada ZIP atualizado é novamente conferido por inventário/CRC/hashes/scanner.
