# PRODUCT_SIMPLIFICATION

## P1 — auditoria da experiência existente

Auditoria feita antes de alterar `interface/app.py`, a partir do checkpoint `a188826`.

| Componente atual | Problema para o analista | Ação de apresentação |
|---|---|---|
| Adicionar → Preparar → compatibilidade → consentimento → Analisar | Exige conhecer estágios internos antes de obter valor | Upload direto, referência e um CTA **Comparar apólices** |
| Upload, catálogo e URL como abas principais | Três caminhos com peso igual tornam o início menos claro | Upload principal; catálogo/URL em **Outras formas de adicionar documentos** |
| Modelos, provider e limite de operações | Configuração técnica sem utilidade imediata para a persona | Manter defaults e controles em expander fechado |
| Cartões com SUSEP, método, origem e metadados extensos | Informação antes do resultado e nomes artificiais | Nome curto da seguradora; arquivo e identificação documental; demais dados secundários |
| Sete abas, matriz com revisão e painel de revisão sempre visível | Obriga o analista a trabalhar no processo de controle | Seis abas de negócio; revisão única e opcional, recolhida |
| Evidência expandida, estados em linguagem de extração | O rigor precede a diferença e sua relevância | Diferença → explicação → relevância → evidência sob demanda → página |
| Uso da IA e diagnósticos expostos após cada resultado | Telemetria ocupa o caminho executivo | **Detalhes técnicos** fechado, preservando métricas e diagnóstico |
| CSV/JSON/MD/PDF com igual destaque | A exportação pede uma escolha técnica | **Exportar relatório** em PDF; **Outros formatos** secundário |

A configuração avançada já iniciava recolhida. A simplificação mantém esse comportamento, remove o provider do caminho principal e preserva o carregamento da sessão validada. Não foi necessário reescrever backend, extração, comparação, routing, accounting ou caches.

## P2 — princípio de produto

InsurMinds é um copiloto para corretores consultivos, analistas de seguros corporativos e risk managers. A primeira resposta deve ser: **o que mudou e por que isso importa?**

A jornada implementada é: adicionar de dois a cinco documentos → escolher referência (primeiro por padrão) → comparar → ver diferenças relevantes → conferir a evidência quando necessário → exportar.

A demonstração usa especificações fictícias e excertos reais, explicitamente sem validade contratual. O resultado pré-processado não é apresentado como inferência feita na sessão. Nenhum ranking global de seguradora ou vantagem sem suporte será criado.

## Escopo e verificação

Mudanças limitadas à apresentação e integração da demo offline. Proveniência, citações, audit trail, telemetria, reservas, human-review model, modelos e limites continuam preservados. A validação inclui testes offline de apresentação e QA real do navegador sem provider HTTP.

## Implementação concluída

- Upload adiciona arquivos automaticamente; o primeiro é referência, com seletor simples e troca pelo card. Limite2–5 e deduplicação existentes preservados.
- A preparação local precede a análise automaticamente. Progresso: Lendo documentos / Identificando condições / Comparando / Preparando evidências.
- O resultado recolhe os documentos em **Documentos desta comparação** e apresenta o resumo executivo primeiro. Contagens ignoram diferenças de identidade e estados inconclusivos não viram conclusões.
- Seis abas, evidência sob demanda, prévia local da página PDF exata, download integral e trechos complementares na demo. Revisão humana única opcional e formatos secundários recolhidos.
- Rótulo fixo de modelo de comparação removido: routing depende do perfil; modelos efetivamente usados aparecem em Uso da IA. Ledger e accounting intactos.
- Demo humana validada por hashes/páginas/trechos, com aviso pré-processado e retorno antes do gateway. Exportações demo mantêm os avisos de dados fictícios/sem validade contratual.

A revisão independente corrigiu o suporte documental para livre escolha de defesa Porto e devolução no cancelamento Allianz, sem regenerar PDFs ou alterar estados/contagens. O QA encontrou e corrigiu a abertura do PDF recolhendo a evidência após rerun; seletores do harness acompanham expanders aninhados.

A clareza em aproximadamente30segundos continua uma avaliação humana pendente, não uma medida produzida pelo navegador. O ponto de parada é a validação final do usuário, antes de congelar/empacotar os acadêmicos. Evidências e contagens finais de testes estão em CURRENT_STATE/WORKLOG.

Verificação final: **538 testes+27 subtestes PASS**, compileall/diff/scanners PASS; Edge demo/uploadfake e sessão aprovada em cache PASS, zero provider HTTP/erros,136arquivos protegidos inalterados.
