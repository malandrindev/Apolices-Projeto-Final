# OpenAI billing — auditoria estática (PHASE A)

Data: 03/10/2026. Baseline recuperável: `a946882`; branch local `feature/product-ux-hardening`. Nenhuma inferência nova, consulta autenticada de billing ou alteração de parâmetro nesta fase.

## OBSERVED

- O usuário informou cobrança real de aproximadamente **US$ 0,00703** no Cost Dashboard, inscrição “You're enrolled for complimentary daily tokens” e o projeto **InsurMinds-I2A2** selecionado em **Share inputs and outputs with OpenAI**.
- O usuário informou que Luna e Sol constam como elegíveis na documentação atual do incentivo. Inscrição, horário de ativação, elegibilidade específica da conta, projeto da chave e lançamento financeiro são informações da conta; não foram independentemente consultadas nesta auditoria. Não afirmar que o projeto errado é a causa.
- Diagnósticos já existentes: E2E OpenAI com **3 chamadas HTTP**, 2 extrações Luna + 1 comparação Sol (modelos observados na sessão anterior; o diagnóstico antigo registra as etapas, mas não os IDs dos modelos), **3.589 tokens de entrada / 1.980 de saída**, zero retry. Ping neutro Luna: **1 HTTP**, **12 / 5 tokens**. Total desses ensaios: **4 HTTP**, **3.601 / 1.985 tokens**. Retomadas dos documentos: **zero chamadas novas**.
- Fontes: `data/processed/e2e_smoke_openai/diagnostics.json`, `data/processed/provider_smoke.json` e snapshot factual `Projeto_Final_Artefatos/EVIDENCE_BUNDLE.json`. Não usar tokens Groq para justificar cobrança OpenAI.

## CODE EVIDENCE

Busca estática dos padrões `tools=`, `"tools"`, `tool_choice`, `function_call`, `web_search`, `file_search`, `computer`, `code_interpreter`, `service_tier`, `responses.create`, `chat.completions.create`, `OpenAI(`, `client.responses`, `client.chat` em todos os arquivos de projeto acessíveis, incluindo código, testes, scripts, documentação e instruções arquivadas. Dependências de terceiros, .git, pesos/cache e valores de .env não representam requests da aplicação e não foram impressos. Ocorrências em cachetools/httptools são nomes de pacotes.

| Arquivo / linha na baseline | Endpoint ou papel | Modelo | Parâmetros / comportamento |
|---|---|---|---|
| src/llm/openai_client.py:43 | Construtor SDK; não é inferência | — | api_key obtida de Settings; timeout_seconds; max_retries=0. SDK 3.24.0 |
| src/llm/openai_client.py:62 | Único despacho OpenAI: POST /v1/responses | Argumento model de complete | model, input, max_output_tokens, store=false; reasoning e text condicionais descritos abaixo |
| src/llm/openai_client.py:97 | Ping CLI opt-in, mesmo endpoint | Settings.model_fast; default gpt-5.6-luna | Mensagem neutra, max_output_tokens=64; não executado nesta fase |
| src/agents/segmentation.py:71 | Gateway.complete, mesmo endpoint se OpenAI | model_fast, default Luna | agent=segmentation, temperature=0 solicitado ao gateway; JSON/Pydantic de classificação; max_output_tokens=1600. Adapter omite temperature |
| src/agents/extraction.py:162 | Gateway.complete, mesmo endpoint se OpenAI | Primeira tentativa model_strong da extração; default Luna no pipeline. Correção model_repair, default Terra | agent=extraction, max_output_tokens=4500, JSON/Pydantic, no máximo uma correção semântica |
| src/agents/comparison.py:138 | Gateway.complete, mesmo endpoint se OpenAI | model_strong; default Sol | agent=comparison, max_output_tokens=2500, JSON/Pydantic; lotes de no máximo 8 campos |
| src/agents/rag.py:55 | Gateway.complete opcional, mesmo endpoint se OpenAI | model_strong; default Sol | agent=rag, max_output_tokens=1800, JSON/Pydantic; não demonstrado com API real |
| src/llm/providers.py:43; src/pipeline.py:58 | Factory e seleção de modelos; nenhum endpoint adicional | Configuração por provider | Extração OpenAI usa fast, reparo intermediate, comparação strong |
| scripts/smoke_end_to_end.py:138 | Encaminha kwargs ao gateway selecionado | Configuração do ensaio | Opt-in --live; orçamento limitado; não executado nesta fase |
| src/llm/groq_client.py:129 | POST chat/completions **Groq** | Modelos Groq | Construtor Groq, não OpenAI. Nome openai/gpt-oss-120b é ID do modelo hospedado pela Groq |
| tests/test_providers.py:43,48,75,92,98 | SDK fake / inspeção de requests | IDs de teste | Não emitem HTTP; ocorrências client.responses.calls são listas de mocks |

UI, pipeline, comparison_cli e rag_cli usam a factory e os agentes acima; não têm chamadas OpenAI paralelas ocultas. Scripts de artefatos usam evidências locais; não inferem.

## REQUEST SHAPE

```python
{
    "model": configured_model,
    "input": messages,                  # apenas texto; conteúdo omitido nesta auditoria
    "max_output_tokens": explicit_limit_or_settings_max_tokens,
    "store": False,
    # para gpt-5.6:
    "reasoning": {"effort": "none" if model_is_fast else configured_effort},
    # quando o caller solicita JSON:
    "text": {"format": {
        "type": "json_schema", "name": "result",
        "schema": strict_pydantic_schema, "strict": True
    }}
}
```

Sem schema de etapa conhecido, text.format usa json_object. Sem response_format solicitado, text é omitido. Esforço padrão dos demais modelos gpt-5.6 é low. Saída padrão OpenAI: 4096 tokens; comparação 2500; RAG 1800; ping 64. **temperature é omitida**, mesmo que o caller forneça 0.

Retry SDK desativado; camada local limita tentativas HTTP a no máximo 3. Nos ensaios históricos houve zero retry. Reparo de extração e nova tentativa para JSON inválido são chamadas possíveis, cobertas por mocks; Terra não foi necessário no E2E registrado.

## TOOLS USED?

| Questão | Resultado |
|---|---|
| tools nas requests da aplicação? | **Não** |
| Hosted tools: web/file search, computer, code interpreter? | **Não** |
| Function calling ou tool_choice? | **Não** |
| Structured Outputs via JSON Schema? | **Sim**, via **text.format**, separado de function calling |
| API OpenAI de embeddings/imagem/áudio/files/vectors/batch? | **Não** no código de aplicação |
| OCR, SQLite, Chroma e agentes locais são tools OpenAI? | **Não**; componentes executados pelo Python, fora do despacho do modelo |

Structured Outputs não configura automaticamente tool use. A documentação oficial distingue schemas de resposta de funções conectadas ao modelo. [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## SERVICE TIER CONFIGURATION

- O código **não define service_tier**. Não envia explicitamente default nem auto; tampouco flex, priority ou ultrafast.
- Na referência oficial, a omissão segue **auto**, que usa a configuração do projeto; o padrão do projeto é default salvo outra configuração. A execução efetiva não foi registrada nos diagnósticos antigos. [Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).
- O SDK local OpenAI 3.24.0 pode herdar **OPENAI_PROJECT_ID**, **OPENAI_ORG_ID**, **OPENAI_BASE_URL** e **OPENAI_CUSTOM_HEADERS** do ambiente. A aplicação não passa esses argumentos explicitamente. A chave também pode determinar o projeto. O nome de projeto relatado pelo usuário não pode ser comprovado apenas pela presença da chave.
- **store=false controla armazenamento da resposta**, não é comando para desativar ou ativar incentivo financeiro. As políticas de dados distinguem estado da aplicação e outros controles. [Data controls](https://developers.openai.com/api/docs/guides/your-data).
- Nenhum parâmetro auditado foi identificado como causa comprovada de inelegibilidade. As páginas técnicas consultadas não estabelecem todos os termos da promoção da conta; não converter essa ausência em garantia de gratuidade.

## LIKELY EXPLANATIONS

Hipóteses para conciliar nos registros da conta, **sem atribuir probabilidades nem declarar causa**:

1. Correspondência entre chave usada, organização/projeto e projeto selecionado para data sharing no instante das requests.
2. Momento de ativação do programa e intervalo/fuso usados no Dashboard; requests anteriores à ativação ou lançamentos de outra utilização no mesmo período.
3. Franquia diária da organização/grupo de modelos já usada por outros workloads; os quatro ensaios locais não medem todo consumo da conta.
4. Linha de cobrança por modelo e processamento efetivo versus benefícios/créditos aplicados; elegibilidade específica dos termos do incentivo para Responses/modelo/data na conta.
5. Eventual discrepância do processamento de billing que precise de análise do suporte.

A auditoria não verificou essas hipóteses por chamadas autenticadas. Não afirma atraso de compensação, exclusão de Structured Outputs, exclusão de Responses ou necessidade de mudar store/service_tier.

## NOT EXPLAINED

**O código não explica a cobrança por tools ou por service_tier explícito.** Não foi identificado recurso adicional usado nos smokes que explique a discrepância.

Se chave/projeto, inscrição ativa no horário, elegibilidade e franquia disponível estiverem confirmados, **US$ 0,00703 permanece inconsistente com a gratuidade esperada informada pelo usuário**. A causa financeira permanece **não determinada**.

Uso agregado não contém distribuição de tokens por modelo, cached tokens, request IDs nem service_tier efetivo suficientes para reconciliar esse valor. Não apresentar um cálculo reverso como prova. As páginas oficiais de [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) e [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) documentam os modelos; elegibilidade promocional informada pelo usuário requer conferência nos termos associados à conta.

## RECOMMENDATION

Preservar as requests. Não modificar service_tier, store ou schema por suposição; não fazer outra inferência para “testar a gratuidade”.

Conferir no Dashboard o projeto e a data das quatro requests, modelo por linha, créditos/descontos e consumo diário da organização. Se a divergência persistir, abrir investigação no suporte com valor, intervalo, IDs disponíveis no Dashboard e comprovação de inscrição/projeto, **sem chaves ou conteúdo de documentos**. Não foi enviado pedido de suporte nesta tarefa.

O relatório técnico editável registra agora o valor observado, o volume dos ensaios e a investigação pendente. PDF/PPTX/MP4/ZIP finais continuam como snapshot do MVP estável; não foram regenerados. Próxima fase: B — especificação de produto e UX.

## Atualizacao posterior E0 - 04/10/2026

O usuario confirmou manualmente as cinco requests E0 no Dashboard Processing Tier Data sharing incentive tier. API returned service_tier=default permanece a observacao tecnica distinta. IDs e tokens preservados em [COMPLIMENTARY_MODEL_VALIDATION.md](COMPLIMENTARY_MODEL_VALIDATION.md); nenhum acesso de billing/API foi realizado para reconciliar. A cobranca historica dos quatro smokes anteriores nao foi retrospectivamente explicada por essa confirmacao. A orientacao inicial de dispensar E0.1 foi substituida por autorizacao explicita posterior: exatamente duas requests neutras Terra/Sol completed/OK, 26 input/10 output, API tier default, zero retries. Dashboard Terra/Sol PENDING_DASHBOARD_RECONCILIATION; nenhum valor monetario inferido. Routing definitivo ainda aberto. Apos E0.1, toda E1 continuou offline.

## Atualização factual — E0/E0.1 encerradas e piloto E2

04/10/2026: usuário confirmou as sete requests E0/E0.1 no Dashboard Data sharing incentive tier;92input/32output/124total,cached0. API returned tierdefault é observação distinta; a atualização não fez request de billing ou novo ping.

UM piloto Berkley autorizado foi executado após recoveryforense0reservas e dry-run7calls. Encerrado com21HTTP/21responseIDs,100165input(incluindo20504cached),6013output;2rejeições locais de schema abriram breaker20%/últimas10. Modelos15Luna/5Terra/1gpt-5.4-mini-2026-03-17. Nenhuma inferência posterior.

Custo **standard-rate counterfactual US$0,09310919**, incluindo respostas rejeitadas; nenhuma taxa de escrita de cache presumida. **EFFECTIVE BILLING = PENDING_DASHBOARD_RECONCILIATION.** Tierdefault21 não prova nem descarta incentivo; nenhuma cobrança efetiva US$0 atribuída.

Revisão humana/controle das21requests no Dashboard é o próximo gate. Billing histórico US$0,00703 permanece uma questão separada. [Relatório E2](BERKLEY_REAL_PILOT.md) registra IDs seguros nos manifestos locais, métricas, limitações e hashes; não executar inferência para testar gratuidade.

## Reconciliação Dashboard — FAST-TRACK

Usuário informou em04/10/2026 a coincidência ledger==Dashboard para28requests,100257input/20504cached/6045output, PROCESSING TIER DATA SHARING INCENTIVE — CONFIRMED. São7requestsneutras e21primeiroBerkley. A confirmação substitui o processamento pendente do primeiro piloto; não altera o APIreturneddefault nem prova valor monetário. Visão Costs não fornecida: custo efetivo NOT_REPORTED/PENDING_COSTS_RECONCILIATION. ContrafactualprimeiroUS$0,09310919 permanece cálculo técnico. Nenhuma request de reconciliação executada; futurasrequestsFAST-TRACK não herdam essa confirmação.

## FAST-TRACK — usage observado da nova sessão

46HTTP reservados/enviados/retornados, todosIDs únicos:22Berkley/23AXA/1comparação;160966input/56018cached input (subconjunto)/20379output/181345total. Inclui4falhas de contrato e respectivas alternativas;0usage desconhecido/0tentativas incertas. API tierdefault46, sem parameter service_tier explícito. ContrafactualtarifapadrãoUS$0,54789527 não é cobrança efetiva. PROCESSING TIER/EFFECTIVE BILLING novosruns PENDING_DASHBOARD_RECONCILIATION; visãoCosts não informada. Nenhuma inferência adicional para testar faturamento; confirmação histórica28requests não aplicada automaticamente. Ledger/SHA/notas em BERKLEY_REAL_PILOT e painel Uso da IA.
