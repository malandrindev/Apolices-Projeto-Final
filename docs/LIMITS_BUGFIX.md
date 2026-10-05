# LIMITS_BUGFIX — correção de entrega, 05/10/2026

READY_FOR_USER_FINAL_VALIDATION = **YES**. Correção offline a partir de `f8122b0`, na branch `feature/product-ux-hardening`. Simplificação do produto e dataset demo preservados. Nenhuma fase reiniciada ou nova inferência real executada.

## BUG ROOT CAUSE / LIMITS GROUP FIX

A extração routed mantém14grupos semânticos, mas o callback final percorria os7grupos legados e acessava `group_hits["LIMITS"]`. Esse ID não existe no contrato routed: limites/sublimites e retenções são grupos distintos. A extração Porto chegou a consumir requests antes desse callback levantar `KeyError`. O problema não era ausência de cobertura nem necessidade de alterar os documentos.

`src/retrieval/groups.py` agora concentra os dois contratos. Retrieval e extração importam a mesma definição; o progresso percorre14grupos routed ou7legados, tanto em execução quanto em cache, e usa acesso defensivo para hits ausentes. IDs, ordem, versões, prompts e chaves de cache anteriores permanecem compatíveis. Cada contrato contém os27campos exatamente uma vez.

O resolvedor explícito conserva desmembramentos: `LIMITS` → `LIMITS_SUBLIMITS` + `RETENTIONS_DEDUCTIBLES`; `CORE_COVERAGES` → coberturas/defesa/consentimento/rateio; `SCOPE` → território/jurisdição; `EXTENSIONS_DEFINITIONS` → extensões/cancelamento/definições. `COVERAGES` é alias explícito de `CORE_COVERAGES`, sem alegar histórico não comprovado desse nome.

O quarto argumento do callback continua sendo booleano de cache de lote, não contagem de candidatos. Grupo sem candidatos preserva diagnóstico `candidate_count=0`, estado `NOT_RETRIEVED` e informação incompleta. A interface usa “Não localizado com segurança”; isso não significa cobertura ausente. `KeyError` eventual é convertido em mensagem de negócio, sem traceback ou ID interno.

## ANY REAL REQUESTS FOUND BEFORE CRASH

Ledger real preexistente: `data/processed/workspace_usage/b872b7a78de8445f8c3037a0a300bfd9-attempt-ledger.json`.
SHA-256 preservado: `9975cfddeac2d9b66152385a6ddddf60e80c7a3821e2a88683eb3d65b732ffc7`.

| Medida | Evidência observada |
|---|---|
| Reserved |24 reservas persistidas |
| Sent |24 envios com24 request IDs distintos |
| Returned |24 response IDs distintos |
| Completed |22 eventos `completed`;2 eventos `error` por `schema_invalid` |
| Uncertain |0 tentativas pendentes/sem recibo nesse ledger |
| Fontes |24 eventos Porto52p;0 Allianz75p;0 comparação |
| Modelos |Terra16 / Sol7 / Luna1 |
| Validação final |5 VALID /8 SEMANTIC_INCOMPLETE /8 EVIDENCE_INVALID /1 AMBIGUOUS /2 SCHEMA_INVALID |

“Completed” descreve processamento da resposta; não comprova extração correta. O workspace não chegou a concluir: não há relatório agregado final aprovado desse par. Existem14caches parciais de lote e2caches OCR nativos, preservados. A exceção escapou do handler antigo antes de gravar resumo interrompido. Nenhum registro faltante foi reconstruído.

Usage registrado:103333input /28034cached como subconjunto /12575output /115908total. Esta auditoria não altera billing/incentive, não afirma gratuidade nem custo efetivo. **Zero novas requests reais durante este bugfix.**

## DUPLICATES PREVENTED

Uma inspeção offline, interrompida antes do dispatch e sem SDK/HTTP, constatou que a primeira etapa inválida não tinha cache reutilizável. Criar novo run_id e repetir o upload poderia reenviar trabalho já consumido. Cache parcial não autoriza replay.

`src/workspace_recovery.py` consulta ledgers em modo somente leitura antes da configuração/construção do gateway. Bloqueia fontes de execução legada/incompleta com tentativas consumidas, incluindo hashes legados de comparação nos dois sentidos. Novos ledgers registram documentos e estados `ACTIVE`/`COMPLETED`/`INTERRUPTED`. Resultado parcial ou parada técnica conserva `INTERRUPTED`. Reservas pendentes, contador consumido sem evento e erros sem recibo continuam incertos e consumidos; mesmo um marker `COMPLETED` não libera esse histórico.

O ledger real não foi editado, encerrado ficticiamente ou apagado. A UI orienta usar a demo offline quando encontra esse histórico. Resultado da mesma comparação na sessão é reutilizado antes do gateway, inclusive parcial; reset não apaga histórico. Falha ao persistir o resumo interrompido também recebe mensagem segura, preservando metadados disponíveis na sessão.

## TESTS

- **601 testes +27subtestes PASS em69,30s**, suíte consolidada offline após o último ajuste. Todos os testes anteriores preservados,63casos novos. SDKs OpenAI/Groq síncronos/assíncronos e transporte httpx bloqueados;0tentativas bloqueadas. Log: `data/processed/limits_bugfix_final_regression.log`.
- `tests/test_group_progress.py`:17casos — grupos/aliases/campos únicos, hit ausente, zero candidatos, cold/cache legado e routed, evidências e chave de cache anterior.
- `tests/test_workspace_recovery.py`:37casos — legado/manual24, pares, workspace5docs, marcadores, pendências/erros sem recibo, contadores incompletos, fonte não envolvida, histórico ilegível sanitizado e ausência de mutação.
- `tests/test_limits_pipeline.py`:4casos — preparação integral Porto52p/Allianz75p; pipeline/routing reais com provider fake,14callbacks por documento, comparação e AppTest/reuso/demo.
- `tests/test_limits_ui_recovery.py`:5casos — bloqueio antes do gateway, demo disponível, KeyError seguro após reserva/retorno, erro de gravação e resultado parcial sem liberação de novo orçamento.
- compileall src/interface/scripts/tests e diff/check PASS. Scanners staged/tracked e integridade final registrados no CURRENT_STATE/WORKLOG.

## BROWSER QA / DEMO FLOW / PRODUCT STATUS

**Microsoft Edge PASS**, servidor isolado8508: upload dos originais Porto52p/Allianz75p → referência Porto → preparação local →14grupos por fonte → resultados/seis abas → Comparar novamente sem incremento → Nova comparação → demo Porto30p/Allianz33p → resultados pré-processados.

Foram **46 operações exclusivamente MOCK**,0SDK/0HTTP real,0IDs/fingerprints duplicados e0erros de navegador. Esses46mocks não são as46requests históricas Berkley/AXA. Resultado mock é inconclusivo e não valida precisão GenAI. Demo conserva14diferenças/3equivalências/4insuficientes, avisos fictício/wording real/sem validade contratual e0inferências. Capturas01–03 inspecionadas.

Evidência: `data/processed/limits_browser_qa/20261005-011225/diagnostics.json`. As primeiras tentativas encontraram conflito local Windows em `os.replace` do checkpoint JSON. Ajuste restrito ao harness: watcher desabilitado e patches estáveis entre reruns; logs das falhas preservados. Nenhuma alteração no mecanismo de OCR/cache de produção.

Upload principal, referência simples, CTA único, fontes secundárias, evidências/revisão/exportação e configuração avançada mantidos. O trabalho PRODUCT SIMPLIFICATION + DEMO DATASET já estava concluído no checkpoint anterior; não havia implementação restante a reiniciar. Backend restante, caches/claims/telemetria, originais, golden, billing e quatro acadêmicos preservados por SHA em174arquivos protegidos. PDF/PPTX/MP4/ZIP finais não foram regenerados; nenhum push/publicação/e-mail.

Servidor normal8503 reiniciado com watcher desabilitado para a sessão de validação; health200. Somente essa instância foi reiniciada;8501 preservada.

## FILES CHANGED / LOCAL COMMIT / NEXT EXACT ACTION

- Interface: `interface/app.py`.
- Extração/retrieval: `src/agents/grouped_extraction.py`, `src/retrieval/local.py`, `src/retrieval/groups.py`.
- Recuperação: `src/workspace_recovery.py`.
- QA: os quatro testes acima e `scripts/check_limits_browser.py`.
- Documentação: este arquivo, `docs/CURRENT_STATE.md`, `docs/WORKLOG.md`, `docs/END_TO_END_VALIDATION_GUIDE.md`.

Checkpoint local posterior a `f8122b0`; obter hash em `git log -1`. Runtime/.env/assets/finais fora do staging. **STOP — USER FINAL VALIDATION**: abrir http://127.0.0.1:8503 → Nova comparação se houver resultado → Usar exemplo de demonstração → Porto referência → Comparar apólices → resumo/seis abas → evidência/página → Exportar relatório. Não retentar o par original interrompido com provider real. Não executar APIs, pings, replay, publicar ou gerar finais sem próxima autorização do usuário.
