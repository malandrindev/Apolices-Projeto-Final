# Arquitetura inicial

Fluxo proposto, sujeito a validação por spike técnico:

```text
Interface
  -> Ingestão e validação do documento
  -> Extração de texto / OCR
  -> Identificação e normalização de cláusulas com IA
  -> Persistência estruturada com referências à origem
  -> Motor de comparação
  -> Tabela, resumo e relatório comparativo
```

## Módulos

- `ingestion`: upload, tipo, tamanho, integridade e identificação do arquivo;
- `extraction`: texto nativo, OCR e metadados de origem;
- `structuring`: esquema, cláusulas, coberturas, limites e exclusões;
- `comparison`: diferenças, equivalências e pontos ausentes;
- `agents`: interpretação e orquestração apoiadas por IA generativa;
- `ui`: experiência demonstrável e apresentação dos resultados.

Cada resposta deverá preservar evidência suficiente para indicar de qual
documento e trecho veio a informação. O modelo de IA não será tratado como
fonte definitiva quando o texto original não sustentar a conclusão.
