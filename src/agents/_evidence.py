"""Normalização estrita de evidências e valores objetivos, sem inferência de LLM."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def fold(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", normalize(text))
                   if not unicodedata.combining(char))


def currency(text: str) -> str | None:
    found = set()
    for pattern, code in (
        (r"r\$|\bbrl\b|\breais\b|\breal\b", "BRL"),
        (r"us\$|\busd\b|\bdolares\b", "USD"),
        (r"€|\beur\b|\beuros?\b", "EUR"), (r"£|\bgbp\b", "GBP"),
    ):
        if re.search(pattern, fold(text)):
            found.add(code)
    return next(iter(found)) if len(found) == 1 else None


def amounts(text: str) -> list[Decimal]:
    """Lê números BR/US; formatos mistos/ambíguos nunca viram comparações."""
    result = []
    for raw in re.findall(r"(?<![\w])[-+]?\d+(?:[.,]\d+)*(?![\w])", text):
        sign = "-" if raw.startswith("-") else ""
        raw = raw.lstrip("+-")
        if "." in raw and "," in raw:
            decimal_sep = "." if raw.rfind(".") > raw.rfind(",") else ","
            thousands_sep = "," if decimal_sep == "." else "."
            whole, fractional = raw.rsplit(decimal_sep, 1)
            if len(fractional) not in (1, 2) or not re.fullmatch(
                rf"\d{{1,3}}(?:\{thousands_sep}\d{{3}})+", whole
            ):
                continue
            cleaned = whole.replace(thousands_sep, "") + "." + fractional
        elif "." in raw or "," in raw:
            separator = "." if "." in raw else ","
            parts = raw.split(separator)
            if len(parts) == 2 and len(parts[1]) in (1, 2):
                cleaned = ".".join(parts)
            elif all(len(part) == 3 for part in parts[1:]) and len(parts[0]) <= 3:
                cleaned = "".join(parts)
            else:
                continue
        else:
            cleaned = raw
        try:
            result.append(Decimal(sign + cleaned))
        except InvalidOperation:
            continue
    return result


def parsed_date(text: str) -> date | None:
    value = fold(text)
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", value)
        if match:
            day, month, year = map(int, match.groups())
        else:
            months = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho",
                      "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
            match = re.fullmatch(r"(\d{1,2}) de (\w+) de (\d{4})", value)
            if not match or match[2] not in months:
                return None
            day, month, year = int(match[1]), months.index(match[2]) + 1, int(match[3])
    try:
        return date(year, month, day)
    except ValueError:
        return None


def dates_in(text: str) -> set[date]:
    patterns = (r"\d{4}-\d{2}-\d{2}", r"\d{1,2}[/-]\d{1,2}[/-]\d{4}",
                r"\d{1,2} de \w+ de \d{4}")
    return {value for pattern in patterns for raw in re.findall(pattern, fold(text))
            if (value := parsed_date(raw)) is not None}


def provider_identity(gateway: object) -> str:
    """Identidade não secreta suficiente para separar caches de provedores."""
    explicit = getattr(gateway, "provider", None)
    if isinstance(explicit, str):
        return explicit
    kind = type(gateway)
    return f"{kind.__module__}.{kind.__qualname__}"
