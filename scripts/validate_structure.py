"""Valida a estrutura mínima do repositório sem acessar dados sensíveis."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "src/apolices_do",
    "data/raw",
    "data/processed",
    "data/samples",
    "docs/requisitos.md",
    "docs/arquitetura.md",
    "notebooks/shared",
    "outputs/entrega",
    "workspaces/vitor",
    "workspaces/leo-vilelela",
    "workspaces/wabassis",
    "Projeto_Final_Artefatos",
)


def main() -> int:
    missing = [relative for relative in REQUIRED if not (ROOT / relative).exists()]
    if missing:
        print("Estrutura incompleta:")
        for relative in missing:
            print(f"- {relative}")
        return 1

    print(f"Estrutura válida: {len(REQUIRED)} itens obrigatórios encontrados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
