# Como colaborar

## Fluxo Git

1. Atualize a `main`.
2. Crie uma branch curta: `feature/<tema>-<pessoa>`, `fix/<tema>-<pessoa>` ou
   `docs/<tema>-<pessoa>`.
3. Faça commits pequenos usando `feat:`, `fix:`, `docs:`, `test:` ou `chore:`.
4. Abra Pull Request para `main`, descrevendo alteração, teste e pendências.
5. Prefira uma revisão de outro integrante antes do merge.

Todos os integrantes têm permissão de escrita. O uso de Pull Requests mantém a
`main` demonstrável sem impedir que cada pessoa crie branches e envie commits.

## Onde trabalhar

- Use `workspaces/<usuario>` apenas para rascunhos, notas e experimentos.
- Mova código reutilizável para `src/apolices_do`.
- Mova análises consolidadas para `notebooks/shared`.
- Registre decisões e conteúdo final em `docs`.

## Segurança e dados

- Nunca versione `.env`, chaves, tokens ou credenciais.
- Não versione apólices reais, dados pessoais ou documentos confidenciais.
- Use somente documentos sintéticos ou anonimizados em `data/samples`.
- Preserve a origem de cada trecho extraído para permitir auditoria.
- Revise tamanho e confidencialidade de PDFs, imagens e vídeos antes do push.
