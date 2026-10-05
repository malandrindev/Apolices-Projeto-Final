# InsurMinds — UX_PRODUCT_SPEC (PHASE B)

Data: 03/10/2026. Base funcional: a946882. Auditoria A: 494335f. Este documento antecede alterações de interface; mudanças C obedecem aos contratos existentes. Origem destas melhorias: nova instrução do usuário, não novos mínimos do edital.

## Produto e usuários

**InsurMinds — Workspace de revisão assistida de condições D&O.** Persona principal: corretor/consultor de seguros corporativos ou analista de riscos que precisa justificar diferenças para cliente, Risk Manager ou Jurídico. Personas secundárias: gestores de riscos/seguros e Jurídico/Compliance. A linguagem principal descreve documentos, condições, diferenças e revisão; provider, modelos e tokens ficam em detalhes técnicos.

A aplicação reúne 2–5 documentos, identifica condições, compara cada candidato com uma referência e liga interpretações às evidências. Apoia análise securitária/jurídica humana; não seleciona a “melhor apólice”, não gera ranking global e não considera contagens uma recomendação de contratação.

Princípio: **diferença → explicação → evidência → página da fonte**. Dados desconhecidos aparecem como não identificados; condição geral de produto não se apresenta como apólice emitida para cliente.

## Auditoria da interface estável

| Ponto observado em interface/app.py | Implicação | Decisão C |
|---|---|---|
| Título genérico e aviso amplo no topo | Pouca identidade e foco no processamento técnico | Home InsurMinds com proposta de valor, aviso contextual e CTA Nova comparação |
| Provider e três modelos antes de documentos | Usuário decide infraestrutura antes de comparar condições | Configuração avançada recolhida; padrão já configurado |
| Uploads fixos A/B | Limita workspace e oculta identidade documental | Cards dinâmicos, 2–5 documentos e referência explícita |
| URL só informa citação | Não recebe documento pela origem pública | Modo URL pública baixa arquivo validado; catálogo curado usa mesma função |
| Uma tabela larga de diferenças/citações | Dificulta leitura e revisão | Abas, matriz consolidada, filtros e detalhe separado |
| Evidência misturada à tabela | Trecho/página ficam distantes da interpretação | Painel de detalhe e aba Evidências com documento/página/trecho |
| Resultados persistem após rerun | Comportamento já correto | Preservar; download/revisão/filtro nunca refazem inferência |
| Cache, pipeline, relatórios e pairwise funcionam | Investimento já validado | Reutilizar sem rewrite; nenhuma comparação combinatória |

## Jornada e layout

1. **Home / workspace vazio**: marca INSURMINDS; headline “Compare condições D&O com evidências, cláusula por cláusula.”; subtexto “Transforme documentos complexos em uma visão comparativa rastreável para apoiar sua análise.”; CTA **Nova comparação**. Identidade própria, azul petróleo/neutros, espaçamento, tipografia legível, cards e contraste. Ícone + texto para estados; nenhuma informação só por cor. Sem gradientes/animações.
2. **Receber documentos**: modos **Upload**, **Catálogo público**, **URL pública**. Upload aceita múltiplos PDF/imagens. Catálogo mínimo usa apenas URLs oficiais já registradas em SOURCES; pesquisa/atualização ampla só na fase D. URL exige link HTTP(S) público direto de PDF/imagem; página HTML não é tratada como documento. Usuário pode repetir adições até 5, remover e tornar qualquer documento referência. Hash evita duplicata; nomes internos seguros evitam sobrescrita e conflito de citações.
3. **Preparar documentos**: ação local valida documentos e extrai texto/OCR sem GenAI. Captura páginas/método, metadados determinísticos quando existentes e estimativa de cláusulas. Permite discutir compatibilidade antes da inferência. Caso ainda não preparado, status explicita isso e não promete compatibilidade alta.
4. **Revisar compatibilidade e confirmar**: referência marcada com ★; candidatos avaliados individualmente. Confirmação para envio de trechos ao provider, próxima ao botão **Analisar e comparar**. Configuração avançada exibe provider/modelos sem chaves. Nenhum fallback automático de conta/provedor. Dados de produto públicos são identificados como tais.
5. **Processar**: reutilizar process_and_structure_document uma vez por documento e ComparisonAgent(reference,candidate) uma vez por candidato. Progressos: documentos validados; texto/OCR n páginas; cláusulas identificadas; analisando condições x/n; comparando candidato x/n; preparando relatórios. Mostrar duração/cache/chamadas discretamente; tokens/modelos em Detalhes técnicos.
6. **Revisar resultados**: abas e filtros abaixo, seleção de campo/candidato e evidência de ambos. Confirmação/“Requer análise” por item; comentário/correção humana opcional local claramente separado do valor extraído.
7. **Exportar**: MD/PDF/JSON por par, reutilizando ComparisonReportAgent. Exportar matriz consolidada CSV e registro de revisão JSON se simples; conjunto não depende de backend N-way. Downloads/filtros/revisão persistem após rerun e não chamam IA. PDF comparativo gerado pela aplicação é saída operacional; PDF técnico final/PPTX/MP4/ZIP acadêmicos continuam congelados.

## Documento e metadados

Card antes de extração: filename, origem (upload/URL/catálogo), status, referência, remover. Após preparação/extração: seguradora e produto quando identificados, processo SUSEP, versão/vigência, páginas, natureza, método nativo/OCR, URL oficial e data de acesso para origem pública, hash em detalhes técnicos.

Os 27 campos do schema existente permanecem intactos. Metadados de apresentação podem usar uma dataclass auxiliar, com fonte e página/trecho quando reconhecidos deterministicamente. Catálogo possui metadados editoriais com origem; valores contratuais vêm exclusivamente da extração comprovada. Nome de arquivo não prova seguradora/produto. Sem dado seguro: “Não identificado”. Depois de GenAI, seguradora/vigência/moeda com evidência podem preencher metadados, sem inventar produto/tipo de empresa.

Título dos cards/resultados: seguradora — produto quando conhecidos; fallback filename. A/B são detalhes internos do agente, não identidade principal.

## Compatibilidade da comparação

Estados: **Alta**, **Média**, **Baixa**, **Não determinada**. Regras simples locais; nenhuma chamada IA para classificar compatibilidade.

Comparar ramo D&O, produto/escopo, tipo de empresa conhecido, período/versão, moeda e natureza. Discordância conhecida de ramo, capital aberto/fechado, moeda ou natureza, ou distância grande de versões reduz compatibilidade e mostra razão. Poucos dados sem discordância não dão “Alta”; mostrar “Compatibilidade ainda não determinada.”. Alta só quando vários atributos conhecidos coincidem e versões/escopos são suficientemente próximos. Média admite equivalência parcial com informação faltante. Explicar sinais utilizados e desconhecidos; confirmação humana continua necessária.

Versão de condições gerais não equivale a vigência da apólice. Campos ausentes não significam cobertura ausente. Compatibilidade é heurística de triagem, não conclusão contratual.

## Resultados e matrizes

Abas: **Visão executiva · Coberturas · Exclusões · Limites & Franquias · Cláusulas · Evidências · Fontes**.

Executiva por candidato: diferenças relevantes; pontos mais amplos que a referência; mais restritivos; sem ordenação segura; exigem revisão. Traduções das classificações locais explicam **vantagem pontual neste item**. Para números, “condição pontualmente mais favorável” descreve direção sem presumir cobertura mais ampla. Identificação/vigência jamais entram em contagem de vantagem. Nenhum ranking ou frase “esta é a melhor apólice”.

Matriz: Item | referência com nome | candidatos com nome (até quatro) | classificação por candidato | revisão | evidência. Todos os 27 campos podem ser consultados, inclusive iguais/ambos não localizados. Colunas de valores remetem ao detalhe pela seleção de item e documento; não inventar classificação igual para dado ausente em ambos.

Filtros: Todos, Diferenças, Coberturas, Exclusões, Limites, Revisão necessária. Grupos de campos definidos no módulo de apresentação; Cláusulas contém condições textuais e identificações não pertencentes aos outros grupos. Aba Fontes lista origem, tipo, URL, data, hash e limitações de escopo.

Detalhe do item: título; valores referência/candidato; classificação traduzida; justificativa do agente ou informação factual sobre igualdade/ausência; “Por que isso importa” apenas texto estático de domínio seguro; evidência de referência e candidato com nome, página, trecho e URL. Exibir página/trecho claramente é a entrega mínima. Viewer PDF pode ser adicionado só se não comprometer execução/estabilidade.

## Revisão humana simples

Estado por par+campo: Não revisado, Confirmado, Corrigido, Requer análise. Confirmar e marcar revisão têm botão; correção exige anotação explícita e aparece como anotação humana, mantendo valor/evidência originais e classificação do agente. Não recalcular vantagem automaticamente de uma correção textual. Armazenar na sessão e permitir exportar JSON; persistência colaborativa, usuários/permissões e histórico corporativo ficam para evolução.

Resultados preservados em rerun. Adição/remoção/troca de referência torna resultado anterior explicitamente histórico ou invalida sua associação ao workspace; jamais apresentar comparação antiga como se fosse do conjunto atual. Não remover dados de backend/caches ao limpar workspace.

## Arquitetura de implementação C

- Streamlit continua host. interface/app.py concentra layout e estado UI; módulos de apresentação pequenos conforme necessário.
- Camada auxiliar de workspace: documento com bytes/hash/metadados, resultado por par, agrupamento/compatibilidade/revisão. Orquestração sequencial referencia×candidatos reutiliza pipeline e ComparisonAgent. Reprocessamento local usa cache existente, inclusive OCR/cláusulas/comparações.
- Sources: UploadSource, PublicURLSource e PublicCatalogSource como adaptadores pequenos para mesma entrada documental. HTTP público limitado por tamanho/timeout/redirects e tipo/magic; bloquear loopback/privado, credenciais e URLs com segredo; falhas seguras sem corpo remoto. Cache local de downloads e proveniência, com atualização explícita sem prometer atualidade ilimitada.
- Providers e schemas preservados. Telemetria acrescenta cached_tokens quando o provider os informa; ausência é desconhecida, não inferir cache pela repetição. Cache local de documento/cláusula/comparação é distinto de cached input tokens do provedor.
- Não mostrar cobrança exata com base em Usage. Estimativa monetária, se implementada, exige preços versionados/configuráveis e rótulo Estimativa; pode ser omitida da UI.
- Limite de custo por execução, se útil, envolve wrapper de orçamento em torno do gateway; não muda requests nem service_tier. Orçamento e preflight público completos discutidos em D.
- OpenInsuranceConsentSource é futuro; não implementar dados particulares sem consentimento.

## Aceitação C e verificação

Preservar 85 casos existentes e assertivas do backend. Testes de UI que amarravam título/rótulo A/B podem acompanhar nova linguagem, mantendo comportamento verificado e sem eliminar casos.

Verificar sem APIs reais: startup/avançado; adição upload/URL/catálogo com mocks de rede; limite 5/duplicata; escolher referência/remover; compatibilidade desconhecida e conflito; execução 2/3/5 documentos via pipeline/gateway simulados; resultados/abas/citações; downloads/filtros/revisão/rerun sem inferência; falhas de URL/quota amigáveis, sem fallback; originais passam.

Browser QA real Streamlit/Edge pode usar somente gateway fake ou caches conhecidos com inferência bloqueada. Capturas são evidência de revisão; não gerar novo vídeo final. Checar hashes congelados, compileall e scanner staged antes do checkpoint C.

## Fase D e limites de entrega

Somente após C funcional: pesquisar SUSEP/Open Insurance/seguradoras, APIs/produtos públicos, catálogo comparável e versão. Apresentar fontes/candidatos/par, páginas/chamadas/cache/estimativa antes da validação paga relevante. Sem benchmark massivo; exigir autorização quando houver risco de gasto significativo ou chamadas pagas relevantes.

PDF técnico final, PPTX, MP4 e ZIP permanecem backups do a946882 até revisão visual do usuário e aprovação UX/validação pública. Não publicar, fazer push/PR, mudar visibilidade ou enviar e-mail. Integrantes/representante ainda dependem de dados reais.

Próxima ação: checkpoint B documental; executar C incrementalmente.
