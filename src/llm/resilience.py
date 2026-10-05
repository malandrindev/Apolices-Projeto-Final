"""Bounded error recovery and metadata telemetry; no prompts/documents/secrets."""
from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging, re, time
from typing import Any
logger = logging.getLogger(__name__)

class LLMClientError(RuntimeError):
    def __init__(self, provider: str, kind: str, *, retry_after: float | None = None,
                 limit_reason: str | None = None):
        self.provider, self.kind, self.retry_after = provider, kind, retry_after
        self.limit_reason = limit_reason
        actions = {
            "authentication": "AutenticaÃ§Ã£o recusada. Confira a chave no .env local.",
            "permission": "PermissÃ£o recusada. Confira as permissÃµes de inferÃªncia do projeto.",
            "quota": "Quota esgotada. Confira Usage/limites do projeto; repetir agora nÃ£o resolve.",
            "credit": "Saldo/limite de cobranÃ§a atingido. Confira Usage/Costs do projeto.",
            "TPD": "Limite diÃ¡rio de tokens atingido. Aguarde a renovaÃ§Ã£o informada pelo provedor ou selecione outro provedor configurado.",
            "RPM": "Limite de chamadas por minuto atingido. Aguarde e repita; o cache preserva o trabalho.",
            "TPM": "Limite de tokens por minuto atingido. Aguarde e repita; o cache preserva o trabalho.",
            "rate_limit": "HTTP 429: limite temporÃ¡rio atingido. Aguarde e repita; o cache preserva o trabalho.",
            "timeout": "Tempo de resposta/conexÃ£o excedido. Repita para retomar o cache.",
            "context_length": "Trecho excede o contexto do modelo. Reduza o tamanho dos chunks.",
            "provider_error": "ServiÃ§o temporariamente indisponÃ­vel. Repita para retomar.",
            "response_incomplete": "Resposta incompleta. Revise LLM_MAX_TOKENS antes de repetir.",
            "budget_exhausted": "Request budget exhausted; no additional request was sent.",
            "circuit_open": "Provider circuit breaker is open; preserve partial results.",
            "routing_limit": "Routing limit reached; preserve uncertainty and partial results.",
            "persistence_error": "Request checkpoint could not be saved; further requests stopped.",
            "model_unavailable": "Requested model is unavailable.",
            "schema_invalid": "Response schema could not be validated or repaired locally.",
            "response_empty": "Resposta vazia ou recusada; nenhuma informaÃ§Ã£o foi aceita.",
        }
        message = actions.get(kind, "Falha de inferÃªncia. Confira modelo/configuraÃ§Ã£o e repita.")
        if retry_after is not None:
            message += f" Retry-After: {retry_after:.0f} segundos; a aplicaÃ§Ã£o encerrou a espera longa."
        super().__init__(f"{provider}: {message}")

def classify_error(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None) or {}
    detail = body.get("error", body) if isinstance(body, dict) else {}
    code = str(detail.get("code", "")).lower() if isinstance(detail, dict) else ""
    # Read only to classify; never log or include provider body in UI.
    description = str(detail.get("message", "")).lower() if isinstance(detail, dict) else ""
    if code in {"insufficient_quota", "quota_exceeded", "organization_usage_limit_exceeded"}: return "quota"
    if code in {"billing_hard_limit_reached", "billing_not_active", "insufficient_credits",
                "credit_balance_exhausted", "organization_spend_limit_exceeded",
                "project_spend_limit_exceeded"}: return "credit"
    if status == 401: return "authentication"
    if status == 403: return "permission"
    if status == 404 or code in {"model_not_found", "model_unavailable", "model_not_available"}:
        return "model_unavailable"
    if code in {"context_length_exceeded", "context_window_exceeded"} or "context length" in description: return "context_length"
    if status == 429:
        if "tokens per day" in description or "tpd" in description or "daily" in code: return "TPD"
        if "tokens per minute" in description or "tpm" in description: return "TPM"
        if "requests per minute" in description or "rpm" in description: return "RPM"
        return "rate_limit"
    if "timeout" in type(error).__name__.lower() or "connection" in type(error).__name__.lower(): return "timeout"
    if status and status >= 500: return "provider_error"
    return "invalid_request"

def retry_after_seconds(error: Exception) -> float | None:
    headers = getattr(getattr(error, "response", None), "headers", {}) or {}
    value = headers.get("retry-after")
    if value:
        try:return max(0.0,float(value))
        except (ValueError,TypeError):
            try:
                target=parsedate_to_datetime(value)
                if target.tzinfo is None:target=target.replace(tzinfo=timezone.utc)
                return max(0.0,(target-datetime.now(timezone.utc)).total_seconds())
            except (TypeError,ValueError,OverflowError):pass
    millis=headers.get("retry-after-ms")
    if millis:
        try:return max(0.0,float(millis)/1000)
        except (ValueError,TypeError):pass
    return None

def _metadata_value(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def reported_token_count(usage: Any, name: str) -> int | None:
    value = _metadata_value(usage, name)
    return value if type(value) is int and value >= 0 else None


def safe_actual_model(value: Any) -> str | None:
    if (isinstance(value, str) and len(value) <= 200
            and re.fullmatch(r"gpt-[A-Za-z0-9][A-Za-z0-9_.-]*", value)
            and "sk-" not in value.lower() and "gsk_" not in value.lower()):
        return value
    return None


def repair_json_locally(content: str) -> str | None:
    """Only remove wrapping fences/BOM and trailing commas outside strings.

    Never synthesize values, quotes, required fields or incomplete responses.
    The repaired document must still parse as finite JSON; callers validate schema.
    """
    if not isinstance(content, str) or len(content) > 256000:
        return None
    candidate = content.strip().lstrip("\ufeff")
    match = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n\s*```", candidate,
                         flags=re.IGNORECASE)
    if match:
        candidate = match.group(1).strip()
    quoted = escaped = False
    output = []
    for index, char in enumerate(candidate):
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        if char == ",":
            next_index = index + 1
            while next_index < len(candidate) and candidate[next_index].isspace():
                next_index += 1
            if next_index < len(candidate) and candidate[next_index] in "]}":
                continue
        output.append(char)
    repaired = "".join(output)
    try:
        import json
        json.loads(repaired, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError):
        return None
    return repaired if repaired != content.strip() else None


def cached_tokens_from_usage(usage: Any, details_name: str) -> int | None:
    """Read reported provider cache usage; missing or invalid is unknown."""
    details = _metadata_value(usage, details_name)
    count = _metadata_value(details, "cached_tokens")
    return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None


def response_metadata(response: Any) -> dict[str, str]:
    """Allow only bounded identifiers and known response service tiers."""
    metadata = {}
    for name, value in (
        ("request_id", _metadata_value(response, "_request_id") or _metadata_value(response, "request_id")),
        ("response_id", _metadata_value(response, "id")),
    ):
        prefix = r"req[_-]" if name == "request_id" else r"(?:resp_|chatcmpl-|cmpl-)"
        if isinstance(value, str) and len(value) <= 128 and re.fullmatch(prefix + r"[A-Za-z0-9_.-]+", value) and "sk-" not in value.lower() and "gsk_" not in value.lower():
            metadata[name] = value
    tier = _metadata_value(response, "service_tier")
    if isinstance(tier, str) and tier in {"auto", "default", "flex", "scale", "priority", "ultrafast"}:
        metadata["effective_service_tier"] = tier
    actual_model = safe_actual_model(_metadata_value(response, "model"))
    if actual_model is not None:
        metadata["actual_model"] = actual_model
    return metadata


def emit_event(owner: Any, model: str, stage: str, started: float, attempt: int, status: str, *,
               input_tokens: int | None = None, output_tokens: int | None = None,
               cached_tokens: int | None = None, total_tokens: int | None = None,
               kind: str = "", request_id: str | None = None, response_id: str | None = None,
               effective_service_tier: str | None = None, actual_model: str | None = None,
               local_json_repair: bool = False) -> None:
    event = {"provider": owner.provider, "model": model, "stage": stage,
             "duration_ms": int((time.perf_counter() - started) * 1000),
             "input_tokens": input_tokens, "output_tokens": output_tokens,
             "cached_tokens": cached_tokens, "total_tokens": total_tokens,
             "attempt": attempt, "cache_hit": False, "status": status, "error_kind": kind,
             "local_json_repair": local_json_repair}
    for name, value in (("request_id", request_id), ("response_id", response_id),
                        ("effective_service_tier", effective_service_tier),
                        ("actual_model", safe_actual_model(actual_model))):
        if value is not None:
            event[name] = value
    owner.events.append(event)
    logger.info("LLM metadata %s", event)
    if owner.event_callback:
        owner.event_callback(event)
