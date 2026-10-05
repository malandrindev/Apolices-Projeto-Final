# MODEL_ROUTING_AND_FALLBACK_POLICY — PHASE E2

Escopo: política configurável para agentes Python existentes, sem hosted tools ou Agents SDK. E2 parte do checkpoint E1 8b39785. As seções E2 originais abaixo são históricas; autorização/resultados FAST-TRACK atuais estão no apêndice vigente e no CURRENT_STATE. APIs da nova sessão também estão encerradas.

## Papéis e configuração

| Papel | Primary | Technical fallback | Semantic escalation |
|---|---|---|---|
| Extração em volume | gpt-5.6-luna | gpt-5.4-mini-2026-03-17 | gpt-5.6-terra |
| Interpretação de cláusulas | gpt-5.6-terra | gpt-5.4-2026-03-05 | gpt-5.6-sol |
| Comparação | gpt-5.6-terra | gpt-5.4-2026-03-05 | gpt-5.6-sol |
| Verifier/adjudicator seletivo | gpt-5.6-sol | gpt-5.6-terra; gpt-5.2-2025-12-11 | Sem cadeia indefinida |
| Auxiliares, somente se necessário | gpt-5.4-mini-2026-03-17 | gpt-4.1-mini-2025-04-14 | Preferir Python |

IDs pertencem à configuração da política, não às regras de extração. Variáveis MODEL_ROUTING_<ROLE>_PRIMARY, _TECHNICAL_FALLBACKS e _ESCALATIONS permitem alterar a política sem editar agentes. Listas de modelos são separadas por vírgula; listas vazias desabilitam alternativas. O piloto verifica a allowlist dos sete modelos autorizados e a integridade do perfil antes do primeiro envio. Não altera .env nem a verdade de referência.

A comparação fica configurada, mas nenhuma comparação entre documentos está autorizada na E2. A rota legada e o provider Groq permanecem disponíveis. A política não autoriza inferência por si só; autorização e limites são responsabilidades do controlador.

## Ordem da decisão

Corpus integral → parsing/OCR local → BM25/títulos/sinônimos → candidatos por campo → lotes semânticos com diversos campos → primary → evidência determinística → ampliação local → escalation seletiva → resultado estruturado/estado → revisão humana.

Um resultado Luna válido, estruturado, evidenciado e consistente não é encaminhado a Terra. Antes de uma escalation semântica: ampliar N, páginas vizinhas e seção relevante, preservando o fallback específico por campo da E1. Sol atende casos sinalizados; não revisa todos os 27 campos. Evidência insuficiente não justifica subir imediatamente a Sol.

Condições gerais não informam necessariamente número de apólice, cliente, montante, prêmio ou datas de contratação. Busca local ampla e validação de valores distinguem definição de valor contratado. Não usar ausência de valor individual para justificar envio indiscriminado de todas as páginas. O texto integral permanece local e nenhuma etapa monta automaticamente uma request com o PDF inteiro.

## Technical fallback versus semantic escalation

Technical fallback responde a timeout, rate limit temporário, indisponibilidade/5xx/modelo indisponível, resposta incompleta ou schema inválido sem reparo local seguro. Primeiro tentar reparo determinístico permitido de JSON, sem inferência. Uma alternativa técnica é outra tentativa HTTP e consome orçamento.

Authentication, permission, crédito/quota esgotados e limites financeiros não são resolvidos alternando modelos. Essas condições interrompem o piloto, preservando resultados e diagnóstico. Um rate limit financeiro não é tratado como 429 temporário.

Semantic escalation ocorre após uma resposta tecnicamente válida que mantém AMBIGUOUS, conflito, evidência insuficiente/inválida ou incompatibilidade crítica, e depois de melhorar o contexto local. Isso é uma decisão de qualidade, não falha operacional. O primary não é reenviado automaticamente para corrigir ambiguidade.

## Limites e circuit breaker

- MAX_SAME_MODEL_RETRIES = 0.
- MAX_MODEL_ATTEMPTS_PER_LOGICAL_STEP = 2.
- MAX_SEMANTIC_ESCALATIONS_PER_FIELD = 2.
- MAX_FALLBACK_DEPTH = 2.
- Piloto: **MAX_HTTP_ATTEMPTS = 50**, incluindo primary, alternativa técnica, escalation, verifier e eventual reparo LLM.
- SDK OpenAI max_retries=0. O adapter recebe max_retries=1, que representa uma única tentativa no loop existente.
- Três falhas técnicas consecutivas **OU** pelo menos 20% de falhas técnicas nas últimas dez requests abrem o circuit breaker.
- Cada reserva é verificada e persistida antes do HTTP; a request 51 nunca pode ser enviada.
- Falha de persistência ou claim já existente impede novos envios; interrupção não autoriza replay.

A lista de fallback do verifier pode ter dois alternativos, mas o limite de duas tentativas por logical step prevalece: primary + no máximo uma alternativa enviada naquele step. O segundo alternativo configurado não autoriza uma terceira tentativa. O controlador registra limites e indisponibilidade com motivo, sem loop Sol → Terra → Sol.

Cache local válido evita inferência e não consome tentativa HTTP. Cache da E2 inclui assinatura da política, modelo/nível, evidência, schema/prompts/settings/corpus. Resultado parcial/tecnicamente interrompido não é promovido a cache final completo. Budget é de HTTP, não de operações presumidas nem teto monetário.

## Estados e evidência

| Estado | Tratamento |
|---|---|
| FOUND | Valor e evidência aceitos após validação |
| NOT_FOUND | Busca local adequada sem a informação específica; não prova ausência de cobertura |
| NOT_RETRIEVED | Busca ainda insuficiente |
| AMBIGUOUS | Evidência conflitante, inválida ou conclusão indeterminada |
| TECHNICAL_UNAVAILABLE | Processamento/conclusão impedidos por problema técnico ou limite; nunca convertido para NOT_FOUND |

Os 27 campos mantêm estado explícito. Documentos anteriores sem sidecars continuam compatíveis. Estados inconclusivos ou técnicos não geram ranking/vantagem contratual.

FOUND exige documento/SHA, página real, trecho literal contíguo na fonte enviada e original, além de valor compatível. Validação preserva moeda/unidade/data e distingue LMG/sublimite, retenção/franquia e território/jurisdição quando o trecho permite essa distinção. Falha não é aceita silenciosamente; marca ambiguidade/revisão ou aciona política limitada.

## Telemetria e recuperação

Cada tentativa real registra timestamp, document_id, logical_step_id, field_group, retrieval_stage, primary/requested/actual model, response/request IDs seguros, fallback level/reason, semantic escalation, input/cached/output/total tokens, latência, API tier, status, evidence validation, páginas e candidatos usados.

Não gravar chave, cabeçalhos sensíveis, erro bruto, prompt interno ou system message. Token/tier ausente permanece desconhecido. Cached tokens do provider e cache local são distintos. Agregados por modelo/papel distinguem primary, alternativas técnicas, escalation e verifier; também contam estados, campos resolvidos/sinalizados, falhas, citações inválidas e uso de Sol.

O piloto possui manifesto e claim independentes da E0/E0.1, com reservas duráveis e snapshots dos resultados. Reexecução real é bloqueada mesmo após falha/interrupção. Revisão, export local e leitura do manifesto não fazem requests.

## Incentivo, custo e gate

E0/E0.1 encerradas: sete requests neutras confirmadas manualmente pelo usuário no Data sharing incentive tier; 92 input/32 output. API service_tier default é observação distinta. Não repetir testes neutros.

Para o piloto, calcular somente custo contrafactual pelas tarifas normais e usage real observado, distinguindo cached input. O custo efetivo começa **PENDING_DASHBOARD_RECONCILIATION**, sem assumir US$ 0. Quota complimentary desconhecida não significa uso ilimitado.

Depois de UM piloto Berkley: parar APIs, revisar 17 âncoras/11 críticas e 27 campos, testar offline/scanner/QA afetado, atualizar documentos e checkpoint local. Não processar AXA, não comparar documentos, não regenerar PDF/PPTX/MP4/ZIP, não publicar ou enviar e-mail.

## Implementação e validação E2

- [Política e contador](../src/llm/model_routing.py), [adapter Responses](../src/llm/openai_client.py), [extração agrupada](../src/agents/grouped_extraction.py) e [controlador persistente](../scripts/run_berkley_pilot.py).
- Factory [providers.py](../src/llm/providers.py) recebe routing_policy/request_control opcionais. MODEL_ROUTING_ENABLED=false preserva defaults; true ativa a governança OpenAI com SDK0/raw1/timeout≤60. O wrapper da UI encaminha contexto/política e mantém seu limite lógico; cache do pipeline distingue políticas.
- A persistência durável pertence ao piloto ou a persist_callback explicitamente fornecido. O RequestControl genérico sem callback mantém contador em memória; não alegar retomada durável da UI.
- Promoção semântica é contada uma vez por documento/campo/papel/nível/modelo, permitindo vários lotes daquela promoção sob hard50. Verifier também conta como adjudicação; fallback técnico não é nova promoção. No máximo duas promoções diferentes por campo.
- Retry-After temporário é respeitado até min(max_retry_wait_seconds,30s), antes de alternativa diferente. Espera maior encerra essa operação sem compensar limite financeiro.
- Receipt seguro preserva response_id imediatamente após retorno, antes de ler output_text/validar JSON. Snapshot dos 27 campos ocorre antes da próxima HTTP; tentativas incertas continuam consumidas e sem replay.
- [Testes de controle](../tests/test_model_routing.py), [integração grouped](../tests/test_grouped_routing.py), [forense/piloto](../tests/test_berkley_pilot.py) e [factory](../tests/test_routing_factory.py), todos fake/offline.

A única execução real Berkley encerrou com21HTTP,1fallback,0escalation/0verifier/0Sol e circuito aberto por2SCHEMA_INVALID/10. Os controles passaram; a qualidade semântica ficou PARTIAL/FAIL. Ver [BERKLEY_REAL_PILOT.md](BERKLEY_REAL_PILOT.md). Nenhuma inferência após o piloto.

Validação literal e guards reduzem erros, mas não garantem preservação de todas as ressalvas/continuações ou completude de campos compostos. O piloto encontrou1FOUND indevido e2resumos com generalização sem ressalva suficiente. Guard de rótulo monetário foi corrigido offline; cache E2v2 é distinto do piloto v1. Dados originais e referência prévia permanecem intactos.


Consolidação final offline: **439 testes + 27 subtestes PASS em 29,06 s** e compileall PASS. QA real no Edge com fake/cache, status técnico visível, três uploads/dois candidatos/sete abas/exports/revisão: zero HTTP de providers/zero erros. SHA-256 dos quatro entregáveis congelados e registros originais do piloto conferidos iguais. Scanner e checkpoint final no [WORKLOG](WORKLOG.md); nenhum novo ensaio real após a correção.

## FAST-TRACK de d5aff5c — política vigente

A autorização atual substitui o STOP histórico acima exclusivamente para esta sessão: gate offline PASS → segundo Berkley até 30 HTTP → revisão Berkley PASS → AXA até 30 HTTP → revisão AXA PASS → uma comparação até 10 HTTP; teto global 70. Não repetir os ledgers anteriores. Implementação e plano vinculados por SHA; cada estágio tem claim exclusivo e revisão do resultado vinculada ao SHA antes de liberar o seguinte.

O provider circuit breaker agora conta somente PROVIDER_TRANSPORT_FAILURE. MODEL_OUTPUT_CONTRACT_FAILURE (schema/estrutura/incomplete) permite reparo local seguro ou alternativa técnica distinta, consumindo tentativa; SEMANTIC_QUALITY_FAILURE dispara validação/estado conservador e adjudicação seletiva. SDK retries=0, uma tentativa raw, no máximo duas tentativas/modelos diferentes por passo. O terceiro alternativo configurado não amplia esse limite.

Campos críticos usam Terra desde o lote inicial; o contexto é expandido localmente antes da request. O verifier seletivo usa o papel verifier: Sol, fallback técnico Terra, secundário 5.2 sujeito ao limite por passo. NOT_RETRIEVED explícito após expansão, conflitos/evidência inválida/qualificadores omitidos/baixa confiança acionam seleção crítica; dados individuais não localizados em CG permanecem conservadores. Ausência de evidência não prova ausência de cobertura.

Runner: scripts/run_fasttrack_validation.py; gate exato offline, SessionStore, reserva global autoritativa antes de SDK, payload público/response ID/output_text/snapshots persistidos sem system prompt, headers ou segredos. Reexecução existente/incerta é recusada. UI grava ledger de attempts por run; relatório de uso deduplica IDs/run e conserva None para métricas desconhecidas. processing tier de novas requests não herda a confirmação histórica de 28 requests.

Validação focada após recuperação: 197 testes PASS em 11,23 s, offline, incluindo timeout Sol → Terra e contagem do verifier. Resultado real/revisão/QA final desta sessão: ver CURRENT_STATE e BERKLEY_REAL_PILOT.

FAST-TRACK executado dentrodos tetos:22Berkley/23AXA/1comparação,46/70;0transport/4contract failures,4fallbacks,13promoções/16HTTP de promoção,13Sol. Dois schemafails emcada documento não abriramproviderbreaker. BerkleyPASS conservador; AXAraw1erro de definição detectado porrevisão eguard genérico corrigido offline. Projeção vinculada aoRAW/SHA só permite FOUND→AMBIGUOUS fundamentado, mantendo fatos/citações; comparação eUI usam exclusivamente essa revisadaPASS. Resultado/comparação/caches brutos permanecem intactos, sem repetir API. Cachev4 evalidatorv2 distinguem correção posterior. Suíte496+27PASS/compileallPASS; testes negam upgrades ou edição de fatos na projeção. Detalhes/SHA em BERKLEY_REAL_PILOT. Próximo STOP/USER MANUAL E2E, nenhum saldo autoriza requests adicionais.
