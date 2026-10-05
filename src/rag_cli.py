"""CLI para perguntas locais com retrieval vetorial e respostas Groq citadas."""

from __future__ import annotations

import argparse
import sys

from src.agents.rag import RagAgent, RagAgentError
from src.config import ConfigurationError, get_settings, get_storage_settings
from src.llm.providers import get_gateway
from src.storage.document_store import DocumentStore
from src.storage.sqlite_repo import RepositoryError
from src.storage.vector_store import VectorStoreError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consulta trechos indexados; resposta baseada só nos resultados recuperados."
    )
    parser.add_argument("question", nargs="+", help="pergunta em linguagem natural")
    parser.add_argument(
        "--document-id",
        help="SHA-256 para limitar a busca a uma apólice específica",
    )
    parser.add_argument("--top-k", type=int, help="quantidade máxima de trechos a recuperar")
    arguments = parser.parse_args()

    try:
        llm_settings = get_settings()
        storage_settings = get_storage_settings()
        store = DocumentStore(storage_settings)
        response = RagAgent(
            gateway=get_gateway(llm_settings),
            model_strong=llm_settings.model_strong,
            vector_store=store.vector_store,
        ).answer(
            " ".join(arguments.question),
            document_id=arguments.document_id,
            top_k=arguments.top_k,
        )
    except (
        ConfigurationError,
        RagAgentError,
        RepositoryError,
        VectorStoreError,
        ValueError,
    ) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1

    print(response.answer)
    for citation in response.citations:
        print(
            f"Fonte: {citation.source_name}, página {citation.page_number} "
            f"(seção: {citation.title})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
