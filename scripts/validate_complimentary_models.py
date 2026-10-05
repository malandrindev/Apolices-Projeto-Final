"""One-shot E0 billing control: five fixed neutral requests, never retried.

Default CLI only prints the plan. --execute is reserved for the explicitly
authorized E0 run. An existing manifest or claim permanently blocks reruns.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
import re
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.llm.resilience import response_metadata

MODELS = (
    "gpt-5.4-mini-2026-03-17",
    "gpt-4.1-mini-2025-04-14",
    "gpt-5.4-2026-03-05",
    "gpt-5.2-2025-12-11",
    "gpt-5.6-luna",
)
RETURNED_MODEL_PATTERN = re.compile(r"(?:gpt-5\.4-mini|gpt-4\.1-mini|gpt-5\.4|gpt-5\.2|gpt-5\.6-luna)(?:-\d{4}-\d{2}-\d{2})?", re.ASCII)
INPUT = "Responda somente com OK."
MAX_OUTPUT_TOKENS = 64
MANIFEST = ROOT / "data" / "processed" / "complimentary_model_test.json"
PENDING_COST = "PENDING_DASHBOARD_RECONCILIATION"
API_STATUSES = {"completed", "failed", "incomplete", "in_progress", "queued", "cancelled"}


class AlreadyAttempted(RuntimeError):
    """No repeat authorization is implied by a failure or interrupted process."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def request_parameters(model: str) -> dict[str, Any]:
    if model not in MODELS:
        raise ValueError("Model outside the fixed E0 allowlist.")
    params = {"model": model, "input": INPUT, "store": False,
              "max_output_tokens": MAX_OUTPUT_TOKENS}
    if model != "gpt-4.1-mini-2025-04-14":
        params["reasoning"] = {"effort": "none"}
    return params


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Flush a whole JSON snapshot before replacing the existing snapshot."""
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _claim(path: Path) -> None:
    """Exclusive persistent claim closes the concurrent-run creation race."""
    if path.exists():
        raise AlreadyAttempted("E0 already reserved; inspect the existing manifest. No rerun.")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise AlreadyAttempted("E0 already claimed; automatic repeat is blocked.") from None
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("E0 reserved; do not remove to retry automatically.\n")
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        raise AlreadyAttempted("E0 manifest appeared during reservation; no rerun.")


def _value(owner: Any, name: str) -> Any:
    return owner.get(name) if isinstance(owner, dict) else getattr(owner, name, None)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _error(error: Exception) -> dict[str, Any]:
    """Allowlisted classification only; never str(error), body or raw headers."""
    status = _count(getattr(error, "status_code", None))
    status = status if status is not None and 100 <= status <= 599 else None
    kind = {401: "authentication", 403: "permission", 404: "model_unavailable",
            429: "rate_limit"}.get(status, "request_failed")
    if status is not None and status >= 500:
        kind = "provider_error"
    elif "timeout" in type(error).__name__.lower():
        kind = "timeout"
    elif "connection" in type(error).__name__.lower():
        kind = "connection"
    return {"kind": kind, "http_status": status}


def _empty_record(model: str) -> dict[str, Any]:
    return {
        "timestamp": None, "response_id": None, "safe_request_id": None,
        "model_requested": model, "model_returned": None, "status": "NOT_ATTEMPTED",
        "input_tokens": None, "cached_input_tokens": None, "output_tokens": None,
        "total_tokens": None, "service_tier": None, "duration": None, "error": None,
        "attempted": False, "ok_received": None, "dashboard_cost": PENDING_COST,
    }


def _response_record(response: Any) -> dict[str, Any]:
    metadata = response_metadata(response)
    usage = _value(response, "usage")
    input_tokens = _count(_value(usage, "input_tokens"))
    output_tokens = _count(_value(usage, "output_tokens"))
    total = _count(_value(usage, "total_tokens"))
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    details = _value(usage, "input_tokens_details")
    status = _value(response, "status")
    model = _value(response, "model")
    content = _value(response, "output_text")
    ok = isinstance(content, str) and content.strip() == "OK"
    return {
        "response_id": metadata.get("response_id"),
        "safe_request_id": metadata.get("request_id"),
        "model_returned": model if isinstance(model, str) and len(model) <= 96 and RETURNED_MODEL_PATTERN.fullmatch(model) else None,
        "status": status if isinstance(status, str) and status in API_STATUSES else "UNKNOWN_RESPONSE_STATUS",
        "input_tokens": input_tokens, "cached_input_tokens": _count(_value(details, "cached_tokens")),
        "output_tokens": output_tokens, "total_tokens": total,
        "service_tier": metadata.get("effective_service_tier"),
        "ok_received": ok,
        "error": None if status == "completed" and ok else {"kind": "response_not_ok"},
    }


def run_validation(*, client_factory: Callable[[], Any], manifest_path: Path = MANIFEST,
                   clock: Callable[[], str] = utc_now,
                   timer: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    """Internal dependency injection is for offline tests; CLI path is fixed."""
    path = Path(manifest_path)
    _claim(path)
    manifest = {
        "schema_version": 1, "experiment": "E0_COMPLIMENTARY_MODEL_CONTROLLED_VALIDATION",
        "created_at_utc": clock(), "finished_at_utc": None, "run_status": "running",
        "request_limit": len(MODELS), "attempts_reserved": 0, "sdk_max_retries": 0,
        "endpoint": "/v1/responses", "input": INPUT, "store": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "dashboard_reconciliation": PENDING_COST,
        "requests": [_empty_record(model) for model in MODELS],
    }
    _atomic_write(path, manifest)
    try:
        client = client_factory()
    except Exception as error:
        manifest.update(run_status="initialization_failed", initialization_error=_error(error),
                        finished_at_utc=clock())
        _atomic_write(path, manifest)
        return manifest

    for record in manifest["requests"]:
        # Persist reservation before HTTP; crash/timeout cannot authorize retry.
        record.update(timestamp=clock(), attempted=True, status="attempted")
        manifest["attempts_reserved"] += 1
        _atomic_write(path, manifest)
        started = timer()
        try:
            response = client.responses.create(**request_parameters(record["model_requested"]))
        except Exception as error:
            record.update(status="error", error=_error(error))
            request_id = getattr(error, "request_id", None)
            record["safe_request_id"] = response_metadata({"request_id": request_id}).get("request_id")
        else:
            record.update(_response_record(response))
        record["duration"] = round(max(0.0, timer() - started), 6)
        _atomic_write(path, manifest)
    manifest.update(run_status="completed", finished_at_utc=clock())
    _atomic_write(path, manifest)
    return manifest


def render_validation_document(manifest: dict[str, Any] | None = None) -> str:
    """Render safe technical evidence only; financial reconciliation stays pending."""
    rows = manifest.get("requests", []) if isinstance(manifest, dict) else []
    by_model = {row.get("model_requested"): row for row in rows if isinstance(row, dict)
                and row.get("model_requested") in MODELS}
    state = "TECHNICAL_RESULTS_RECORDED" if manifest else "PENDING_EXECUTION"
    lines = [
        "# COMPLIMENTARY_MODEL_VALIDATION — E0",
        "",
        "Estado: **" + state + "**. Preparação/documentação verificada em 04/10/2026.",
        "Baseline 22fd581; nenhuma mudança de família/modelo principal foi decidida.",
        "",
        "O usuário autorizou um experimento E0 mínimo: uma request por modelo,",
        "até cinco requests, sem retry. Este documento não atribui custo, gratuidade",
        "ou elegibilidade financeira; a reconciliação pertence ao dashboard da conta.",
        "",
        "| MODEL | REQUEST ID | INPUT | OUTPUT | SERVICE TIER RETURNED | API STATUS | DASHBOARD COST | CONCLUSION |",
        "|---|---|---:|---:|---|---|---|---|",
    ]
    statuses = API_STATUSES | {"error", "attempted", "NOT_ATTEMPTED", "UNKNOWN_RESPONSE_STATUS"}
    for model in MODELS:
        row = by_model.get(model, {})
        metadata = response_metadata({"request_id": row.get("safe_request_id"),
                                      "id": row.get("response_id"),
                                      "service_tier": row.get("service_tier")})
        identifiers = [metadata[name] for name in ("request_id", "response_id") if name in metadata]
        identifier = " / ".join(identifiers) or "—"
        input_count, output_count = _count(row.get("input_tokens")), _count(row.get("output_tokens"))
        raw_status = row.get("status")
        status = raw_status if isinstance(raw_status, str) and raw_status in statuses else "PENDING_EXECUTION"
        lines.append("| " + " | ".join((model, identifier,
            str(input_count) if input_count is not None else "—",
            str(output_count) if output_count is not None else "—",
            metadata.get("effective_service_tier", "—"), status, PENDING_COST,
            "PENDING_DASHBOARD_RECONCILIATION")) + " |")
    lines.extend([
        "",
        "REQUEST ID reúne request ID seguro e response ID quando retornados.",
        "Contagens ausentes ficam desconhecidas; nenhuma resposta/erro bruto é armazenado.",
        "O JSON local preserva também timestamp, modelo retornado, cached input, total",
        "de tokens, duração e classificação segura de erro.",
        "",
        "## Política de request e documentação oficial",
        "",
        "- Endpoint fixo: /v1/responses em https://api.openai.com/v1.",
        "- Entrada única: `Responda somente com OK.`; sem PDFs, dados privados ou pipeline.",
        "- `store=False`, `max_output_tokens=64` em todos; nenhum tool/service_tier.",
        "- SDK `max_retries=0`; sem loop de retry, reparo, fallback ou request extra.",
        "- Reasoning `effort=none` nos quatro GPT-5; parâmetro omitido no GPT-4.1 mini.",
        "",
        "Snapshots e compatibilidade verificados na OpenAI Docs: "
        "[GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini), "
        "[GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini), "
        "[GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4), "
        "[GPT-5.2](https://developers.openai.com/api/docs/models/gpt-5.2), "
        "[Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).",
        "A API usa max_output_tokens para limitar toda saída gerada, inclusive tokens "
        "internos; 64 é o cap deste experimento, não uma contagem garantida de consumo. "
        "[Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create), "
        "[Counting tokens](https://developers.openai.com/api/docs/guides/token-counting).",
        "",
        "## Execução única e evidência",
        "",
        "Script: [validate_complimentary_models.py](../scripts/validate_complimentary_models.py).",
        "Execução sem argumento mostra somente o plano e não carrega a chave/SDK nem cria manifesto.",
        "A opção `--execute` fica reservada à execução E0 autorizada pelo usuário, após revisão.",
        "",
        "O manifesto fica em `data/processed/complimentary_model_test.json`.",
        "Um claim exclusivo persistente fecha a corrida entre processos. Cada registro",
        "é marcado `attempted`, gravado por arquivo temporário+fsync+replace antes do HTTP.",
        "Manifesto ou claim existentes bloqueiam toda repetição, inclusive após falha,",
        "interrupção ou JSON incompleto. Não apagar nem retomar automaticamente.",
        "Falha de persistência interrompe o controlador antes de qualquer envio seguinte.",
        "Dados de erro ficam limitados a categoria e status HTTP; IDs/tier têm allowlist.",
        "",
        "Após até cinco tentativas, parar inferências E0. O renderer deste documento usa",
        "apenas o JSON técnico já salvo e não faz chamada de API. A coluna financeira",
        "permanece PENDING_DASHBOARD_RECONCILIATION até o usuário conferir Usage/Costs.",
        "E1 continua offline; processamento GenAI real de Berkley/AXA e regeneração",
        "de PDF/PPTX/MP4/ZIP acadêmicos continuam sem autorização.",
        "",
    ])
    return "\n".join(lines)


def _real_client() -> Any:
    """Called only by --execute, never by plan mode or module import."""
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(ROOT / ".env", override=False)
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY missing.")
    for name in ("openai", "httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    return OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                  timeout=30.0, max_retries=0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Execute the explicitly authorized E0 once; permanently blocks reruns.")
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "PLAN_ONLY", "requests_max": len(MODELS),
                          "sdk_max_retries": 0,
                          "requests": [request_parameters(model) for model in MODELS]},
                         ensure_ascii=True, indent=2))
        return 0
    try:
        manifest = run_validation(client_factory=_real_client)
    except AlreadyAttempted:
        print(json.dumps({"status": "BLOCKED_ALREADY_ATTEMPTED", "requests_sent": 0}))
        return 2
    except Exception:
        # Includes persistence failures: stop rather than send another request.
        print(json.dumps({"status": "STOPPED_SAFE", "action": "Inspect the persistent E0 evidence; no automatic rerun."}))
        return 1
    print(json.dumps({"status": manifest["run_status"],
                      "attempts_reserved": manifest["attempts_reserved"],
                      "dashboard_reconciliation": PENDING_COST,
                      "results": [{"model": row["model_requested"], "status": row["status"]}
                                  for row in manifest["requests"]]}, ensure_ascii=True))
    return 0 if manifest["run_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
