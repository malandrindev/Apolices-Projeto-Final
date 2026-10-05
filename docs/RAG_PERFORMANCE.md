# RAG: desempenho local e cache de embeddings

Medição realizada em **03/10/2026**, às **19h53–19h55 (America/Sao_Paulo)**, no Windows deste workspace, Python **3.11.0**, CPU com **12 processadores lógicos**, `sentence-transformers==6.1.0`, `chromadb==1.5.9`, `torch==2.8.0` e `pydantic==2.13.5`. O RAG existente foi preservado. O ensaio usou quatro textos técnicos sintéticos de diagnóstico, sem apólices de clientes e sem chamadas a LLMs.

Modelo configurado: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, executado em CPU, vetores observados de **384 dimensões**. A revisão efetivamente baixada foi `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`; sua origem pública está no [repositório do modelo no Hugging Face](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/tree/e8f8c211226b894fcb81acc59f3b34ba3efd5f42).

## Método e evidência

O script [scripts/benchmark_rag.py](../scripts/benchmark_rag.py) mede imports em processos Python novos e executa o download/carregamento, encode, indexação e busca em outro processo. Um supervisor externo limita cada fase a **60 segundos** e encerra o processo em caso de timeout; o resultado parcial permanece registrado. Não foi necessário aplicar timeout nas duas execuções reais: todas as fases concluíram.

A primeira execução usou um `HF_HOME` local inicialmente vazio. A segunda reutilizou os arquivos do modelo, iniciou processos novos e criou uma coleção Chroma nova. Portanto, a segunda execução mede carregamento do modelo já baixado e cache de arquivos do sistema operacional; o modelo não permaneceu carregado entre execuções.

Os relatórios reais estão em:

- `data/processed/rag_benchmark/run_20261003T225302/rag_benchmark_report.json`: primeira execução, cache de modelo inicialmente vazio.
- `data/processed/rag_benchmark/run_20261003T225512/rag_benchmark_report.json`: repetição, com CPU e memória RSS medidas.

Os arquivos ficam ignorados pelo Git. Os horários nos JSON são UTC. A primeira versão do script não encontrou `psutil` e registrou RSS como `null`; a segunda usa a API nativa do Windows como alternativa, sem acrescentar dependência Python.

## Resultados observados

| Fase | 1ª execução: tempo de parede (s) | Modelo já baixado: tempo de parede (s) | CPU na 2ª execução (s) | RSS após fase na 2ª execução (MiB) |
|---|---:|---:|---:|---:|
| Import do adaptador `src.storage.vector_store` | 0,1513 | 0,1451 | 0,1406 | 31,914 |
| Import isolado ChromaDB | 1,3036 | 0,9777 | 0,7969 | 68,551 |
| Import isolado Sentence Transformers | 11,6422 | 6,5266 | 5,7969 | 401,113 |
| Download e carregamento / carregamento de arquivos já baixados | 25,5578 | 5,1575 | 2,0625 | 1159,648 |
| Encode de quatro chunks, primeira chamada | 1,3458 | 0,1508 | 0,5781 | 1167,047 |
| Encode repetido de quatro chunks, sem cache de vetores | 0,0327 | 0,0280 | 0,0938 | 1167,508 |
| Abertura do cliente Chroma local | 2,1686 | 0,3494 | 0,1562 | 1181,980 |
| Primeira indexação dos quatro chunks | 0,1337 | 0,2454 | 1,1250 | 1192,078 |
| Reindexação idêntica com cache de vetores | 0,0421 | 0,0419 | 0,0000 | 1192,105 |
| Primeira busca, filtrada por documento | 0,0206 | 0,0340 | 0,1719 | 1192,391 |
| Busca repetida com cache do vetor da pergunta | 0,0103 | 0,0120 | 0,0781 | 1192,391 |
| Novo encoder: reindexação com cache persistente | 0,0152 | 0,0197 | 0,0781 | 1192,426 |

A fase de download/carregamento mede conjuntamente a obtenção dos arquivos e a criação do modelo; esses custos não foram separados. O import do Sentence Transformers no processo do modelo levou 6,0937 s / 6,5159 s, adicionalmente ao carregamento indicado na tabela. A CPU registra tempo agregado do processo e pode superar o tempo de parede quando há várias threads. RSS é uma leitura após cada fase, não o pico absoluto de memória do processo.

O cache Hugging Face passou de **0 para 479.729.245 bytes (aproximadamente 457,5 MiB)** na primeira execução e não cresceu na segunda. O cache de cinco vetores (quatro chunks e uma pergunta) ocupou **52.949 bytes** na segunda execução. Chroma retornou três trechos com o filtro de documento preservado. Um encoder novo reindexou os chunks sem carregar seu modelo (`model_loaded=false`).

As duas execuções são observações deste computador e desta carga pequena. Não estabelecem latência garantida, qualidade de recuperação em apólices reais, escalabilidade ou duração em outras redes/máquinas. Não houve medição de espera de 30 minutos nem extrapolação para esse valor.

## Trabalho repetido e correção aplicada

Antes da alteração, um ensaio com `CountingEncoder` e os adaptadores FakeEncoder/FakeChroma originais executou duas indexações idênticas de quatro chunks: **duas chamadas ao encoder, oito textos codificados e quatro IDs armazenados**. Os tempos de 0,032 ms e 0,018 ms eram do simulador e só demonstravam a recomputação; não representam custo do modelo real.

O cache atual usa **ID do modelo + SHA-256 do texto + versão do cache + opção de normalização** como identidade. `EmbeddingCacheEntry`, validado por Pydantic, exige vetor não vazio, valores finitos e correspondência entre dimensão declarada e comprimento do vetor. Cada JSON é gravado atomicamente por `JsonCache`, ao lado da pasta Chroma em `embedding_cache/`. A entrada guarda modelo, hash, dimensão e vetor; não guarda o texto original. Cache inválido é recalculado. Falha na gravação do cache não impede a indexação principal.

Textos iguais no mesmo lote são codificados uma vez e continuam recebendo seus IDs próprios. Mudanças de texto ou modelo criam outras entradas. IDs, páginas, filtros, cosseno e remoção de chunks antigos depois do upsert bem-sucedido seguem o contrato original. A busca usa o mesmo cache para a pergunta normalizada; pode-se desativá-lo com `ChromaVectorStore(..., cache_queries=False)`.

Após a alteração, duas indexações e duas consultas idênticas produziram **duas chamadas ao encoder e cinco textos codificados** no simulador: um lote de quatro chunks e uma pergunta. As operações repetidas reutilizaram seus vetores. A reindexação em outra instância também reutilizou os JSON, sem inicializar o modelo quando todos os vetores já existiam.

Validação: **18 testes passaram**, sendo nove testes originais de armazenamento/RAG e nove novos em [tests/test_vector_cache.py](../tests/test_vector_cache.py). Cobrem persistência entre instâncias, mudança de texto/modelo, NaN/inf/dimensões/hash inválidos, textos duplicados, cache de pergunta opcional e preservação dos IDs antigos quando o upsert falha. Os contratos FakeEncoder/FakeChroma originais foram mantidos. A revisão final também reproduziu uma dimensão incompatível com Chroma real: seus erros específicos agora são convertidos em `VectorStoreError` sem exibir mensagens do backend e sem importar Chroma antecipadamente. Erros de programação fora dessa hierarquia continuam sendo propagados.

## Consequência para o MVP

O RAG permanece disponível como função opcional. As medições indicam preparação inicial relevante (download, import e aproximadamente 1,2 GiB de RSS observada com o modelo/índice carregados), enquanto as operações pequenas e já preparadas concluíram em frações de segundo. A comparação principal de duas apólices não deve depender da indexação vetorial. Não foi adicionada arquitetura global para compartilhar modelos entre instâncias: a cache persistente já evita carregamento nas reindexações idênticas; perguntas novas ainda exigem o modelo.

Não há invalidação automática por revisão dos pesos quando o mesmo ID de modelo passa a apontar para outra versão. Se alterar os pesos mantendo esse ID, limpe apenas o cache de embeddings correspondente e recrie a coleção compatível antes de reutilizar resultados. O cache não tem política automática de expiração; é local, descartável e ignorado pelo Git. A fonte estruturada continua sendo SQLite.

## Reprodução

Na raiz do projeto, com `.venv` e dependências originais instaladas:

```powershell
.venv/Scripts/python.exe scripts/benchmark_rag.py --timeout 60
.venv/Scripts/python.exe -m unittest tests.test_storage_rag tests.test_vector_cache -v
```

`--skip-model` mede imports e o comportamento simulado do cache sem baixar/carregar pesos. `--model` permite informar outro ID explicitamente, sem modificar a configuração do projeto. O padrão usa `EMBEDDING_MODEL` configurado.

O benchmark isola seus downloads em `data/processed/rag_benchmark/huggingface_cache`. Para reutilizar esses arquivos na execução local da interface, defina `HF_HOME` nesse caminho no processo que inicia o aplicativo; o script não altera `.env` nem variáveis persistentes do Windows:

```powershell
$env:HF_HOME = Join-Path (Get-Location) 'data/processed/rag_benchmark/huggingface_cache'
.venv/Scripts/python.exe -m streamlit run interface/app.py
```
