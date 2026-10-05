# PUBLIC_VALIDATION_COST_MODEL — preflight estático

Data de verificação: **04/10/2026**. Baseline de código: **890ab0b**, branch local `feature/product-ux-hardening`. Documento da PHASE D: pesquisa oficial, contagem local e planejamento; **0 chamadas GenAI**. Nenhum cliente SDK, ping, endpoint de contagem de tokens ou inferência foi executado para esta análise.

O custo abaixo é um modelo de referência, não uma fatura nem uma promessa de gratuidade. O valor histórico de US$0.00703 informado pelo usuário continua sem conciliação por request/projeto/incentivo; ver [auditoria de billing](OPENAI_BILLING_AUDIT.md). Artefatos acadêmicos PDF/PPTX/MP4/ZIP permanecem congelados na baseline a946882. Qualquer GenAI real em documentos públicos exige autorização específica do usuário.

## 1. Roteamento e limites verificados no código

Os nomes abaixo são os defaults de [config.py](../src/config.py), usados pelo roteamento em [pipeline.py](../src/pipeline.py). Overrides de configuração podem mudar os modelos efetivos; confirmar somente os identificadores seguros antes de uma execução autorizada, sem expor credenciais.

| Etapa | Modelo OpenAI | Unidade da chamada | Limite de saída por chamada lógica | Reparo do agente |
|---|---|---|---:|---|
| Classificação da segmentação | `gpt-5.6-luna` | Até 20 seções ainda `UNKNOWN`, antes de dividir em chunks | 1600 tokens | Nenhum |
| Extração estruturada | `gpt-5.6-luna` | Um chunk/cláusula de até 4000 caracteres | 4500 tokens | Até uma chamada em Terra |
| Reparo da extração | `gpt-5.6-terra` | Schema/evidência inválidos após resposta JSON completa | 4500 tokens | Nenhum reparo adicional |
| Comparação semântica | `gpt-5.6-sol` | Até 8 campos elegíveis por batch, por par | 2500 tokens | Até uma chamada adicional em Sol |

Evidências: [segmentation.py](../src/agents/segmentation.py), [extraction.py](../src/agents/extraction.py), [comparison.py](../src/agents/comparison.py) e [openai_client.py](../src/llm/openai_client.py).

O schema contém 27 campos. Comparação monetária, de datas e de identidade é local: os conjuntos disjuntos têm respectivamente 4, 3 e 4 campos. Restam **no máximo 16 campos semânticos**, portanto **até 2 batches iniciais por par** com batch 8, ou até **4 chamadas lógicas/12 tentativas HTTP por par**, incluindo reparos e retries. Igualdade, ausência, conflito e trechos grandes podem reduzir esse número. O código forma os batches antes de filtrar limites de trecho/valor: uma contagem exata deve contar batches não vazios após esse filtro.

A extração não é “uma chamada por PDF” nem “uma chamada por página”. Títulos e divisão local determinam a quantidade de chunks. Um reparo só acontece quando a validação de schema/evidência do agente falha; erro HTTP, quota, resposta incompleta ou JSON inválido no adapter não são automaticamente esse reparo.

O SDK está configurado com retries internos desativados. O provider faz no máximo **3 tentativas HTTP por operação lógica, incluindo a primeira**: até 2 retries para erros transitórios elegíveis. Quota/crédito, autenticação, permissão e limites fatais interrompem; esperas acima do máximo também interrompem. Não presumir custo zero de toda tentativa falha: uma resposta perdida/timeout pode ter sido processada no servidor.

Na UI, `BoundedLogicalGateway` limita as operações a **30 por análise, configuráveis de 1 a 100**. Logo, o teto físico é 90 tentativas no default ou 300 no máximo, sem multiplicador de retry oculto do SDK. Esse limite pertence à UI; CLI/pipeline não recebem automaticamente o mesmo teto global. Atingir o limite pode interromper antes da comparação, preservando caches válidos já produzidos. Não elevar o limite nem contornar a UI silenciosamente.

Preparar documentos executa OCR nativo/Tesseract e segmentação local com `gateway=None`; não preenche automaticamente o cache de classificação GenAI. SQLite, JSON, relatórios operacionais, revisão, downloads e reruns de resultados não adicionam inferência. O RAG opcional não participa deste orçamento da UI.

## 2. Fórmula de chamadas por conjunto

Para `D` documentos únicos, de 2 a 5, há `D-1` pares referência × candidato; a referência é estruturada uma única vez. Não usar todas as combinações possíveis.

Para cada documento `i`:

- `U_i`: seções UNKNOWN antes de chunking.
- `B_i`: 0 se o cache de segmentação correspondente for válido; caso contrário, `ceil(U_i/20)`.
- `C_i`: chunks reais da segmentação local; `H_i`: hits validados de extração; `E_i = C_i - H_i`.
- `X_i`: reparos de extração, com `0 <= X_i <= E_i`.

Para cada par `j`, `Q_j` é o número de batches semânticos iniciais efetivamente enviados: 0 se houver cache válido do par; senão, entre 0 e 2 no schema/batch atuais. Reparos de comparação: `0 <= Y_j <= Q_j`.

```text
L_Luna  = sum_i(B_i + E_i)
L_Terra = sum_i(X_i)
L_Sol   = sum_j(Q_j + Y_j)

L_sem_reparos = sum_i(B_i + E_i) + sum_j(Q_j)
L_pior_caso   = sum_i(B_i + 2*E_i) + 2*sum_j(Q_j)

HTTP_UI <= R * min(L_pior_caso, limite_logico), com 1 <= R <= 3
```

A expressão de HTTP limita tentativas; não garante conclusão. Sem respostas ainda não é possível calcular quantos campos precisarão de comparação, quantos reparos ocorrerão ou quantos tokens serão efetivamente gerados.

Limites de saída do planejamento, antes do multiplicador conservador de tentativas HTTP:

```text
O_Luna_max  = 1600*sum_i(B_i) + 4500*sum_i(E_i)
O_Terra_max = 4500*sum_i(X_i)
O_Sol_max   = 2500*sum_j(Q_j + Y_j)
```

Os caps explícitos dos agentes prevalecem sobre `LLM_MAX_TOKENS=4096`. Tokens internos de reasoning entram no total de saída faturável e no limite `max_output_tokens`; texto visível não mede todo o consumo. Uma resposta incompleta pode consumir o cap sem entregar JSON útil. [Reasoning — OpenAI](https://developers.openai.com/api/docs/guides/reasoning).

## 3. Cache local e identidade

| Camada | Identidade e validação relevantes | Efeito no orçamento |
|---|---|---|
| Download público | Fonte/captura original, bytes e SHA256; proveniência em [sources.py](../src/sources.py) | Evita novo download; não comprova cache GenAI |
| OCR completo/página | SHA256 + `ocr-page-v1` + idiomas, mínimo de texto nativo, DPI e timeout; tamanho/media/páginas validados | Economiza CPU/OCR local; não é prompt caching OpenAI |
| Segmentação | SHA256 + `headings-bounded-v2` + modelo/provider + páginas completas | Hit válido evita classificação; metadata/timing de PageText participa da chave |
| Extração por cláusula | SHA256 + `clause-verified-v2` + cláusula inteira + modelo/provider + hash do prompt/schema + modelo de reparo | Hit precisa passar schema e citação literal; evita a extração daquela cláusula |
| Comparação por par | SHA256 de referência + `objective-semantic-bounded-v2` + ambos os reports + modelo/provider + hash do prompt + batch size | Hit válido evita as chamadas semânticas do par |

Referências: [ocr.py](../src/agents/ocr.py), [cache.py](../src/storage/cache.py), [identidade do provider](../src/agents/_evidence.py) e agentes acima. O wrapper da UI repassa a identidade do provider, preservando isolamento OpenAI/Groq.

Mudança de documento, modelo/provider, versão, prompt ou report pode invalidar caches. O cache de segmentação usa versão explícita, sem hash separado do prompt; alterações desse prompt exigem cuidado com versionamento. Reprocessamento OCR com novo timing também pode mudar a chave de segmentação. O JSON consolidado `policy-report-v1` é escrito ao final; sua existência não substitui a verificação dos caches das etapas. Falhas não são salvas como extrações válidas.

## 4. Preços oficiais verificados

Referência **Standard, USD por 1 milhão de tokens de texto**, consultada em **04/10/2026**. Os três IDs exatos existem nas páginas oficiais e não foram substituídos por outros modelos.

| Modelo | Entrada comum | Entrada lida de cache | Entrada escrita no cache (1.25×) | Saída | Fonte oficial |
|---|---:|---:|---:|---:|---|
| `gpt-5.6-luna` | 0.20 | 0.02 | 0.25 | 1.20 | [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) |
| `gpt-5.6-terra` | 2.00 | 0.20 | 2.50 | 12.00 | [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) |
| `gpt-5.6-sol` | 4.00 | 0.40 | 5.00 | 20.00 | [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) |

As páginas documentam que requests com **mais de 272000 tokens de entrada** multiplicam a entrada por 2 e a saída por 1.5 para o request inteiro. Sol registra preço promocional pelo menos até 21/11/2026; a tabela é um snapshot datado, não garantia futura. Confirmar novamente antes da execução. [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra), [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

O código não pede Batch/Flex/priority/ultrafast nem envia `service_tier`; `store=False` é preservado. A tabela não comprova o tier efetivo, condições regionais, impostos, câmbio ou aplicação de incentivo da conta. A telemetria registra tier seguro da resposta quando disponível; condições adicionais têm preços próprios. [Pricing — OpenAI](https://developers.openai.com/api/docs/pricing).

## 5. Tokens de entrada e fórmula monetária

Para cada operação, medir o payload completo: prompts, mensagens, IDs/categorias/páginas, JSON, schema e formato de saída. Na extração, o schema aparece **no system prompt e em `text.format`**; contar ambas as representações. Medição local do schema Pydantic desta baseline: **2198 caracteres/2206 bytes UTF-8**, antes de conversão para schema estrito e framing. Reparos acrescentam instrução de correção; os agentes não anexam a resposta anterior inteira.

`tiktoken` está ausente no ambiente verificado. Uma estimativa futura com encoding local só deve ser chamada de contagem do modelo se o mapeamento para o ID exato estiver verificado; fallback precisa registrar o encoding e a imprecisão. Dividir caracteres por uma constante não é contagem oficial; roles, schema, framing e instruções internas podem alterar os tokens. O endpoint oficial de contagem não foi chamado nesta fase. [Counting tokens — OpenAI](https://developers.openai.com/api/docs/guides/token-counting).

Hipótese do preflight local, para cada payload explícito `P_r`:

```text
I_referencia(r) = ceil(numero_caracteres(P_r)/3.5) + 1000
I_conservador(r) = numero_bytes_UTF8(P_r) + 1000
```

A reserva de 1000 tokens por operação e a contagem por bytes são margens de planejamento, não limites matemáticos sobre o tokenizer/overhead oculto da API. Aplicar ao payload inteiro, incluindo schema duplicado e correção quando houver. Guardar apenas contagens/metadados no relatório, sem prompts ou documentos integrais.

Para request com `I` tokens de entrada, `C` cache-read, `W` cache-write e `O` saída total, usar `N=I-C-W`, quando todos forem conhecidos e consistentes. As categorias de entrada são disjuntas; escrever cache custa 1.25× a tarifa comum daquela categoria, **não** uma taxa aditiva sobre todos os tokens. Cache hits dependem de prefixo elegível e estado do servidor, não da presença de um cache local. [Prompt caching — OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching).

```text
Custo_request = (N*P_input + C*P_cached + W*(1.25*P_input) + O*P_output) / 1000000
```

O provider atual registra `cached_tokens` conhecido ou `None`; uma operação sem detalhes torna o agregado desconhecido. **Não registra `cache_write_tokens`** e não configura `prompt_cache_options`. Portanto, nem mesmo `cached_tokens=0` permite reconstruir exatamente o custo de entrada. Não tratar `None` como zero nem presumir desconto no preflight.

Cenário conservador para planejamento: nenhum desconto de leitura de cache e toda entrada estimada cobrada como escrita; toda saída no cap; até `R` tentativas por operação enviada:

```text
Custo_planejado = sum_requests R * (1.25*P_input*I_conservador + P_output*O_cap) / 1000000
```

Aplicar os multiplicadores de contexto longo quando cabíveis e apresentar as hipóteses de tier. A expressão é conservadora em relação às tarifas consideradas; permanece dependente da qualidade da estimativa de entrada, do escopo e das condições da conta. Não é teto garantido de cobrança nem conciliador da cobrança histórica.

## 6. Contagens públicas locais já disponíveis

Contagem das cinco fontes reais com o segmentador atual, sem GenAI e sem assumir hits GenAI. Snapshot de 04/10/2026: [candidates.json](../data/processed/public_validation_preflight/candidates.json), atualizado às 05:07:22 UTC, com captura, hashes e método nativo por documento. Não inferir volume por páginas.

| Documento | Páginas | Chunks de extração | Seções UNKNOWN | Batches de classificação | Chamadas lógicas sem cache/reparo, apenas estruturação |
|---|---:|---:|---:|---:|---:|
| Chubb Capital Fechado 202512 | 70 | 700 | 392 | 20 | 720 |
| Berkley D&O, início 11/12/2025 | 83 | 480 | 316 | 16 | 496 |
| Sompo D&O | 49 | 444 | 283 | 15 | 459 |
| AXA D&O | 104 | 620 | 476 | 24 | 644 |
| Fator D&O 12/2025 | 150 | 1252 | 934 | 47 | 1299 |

Nenhum desses documentos integrais cabe isoladamente no orçamento UI de 30, nem no máximo 100, em execução fria. A comparação adicionaria chamadas somente depois da estruturação. Títulos em caixa alta produzem muitas seções curtas: 70 páginas não implicam 70 chamadas. OCR/download concluídos não comprovam cache de classificação/extração; categorias do preflight local ainda podem diferir das categorias classificadas com GenAI e afetar a chave de extração.

Esta tabela não demonstra qualidade de extração, comparação ou cobertura contratada. São condições públicas, não apólices individuais preenchidas; compatibilidade, escopo e exclusões de opcionais devem constar no preflight principal. Os cálculos monetários concretos precisam usar os payloads e escopo escolhidos, sem reaproveitar números sintéticos.

Antes de solicitar autorização de GenAI, o preflight principal deverá apresentar par/trio selecionado, hashes/fontes, chunks/batches/hits verificáveis, orçamento lógico/HTTP, estimativa de entrada com suas hipóteses, saída máxima, risco de reparo/tier/cobrança e estratégia de execução que caiba no limite. Um recorte precisa ter páginas originais e escopo explicitados; não pode ser apresentado como análise integral. **Até autorização específica, continuar apenas pesquisa e processamento local.**
