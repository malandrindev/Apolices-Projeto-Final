# END_TO_END_VALIDATION_GUIDE — validação manual final

Estado esperado: **READY_FOR_USER_FINAL_VALIDATION=YES**. Comece pela demo Porto/Allianz offline. Os wordings são reais; especificações fictícias; sem validade contratual. O resultado é pré-processado e não aciona OpenAI/Groq.

No PowerShell:

~~~powershell
Set-Location -LiteralPath "C:\dev\I2A2\Apolices-Projeto-Final-I2A2"
$env:LLM_PROVIDER = "openai"
$env:MODEL_ROUTING_ENABLED = "true"
.\.venv\Scripts\python.exe -m streamlit run interface/app.py --server.address 127.0.0.1 --server.port 8503 --server.headless true --browser.gatherUsageStats false --server.fileWatcherType none
~~~

Abra http://127.0.0.1:8503. Se a instância já estiver servindo nessa porta, abra o endereço sem iniciar outra. A demo não exige chave de API. O perfil configurado continua disponível para usos futuros autorizados.

| Passo | Ação | Resultado esperado |
|---|---|---|
| 1 — Iniciar | Abrir a Home. | Upload principal, exemplo visível; configuração avançada e outras fontes fechadas. Nenhuma etapa técnica obrigatória. |
| 2 — Demo | Clicar **Usar exemplo de demonstração**. | Porto30p + Allianz33p; Porto referência inicial; aviso de dados fictícios e wordings reais. |
| 3 — Comparar | Clicar **Comparar apólices**. | Resumo executivo primeiro; resultado pré-processado; 14 diferenças, 3 equivalências, 4 sem conclusão segura. Não há ranking global. |
| 4 — Explorar | Consultar Resumo, Coberturas, Limites & Franquias, Exclusões, Cláusulas e Evidências. | Valores referência/candidato, explicação e relevância por item. Inconclusivo não significa cobertura ausente. |
| 5 — Conferir | Abrir **Ver evidência** de um item e **Abrir documento na página N**. | Documento correto, página demo e trecho literal; origem sintética ou página do wording original. PDF disponível para abrir/baixar. |
| 6 — Exportar | Clicar **Exportar relatório**. | PDF operacional por par; CSV/JSON/Markdown e revisão JSON em Outros formatos. Nenhuma inferência. |
| 7 — Revisão opcional | Abrir **Revisão humana — opcional**; Confirmar, Precisa de revisão ou Corrigir. | Anotação preservada no rerun; fatos e saída original mantidos. Não é requisito para exportar. |
| 8 — Detalhes opcionais | Abrir **Detalhes técnicos → Uso da IA**. | Demo sem requests/tokens/modelos; cache local separado de cached input do provider. Histórico anterior não é uso da demo. |
| 9 — Referência | Abrir **Documentos desta comparação**, selecionar Allianz e comparar. | Resultado anterior invalidado; mesma fixture com referência invertida; citações e direções locais corretas. |
| 10 — Reset | Clicar **Nova comparação**. | Home limpa; sem apagar caches, claims, ledgers ou arquivos reais. |

O upload convencional aceita 2–5 documentos, primeiro como referência e preparação automática. **A comparação convencional de documentos novos pode chamar o provider configurado.** Nesta validação manual, use o botão demo para manter tudo offline. O QA automatizado de upload usa gateway fake com SDKs/HTTP bloqueados; não confundir esse wrapper de teste com a aplicação normal.

## Execução Porto/Allianz interrompida — não repetir com provider

O bug LIMITS foi corrigido e o caminho completo dos originais52/75p passou no Edge com pipeline/routing reais e provider fake, sem HTTP. A execução manual anterior consumiu24requests Porto (22respostas processadas+2schema_invalid), não concluiu Allianz nem a comparação. Os registros e caches parciais permanecem preservados; o guard bloqueia repetição automática antes do gateway. Para esta validação final, clicar **Nova comparação → Usar exemplo de demonstração → Comparar apólices**. Não apagar históricos ou retentar o par convencional para contornar o bloqueio. Veja docs/LIMITS_BUGFIX.md.

## Sessão real histórica aprovada — opcional, somente cache

Em **Configuração avançada**, **Carregar sessão validada** restaura Berkley/AXA/comparação já revisados, sem nova inferência. As fontes têm 83/104 páginas e Berkley é referência. Não é necessário preparar documentos ou marcar checkbox. Reutilizar **Comparar apólices** com documentos/referência idênticos retorna a sessão aprovada antes do gateway.

São **46 requests históricas**, 160966 input / 56018 cached como subconjunto / 20379 output / 181345 total; Terra30/Sol13/Luna2/mini1. Downloads/revisão/reruns não aumentam esses números. AXA usa projeção conservadora revisada: um campo original inseguro foi rebaixado para AMBIGUOUS sem editar fatos. A comparação tem 24/27 itens insuficientes e não permite ranking. Mantêm-se as limitações do FAST-TRACK e a reconciliação financeira pendente documentadas no estado atual; esta fase não altera billing.

Para registrar problemas: ação → esperado → observado → documento/campo/página → screenshot/mensagem sem credenciais. A clareza em aproximadamente 30 segundos deve ser avaliada pelo usuário; o QA automatizado confirma a jornada, não mede compreensão humana.

Próximo fluxo após seu relato: **USER FINAL VALIDATION → FIX → VERIFY → FREEZE → PACKAGE → DELIVER**. Parar aqui. Não regenerar os quatro artefatos acadêmicos finais nem publicar, enviar e-mail ou executar nova API.
