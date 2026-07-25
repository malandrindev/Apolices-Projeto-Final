# Plataforma Inteligente para Análise e Comparação de Apólices D&O

Projeto Final do programa InsurMinds. A plataforma receberá apólices em PDF ou
imagem, extrairá e estruturará informações relevantes e comparará pelo menos
duas apólices com apoio de IA generativa.

## Objetivo do MVP

- receber documentos por uma interface demonstrável;
- extrair conteúdo automaticamente, com OCR quando necessário;
- identificar e estruturar cláusulas e dados relevantes;
- armazenar os dados de forma consultável;
- comparar ao menos duas apólices;
- destacar diferenças de forma clara e rastreável;
- usar ao menos um modelo de IA generativa.

## Estado atual

Estrutura inicial preparada para trabalho colaborativo. As decisões de
framework, provedor de IA, OCR e armazenamento serão registradas em
`docs/decisoes.md` antes da implementação.

## Início rápido

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/validate_structure.py
```

Ainda não há uma aplicação executável: este commit estabelece a base do
projeto e a divisão inicial do trabalho.

## Organização

```text
src/apolices_do/             Código compartilhado da solução
data/                        Entradas locais, amostras anônimas e derivados
docs/                        Requisitos, arquitetura e documentação da entrega
notebooks/shared/            Explorações consolidadas da equipe
outputs/                     Comparações e evidências geradas
workspaces/                  Rascunhos individuais, sem código definitivo
Projeto_Final_Artefatos/     Apresentação, vídeo e artefatos obrigatórios
```

O conteúdo aprovado deve migrar dos `workspaces` para `src`, `docs` ou
`notebooks/shared`.

## Entrega

- repositório público;
- relatório técnico em PDF;
- ZIP com código e artefatos;
- `Projeto_Final_Artefatos/InsurMinds_Projeto_Final.pptx`;
- `Projeto_Final_Artefatos/InsurMinds_Projeto_Final.mp4`, com até 5 minutos;
- prazo: **06/10/2026 às 23h59**.

## Equipe

- Vitor Ferreira (`malandrindev`)
- José Leonardo Alves Vilela (`leo-vilelela`)
- Wagner Assis (`wabassis`)

Consulte `CONTRIBUTING.md` para o fluxo de branches e `TASKS.md` para a divisão
inicial. Este projeto é distribuído sob a licença MIT.
