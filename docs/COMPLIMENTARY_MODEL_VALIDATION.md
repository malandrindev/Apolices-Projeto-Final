# COMPLIMENTARY_MODEL_VALIDATION — E0

**Estado atual: E0/E0.1 encerradas, sete requests CONFIRMED DATA SHARING INCENTIVE pelo usuário. Registros abaixo preservam a cronologia; não repetir pings. Piloto E2 encerrado parcial e billing pendente.**

Estado: **CONFIRMED DATA SHARING INCENTIVE** para as cinco requests E0, conforme reconciliação manual informada pelo usuário em 04/10/2026. Evidência técnica preservada no checkpoint `054e4ac`.
Baseline 22fd581; nenhuma mudança de família/modelo principal foi decidida.

O usuário autorizou um experimento E0 mínimo: uma request por modelo,
até cinco requests, sem retry. O usuário confirmou no Dashboard que as cinco requests estão integralmente no **Processing Tier → Data sharing incentive tier**. Esta é uma observação da conta informada pelo usuário; não houve consulta autenticada de billing pelo código. Valores monetários não foram fornecidos.

| MODEL | REQUEST ID | INPUT | OUTPUT | SERVICE TIER RETURNED | API STATUS | DASHBOARD COST | CONCLUSION |
|---|---|---:|---:|---|---|---|---|
| gpt-5.4-mini-2026-03-17 | req_d218a96e66fb48c8a2739580c3e7aa6b / resp_0aecac1301629f9a016ac26d14776487d2ad22b539930f60c6 | 13 | 5 | default | completed | NOT_REPORTED | CONFIRMED_DATA_SHARING_INCENTIVE |
| gpt-4.1-mini-2025-04-14 | req_a5f2c4ef47a54dc093a1210aceb70c50 / resp_004431d00132767b016ac26d15d86887d2a70e3918b0f91d36 | 14 | 2 | default | completed | NOT_REPORTED | CONFIRMED_DATA_SHARING_INCENTIVE |
| gpt-5.4-2026-03-05 | req_0cde811a1c59499b86952cd7aca5f7e5 / resp_027ef18abd29e8e8016ac26d172bd087d2a8e7211729e8e838 | 13 | 5 | default | completed | NOT_REPORTED | CONFIRMED_DATA_SHARING_INCENTIVE |
| gpt-5.2-2025-12-11 | req_8f630a0bcb06430a98149e2cba290ebe / resp_0f28c3847b4ae7b7016ac26d17f9c887d2b1569a894c564350 | 13 | 5 | default | completed | NOT_REPORTED | CONFIRMED_DATA_SHARING_INCENTIVE |
| gpt-5.6-luna | req_f36cd84aa4634916b5784808b5ce9ebb / resp_022c7b03755a8212016ac26d18ca8487d2a844cd8380c0fd5b | 13 | 5 | default | completed | NOT_REPORTED | CONFIRMED_DATA_SHARING_INCENTIVE |

REQUEST ID reúne request ID seguro e response ID quando retornados.
Contagens ausentes ficam desconhecidas; nenhuma resposta/erro bruto é armazenado.
O JSON local preserva também timestamp, modelo retornado, cached input, total
de tokens, duração e classificação segura de erro.

## Política de request e documentação oficial

- Endpoint fixo: /v1/responses em https://api.openai.com/v1.
- Entrada única: `Responda somente com OK.`; sem PDFs, dados privados ou pipeline.
- `store=False`, `max_output_tokens=64` em todos; nenhum tool/service_tier.
- SDK `max_retries=0`; sem loop de retry, reparo, fallback ou request extra.
- Reasoning `effort=none` nos quatro GPT-5; parâmetro omitido no GPT-4.1 mini.

Snapshots e compatibilidade verificados na OpenAI Docs: [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini), [GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4), [GPT-5.2](https://developers.openai.com/api/docs/models/gpt-5.2), [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).
A API usa max_output_tokens para limitar toda saída gerada, inclusive tokens internos; 64 é o cap deste experimento, não uma contagem garantida de consumo. [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create), [Counting tokens](https://developers.openai.com/api/docs/guides/token-counting).

## Execução única e evidência

Script: [validate_complimentary_models.py](../scripts/validate_complimentary_models.py).
Execução sem argumento mostra somente o plano e não carrega a chave/SDK nem cria manifesto.
A opção `--execute` fica reservada à execução E0 autorizada pelo usuário, após revisão.

O manifesto fica em `data/processed/complimentary_model_test.json`.
Um claim exclusivo persistente fecha a corrida entre processos. Cada registro
é marcado `attempted`, gravado por arquivo temporário+fsync+replace antes do HTTP.
Manifesto ou claim existentes bloqueiam toda repetição, inclusive após falha,
interrupção ou JSON incompleto. Não apagar nem retomar automaticamente.
Falha de persistência interrompe o controlador antes de qualquer envio seguinte.
Dados de erro ficam limitados a categoria e status HTTP; IDs/tier têm allowlist.

Após até cinco tentativas, parar inferências E0. O renderer deste documento usa
apenas o JSON técnico já salvo e não faz chamada de API. O manifesto original mantém o estado financeiro inicial, sem reescrever a observação histórica. A reconciliação posterior é registrada neste documento; não renderizar por cima dela usando somente o manifesto técnico.
E1 continua offline; processamento GenAI real de Berkley/AXA e regeneração
de PDF/PPTX/MP4/ZIP acadêmicos continuam sem autorização.

## Reconciliação E0 e E0.1 — 04/10/2026

**API returned service_tier = default** e **Dashboard Processing Tier = Data sharing incentive tier** são observações distintas. O usuário confirmou manualmente o tier no Dashboard para todas as cinco E0. Valores monetários e quota restante não foram fornecidos; essa confirmação não explica a cobrança histórica US$ 0,00703.

A orientação anterior de omitir E0.1 foi substituída pela nova instrução explícita de executar exatamente uma request Terra e uma Sol. Ambas concluíram; E0 não foi repetida. Nenhuma inferência real adicional está autorizada. Routing definitivo aberto, aguardando reconciliação manual no Dashboard.

### E0.1 — observações técnicas exatas

| UTC timestamp | Requested model | Returned model | Response ID | Request ID | Status | Input | Cached input | Output | Total | API tier | Duration s | Error |
|---|---|---|---|---|---|---:|---:|---:|---:|---|---:|---|
| 2026-10-04T17:44:12.761845Z | gpt-5.6-terra | gpt-5.6-terra | resp_0e46f7297df20d1d016ac2906df9e887d28ed4dca0af7aa420 | req_0ebfb047b7254807bc2f42d568247439 | completed | 13 | 0 | 5 | 18 | default | 2.389544 | None |
| 2026-10-04T17:44:15.156201Z | gpt-5.6-sol | gpt-5.6-sol | resp_06541e36c3994d92016ac2906f5bb087d29b3d95aafdc030bf | req_b0d4925de70948e58073a053497e1a31 | completed | 13 | 0 | 5 | 18 | default | 1.810408 | None |

**gpt-5.6-terra = CONFIRMED DATA SHARING INCENTIVE**

**gpt-5.6-sol = CONFIRMED DATA SHARING INCENTIVE**

Ambas retornaram OK. Total E0.1: duas requests, 26 tokens input, zero cached input, 10 output, 36 total; zero retries. Custo e incentivo não são inferidos do API tier default.

Controlador: [validate_complimentary_models_e01.py](../scripts/validate_complimentary_models_e01.py). Manifesto runtime independente: data/processed/complimentary_model_test_e01.json e .json.lock persistente. Manifesto ou claim existente bloqueia repetição, inclusive após erro/interrupção. Reserva atômica gravada antes do HTTP; falha de persistência interrompe envios. Controlador, manifesto e claim originais E0 permanecem intactos.

Entrada exata: Responda somente com OK. Endpoint /v1/responses; store=false; max_output_tokens=64; reasoning.effort=none; SDK max_retries=0; timeout=30 segundos. Sem PDF/documentos do projeto, tools, hosted tools ou service_tier explícito. Nenhuma resposta/erro bruto ou chave registrada.

Compatibilidade conferida na documentação oficial OpenAI: [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra), [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

Depois dessas duas tentativas, E1 e todo planejamento continuam offline. GenAI pública segue sem autorização e PDF/PPTX/MP4/ZIP finais congelados. Resultado manual do Dashboard pode atualizar este documento sem novas requests.

## Reconciliação final E0/E0.1 — PHASE E2, 04/10/2026

O usuário confirmou manualmente, no filtro **Processing Tier → Data sharing incentive tier**, as duas requests E0.1 Terra/Sol e as cinco E0. **E0/E0.1 oficialmente encerradas: CONFIRMED DATA SHARING INCENTIVE para as sete requests.** Total experimental: 92 input + 32 output = 124 tokens, zero cached input, zero retries. API returned service_tier=default permanece a observação técnica; não equivale ao Processing Tier do Dashboard.

Essa atualização é documental, sem request adicional e sem modificar os manifestos/claims técnicos. Valor financeiro histórico e quota disponível não foram informados. A confirmação experimental não torna o custo efetivo do futuro piloto automaticamente US$ 0.

O novo anexo f9dca7d7 autoriza **PHASE E2: UM piloto Berkley**, somente após dry-run válido, com limite rígido de 50 tentativas HTTP totais, zero retries do mesmo modelo, routing/fallback/escalation/circuit breaker testados. AXA, comparação entre documentos e testes neutros continuam proibidos. Billing do piloto começa **PENDING_DASHBOARD_RECONCILIATION**.


Piloto E2 posteriormente autorizado e encerrado:21HTTP/21IDs, breaker20% por schema. Nenhuma inferência adicional. [Resultado real](BERKLEY_REAL_PILOT.md); custo efetivo PENDING_DASHBOARD_RECONCILIATION.

## Reconciliação comunicada pelo usuário — FAST-TRACK de d5aff5c

Anexo568713bb, 04/10/2026: **28 requests reconciliadas**,100257 input tokens,20504 cached input tokens e6045 output tokens; ledger local==Dashboard. Inclui as sete E0/E0.1 (92input/32output) e as21requests do primeiro Berkley (100165input/20504cached/6013output).

**PROCESSING TIER = DATA SHARING INCENTIVE — CONFIRMED**, conforme observação manual do usuário. API service_tier=default permanece distinta. **Costs não fornecidos; custo efetivo NOT_REPORTED/PENDING_COSTS_RECONCILIATION.** Nenhum US$0 atribuído, nenhum manifesto histórico alterado e nenhum ping/consulta autenticada executado.

A autorizaçãoFAST-TRACK é uma nova sessão: Berkley<=30 apósgateoffline; AXA<=30 somenteBerkleyPASS; comparação<=10 somenteAXAseguro; total<=70. Resultados/processingtier/billing dessas novasrequests dependem de nova evidência, sem herdar confirmação das28.

## Nova sessão FAST-TRACK — incentivo não herdado

Executadas46requests autorizadas em três etapas:22segundoBerkley/23AXA/1comparação. Usage:160966input,56018cached(subconjunto),20379output,181345total; APIdefault46. Modelos30Terra/13Sol/2Luna/1mini. **PROCESSING TIER e EFFECTIVE BILLING dos novosruns PENDING_DASHBOARD_RECONCILIATION.** Cálculo técnico contrafactualUS$0,54789527, sem cobrança/gratuidade presumida. As28requests anteriores continuam DATA SHARING INCENTIVE — CONFIRMED pelo usuário; Costs não apresentados. Pings/replays encerrados; detalhes/SHA em BERKLEY_REAL_PILOT.
