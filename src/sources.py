"""Local/upload and bounded public-document inputs; no IA calls or web search.

Catalog entries use explicit official references recorded in project docs.
Only explicit resolve() downloads. Cached captures preserve their capture date.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import logging
import os
import queue
import re
import socket
import ssl
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit, urlunsplit

from src.agents.ingestion import IngestionAgent, IngestionError
from src.agents.report import ComparisonReportAgent, ReportError
from src.config import IngestionSettings
from src.storage.cache import JsonCache

logger = logging.getLogger(__name__)


class SourceError(ValueError):
    """Safe user-facing source error; never includes credentials or HTTP bodies."""


@dataclass(frozen=True, slots=True)
class SourceDocument:
    filename: str
    data: bytes
    sha256: str
    origin: str
    source_url: str = ""
    access_date: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    cache_hit: bool = False


PUBLIC_CATALOG: list[dict[str, Any]] = [
    {
        "id": "chubb_do_capital_aberto_2025",
        "insurer": "Chubb",
        "product_name": "Seguro RC D&O Capital Aberto",
        "product_scope": "capital_aberto",
        "susep_process": "15414.900831/2017-45",
        "effective_date": "2025-12-16",
        "document_type": "condicoes_gerais",
        "filename": "chubb_do_capital_aberto_susep_15414-900831-2017-45.pdf",
        "source_url": "https://www.chubb.com/content/dam/chubb-sites/chubb-com/br-pt/condicoes-gerais/diretores-e-administradores/capital-aberto-processo-susep-15414-900831-2017-45-versao-a-partir-de-16-12-2025.pdf",
        "version_note": "Capital Aberto; versao indicada a partir de 16/12/2025. Condicoes gerais, nao apolice individual emitida.",
    },
    {
        "id": "aig_do_capital_fechado_2017",
        "insurer": "AIG",
        "product_name": "Condicoes contratuais D&O Capital Fechado",
        "product_scope": "capital_fechado",
        "susep_process": None,
        "effective_date": None,
        "document_type": "condicoes_gerais",
        "filename": "aig_do_capital_fechado_condicoes_gerais_2017.pdf",
        "source_url": "https://www.aig.com.br/content/dam/aig/lac/brazil/documents/brochure/2017-cc-deo-553-capital-fechado.pdf",
        "version_note": "Documento de 2017, Capital Fechado; data efetiva exata e processo nao inferidos. Escopo/versao distintos da entrada Chubb.",
    },
    {'id': 'berkley_do_202512',
     'insurer': 'Berkley',
     'product_name': 'Seguro RC Diretores e Administradores D&O — 12/2025 v2',
     'product_scope': None,
     'susep_process': '15414.901494/2017-11',
     'effective_date': '2025-12-11',
     'document_type': 'condicoes_gerais_bundle',
     'filename': 'berkley_do_202512.pdf',
     'source_url': 'https://www.berkley.com.br/wp-content/uploads/2022/03/Seguro-DO_Vigencia-a-partir-de-11.12.2025_v2.pdf',
     'version_note': 'Condições públicas 12/2025 v2; capital aberto/fechado não confirmado. Coberturas dependem '
                     'da especificação; não é apólice individual.'},
    {'id': 'axa_do_202512_v1',
     'insurer': 'AXA',
     'product_name': 'Seguro RC D&O — 30/12/2025 v1',
     'product_scope': None,
     'susep_process': '15414.901016/2017-01',
     'effective_date': '2025-12-30',
     'document_type': 'condicoes_gerais_bundle',
     'filename': 'axa_do_202512.pdf',
     'source_url': 'https://axa.com.br/minio/cms/CG_AXA_D_and_O_15414_901016_2017_01_20251230_90a6f5bcbc.pdf',
     'version_note': 'Condições públicas 30/12/2025 v1; condições particulares dependem de contratação. Escopo '
                     'individual não presumido.'},
]


def _safe_filename(name: str, suffix: str | None = None) -> str:
    name = Path(str(name).replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "documento"
    current = Path(name)
    stem = current.stem if current.suffix else name
    ending = suffix if suffix is not None else current.suffix.lower()
    if stem.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        stem = "documento_" + stem
    return stem[:150].strip(" .") + ending


def _signature_suffix(data: bytes) -> str:
    for suffix, media in ((".pdf", "application/pdf"), (".png", "image/png"), (".jpg", "image/jpeg"), (".tif", "image/tiff"), (".bmp", "image/bmp")):
        if IngestionAgent._has_signature(data[:16], media):
            return suffix
    raise SourceError("Fonte nao e PDF/imagem valido; paginas HTML nao sao documentos aceitos.")


class UploadSource:
    def __init__(self, max_document_mb: int = 30) -> None:
        if not isinstance(max_document_mb, int) or max_document_mb < 1:
            raise ValueError("max_document_mb deve ser inteiro positivo.")
        self.max_document_mb = max_document_mb

    def resolve(self, name: str, data: bytes) -> SourceDocument:
        if not isinstance(data, bytes) or not data:
            raise SourceError("Documento vazio ou conteudo binario invalido.")
        if len(data) > self.max_document_mb * 1024 * 1024:
            raise SourceError("Documento excede o limite de tamanho configurado.")
        filename = _safe_filename(name)
        _signature_suffix(data)
        try:
            with tempfile.TemporaryDirectory(prefix="insurminds-source-") as directory:
                folder = Path(directory)
                path = folder / filename
                path.write_bytes(data)
                settings = IngestionSettings(self.max_document_mb, "por+eng", 20, 60, 200, folder)
                document = IngestionAgent(settings).ingest(path)
        except (IngestionError, OSError) as error:
            if isinstance(error, IngestionError):
                raise SourceError(str(error)) from None
            raise SourceError("Nao foi possivel validar o documento local.") from None
        return SourceDocument(filename=filename, data=data, sha256=document.sha256, origin="upload", metadata={"media_type": document.media_type, "page_count": document.page_count, "size_bytes": document.size_bytes})


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return address.is_global and not (address.is_multicast or address.is_reserved or address.is_loopback or address.is_link_local or address.is_unspecified)


def _validate_url(url: str) -> str:
    try:
        value = ComparisonReportAgent.validate_source_urls({"source": url})["source"]
        parts = urlsplit(value)
        host = parts.hostname
        if not host or "\\" in value or "%" in host:
            raise ValueError()
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home")):
            raise ValueError()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not _is_public_ip(str(literal)):
            raise ValueError()
        port = parts.port
        if port is not None and port != (443 if parts.scheme.lower() == "https" else 80):
            raise ValueError()
        secret_names = {"token", "access_token", "api_key", "key", "secret", "signature", "sig", "password", "authorization", "credential", "x_amz_signature", "x_amz_credential", "apikey", "auth", "auth_token", "bearer", "client_secret", "jwt", "session_token"}
        if {key.lower().replace("-", "_") for key, _ in parse_qsl(parts.query, keep_blank_values=True)} & secret_names:
            raise ValueError()
        hostpart = f"[{host}]" if ":" in host else host
        return urlunsplit((parts.scheme.lower(), hostpart, parts.path or "/", parts.query, ""))
    except (ReportError, ValueError, KeyError, UnicodeError, TypeError):
        raise SourceError("Use URL HTTP(S) publica, porta padrao, sem credenciais/assinaturas ou endereco local.") from None


class PublicURLSource:
    MAX_REDIRECTS = 3

    def __init__(self, processed_dir: Path, max_document_mb: int = 30, timeout_seconds: float = 20) -> None:
        self.upload = UploadSource(max_document_mb)
        if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
            raise ValueError("timeout_seconds deve estar entre zero e 120 segundos.")
        self.timeout_seconds = float(timeout_seconds)
        self.directory = Path(processed_dir) / "public_sources"
        self.cache = JsonCache(self.directory / "urls")
        self.max_bytes = max_document_mb * 1024 * 1024

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError("Tempo limite de download excedido; tente novamente ou use upload.")
        return remaining

    def _resolve_addresses(self, host: str, port: int, deadline: float) -> list[str]:
        """Bound DNS wait; the daemon only resolves, and never opens connections."""
        result: queue.Queue[Any] = queue.Queue(maxsize=1)
        def lookup() -> None:
            try:
                result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
            except OSError:
                result.put(None)
        threading.Thread(target=lookup, daemon=True, name="public-source-dns").start()
        try:
            answers = result.get(timeout=self._remaining(deadline))
        except queue.Empty:
            raise SourceError("Tempo limite para resolver a fonte publica.") from None
        if not answers:
            raise SourceError("Nao foi possivel resolver a fonte publica.")
        addresses = list(dict.fromkeys(item[4][0] for item in answers))
        if not addresses or any(not _is_public_ip(address) for address in addresses):
            raise SourceError("A fonte resolve para endereco local/privado ou nao publico; download bloqueado.")
        return addresses

    def _open_response(self, url: str, deadline: float) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        parts = urlsplit(url)
        host = parts.hostname
        port = 443 if parts.scheme == "https" else 80
        addresses = self._resolve_addresses(host, port, deadline)
        pinned = addresses[0]
        remaining = self._remaining(deadline)
        connection = (http.client.HTTPSConnection(host, port, timeout=remaining, context=ssl.create_default_context()) if parts.scheme == "https" else http.client.HTTPConnection(host, port, timeout=remaining))
        active_socket: list[Any] = [None]
        expired = threading.Event()
        def expire_connection() -> None:
            expired.set()
            sock = connection.sock or active_socket[0]
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
        timer = threading.Timer(self._remaining(deadline), expire_connection)
        timer.daemon = True
        connection._source_deadline_timer = timer
        def connect(address: Any, timeout: Any, source_address: Any = None) -> socket.socket:
            # Connect to the validated literal IP, while HTTPSConnection retains
            # the original host for certificate verification and TLS SNI.
            sock = socket.create_connection((pinned, port), self._remaining(deadline), source_address)
            active_socket[0] = sock
            if not _is_public_ip(sock.getpeername()[0]) or ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(pinned):
                sock.close()
                raise SourceError("Destino da conexao divergiu do endereco publico validado.")
            if expired.is_set():
                sock.close()
                raise SourceError("Tempo limite de download excedido.")
            sock.settimeout(self._remaining(deadline))
            return sock
        connection._create_connection = connect
        target = quote(parts.path or "/", safe="/%:@!$&'()+,;=-._~")
        if parts.query:
            target += "?" + quote(parts.query, safe="%=&;:+,/?!$'()~-._")
        try:
            timer.start()
            connection.connect()
            active_socket[0] = connection.sock
            if expired.is_set():
                raise SourceError("Tempo limite de download excedido.")
            if connection.sock is not None:
                connection.sock.settimeout(self._remaining(deadline))
            connection.request("GET", target, headers={"User-Agent": "InsurMinds-MVP/1.0", "Accept": "application/pdf,image/*", "Accept-Encoding": "identity"})
            self._remaining(deadline)
            return connection, connection.getresponse()
        except Exception:
            timer.cancel()
            # A connect/TLS failure can occur before HTTPConnection owns the
            # pinned socket; release that reference as well.
            if active_socket[0] is not None:
                try:
                    active_socket[0].close()
                except OSError:
                    pass
            connection.close()
            raise

    def _fetch(self, url: str) -> tuple[bytes, str]:
        """Bounded streaming fetch; mock this helper in offline unit tests."""
        deadline = time.monotonic() + self.timeout_seconds
        current = url
        try:
            for redirect_number in range(self.MAX_REDIRECTS + 1):
                current = _validate_url(current)
                connection, response = self._open_response(current, deadline)
                try:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.getheader("Location")
                        if not location or redirect_number == self.MAX_REDIRECTS:
                            raise SourceError("Redirecionamento ausente ou limite de 3 redirecionamentos excedido.")
                        destination = _validate_url(urljoin(current, location))
                        if urlsplit(current).scheme == "https" and urlsplit(destination).scheme != "https":
                            raise SourceError("Redirecionamento HTTPS para HTTP foi bloqueado.")
                        current = destination
                        continue
                    if response.status != 200:
                        raise SourceError("Fonte publica indisponivel ou download recusado; use link direto para PDF/imagem.")
                    if response.getheader("Content-Encoding", "identity").lower().strip() not in {"", "identity"}:
                        raise SourceError("Download comprimido nao suportado; use link direto ou upload.")
                    length = response.getheader("Content-Length")
                    if length is not None:
                        try:
                            length = int(length)
                        except ValueError:
                            raise SourceError("Tamanho informado pelo servidor e invalido.") from None
                        if length < 0 or length > self.max_bytes:
                            raise SourceError("Documento excede o limite de tamanho configurado.")
                    chunks = []
                    size = 0
                    while True:
                        remaining = self._remaining(deadline)
                        # HTTP/1.0 or Connection: close transfers socket ownership
                        # to response.fp; keep each read inside the total deadline.
                        active_socket = connection.sock or getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                        if active_socket is not None:
                            active_socket.settimeout(remaining)
                        read = getattr(response, "read1", response.read)
                        chunk = read(min(65536, self.max_bytes + 1 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise SourceError("Documento excede o limite de tamanho configurado.")
                        chunks.append(chunk)
                    if length is not None and size != length:
                        raise SourceError("Download incompleto; tamanho recebido difere do informado.")
                    return b"".join(chunks), current
                finally:
                    timer = getattr(connection, "_source_deadline_timer", None)
                    if timer is not None:
                        timer.cancel()
                    try:
                        response.close()
                    finally:
                        connection.close()
        except SourceError:
            raise
        except (OSError, http.client.HTTPException, TimeoutError):
            raise SourceError("Nao foi possivel baixar a fonte no tempo limite; confira o link ou use upload.") from None
        raise SourceError("Nao foi possivel concluir o download publico.")

    @staticmethod
    def _url_key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _load_cache(self, url: str) -> SourceDocument | None:
        manifest = self.cache.load(self._url_key(url))
        if not manifest or manifest.get("version") != 1 or manifest.get("requested_url") != url:
            return None
        try:
            digest = manifest["sha256"]
            suffix = manifest["suffix"]
            filename = manifest["filename"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                return None
            if suffix not in {".pdf", ".png", ".jpg", ".tif", ".bmp"} or not isinstance(filename, str) or _safe_filename(filename) != filename:
                return None
            blob = self.directory / "blobs" / (digest + suffix)
            if blob.stat().st_size > self.max_bytes:
                return None
            data = blob.read_bytes()
            document = self.upload.resolve(filename, data)
            if document.sha256 != digest or _signature_suffix(data) != suffix:
                return None
            final_url = _validate_url(manifest["final_url"])
            captured = datetime.fromisoformat(manifest["retrieved_at"])
            if captured.tzinfo is None:
                return None
            metadata = dict(document.metadata)
            metadata.update({"requested_url": url, "final_url": final_url, "retrieved_at": captured.isoformat(), "cache_persisted": True})
            return replace(document, origin="public_url", source_url=final_url, access_date=captured.astimezone(timezone.utc).date().isoformat(), metadata=metadata, cache_hit=True)
        except (KeyError, OSError, SourceError, TypeError, ValueError):
            return None

    def _save_cache(self, document: SourceDocument, requested_url: str) -> bool:
        suffix = _signature_suffix(document.data)
        blob = self.directory / "blobs" / (document.sha256 + suffix)
        temporary = None
        try:
            blob.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=blob.parent, prefix=".download-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(document.data)
            os.replace(temporary, blob)
            self.cache.save(self._url_key(requested_url), {
                "version": 1, "requested_url": requested_url, "final_url": document.source_url,
                "sha256": document.sha256, "suffix": suffix, "filename": document.filename,
                "retrieved_at": document.metadata["retrieved_at"],
            })
            return True
        except OSError:
            logger.warning("Public source validated but local capture cache could not be persisted.")
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def resolve(self, url: str, refresh: bool = False) -> SourceDocument:
        requested = _validate_url(url)
        if not refresh:
            cached = self._load_cache(requested)
            if cached is not None:
                return cached
        data, final_url = self._fetch(requested)
        final_url = _validate_url(final_url)
        suffix = _signature_suffix(data)
        suggested = unquote(Path(urlsplit(final_url).path).name) or "documento"
        filename = _safe_filename(suggested, suffix)
        document = self.upload.resolve(filename, data)
        captured = datetime.now(timezone.utc)
        metadata = dict(document.metadata)
        metadata.update({"requested_url": requested, "final_url": final_url, "retrieved_at": captured.isoformat()})
        document = replace(document, origin="public_url", source_url=final_url, access_date=captured.date().isoformat(), metadata=metadata)
        persisted = self._save_cache(document, requested)
        metadata = dict(document.metadata, cache_persisted=persisted)
        return replace(document, metadata=metadata)


class PublicCatalogSource:
    def __init__(self, url_source: PublicURLSource) -> None:
        self.url_source = url_source

    def resolve(self, entry_id: str, refresh: bool = False) -> SourceDocument:
        entry = next((item for item in PUBLIC_CATALOG if item["id"] == entry_id), None)
        if entry is None:
            raise SourceError("Documento nao pertence ao catalogo publico curado.")
        document = self.url_source.resolve(entry["source_url"], refresh=refresh)
        if document.metadata.get("media_type") != "application/pdf":
            raise SourceError("A entrada do catalogo exige PDF; a fonte retornou outro formato.")
        validated = self.url_source.upload.resolve(entry["filename"], document.data)
        if validated.sha256 != document.sha256:
            raise SourceError("Conteudo do catalogo diverge do hash validado.")
        metadata = dict(document.metadata)
        metadata.update(validated.metadata)
        metadata.update(entry)
        metadata["catalog_id"] = entry["id"]
        metadata["trusted_catalog"] = True
        return replace(document, filename=entry["filename"], origin="catalog", metadata=metadata)
