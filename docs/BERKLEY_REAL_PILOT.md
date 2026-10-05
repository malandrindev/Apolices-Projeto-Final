# BERKLEY_REAL_PILOT — PHASE E2

## Recuperação forense antes de qualquer request real

Em 04/10/2026, retomada autorizada pelos anexos f9dca7d7 e d6c4fe70 sobre HEAD E1 `8b39785`, branch `feature/product-ux-hardening`. Git status/branch/log15/diff/cached e CURRENT_STATE/WORKLOG/política/plano foram lidos. Alterações E2 existentes foram preservadas; staged vazio; sem reset, clean ou rollback.

Busca dos arquivos persistentes encontrou apenas `berkley_pilot_plan.json` e `berkley_pilot_manual_reference.json`. Não existiam claim, manifesto de execução, attempt ledger, telemetria, partial output, response ID nem cache de execução do piloto.

| Evidência anterior | Quantidade encontrada |
|---|---:|
| Chamadas iniciais planejadas | 7 |
| Tentativas reservadas | 0 |
| Envios comprovados | 0 |
| Tentativas concluídas | 0 |
| Response IDs | 0 |
| Resultados persistidos | 0 |
| Tentativas incertas | 0 |
| Duplicações anteriores detectadas | 0 |

Estado: **NOT_STARTED**. O próximo logical step ainda não havia sido reservado. Esta conclusão deriva dos artefatos locais e do contrato que exige claim/reserva antes do provider, sem usar ausência de mensagem no chat como prova.

Se houver reserva sem prova de envio/retorno em recuperação futura: **UNCERTAIN_PREVIOUS_ATTEMPT**; continua consumida no orçamento e nunca é repetida automaticamente. Claim/manifesto/resultado/telemetria existentes bloqueiam replay. O CLI não oferece reinício destrutivo; recuperação com ledger inconsistente exige revisão, preservando arquivos.

## Preflight offline

Fonte pública oficial Berkley: [Seguro D&O — vigência a partir de 11/12/2025, v2](https://www.berkley.com.br/wp-content/uploads/2022/03/Seguro-DO_Vigencia-a-partir-de-11.12.2025_v2.pdf). Processo SUSEP 15414.901494/2017-11. São condições públicas; não comprovação de contratação individual.

- SHA-256: `038683e096c2ae0378df25ef18d147493f72627399d1530310af14023df8fea8`.
- Corpus integral: 83 páginas, 480 chunks, 242537 caracteres nativos; cache OCR local.
- Candidatos iniciais: 80; sete grupos/27 campos; 7 lotes iniciais previstos.
- Orçamento: **50 TOTAL HTTP ATTEMPTS**; 43 restantes após o plano inicial, reserva informativa 35 fallback/8 verifier compartilhada.
- Plano local: `data/processed/berkley_pilot_plan.json`; estimativa inicial 33060 input tokens, 10500 output assumidos; reserva UTF-8 99814 input e cap agregado inicial 31500 output.
- Custo inicial contrafactual estimado sem cached input: US$ 0,1410594 com saída assumida; reserva inicial US$ 0,4231988. Fallbacks/escalations/verifier podem aumentar consumo. Essas estimativas não são billing observado nem teto monetário.
- Golden E1 imutável: 20 âncoras; Berkley 17/11 críticas. Referência manual independente preservada antes da inferência; nenhum desses controles entra no prompt.
- Sem GenAI no dry-run, sem outro documento e sem comparação.


## Resultado real e stop

**PHASE E2: controles implementados e validados; piloto PARTIAL_STOPPED; gate de qualidade FAIL.** A única execução real terminou por circuit_open. Não foi repetida nem retomada; nenhuma inferência depois dela. A saída original fica intacta, com a revisão em arquivo separado.

- 21 reservas duráveis / 21 envios com retorno comprovado / 21 response IDs únicos.
- 19 respostas aceitas pelo contrato de schema e 2 rejeitadas por SCHEMA_INVALID; todas as 21 respostas do provider retornaram completed.
- 19 tentativas com resultado estruturado associado a logical_step_id persistido; 27 estados de campo preservados.
- Zero tentativas incertas ou retornos sem merge pendente; zero duplicações observadas. Bloqueio de replay protege as 21 tentativas, sem alegar que 21 duplicações foram efetivamente tentadas.
- 29 tentativas aritmeticamente restantes. O circuito e a autorização encerrada impedem usá-las.
- 20 operações lógicas / 21 HTTP; 7 lotes iniciais, 13 lotes adicionais de retrieval; união local ampliada 248 chunks. Corpus integral continua 83/480; não confundir união local com trechos enviados.
- 20 primary calls + 1 technical fallback; zero semantic escalations, verifier, Sol ou comparação. A parada ocorreu antes de alcançar adjudicação.

O breaker abriu por **2/10 = 20% de falhas técnicas locais de schema**, tentativas 16 e 21, e não por três consecutivas. Houve zero falhas HTTP/transporte observadas. Rejeição de schema pode significar JSON/Pydantic/campo faltante, extra ou duplicado; a telemetria não distingue essa subcausa. Não afirmar “JSON malformado” sem prova.

A alternativa técnica da tentativa 17 usou gpt-5.4-mini-2026-03-17 após SCHEMA_INVALID de Luna, no mesmo logical step IDENTIFICATION:stage-4:batch-4. Ela também consumiu orçamento. A segunda rejeição, na tentativa 21, abriu o circuito antes de qualquer request 22. Três requests tiveram evidência rejeitada (moeda/retroatividade), categoria semântica distinta e não incluída no breaker técnico.

## Modelos, tokens, desempenho e custo

| Modelo pedido e retornado | HTTP | Input | Cached input | Output | Total | Contrafactual USD |
|---|---:|---:|---:|---:|---:|---:|
| gpt-5.4-mini-2026-03-17 | 1 | 6039 | 0 | 101 | 6140 | 0.00498375 |
| gpt-5.6-luna | 15 | 78626 | 15792 | 1687 | 80313 | 0.01490704 |
| gpt-5.6-terra | 5 | 15500 | 4712 | 4225 | 19725 | 0.07321840 |
| **Total** | **21** | **100165** | **20504** | **6013** | **106178** | **0,09310919** |

Cached input é subconjunto de input, não tokens extras. Cache local de resultado teve zero hits neste primeiro piloto; OCR local teve hit. Nenhum token observado ficou desconhecido.

Tempo total: **72,900044 s**; soma de latências do gateway **68,846 s**; resíduo de processamento local **4,054044 s**. Mediana/request **2,227 s** e p95 nearest-rank **7,595 s**. A soma inclui tratamento local/validação dentro do gateway; o resíduo não é CPU time.

Custo normal contrafactual: **US$ 0,09310919**, calculado por (input−cached)×input_rate + cached×cached_rate + output×output_rate, dividido por 1 milhão, conforme [tarifas datadas](OPENAI_PRICES_2026-10-04.json). Inclui as duas respostas rejeitadas, com subtotal US$ 0,00228692. Não há taxa hipotética de escrita de cache aplicada a essas observações.

**EFFECTIVE BILLING: PENDING_DASHBOARD_RECONCILIATION.** API tier default apareceu nas 21 respostas. E0/E0.1 confirmadas no incentivo pelo usuário não comprovam billing deste piloto. Nenhuma consulta autenticada ou request adicional para billing.

## Golden set e evidências

A revisão usa os significados independentes preservados antes do piloto e as páginas originais. Não alterou fixture, referência, resultados ou expected values depois das respostas. Avaliação estrita considera condições e completude, além da literalidade.

- Críticas: **2/11 PASS**, 3 parciais e 6 misses.
- Saída GenAI: **4/17 âncoras PASS**; mais 1 metadado de origem correto, sem crédito à extração GenAI.
- Resultado de ponta a ponta: **5/17 corretas incluindo esse metadado**, 4 parciais e 8 misses.
- As três âncoras AXA do conjunto E1 de 20 não foram avaliadas pelo piloto Berkley.
- **18/18 citações localizadas literais**, zero páginas inválidas, zero trechos fabricados encontrados.
- **1 FOUND indevido confirmado**: prêmio como rótulo; **2 generalizações sem ressalva suficiente**: consentimento e mecanismo das exclusões. Não declarar zero alucinações semânticas.
- Nenhum montante ou data individual inventado foi identificado. Citações válidas não garantem interpretação nem cobertura contratada.

| ID | Campo | Referencia p. | Revisao | Motivo |
|---|---|---:|---|---|
| G01 | seguradora | 1 | PASS_METADATA | Processo SUSEP preservado como metadado de origem; não é número de apólice nem crédito à GenAI. |
| G02 | seguradora | 6 | PASS | Nome formal da seguradora na página 20; evidência equivalente aceita pela referência prévia. |
| G03 | limite_maximo_garantia | 9 | MISSING_TECHNICAL | LMG não concluído; a definição da página 9 não fornece montante individual. |
| G04 | retencao_franquia | 30 | MISSING_TECHNICAL | Franquia não concluída; regra/remissão à especificação na página 30. |
| G05 | side_a | 50 | PASS | Side A descrita com condições e fonte da página 50. |
| G06 | side_b | 50 | PASS | Side B descreve reembolso à sociedade/tomador com fonte da página 50. |
| G07 | side_c | 76 | PARTIAL_TECHNICAL | Texto condicional correto da página 76 preservado, mas estado final TECHNICAL_UNAVAILABLE. |
| G08 | custos_defesa | 12 | PARTIAL | Resumo omite anuência prévia; citação termina na página 12 e definição continua na 13. |
| G09 | data_retroativa | 8 | MISSING_TECHNICAL | Retroatividade não concluída; definição da página 8 não fornece data contratada. |
| G10 | territorialidade | 23 | MISSING_MEANING | Definição da página 12 não resolve regra mundial e exceções da página 23. |
| G11 | jurisdicao_lei | 49 | MISSING_MEANING | Lei brasileira citada; foro do domicílio e exceção por ausência de hipossuficiência ficaram omitidos. |
| G12 | exclusoes | 23 | PARTIAL | Três exclusões citadas, mas resumo restringe mecanismo a decisão/confissão e omite reconhecimento da página 24. |
| G13 | sublimites | 82 | MISSING_TECHNICAL | Sublimites não concluídos; cláusula condicional de excesso da página 82. |
| G14 | prazo_aviso_sinistro | 8 | PASS | Aviso com vigência/prazo aplicável e comunicação após conhecimento, sem prazo numérico inventado. |
| G15 | cancelamento_renovacao | 33 | MISSING_MEANING | Cancelamento da página 47 não resolve renovação não automática e pedido antecipado da página 33. |
| G16 | premio | 36 | MISSING_MEANING | FOUND='Prêmio' é rótulo sem valor/regra útil; devolução da página 46 não resolve referência da 36. |
| G17 | periodo_estendido_notificacao | 7 | PARTIAL | Relação prazo adicional/LMG citada na página 29, com mecanismo e requisitos incompletos. |

## Estados e revisao dos 27 campos

Estados observados: **17 FOUND / 0 NOT_FOUND / 4 NOT_RETRIEVED / 0 AMBIGUOUS / 6 TECHNICAL_UNAVAILABLE**. O estado tecnico pode preservar citacao anterior (Side C); isso nao representa conclusao validada.

| Campo | Estado original | Pagina | Revisao | Observacao |
|---|---|---:|---|---|
| seguradora | FOUND | 20 | SUPPORTED | Nome formal apoiado na página 20. |
| numero_apolice | NOT_RETRIEVED | - | INCONCLUSIVE | CG sem identificação individual conclusiva; preservar NOT_RETRIEVED. |
| tomador_segurado | NOT_RETRIEVED | - | INCONCLUSIVE | Não há tomador individual concluído. |
| vigencia_inicio | NOT_RETRIEVED | - | INCONCLUSIVE | Não confundir versão editorial com data contratada. |
| vigencia_fim | NOT_RETRIEVED | - | INCONCLUSIVE | Não há término individual concluído. |
| moeda | TECHNICAL_UNAVAILABLE | - | TECHNICAL | Sem conclusão; registro técnico preservado. |
| premio | FOUND | 46 | UNSUPPORTED_FOUND | Rótulo 'Prêmio' não estabelece valor ou regra contratual; correção de guard aplicada offline. |
| limite_maximo_garantia | TECHNICAL_UNAVAILABLE | - | TECHNICAL | Sem conclusão; não inventar montante a partir de definição. |
| sublimites | TECHNICAL_UNAVAILABLE | - | TECHNICAL | Sem conclusão; não inferir valores de placeholders. |
| retencao_franquia | TECHNICAL_UNAVAILABLE | - | TECHNICAL | Sem conclusão; não inventar franquia. |
| side_a | FOUND | 50 | SUPPORTED | Descrição e condições da cobertura A estão citadas. |
| side_b | FOUND | 50 | SUPPORTED | Reembolso à sociedade/tomador sob condições. |
| side_c | TECHNICAL_UNAVAILABLE | 76 | PRESERVED_TECHNICAL | Texto condicional correto preservado; estado final técnico impede usar conclusão. |
| custos_defesa | FOUND | 12 | INCOMPLETE | Anuência prévia não aparece no valor resumido; definição continua na página 13. |
| controle_defesa | FOUND | 43 | SUPPORTED | Escolha dos advogados pelos segurados na página 43. |
| consentimento_acordo | FOUND | 43 | UNSAFE_GENERALIZATION | Resumo omite dispensa de consentimento para perdas até a franquia, cláusula 17.29/p44. |
| rateio | FOUND | 44 | SUPPORTED | Alocação de perdas cobertas/não cobertas com esforços para rateio justo. |
| base_cobertura | FOUND | 7 | INCOMPLETE | Definição literal encontrada; requisitos e alternativa de notificação foram condensados. |
| data_retroativa | TECHNICAL_UNAVAILABLE | - | TECHNICAL | Sem data individual concluída; mecanismo não equivale a data. |
| periodo_estendido_notificacao | FOUND | 29 | INCOMPLETE | Resumo não completa mecanismo/requisitos; remissão à especificação deve permanecer. |
| prazo_aviso_sinistro | FOUND | 8 | SUPPORTED | Condições temporais e comunicação após conhecimento preservadas. |
| jurisdicao_lei | FOUND | 49 | INCOMPLETE | Lei brasileira correta; foro/exceção ficaram fora da saída. |
| territorialidade | FOUND | 12 | INCOMPLETE | Definição remissiva não resolve regra mundial e quatro exceções disponíveis. |
| exclusoes | FOUND | 23 | INCOMPLETE_UNSAFE_GENERALIZATION | Resumo omite reconhecimento pelo segurado como condição alternativa de aplicação. |
| extensoes_cobertura | FOUND | 59 | INCOMPLETE | Uma extensão condicional citada; não representa inventário completo de extensões. |
| cancelamento_renovacao | FOUND | 47 | INCOMPLETE | Regra de cancelamento citada; renovação omitida. |
| definicoes_relevantes | FOUND | 6 | INCOMPLETE | Introdução do glossário citada; não descreve as definições relevantes. |

## Correção offline após a revisão

A saída observada premio='Prêmio' revelou um guard insuficiente para rótulos monetários sem informação. Foi corrigido deterministicamente, com cinco casos de regressão sintéticos (rótulos de prêmio/LMG/franquia/sublimite e LMG apoiado só na franquia). Identidade do cache E2 passou de grouped-routed-e2-v1 para v2, preservando os registros da execução v1.

**Não houve reprocessamento real nem reescrita do resultado do piloto.** Melhorias de completude territorial/foro/renovação/condições transversais continuam no backlog; não há alegação de que o novo guard atingiu o gate no documento real.

## Evidências persistentes e reprodutibilidade offline

O plano e a referência manual foram preservados; raw prompts, headers, erros brutos e segredos não foram registrados. Cada tentativa foi reservada antes do SDK, response metadata/ID persistidos imediatamente ao retorno e snapshots antes da próxima request.

- [Plano offline](../data/processed/berkley_pilot_plan.json).
- [Claim exclusivo](../data/processed/berkley_pilot_claim.json).
- [Manifesto autoritativo](../data/processed/berkley_pilot_manifest.json).
- [Telemetria](../data/processed/berkley_pilot_telemetry.json).
- [Resultado original parcial](../data/processed/berkley_pilot_result.json).
- [Revisão independente](../data/processed/berkley_pilot_review.json).
- [Golden fixture E1](../tests/fixtures/public_retrieval_golden.json).

Esses dados runtime permanecem locais/ignorados pelo Git. Os hashes abaixo permitem conferir integridade sem reproduzir inferência; o documento versionado registra as medições reais.

| Arquivo local | SHA-256 |
|---|---|
| berkley_pilot_claim.json | 84ee07c0c919e9077a323fae66af94368920d0ea4320ee92cd4a73ecc7d4b2f6 |
| berkley_pilot_manifest.json | 282b51ced22ffb644a2c4457deed408aea960345157a89fa1cc901af4790e227 |
| berkley_pilot_result.json | 911d6551a25f649c504983c28a3f2746d2a8af06907878af8f29fa436254cc84 |
| berkley_pilot_telemetry.json | 56a58a95c3e907db1ced2030daa1d11e8ea328f8a779483238d910c2b19a54d7 |
| Golden fixture E1 | 673af23d838e61e267bafa44afd20b6fb89786688dbc5f64b0ebdc5ef143206f |
| Referencia manual anterior | 097ec4c3bebd3afa301e9298855b8c0ce31f995de30450a58a2a8166bd1c0877 |

## Validação e próximo gate

Validação específica pré-piloto: 191 testes PASS, com reserva/restart/duplicate/hard50/51/circuit/fallback/schema/evidência/TECH/selective Sol. Após o piloto, a suíte inicialmente passou 432 testes +27 subtestes. A consolidação após as correções offline passou **439 testes + 27 subtestes em 29,06 s**, sem chamadas reais. **compileall src/interface/scripts/tests PASS**. Não há alegação de qualidade real medida para o guard corrigido.

Edge real com fake/cache e SDKs bloqueados: PASS, health200, 3 uploads, 2 candidatos, 7 abas, 3 downloads MD/PDF/JSON, revisão/rerun sem nova inferência, TECH explicitamente visível; 4 operações fake, **0 HTTP de providers/0 erros**. Evidência em data/processed/workspace_browser_qa/diagnostics.json e capturas01–05. Os quatro arquivos runtime do piloto mantiveram seus hashes durante o QA.

Integridade final PASS: quatro entregáveis acadêmicos congelados, fonte Berkley, golden E1, referência anterior e quatro registros originais do piloto conferidos por SHA-256. Scanners PASS: 24 arquivos staged, 115 tracked e 23 registros/cache locais do piloto, sem localizações de segredos. Checkpoint local registrado no [WORKLOG](WORKLOG.md).

**Próximo gate: revisão humana deste resultado parcial e reconciliação das 21 requests no Dashboard.** Prioridades técnicas futuras: reduzir expansão improdutiva de campos de especificação, diferenciar subcausa de schema sem armazenar conteúdo bruto, preservar cláusulas operativas/exceções/continuação antes de considerar um resumo concluído.

Nenhuma nova API está autorizada. Não executar AXA, comparação, replay, publicação/push/e-mail, ou geradores finais PDF/PPTX/MP4/ZIP. Estes binários continuam congelados no a946882.

Execucao: 2026-10-04T19:19:24.812129+00:00 ate 2026-10-04T19:20:36.489372+00:00 (UTC); run_id 1253c61fcc6f4b2db7c50e5705e4ee8e.

## Atualização de retomada FAST-TRACK — d5aff5c

As medições acima descrevem o primeiro piloto, preservado integralmente. Anexo568713bb reconciliou as28requests E0/E0.1+primeiroBerkley no DATA SHARING INCENTIVE — CONFIRMED (100257input/20504cached/6045output). Costs não apresentados; não inferir custoefetivoUS$0.

Nova autorização condicionada: correções/replayofflinePASS→segundoBerkley<=30emNOVOledger; somentePASS→AXA<=30; somenteAXAseguro→UMAcomparação<=10, global<=70. Nenhum retorno do primeiro piloto será sobrescrito/repetido. Resultado desta execução será registrado separadamente abaixo.

## FAST-TRACK concluído — resultados novos separados do primeiro piloto

Sessão `17e6149e4572430baff1b08133427f7d`, iniciada após gate offline e preservação de 30 arquivos. Retomada encontrou 19 arquivos não commitados e zero novas reservas/envios; nenhuma análise offline concluída foi repetida. Replay preservado detectou os11erros materiais anteriores, zero ainda silenciosos. Payloads brutos antigos/rejeições de schema ausentes continuam uma limitação histórica; não foram reconstruídos.

| Etapa | Run ID | HTTP | Input | Cached input (subconjunto) | Output | Total | Tarifa padrão contrafactual |
|---|---|---:|---:|---:|---:|---:|---:|
| Segundo Berkley | f452685083fe490cb9e355cdd6fd0197 | 22 | 83533 | 23827 | 10266 | 93799 | US$0,29441245 |
| AXA | 71549bc4fadb4a4886faa84b5a492a1d | 23 | 76053 | 32191 | 9896 | 85949 | US$0,24811882 |
| Berkley × AXA revisada | b5066bddca324e4ea42a59fcbe83cfb2 | 1 | 1380 | 0 | 217 | 1597 | US$0,00536400 |
| Sessão | três etapas, sem replay | 46/70 | 160966 | 56018 | 20379 | 181345 | US$0,54789527 |

Provider responses:46retornos/IDs únicos, zero incertos, nenhuma reserva sem merge, zero falhas de transporte/breaker. Quatro falhas de contrato/schema consumiram orçamento e tiveram alternativa técnica distinta; nenhum retry automático do mesmo modelo. Foram30Terra/13Sol/2Luna/1mini,4fallbacks técnicos,13promoções semânticas distintas e16HTTP de promoção (inclui alternativas),13invocações Sol. Todas46respostas API tierdefault; incentivo/billing desses novosruns **PENDING_DASHBOARD_RECONCILIATION**, distinto das28requests reconciliadas pelo usuário. Costs não fornecidos, sem alegação custoefetivoUS$0.

Segundo Berkley: **PASS no gate conservador autorizado**.11/11críticas seguras=4corretas+7conservadoras;17/17âncoras seguras=7corretas (inclui metadadoSUSEP)+10conservadoras.27estados:7FOUND/16AMBIGUOUS/4NOT_RETRIEVED,13/13citações literais,0páginas inválidas/trechos fabricados/FOUND materialmente indevido/erro material silencioso na revisão. Conservador não recebe crédito de extração correta. Nome formal contém rótulo adjacente vazio “Apólice:” (ruído não material). Jurisdição/rateio resumem componentes comprovados no contexto/documento que extrapolam a única citação exibida; não afirmar entailment integral de cada componente por esse quote.

AXA: raw completo27estados/23HTTP,7FOUND/13AMBIGUOUS/7NOT_RETRIEVED. Revisão independente detectou **1erro material em definições relevantes**: cláusula particular condicional e inclusão/exclusão conflitantes apresentadas como FOUND. **RAW FAIL nesse campo permanece registrado e intacto.** Guard genérico CONTRADICTORY_DEFINITION_SCOPE corrigido offline, cache routedv4/semanticvalidatorv2 evita validação anterior. Sem novaAPI, projeção revisada só altera esse estado FOUND→AMBIGUOUS e anota razão, mantendo valores/páginas/quotes iguais. Só essa projeção recebe **PASS** e pode alimentar comparação/UI:6FOUND/14AMBIGUOUS/7NOT_RETRIEVED;11críticas seguras=3corretas+8conservadoras;3âncorasgolden conservadoras fundamentadas.14/14citações válidas,0páginas/fabricação/FOUND indevido na revisada. Guard é heurístico; revisão humana continua necessária.

Comparação real: **PASS seguro**,1Terra sobre somente SideA/B estruturados e quotes; nenhuma releitura de PDF.27linhas,54/54paresvalor+citação idênticos às entradas aprovadas,27citações localizadas+27placeholders explícitos,0evidências inválidas.22campos insuficientes porestado+2portexto extenso=24insuficientes; outros3 são identificação e duas comparações qualitativas SideA/B. Todas27classificações diferente_nao_comparavel,0vantagem/ranking. “27diferenças” do resumo representa27linhas examinadas, não27diferenças contratuais comprovadas.

Tempo: Berkley121,572s total/115,968s gateway,mediana4,397s/p959,774s; AXA114,623s/108,549s,mediana4,536s/p957,881s; comparação4,725s/3,943s. Sessão240,920s/228,460s,mediana4,4285s/p959,213s. Resíduo local12,460s é diferença de wall time, não CPU.

| Artefato novo (local/ignorado) | SHA-256 |
|---|---|
| Segundo Berkley result.json | 74a33a687dd92e860cfddcd7ee1a2eef035dde54eedb996032048225f16efc14 |
| AXA result.json RAW | 677acd3d4b0e87e860d40835d50e00b2da1659d4ca2e83232c22db7e597fd0b8 |
| AXA reviewed_report.json | cf2f26eaa86b89b96d8c152a4054972d68a84d709336d1cf5be60afa8c1e9509 |
| Comparação result.json | 0a6b28175640e4a21fb9feae642b25284f4c223fd1cb8182291d487bebb9d438 |

Cada review.json é vinculado ao resultSHA original. Projeção conservadora deve corresponder exatamente ao SHA e só permite FOUND→AMBIGUOUS com razão; upgrades/edições de fatos são recusados. Primeiro piloto, golden, referência, PDFs oficiais e quatro artefatos acadêmicos permanecem intactos. Sessão/UI aprovada em data/processed/fasttrack_validation/validated_ui_session.json. Nenhuma request adicional é autorizada pelo saldo aritmético de24; próximas ações são QA/cache/checkpoint/USER MANUAL E2E, sem regenerar finais/push.
