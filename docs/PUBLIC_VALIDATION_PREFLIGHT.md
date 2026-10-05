# PHASE D — pesquisa e preflight público, sem inferência

Verificado em **04/10/2026**. A PHASE C foi concluída e salva em **890ab0b**, após QA no Edge, suíte consolidada e scanner. Esta primeira etapa D pesquisou SUSEP/Open Insurance/seguradoras e executou somente download, leitura nativa/OCR local, segmentação determinística e estimativa. **Nenhuma chamada OpenAI/Groq real; nenhuma validação semântica pública com GenAI.**

## Descoberta oficial

O Open Insurance tem recurso específico D&O na Fase 1: `GET /open-insurance/products-services/v2/directors-officers-liability`. A versão **2.0.0 é Current**; **3.0.0 é Release Candidate**. Os contratos examinados não declaram autenticação para esse recurso público. O host é da participante; não há um servidor central de produtos na SUSEP comprovado por esta pesquisa. [Portal oficial OPIN](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/753678), [OAS Current](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/current/directors-officers-liability.yaml).

A resposta é JSON com metadados de produto. `termsAndConditions.definition` pode conter URL, mas o schema não garante URI, PDF ou texto completo. LMG máximo admitido pelo produto não equivale ao limite contratado. Apólices particulares pertencem a contratos separados com autorização/consentimento. Nenhum endpoint operacional de participante foi consultado; disponibilidade D&O por seguradora ainda não foi demonstrada. [OAS Current](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/current/directors-officers-liability.yaml), [SUSEP](https://www.gov.br/susep/pt-br/assuntos/open-insurance/).

Detalhes de autenticação, campos, versões, descoberta e limites: [OPEN_INSURANCE_RESEARCH.md](OPEN_INSURANCE_RESEARCH.md).

## Cinco documentos efetivamente capturados

Todos são condições públicas oficiais; nenhum é apólice individual emitida. As versões são próximas, novembro/dezembro de 2025. A tabela completa com produto, processo SUSEP, escopo, data, origem, URL oficial, adequação e SHA está em [PUBLIC_DOCUMENT_CANDIDATES.md](PUBLIC_DOCUMENT_CANDIDATES.md).

| Seguradora | Versão confirmada | Páginas | Seções por título | Chunks locais | Lotes possíveis de classificação |
|---|---|---:|---:|---:|---:|
| Chubb — capital fechado | 202512 | 70 | 699 | 700 | 20 |
| Berkley — pacote geral | 12/2025 | 83 | 480 | 480 | 16 |
| Sompo — CG, especiais referenciadas | novembro/2025 v1.5 | 49 | 444 | 444 | 15 |
| AXA — CG e particulares | 12/2025 V1 | 104 | 620 | 620 | 24 |
| Fator — CG e extensões | 12/2025 | 150 | 1252 | 1252 | 47 |
| **Total** | | **456** | **3495** | **3496** | **122** |

**Chunk/seção não é contagem jurídica de cláusulas.** Cabeçalhos, rodapés repetidos, sumário e subitens podem formar segmentos adicionais na heurística atual. Os 122 lotes são estimados, não executados. Todos os chunks têm até 4000 caracteres.

Os cinco arquivos passaram por validação de PDF, SHA e páginas. Todas as 456 páginas tinham texto nativo, sem necessidade de Tesseract. A execução do estimador reconferiu hash e chunk count; a retomada comprovou **5/5 hits de cache de OCR local**, sem GenAI. Não foi assumido cache de extração/classificação/comparação GenAI nem desconto de cached tokens do provedor.

Sompo exige ajuste do contexto de certificados para este ambiente: a captura de pesquisa usou CA certifi verificada; o resolvedor padrão da aplicação continua falhando nessa origem. Os bytes locais validados permitem upload. Allianz foi uma alternativa oficial, mas retornou HTTP 403 na rede local e no Edge: não entrou no conjunto medido. Não foram removidos controles TLS/SSRF e o catálogo da interface não foi alterado.

## Par e trio recomendados

**MELHOR PAR PARA PRIMEIRA VALIDAÇÃO REAL: Berkley × AXA**, para comparar disposições gerais comuns de pacotes D&O de 12/2025. Referência proposta: Berkley. O capital da Berkley não foi confirmado; particulares da AXA para companhias abertas e crise continuam condicionais. A recomendação não afirma equivalência integral de produtos ou cobertura contratada.

**MELHOR CONJUNTO DE 3 DOCUMENTOS PARA DEMO MULTI-DOCUMENTO: Berkley + AXA + Fator**, com Berkley como referência e dois candidatos. Fator acrescenta outro pacote geral da mesma competência, com muitas extensões e maior volume. É uma proposta de conjunto documental, condicionada ao orçamento e à resolução da fragmentação antes de inferência.

Chubb + Berkley + AXA é alternativa menor, com recorte fechado da Chubb explícito; não se presume escopo fechado para as demais. Sompo + Berkley reduz volume, mas não atende uma comparação completa das coberturas especiais da Sompo.

## Chamadas e custo antes de autorização

Cálculo por documento único, estruturação reaproveitada entre pares: `N` chunks de extração; `B` lotes de títulos ambíguos; até 2 lotes semânticos por par. Com reparo em todos os chunks e lotes de comparação: `2N+B+4P` operações; até 3 tentativas HTTP por operação, incluindo a inicial.

| Cenário documental | Páginas | Chunks / lotes de títulos | Pares | Operações sem reparos | Operações com todos os reparos | HTTP no estresse |
|---|---:|---|---:|---:|---:|---:|
| **Berkley × AXA** | 187 | 1100 / 40 | 1 | **1142** | 2244 | 6732 |
| **Berkley + AXA + Fator** | 337 | 2352 / 87 | 2 | **2443** | 4799 | 14397 |
| Sompo × Berkley, somente regras gerais | 132 | 924 / 31 | 1 | 957 | 1883 | 5649 |

Essas contagens são máximos lógicos do fluxo frio nesta configuração; falhas/quota/orçamento podem interromper a execução. Operações lógicas e tentativas HTTP são distintas. O cenário de estresse não significa que toda tentativa seria cobrada.

Tarifas oficiais consultadas em 04/10/2026, USD por milhão de tokens: Luna **0,20 entrada / 0,02 cached / 1,20 saída**; Terra **2,00 / 0,20 / 12,00**; Sol **4,00 / 0,40 / 20,00**. [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra), [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

Sem tiktoken instalado, o planejamento usa caracteres/3,5 + margem de 1000 por operação. A reserva de entrada usa bytes UTF-8 + margem, incluindo os schemas de mensagem e structured output. Saída planejada: 1500 por extração/comparação e 800 por classificação, hipóteses sem medição de LLM. Reserva sem reparos usa caps 4500/1600/2500. Todos os tokens de entrada foram valorizados a 1,25× como cenário conservador de cache-write, sem desconto de cache nem crédito complimentary. Reasoning conta na saída. [Prompt caching OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching).

| Cenário | Planejamento, sem reparos | Reserva de entrada + caps de saída, sem reparos | Estresse: todos os reparos Terra/Sol e 3 tentativas |
|---|---:|---:|---:|
| **Berkley × AXA** | **US$ 2,9793** | **US$ 9,8751** | **US$ 284,3190** |
| **Berkley + AXA + Fator** | **US$ 6,3279** | **US$ 20,7967** | **US$ 604,2913** |
| Sompo × Berkley | US$ 2,5280 | US$ 8,5801 | US$ 240,3081 |

São cenários de planejamento, **não preço garantido, teto financeiro imposto ou cobrança prevista**. O estresse assume conservadoramente que todas as tentativas consumiriam tokens, sem afirmar a política de cobrança de erros. Tokens reais, quantidade de reparos, tier efetivo, cache, incentivo da conta, tarifas e tributos podem alterar o total. Revalidar preços/configuração imediatamente antes de qualquer execução autorizada. Auditoria do incentivo continua inconclusiva em [OPENAI_BILLING_AUDIT.md](OPENAI_BILLING_AUDIT.md).

## Decisão e próximo passo

**Não executar os PDFs completos com a configuração atual.** O orçamento da interface é 30 operações por padrão, máximo 100. Mesmo o menor par frio exige centenas de operações. O CLI não oferece o mesmo limite global automaticamente; não deve ser usado para contornar o orçamento. Aumentar o limite sem reduzir fragmentação/definir um piloto não resolve custo e tempo.

Ações reais para a etapa posterior, sujeita ao escopo autorizado:

1. Aguardar autorização explícita para avançar; nenhuma inferência pública está autorizada agora.
2. Definir piloto de cláusulas com páginas/evidências ou ajustar a segmentação local preservando conteúdo/proveniência e cache. Repetir somente esse preflight após a mudança.
3. Apresentar teto de operações/tokens/custo e comportamento de interrupção/retomada do piloto antes da primeira chamada paga. Não ampliar limites ou presumir gratuidade.
4. Validar qualidade por campo/citação, escopo e contratação antes de atualizar o catálogo ou descongelar artefatos finais.

Reprodução **somente local**, com o manifesto/PDFs desta captura já presentes:

~~~powershell
.\.venv\Scripts\python.exe scripts/preflight_public_validation.py --scenario berkley_do_202512 axa_do_202512_v1 fator_do_2025
~~~

O script não constrói cliente de inferência. OCR é local; segmentação tem gateway None e a extração só fornece schema, com gateway que rejeita chamadas. O resultado fica em `data/processed/public_validation_preflight/cost_estimates.json`, ignorado pelo Git; não imprime documentos/prompts/chaves. Método e preços versionados: [PUBLIC_VALIDATION_COST_MODEL.md](PUBLIC_VALIDATION_COST_MODEL.md), [OPENAI_PRICES_2026-10-04.json](OPENAI_PRICES_2026-10-04.json). PDFs/caches permanecem locais. Requisitos acadêmicos, checkpoints A/B/C e PDF/PPTX/MP4/ZIP finais continuam preservados.
