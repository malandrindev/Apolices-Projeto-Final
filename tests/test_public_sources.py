"""Public sources tested entirely offline: document validity, cache and SSRF bounds."""
from __future__ import annotations
import io
import json
import socket
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf
import pytest
from PIL import Image

from src.sources import PUBLIC_CATALOG, PublicCatalogSource, PublicURLSource, SourceError, UploadSource, _is_public_ip


@pytest.fixture
def pdf_bytes():
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Synthetic source validation only")
        return document.tobytes()


class Response:
    def __init__(self, data=b"", status=200, headers=None):
        self.status=status
        self.headers=headers or {}
        self.stream=io.BytesIO(data)
        self.closed=False
    def close(self):
        self.closed=True
        self.stream.close()
    def getheader(self, name, default=None):
        return self.headers.get(name, default)
    def read(self, amount):
        return self.stream.read(amount)


class Connection:
    def __init__(self, response):
        self.response=response
        self.sock=None
        self.closed=False
        self.requests=[]
    def connect(self):
        pass
    def request(self, *args, **kwargs):
        self.requests.append((args,kwargs))
    def getresponse(self):
        return self.response
    def close(self):
        self.closed=True


def test_upload_sanitizes_path_and_validates_document(pdf_bytes):
    document=UploadSource().resolve("../../safe.pdf",pdf_bytes)
    assert document.filename=="safe.pdf"
    assert document.origin=="upload" and document.metadata["page_count"]==1
    assert len(document.sha256)==64 and not document.source_url
    with pytest.raises(SourceError):
        UploadSource().resolve("fake.png",pdf_bytes)
    with pytest.raises(SourceError):
        UploadSource().resolve("fake.pdf",b"%PDF-1.7 broken page tree")


def test_upload_validates_image_and_empty_size_limits():
    buffer=io.BytesIO();Image.new("RGB",(12,12),"white").save(buffer,format="PNG")
    assert UploadSource().resolve("C:\\private\\CON.png",buffer.getvalue()).filename=="documento_CON.png"
    for content in (b"",b"%PDF-"+b"x"*(1024*1024)):
        with pytest.raises(SourceError):UploadSource(1).resolve("a.pdf",content)


@pytest.mark.parametrize("url", [
    "file:///tmp/a.pdf", "ftp://example.com/a.pdf", "http://localhost/a.pdf",
    "http://127.0.0.1/a.pdf", "http://10.0.0.1/a.pdf", "http://169.254.169.254/latest",
    "http://[::1]/a.pdf", "http://[::ffff:127.0.0.1]/a.pdf", "https://intranet.local/a.pdf",
    "https://user:pass@example.com/a.pdf", "https://example.com:8443/a.pdf",
    "https://example.com/a.pdf?token=hidden", "https://example.com/a.pdf?token=", "https://example.com/a.pdf?api%5Fkey=hidden",
    "https://example.com/a.pdf?apikey=hidden", "https://example.com/a.pdf?x-amz-signature=hidden",
    "https://example.com\\@127.0.0.1/a.pdf",
])
def test_invalid_urls_rejected_before_fetch(tmp_path,url):
    source=PublicURLSource(tmp_path)
    with patch.object(source,"_fetch") as fetch,pytest.raises(SourceError):source.resolve(url)
    fetch.assert_not_called()


def test_dns_rejects_any_private_answer(tmp_path):
    source=PublicURLSource(tmp_path)
    mixed=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443)),(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))]
    with patch("src.sources.socket.getaddrinfo",return_value=mixed),pytest.raises(SourceError):
        source._resolve_addresses("example.com",443,time.monotonic()+1)
    assert not _is_public_ip("224.0.0.1")
    assert not _is_public_ip("::ffff:127.0.0.1")


def test_dns_wait_has_deadline(tmp_path):
    source=PublicURLSource(tmp_path)
    def slow(*args,**kwargs):
        time.sleep(.08)
        return []
    with patch("src.sources.socket.getaddrinfo",side_effect=slow),pytest.raises(SourceError):
        source._resolve_addresses("example.com",443,time.monotonic()+.015)


def test_https_connection_pins_validated_ip_retains_hostname(tmp_path):
    source=PublicURLSource(tmp_path)
    connection=Connection(Response())
    sock=Mock();sock.getpeername.return_value=("93.184.216.34",443)
    with patch.object(source,"_resolve_addresses",return_value=["93.184.216.34"]),patch("src.sources.http.client.HTTPSConnection",return_value=connection) as constructor,patch("src.sources.socket.create_connection",return_value=sock) as create:
        source._open_response("https://example.com/a.pdf",time.monotonic()+1)
        connection._create_connection(("example.com",443),1,None)
    assert constructor.call_args.args[:2]==("example.com",443)
    assert constructor.call_args.kwargs["context"].check_hostname
    assert create.call_args.args[0]==("93.184.216.34",443)


def test_redirect_revalidates_dns_for_each_host(tmp_path,pdf_bytes):
    source=PublicURLSource(tmp_path)
    first=Connection(Response(status=302,headers={"Location":"https://cdn.example.com/policy"}))
    second=Connection(Response(pdf_bytes))
    with patch.object(source,"_resolve_addresses",return_value=["93.184.216.34"]) as dns,patch("src.sources.http.client.HTTPSConnection",side_effect=[first,second]):
        data,final=source._fetch("https://example.com/start")
    assert data==pdf_bytes and final=="https://cdn.example.com/policy"
    assert [call.args[0] for call in dns.call_args_list]==["example.com","cdn.example.com"]
    assert first.closed and second.closed


@pytest.mark.parametrize("destination",["http://127.0.0.1/a.pdf","http://example.com/a.pdf","https://example.com/a.pdf?signature=secret"])
def test_redirect_blocks_private_secret_and_https_downgrade(tmp_path,destination):
    source=PublicURLSource(tmp_path);connection=Connection(Response(status=302,headers={"Location":destination}))
    with patch.object(source,"_open_response",return_value=(connection,connection.response)) as opening,pytest.raises(SourceError):
        source._fetch("https://example.com/start")
    assert opening.call_count==1 and connection.closed


def test_redirect_count_is_bounded(tmp_path):
    source=PublicURLSource(tmp_path)
    connection=Connection(Response(status=302,headers={"Location":"/again"}))
    with patch.object(source,"_open_response",return_value=(connection,connection.response)) as opening,pytest.raises(SourceError):source._fetch("https://example.com/start")
    assert opening.call_count==4


def test_download_size_checked_header_and_stream(tmp_path,pdf_bytes):
    source=PublicURLSource(tmp_path,max_document_mb=1)
    for response in (Response(pdf_bytes,headers={"Content-Length":str(1024*1024+1)}),Response(b"x"*(1024*1024+1))):
        connection=Connection(response)
        with patch.object(source,"_open_response",return_value=(connection,response)),pytest.raises(SourceError):source._fetch("https://example.com/a.pdf")
        assert connection.closed and response.closed


def test_download_encoding_and_http_errors_are_bounded(tmp_path):
    source=PublicURLSource(tmp_path)
    for response in (Response(status=404),Response(headers={"Content-Encoding":"gzip"}),Response(headers={"Content-Length":"invalid"})):
        connection=Connection(response)
        with patch.object(source,"_open_response",return_value=(connection,response)),pytest.raises(SourceError):source._fetch("https://example.com/a.pdf")


def test_html_rejected_and_extension_inferred_from_magic(tmp_path,pdf_bytes):
    source=PublicURLSource(tmp_path)
    with patch.object(source,"_fetch",return_value=(b"<html>login</html>","https://example.com/download")),pytest.raises(SourceError):source.resolve("https://example.com/download")
    with patch.object(source,"_fetch",return_value=(pdf_bytes,"https://example.com/download")):
        document=source.resolve("https://example.com/download")
    assert document.filename=="download.pdf" and document.metadata["media_type"]=="application/pdf"


def test_persistent_url_cache_has_no_fetch_and_preserves_capture(tmp_path,pdf_bytes):
    source=PublicURLSource(tmp_path)
    url="https://example.com/policy.pdf"
    with patch.object(source,"_fetch",return_value=(pdf_bytes,url)) as fetch:
        first=source.resolve(url)
        second=PublicURLSource(tmp_path).resolve(url)
    fetch.assert_called_once()
    assert not first.cache_hit and second.cache_hit
    assert second.data==first.data and second.sha256==first.sha256
    assert second.access_date==first.access_date and second.metadata["retrieved_at"]==first.metadata["retrieved_at"]


def test_hash_tampering_refetches_and_refresh_is_explicit(tmp_path,pdf_bytes):
    source=PublicURLSource(tmp_path);url="https://example.com/policy.pdf"
    with patch.object(source,"_fetch",return_value=(pdf_bytes,url)) as fetch:
        first=source.resolve(url)
        blob=next((source.directory/"blobs").glob("*.pdf"));blob.write_bytes(b"%PDF-broken")
        repaired=source.resolve(url)
        refreshed=source.resolve(url,refresh=True)
    assert fetch.call_count==3 and first.sha256==repaired.sha256==refreshed.sha256
    assert not repaired.cache_hit and not refreshed.cache_hit


def test_content_hash_deduplicates_different_urls(tmp_path,pdf_bytes):
    source=PublicURLSource(tmp_path)
    with patch.object(source,"_fetch",side_effect=[(pdf_bytes,"https://example.com/a.pdf"),(pdf_bytes,"https://example.com/b.pdf")]):
        a=source.resolve("https://example.com/a.pdf");b=source.resolve("https://example.com/b.pdf")
    assert a.sha256==b.sha256
    assert len(list((source.directory/"blobs").glob("*.pdf")))==1
    assert len(list((source.directory/"urls").glob("*.json")))==2


def test_catalog_is_existing_curated_registry_and_preserves_unknowns(tmp_path,pdf_bytes):
    registry=Path("data/samples/SOURCES.md").read_text(encoding="utf-8")
    assert {entry["id"] for entry in PUBLIC_CATALOG} == {
        "chubb_do_capital_aberto_2025", "aig_do_capital_fechado_2017",
        "berkley_do_202512", "axa_do_202512_v1"
    }
    for entry in PUBLIC_CATALOG:assert entry["source_url"] in registry
    url_source=PublicURLSource(tmp_path)
    base=UploadSource().resolve("a.pdf",pdf_bytes)
    with patch.object(url_source,"resolve",return_value=base) as resolve:
        a=PublicCatalogSource(url_source=url_source).resolve(PUBLIC_CATALOG[1]["id"])
    assert a.origin=="catalog" and a.metadata["trusted_catalog"] is True
    assert a.metadata["product_scope"]=="capital_fechado"
    assert a.metadata["effective_date"] is None and a.metadata["susep_process"] is None
    resolve.assert_called_once_with(PUBLIC_CATALOG[1]["source_url"],refresh=False)
    with pytest.raises(SourceError):PublicCatalogSource(url_source=url_source).resolve("unknown")


def test_connection_close_response_socket_receives_remaining_deadline(tmp_path,pdf_bytes):
    from types import SimpleNamespace
    source=PublicURLSource(tmp_path)
    response=Response(pdf_bytes)
    active=Mock()
    response.fp=SimpleNamespace(raw=SimpleNamespace(_sock=active))
    connection=Connection(response)
    with patch.object(source,"_open_response",return_value=(connection,response)):
        data,_=source._fetch("https://example.com/a.pdf")
    assert data==pdf_bytes and response.closed
    assert active.settimeout.call_count>=1
    assert all(0<call.args[0]<=source.timeout_seconds for call in active.settimeout.call_args_list)


def test_incomplete_content_length_rejected(tmp_path,pdf_bytes):
    source=PublicURLSource(tmp_path)
    response=Response(pdf_bytes,headers={"Content-Length":str(len(pdf_bytes)+10)})
    connection=Connection(response)
    with patch.object(source,"_open_response",return_value=(connection,response)),pytest.raises(SourceError):
        source._fetch("https://example.com/a.pdf")
    assert response.closed


def test_peer_address_mismatch_blocks_connection(tmp_path):
    source=PublicURLSource(tmp_path)
    connection=Connection(Response())
    sock=Mock();sock.getpeername.return_value=("127.0.0.1",443)
    with patch.object(source,"_resolve_addresses",return_value=["93.184.216.34"]),patch("src.sources.http.client.HTTPSConnection",return_value=connection),patch("src.sources.socket.create_connection",return_value=sock):
        source._open_response("https://example.com/a.pdf",time.monotonic()+1)
        with pytest.raises(SourceError):connection._create_connection(("example.com",443),1,None)
    sock.close.assert_called_once()


def test_catalog_rejects_png_before_pdf_rename_or_trusted_metadata(tmp_path):
    buffer=io.BytesIO();Image.new("RGB",(12,12),"white").save(buffer,format="PNG")
    image=UploadSource().resolve("valid.png",buffer.getvalue())
    url_source=PublicURLSource(tmp_path)
    with patch.object(url_source,"resolve",return_value=image),pytest.raises(SourceError,match="exige PDF"):
        PublicCatalogSource(url_source=url_source).resolve(PUBLIC_CATALOG[0]["id"])
    assert image.filename=="valid.png" and "trusted_catalog" not in image.metadata


class DeadlineSocket:
    def __init__(self):
        import threading
        self.stopped=threading.Event()
        self.closed=False
    def settimeout(self, seconds):
        pass
    def shutdown(self, how):
        self.stopped.set()
    def close(self):
        self.closed=True
        self.stopped.set()


class TrickleConnection(Connection):
    def __init__(self, headers=False):
        super().__init__(Response())
        self.sock=DeadlineSocket()
        self.slow_headers=headers
        self.response=TrickleResponse(self.sock)
    def getresponse(self):
        if self.slow_headers:
            while not self.sock.stopped.wait(.004):
                pass  # Peer supplies a few header bytes repeatedly before timeout.
            raise OSError("closed at absolute deadline")
        return self.response
    def close(self):
        super().close()
        self.sock.close()


class TrickleResponse(Response):
    def __init__(self,sock):
        super().__init__()
        self.sock=sock
    def read(self,amount):
        # Like buffered HTTPResponse.read, each short recv can reset inactivity
        # timeout while never satisfying the requested buffer size.
        while not self.sock.stopped.wait(.004):
            pass
        raise OSError("closed at absolute deadline")


@pytest.mark.parametrize("slow_headers",[False,True])
def test_absolute_deadline_interrupts_slow_trickle_headers_and_body(tmp_path,slow_headers):
    source=PublicURLSource(tmp_path,timeout_seconds=.04)
    connection=TrickleConnection(headers=slow_headers)
    start=time.monotonic()
    with patch.object(source,"_resolve_addresses",return_value=["93.184.216.34"]),patch("src.sources.http.client.HTTPSConnection",return_value=connection),pytest.raises(SourceError):
        source._fetch("https://example.com/policy.pdf")
    elapsed=time.monotonic()-start
    assert elapsed<.3,elapsed
    assert connection.closed and connection.sock.stopped.is_set()
    assert not connection._source_deadline_timer.is_alive() or connection.sock.stopped.is_set()
