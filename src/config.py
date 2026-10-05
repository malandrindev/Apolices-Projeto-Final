"""Configuração central da aplicação; segredos são obtidos apenas do ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    """Erro de configuração sem expor valores de variáveis de ambiente."""


@dataclass(frozen=True, slots=True)
class Settings:
    groq_api_key: str = field(repr=False)
    model_fast: str
    model_strong: str
    model_vision: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    max_retries: int
    llm_provider: str = "groq"
    openai_api_key: str = field(default="", repr=False)
    model_intermediate: str = ""
    max_retry_wait_seconds: float = 30
    openai_reasoning_effort: str = "low"


@dataclass(frozen=True, slots=True)
class IngestionSettings:
    max_document_mb: int
    ocr_languages: str
    min_native_chars: int
    ocr_timeout_seconds: float
    render_dpi: int
    processed_dir: Path


@dataclass(frozen=True, slots=True)
class StorageSettings:
    sqlite_path: Path
    chroma_path: Path
    chroma_collection: str
    embedding_model: str
    embedding_device: str
    chunk_size_chars: int
    chunk_overlap_chars: int
    rag_top_k: int


def get_settings(*, require_api_key: bool = True, provider: str | None = None) -> Settings:
    """Carrega as configurações do .env e do ambiente, sem sobrescrever o ambiente."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)

    provider = (provider or os.getenv("LLM_PROVIDER", "groq")).strip().lower()
    if provider not in {"groq", "openai"}:
        raise ConfigurationError("LLM_PROVIDER deve ser groq ou openai.")
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    key_name = "OPENAI_API_KEY" if provider == "openai" else "GROQ_API_KEY"
    if require_api_key and not (openai_key if provider == "openai" else api_key):
        raise ConfigurationError(f"Defina {key_name} no arquivo .env.")

    def required(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise ConfigurationError(
                f"Defina {name} no arquivo .env (copie .env.example para .env)."
            )
        return value

    try:
        temperature = float(os.getenv("LLM_TEMPERATURE", os.getenv("GROQ_TEMPERATURE", "0")))
        max_tokens = int(os.getenv("LLM_MAX_TOKENS", "4096" if provider == "openai" else os.getenv("GROQ_MAX_TOKENS", "1024")))
        timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", os.getenv("GROQ_TIMEOUT_SECONDS", "30")))
        max_retries = int(os.getenv("LLM_MAX_RETRIES", os.getenv("GROQ_MAX_RETRIES", "3")))
        max_wait = float(os.getenv("LLM_MAX_RETRY_WAIT_SECONDS", "30"))
    except ValueError as error:
        raise ConfigurationError(
            "GROQ_TEMPERATURE, GROQ_MAX_TOKENS, GROQ_TIMEOUT_SECONDS e "
            "GROQ_MAX_RETRIES devem conter valores numéricos válidos."
        ) from error

    if not 0 <= temperature <= 2:
        raise ConfigurationError("GROQ_TEMPERATURE deve estar entre 0 e 2.")
    if max_tokens < 1 or timeout_seconds <= 0 or max_wait < 0 or not 1 <= max_retries <= 3:
        raise ConfigurationError(
            "GROQ_MAX_TOKENS e GROQ_TIMEOUT_SECONDS devem ser positivos; "
            "GROQ_MAX_RETRIES deve estar entre 1 e 3."
        )

    reasoning = os.getenv("OPENAI_REASONING_EFFORT", "low")
    if reasoning not in {"none", "low", "medium", "high"}:
        raise ConfigurationError("OPENAI_REASONING_EFFORT inválido.")
    return Settings(
        groq_api_key=api_key,
        model_fast=os.getenv("OPENAI_MODEL_FAST", "gpt-5.6-luna") if provider == "openai" else required("GROQ_MODEL_FAST"),
        model_strong=os.getenv("OPENAI_MODEL_STRONG", "gpt-5.6-sol") if provider == "openai" else required("GROQ_MODEL_STRONG"),
        model_vision=os.getenv("OPENAI_MODEL_FAST", "gpt-5.6-luna") if provider == "openai" else required("GROQ_MODEL_VISION"),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        llm_provider=provider,
        openai_api_key=openai_key,
        model_intermediate=os.getenv("OPENAI_MODEL_INTERMEDIATE", "gpt-5.6-terra") if provider == "openai" else required("GROQ_MODEL_STRONG"),
        max_retry_wait_seconds=max_wait,
        openai_reasoning_effort=reasoning,
    )


def get_ingestion_settings() -> IngestionSettings:
    """Carrega parâmetros locais de ingestão sem exigir configuração/chave Groq."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
    try:
        settings = IngestionSettings(
            max_document_mb=int(os.getenv("MAX_DOCUMENT_MB", "30")),
            ocr_languages=os.getenv("OCR_LANGUAGES", "por+eng").strip(),
            min_native_chars=int(os.getenv("OCR_MIN_NATIVE_CHARS", "20")),
            ocr_timeout_seconds=float(os.getenv("OCR_TIMEOUT_SECONDS", "60")),
            render_dpi=int(os.getenv("OCR_RENDER_DPI", "200")),
            processed_dir=PROJECT_ROOT / "data" / "processed",
        )
    except ValueError as error:
        raise ConfigurationError(
            "MAX_DOCUMENT_MB, OCR_MIN_NATIVE_CHARS, OCR_TIMEOUT_SECONDS e "
            "OCR_RENDER_DPI devem conter valores numéricos válidos."
        ) from error

    if (
        settings.max_document_mb < 1
        or settings.min_native_chars < 1
        or settings.ocr_timeout_seconds <= 0
        or settings.render_dpi < 72
        or not settings.ocr_languages
    ):
        raise ConfigurationError("As configurações de ingestão/OCR devem ser positivas.")
    return settings


def get_storage_settings() -> StorageSettings:
    """Carrega opções de persistência local sem chave ou conexão externa."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
    try:
        settings = StorageSettings(
            sqlite_path=PROJECT_ROOT / "data" / "processed" / "policies.sqlite3",
            chroma_path=PROJECT_ROOT / "data" / "processed" / "chroma",
            chroma_collection=os.getenv(
                "CHROMA_COLLECTION_NAME", "do_policy_chunks_v1"
            ).strip(),
            embedding_model=os.getenv(
                "EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ).strip(),
            embedding_device=os.getenv("EMBEDDING_DEVICE", "cpu").strip(),
            chunk_size_chars=int(os.getenv("VECTOR_CHUNK_SIZE_CHARS", "1200")),
            chunk_overlap_chars=int(os.getenv("VECTOR_CHUNK_OVERLAP_CHARS", "150")),
            rag_top_k=int(os.getenv("RAG_TOP_K", "5")),
        )
    except ValueError as error:
        raise ConfigurationError(
            "VECTOR_CHUNK_SIZE_CHARS, VECTOR_CHUNK_OVERLAP_CHARS e RAG_TOP_K "
            "devem conter valores inteiros válidos."
        ) from error
    if (
        not settings.chroma_collection
        or not settings.embedding_model
        or settings.embedding_device not in {"cpu", "cuda", "mps"}
        or settings.chunk_size_chars < 200
        or settings.chunk_overlap_chars < 0
        or settings.chunk_overlap_chars >= settings.chunk_size_chars
        or settings.rag_top_k < 1
    ):
        raise ConfigurationError("Configurações locais de armazenamento/RAG inválidas.")
    return settings


@dataclass(frozen=True, slots=True)
class ExtractionOptimizationSettings:
    """Local retrieval/batching controls; strategy never authorizes an API call."""
    strategy: str = "auto"
    batch_chars: int = 20000
    initial_top_n: int = 2
    expanded_top_n: int = 4
    confidence_threshold: float = 0.75
    auto_min_pages: int = 8
    auto_min_chunks: int = 20

    def __post_init__(self) -> None:
        if (self.strategy not in {"auto", "optimized", "legacy"}
                or not 4000 <= self.batch_chars <= 64000
                or not 1 <= self.initial_top_n <= 20
                or not self.initial_top_n <= self.expanded_top_n <= 100
                or not 0 < self.confidence_threshold <= 1
                or self.auto_min_pages < 1 or self.auto_min_chunks < 1):
            raise ConfigurationError("Invalid local extraction/retrieval settings.")


@dataclass(frozen=True, slots=True)
class ExtractionRouting:
    """Logical roles retain current models unless the environment overrides them."""
    simple_model: str
    interpretation_model: str
    verification_model: str
    comparison_model: str = ""

    def __post_init__(self) -> None:
        import re
        values = (self.simple_model, self.interpretation_model, self.verification_model)
        if self.comparison_model:
            values += (self.comparison_model,)
        if any(not isinstance(value, str) or not value
               or len(value) > 200 or not re.fullmatch(r"[A-Za-z0-9./:_-]+", value)
               or value.startswith(("sk-", "gsk_")) for value in values):
            raise ConfigurationError("Invalid configured extraction model identifier.")


def get_extraction_optimization_settings() -> ExtractionOptimizationSettings:
    """Read offline planner controls without credentials or model calls."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
    try:
        return ExtractionOptimizationSettings(
            strategy=os.getenv("PUBLIC_EXTRACTION_STRATEGY", "auto").strip().lower(),
            batch_chars=int(os.getenv("PUBLIC_RETRIEVAL_BATCH_CHARS", "20000")),
            initial_top_n=int(os.getenv("PUBLIC_RETRIEVAL_INITIAL_TOP_N", "2")),
            expanded_top_n=int(os.getenv("PUBLIC_RETRIEVAL_EXPANDED_TOP_N", "4")),
            confidence_threshold=float(os.getenv("PUBLIC_EXTRACTION_CONFIDENCE_THRESHOLD", "0.75")),
            auto_min_pages=int(os.getenv("PUBLIC_EXTRACTION_AUTO_MIN_PAGES", "8")),
            auto_min_chunks=int(os.getenv("PUBLIC_EXTRACTION_AUTO_MIN_CHUNKS", "20")),
        )
    except (ValueError, TypeError) as error:
        raise ConfigurationError("Invalid extraction/retrieval environment configuration.") from error


def get_extraction_routing(settings: Settings) -> ExtractionRouting:
    """No principal-family migration: defaults follow the existing provider settings."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
    def role(name: str, fallback: str) -> str:
        return os.getenv(name, "").strip() or fallback
    return ExtractionRouting(
        simple_model=role("EXTRACTION_MODEL_SIMPLE", settings.model_fast),
        interpretation_model=role("EXTRACTION_MODEL_INTERPRETATION", settings.model_strong),
        verification_model=role("EXTRACTION_MODEL_VERIFICATION", settings.model_intermediate or settings.model_strong),
        comparison_model=role("EXTRACTION_MODEL_COMPARISON", settings.model_strong),
    )
