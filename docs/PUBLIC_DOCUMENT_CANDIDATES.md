# Candidatos públicos D&O e preflight local — fase D

Snapshot: **04/10/2026**, concluído às `2026-10-04T05:07:22.817148+00:00`. Esta fase pesquisou fontes primárias, baixou cinco PDFs oficiais de seguradoras distintas e verificou integridade, leitura e segmentação local. **Chamadas de IA Generativa nesta fase: 0.** A validação semântica com modelos permanece uma etapa posterior, sujeita ao orçamento e à autorização correspondentes.

Os documentos são condições gerais/contratuais publicadas, não apólices individuais emitidas. A seleção apoia a homologação do MVP e a comparação de disposições; não demonstra contratação de uma cobertura nem permite escolher uma seguradora a partir do pacote publicado. Todos os arquivos têm texto nativo extraível: nenhuma página exigiu Tesseract neste preflight.

## Fontes, versões e adequação

| ID local / seguradora | Produto e processo SUSEP | Versão e início confirmado | Escopo e tipo | Páginas | Fonte primária | Adequação para homologação |
|---|---|---|---|---:|---|---|
| `chubb_fechado_202512` / Chubb | RC D&O Capital Fechado; `15414.900832/2017-90` | Rodapé `202512`; PDF p.4: riscos iniciados desde **11/12/2025** | Capital fechado explícito; CG, especiais e particulares | 70 | [PDF Chubb](https://www.chubb.com/content/dam/chubb-sites/chubb-com/br-pt/condicoes-gerais/diretores-e-administradores/capital-fechado-processo-susep-15414-900832-2017-90-versao-a-partir-de-16-12-2025.pdf) | Primeiro candidato para recorte de capital fechado; separar cláusulas gerais das extensões condicionais. |
| `berkley_do_202512` / Berkley | RC de Diretores e Administradores D&O; `15414.901494/2017-11` | PDF p.2: **12/2025**; página oficial: início **11/12/2025** | Pacote geral, especiais e particulares; capital aberto/fechado **não confirmado** | 83 | [Produto Berkley](https://www.berkley.com.br/produtos/directors-officers/) · [PDF Berkley](https://www.berkley.com.br/wp-content/uploads/2022/03/Seguro-DO_Vigencia-a-partir-de-11.12.2025_v2.pdf) | Boa segunda fonte recente para disposições comuns, com alerta explícito sobre escopo e coberturas contratadas. |
| `sompo_do_202511_v15` / Sompo | RC para Conselheiros, Diretores e/ou Administradores D&O; `15414.652408/2023-71` | PDF p.1: **novembro/2025, v1.5**, comercialização desde **29/11/2025** | CG; produto comercial admite capital aberto/fechado e gestoras; especiais referenciadas no texto | 49 | [Produto Sompo](https://sompo.com.br/produto/sompo-responsabilidade-civil-do) · [PDF Sompo](https://sompo.com.br/documents/d/guest/cg-rcd-o_28112025-71-v1-5) | Menor volume; útil para definições e regras gerais. PDF p.5 remete coberturas básicas às condições especiais, que não devem ser presumidas completas nesta amostra. |
| `axa_do_202512_v1` / AXA | RC de Administradores e Diretores D&O; `15414.901016/2017-01` | PDF p.1: **12/2025 V1**; dia de vigência **não confirmado** | CG e particulares; sumário p.3 inclui companhias abertas e cláusulas distintas para crise em capital fechado/aberto | 104 | [PDF oficial AXA](https://axa.com.br/minio/cms/CG_AXA_D_and_O_15414_901016_2017_01_20251230_90a6f5bcbc.pdf) | Boa fonte para comparação de pacotes, desde que a seleção de cláusulas preserve as condições de aplicação de cada extensão. |
| `fator_do_2025` / Fator Seguradora | RC de Diretores e Administradores D&O; `15414.672942/2025-66` | Rodapé **12/2025**; tabela oficial: início **11/12/2025** | CG e numerosas extensões/particulares; título não segrega capital aberto/fechado | 150 | [Produto Fator](https://fatorseguradora.com.br/responsabilidade-civil-administradores-do/) · [PDF Fator](https://fatorseguradora.com.br/wp-content/uploads/2026/05/Condicoes-Contratuais-DO-12.2025.pdf) | Amostra adicional para volume e extensões; PDF p.8 condiciona coberturas à especificação da apólice. Evitar como primeiro documento por volume. |

A origem registrada para os cinco candidatos é `official_insurer_public_pdf`, com acesso em **2026-10-04**. A fonte do processo é o próprio PDF; isso não representa consulta de homologação ou recomendação pela SUSEP. A vigência de uma versão das condições também não equivale à vigência de uma apólice individual.

A data contida na URL não substitui o documento: o nome Chubb menciona 16/12/2025, mas seu texto informa 11/12/2025; a pasta Berkley `2022/03` não corresponde à versão 12/2025; a pasta Fator `2026/05` não altera a versão 12/2025; a string AXA `20251230` não comprovou uma vigência diária. Na Sompo, o PDF confirmou 29/11/2025 apesar de `28112025` no link. Essas diferenças permanecem no manifesto, sem normalização inventada.

## Evidência local reproduzível

Manifesto completo: `data/processed/public_validation_preflight/candidates.json` (ignorado pelo Git). Os PDFs abaixo ficam nesse mesmo diretório; nenhum foi adicionado aos artefatos finais nesta fase.

| Arquivo | Bytes | SHA-256 |
|---|---:|---|
| `chubb_fechado_202512.pdf` | 573314 | `bd070d40cdb7b99db5dd53e9652caf46cb7adade8d7dc2fe534f3c8c05820396` |
| `berkley_do_202512.pdf` | 1133026 | `038683e096c2ae0378df25ef18d147493f72627399d1530310af14023df8fea8` |
| `sompo_do_202511_v15.pdf` | 762706 | `a6964c4f6098bdf1791c880560317f9986c9bd958da462ba0cba44c9e7dad688` |
| `axa_do_202512_v1.pdf` | 978384 | `1798723c7a3078496bfc9dcedcc5a02ac5e1a5466784264d517f3ad9f8bc5f19` |
| `fator_do_1225.pdf` | 1590836 | `b23270f558168920a381f995aa352fd86bf3dbf13c34149eecad3438c70c659a` |

Cada arquivo passou por assinatura PDF, limite de 30 MB, validação de documento legível/não protegido, SHA-256 e contagem de páginas. `OcrAgent` extraiu página a página e `SegmentationAgent(gateway=None, max_chunk_chars=4000)` segmentou sem classificador remoto. Sompo e AXA usaram o diretório padrão de processamento para possibilitar reutilização de OCR local; as capturas e os demais caches de preflight permanecem no diretório ignorado correspondente.

| Seguradora | Páginas nativas | Caracteres extraídos | Chunks | Maior chunk | Seções por título | Seções ambíguas | Lotes de classificação de até 20 títulos* |
|---|---:|---:|---:|---:|---:|---:|---:|
| Chubb | 70 | 195824 | 700 | 4000 | 699 | 392 | 20 |
| Berkley | 83 | 242537 | 480 | 3899 | 480 | 316 | 16 |
| Sompo | 49 | 122301 | 444 | 3144 | 444 | 283 | 15 |
| AXA | 104 | 236524 | 620 | 3259 | 620 | 476 | 24 |
| Fator | 150 | 304562 | 1252 | 2664 | 1252 | 934 | 47 |
| **Total** | **456** | **1101748** | **3496** | **4000** | **3495** | **2401** | **122** |

\* Lotes calculados por documento com `ceil(seções ambíguas / 20)`; **não foram executados**. Títulos repetidos de rodapés, índices e subitens podem virar seções. Seção/chunk é unidade do pipeline, não contagem jurídica de cláusulas, e categorias heurísticas não comprovam precisão semântica. A divisão de uma seção longa explica Chubb possuir 700 chunks para 699 seções. Essas medições servem ao planejamento de chamadas, não a uma estimativa de cobrança baseada somente em páginas.

As durações locais registradas não são diretamente comparáveis: Chubb/Berkley incluíram captura e preflight; Sompo/AXA mediram validação/extração/segmentação após captura; Fator foi processado separadamente. Nenhuma duração de LLM, custo ou precisão de mercado foi medida nesta fase.

## Recomendações para a próxima homologação

**Par inicial: Berkley + AXA**, com **187 páginas, 1100 chunks e 40 lotes possíveis de classificação**. Ambos são pacotes D&O com versão 12/2025 e condições gerais/particulares. As disposições de mercado de capitais da AXA integram cláusulas particulares do pacote, não demonstram um produto exclusivamente de capital aberto. Comparar primeiro as regras gerais comuns e manter alertas sobre contratação de extensões; o capital aberto/fechado de Berkley não foi confirmado. A compatibilidade documental precisa ser avaliada por cláusula, sem equiparar automaticamente os produtos.

**Trio principal: Berkley + AXA + Fator**, com **337 páginas, 2352 chunks e 87 lotes possíveis**, se o orçamento autorizar esse volume. Fator acrescenta outro pacote D&O geral da mesma competência 12/2025. A classificação de capital aberto/fechado de Fator também não foi confirmada, e suas numerosas extensões dependem da especificação. A indicação é técnica para homologação, sem recomendar contratação nem afirmar equivalência integral.

**Alternativa de menor volume: Chubb + Berkley + AXA**, com **257 páginas, 1800 chunks e 60 lotes possíveis**. O recorte de Chubb é explicitamente capital fechado; as outras duas fontes devem permanecer com seus escopos e particulares registrados. Não se atribui alta comparabilidade automática a capital fechado versus capital não confirmado. Se necessário um par centrado no documento de capital fechado, Chubb + Berkley soma **153 páginas, 1180 chunks e 36 lotes possíveis**, com a mesma ressalva.

Para um ensaio limitado de leitura e regras gerais, **Sompo + Berkley** reduz o volume a **132 páginas, 924 chunks e 31 lotes possíveis**. É o menor par disponível, mas não substitui a homologação comparativa de coberturas especiais da Sompo. O menor trio por chunks é **Sompo + Berkley + AXA**: **236 páginas, 1544 chunks e 55 lotes possíveis**; a mesma limitação documental permanece.

Antes de usar modelos, o preflight de orçamento deverá considerar os chunks efetivamente extraídos, títulos ambíguos, modelos, tokens previstos de entrada/saída, limite de gastos e possibilidade de interrupção/retomada. O número de chunks não inclui chamadas de comparação, correções/retries ou consultas RAG. Dados como prêmio, LMG numérico, franquia contratada, segurado e período individual podem legitimamente estar ausentes em condições gerais; `nao_localizado` deve ser tratado conforme a evidência, sem preencher valores fictícios.

## Acesso e candidato indisponível

Chubb, Berkley e AXA foram capturados pelo `PublicURLSource` padrão, com DNS/IP público validado, TLS verificado, limite de redirects, tempo e corpo. Sompo falhou no contexto TLS padrão do Python com `SSLCertVerificationError` após aproximadamente 0,275 s; uma captura independente com certificados CA `certifi` validados e User-Agent normal de navegador passou pelos mesmos controles de rede e pela validação de documento. Nenhum controle TLS/SSRF da aplicação foi removido. Essa incompatibilidade do resolvedor padrão permanece relevante para futuros uploads por URL; os bytes locais validados podem ser usados pelo fluxo de upload.

Allianz foi pesquisada como candidata adicional: [produto oficial](https://www.allianz.com.br/seguros/grandes-riscos/linhas-financeiras/responsabilidade-civil-diretores-e-administradores.html), [PDF dezembro/2025](https://www.allianz.com.br/content/dam/onemarketing/iberolatam/allianz-br/doc-para-links/CG_RCDO_1225.pdf), processo `15414.901113/2017-96`, 75 páginas verificadas pelo leitor web. O conteúdo reúne disposições para empresas abertas e extensões de fundos/SPAC, portanto não foi classificado como exclusivo de capital fechado. A rede local recebeu **HTTP 403** tanto no leitor seguro quanto no Edge real, inclusive no produto oficial; o primeiro retorno levou aproximadamente 0,06 s. O Edge esperou 20 s por um evento de download que não aconteceu. **Não há PDF Allianz local nem contagem local de chunks.** O manifesto mantém o erro como indisponibilidade de acesso, sem atribuir um timeout fictício ao PDF e sem usá-lo no par/trio recomendado.

A fase D preservou o catálogo existente. Qualquer incorporação destas novas referências à interface e qualquer validação com IA devem ocorrer na fase autorizada correspondente, usando estes hashes e metadados como origem verificável.
