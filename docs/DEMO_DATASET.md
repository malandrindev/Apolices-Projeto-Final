# DEMO_DATASET — Porto × Allianz

Dataset local `demo-do-v1`, criado exclusivamente para demonstração offline. **DOCUMENTO DE DEMONSTRAÇÃO / DADOS FICTÍCIOS / SEM VALIDADE CONTRATUAL.** Não foi feita inferência nem captura pública nesta fase.

## Fontes reais e recorte

Os dois arquivos foram fornecidos pelo usuário em `data/demo_sources/`. São condições gerais/contratuais, não apólices individuais emitidas. Não há URL de publicação verificada para esses arquivos; sua origem é o upload local. Não inventar URL, data de captura pública ou contratação.

| Fonte | Versão identificada | Processo SUSEP | Original | Demo | Composição |
|---|---|---|---:|---:|---|
| Porto Seguro Cia de Seguros Gerais | Fevereiro/2022; vigente a partir de 01/02/2022 | 15414.901349/2019-94 | 52 páginas | 30 páginas | 2 sintéticas + 28 páginas originais |
| Allianz Seguros S.A. | Dezembro/2025 | 15414.901113/2017-96 | 75 páginas | 33 páginas | 2 sintéticas + 31 páginas originais |

Porto possui escopo de capital fechado. A especificação fictícia comum descreve ALPHA TECNOLOGIA S.A., capital fechado. A distância entre as versões 2022/2025 impede apresentar o par como validação comercial recente homogênea; o objetivo é demonstrar navegação, valores objetivos e diferenças dos excertos. Os recortes não representam a integralidade dos produtos.

Cada demo conserva integralmente o texto das páginas jurídicas selecionadas. Não foram reescritas cláusulas, acrescentadas coberturas ou alterados PDFs originais. Os mapas `page_map_porto.json` e `page_map_allianz.json` relacionam todas as páginas demo com sua origem, página original e hash. As primeiras duas páginas são sintéticas e têm os três avisos obrigatórios.

## Especificação fictícia

| Campo | Porto | Allianz |
|---|---|---|
| Tomador | ALPHA TECNOLOGIA S.A. | ALPHA TECNOLOGIA S.A. |
| Número | DEMO-D&O-PORTO-2026-001 | DEMO-D&O-ALLIANZ-2026-002 |
| Vigência | 01/01/2026–31/12/2026 | 01/01/2026–31/12/2026 |
| Moeda | BRL | BRL |
| LMG | R$ 20.000.000 | R$ 30.000.000 |
| Prêmio | R$ 120.000 | R$ 135.000 |
| Retenção Side B | R$ 100.000 | R$ 250.000 |
| Data retroativa | 01/01/2020 | 01/01/2018 |

São sintéticos também o nome da seguradora na especificação, a não seleção de Side C e a seleção territorial Brasil da Allianz. Esta última não significa que o produto Allianz tenha alcance exclusivamente nacional. A territorialidade Porto vem do wording. Dados monetários e datas da especificação não foram extraídos como contratação real.

## Ground truth e comparação

`ground_truth_porto.json` e `ground_truth_allianz.json` cobrem os **27 campos** do schema existente, incluindo os dez campos mínimos de identificação/datas/moeda/valores. Cada campo registra valor esperado, estado, tipo de fonte, documento/página demo, documento/página original quando aplicável, trecho literal, fundamento e evidências complementares.

Porto tem 11 campos de especificação sintética, 15 de wording real e 1 não recuperado. Allianz tem 12 sintéticos, 14 de wording real e 1 não recuperado. Campos jurídicos abrangem Side A/B, defesa, extensões, base de cobertura, prazo adicional, exclusões, aviso, controle de defesa, acordo, rateio, jurisdição, territorialidade, cancelamento/renovação e definições. Estados AMBIGUOUS conservam a informação e bloqueiam conclusões.

A comparação pré-processada `ground_truth_comparison.json` mantém todos os campos e duas evidências. O resumo exclui seis campos de identificação das contagens de negócio: **14 diferenças, 3 equivalências e 4 sem conclusão segura**. Pontos de atenção são essas mesmas 14 diferenças para conferência, não uma classificação de gravidade. Só LMG, prêmio e retenção recebem direção favorável local, dentro da especificação fictícia comum. Não há ranking global.

Limitações explícitas:

- Prazo adicional Porto: prazo-base não definido pela especificação; contratação opcional depende de aceitação/pagamento e condições.
- Exclusão de conduta Allianz: diferenças de redação/irrecorribilidade exigem confirmação; não afirmar vantagem.
- Sublimites e definições: informação insuficiente/escopo parcial.
- Defesa, antecipação Side A/B, consentimento, rateio e demais condições: diferenças textuais não equivalem a superioridade jurídica; preservar ressalvas e conferir evidências complementares.
- Confiança 1,0 para especificações e 0,9 para trechos é uma indicação da fixture humana, sem calibração probabilística.

## Integração e verificação

`src/demo.py` valida hashes do manifesto, páginas, avisos, origem real/sintética, igualdade textual das 59 páginas reais e literalidade de todas as citações antes de carregar. Recusa assets/ground truth/mapas alterados. A demo retorna antes da construção do gateway e usa apenas fixtures, texto nativo e relatórios locais. Trocar referência inverte valores/citações e direções locais da mesma fixture.

`scripts/build_demo_dataset.py` documenta a construção local reproduzível. Os PDFs já criados foram preservados nesta retomada; não é necessário executar o builder para usar a demo. `tests/test_demo_dataset.py` bloqueia explicitamente gateway, SDKs e HTTP e falha se houver tentativa de provider.

O botão **Usar exemplo de demonstração** carrega Porto como referência e Allianz como candidata. **Comparar apólices** apresenta o resultado identificado como pré-processado, permite evidência/página, revisão opcional e exportação operacional. Não simula IA ao vivo.

Os PDFs demo são materiais de demonstração, distintos do PDF técnico, PPTX, MP4 e ZIP acadêmicos congelados.
