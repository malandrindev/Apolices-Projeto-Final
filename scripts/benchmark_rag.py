"""Mede embeddings/RAG locais em subprocessos limitados; não usa chaves ou LLMs.

Uso: python scripts/benchmark_rag.py --timeout 60
Os resultados e downloads ficam em data/processed/rag_benchmark (ignorado pelo Git).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
PREFIX = 'RAG_METRIC '
TEXTS = [
    'Diagnostico tecnico alpha: recuperar um trecho da pagina um.',
    'Diagnostico tecnico beta: manter a proveniencia da pagina dois.',
    'Diagnostico tecnico gamma: pesquisar um termo no indice local.',
    'Diagnostico tecnico delta: preservar os identificadores do documento.',
]


def _event(value: dict[str, Any]) -> None:
    print(PREFIX + json.dumps(value, ensure_ascii=False), flush=True)


def _rss_mb() -> float | None:
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / (1024 * 1024), 3)
    except ImportError:
        if os.name != 'nt':
            return None
        import ctypes
        from ctypes import wintypes
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage',
                )
            ]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        api = ctypes.WinDLL('psapi', use_last_error=True)
        api.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
        api.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not api.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return round(counters.WorkingSetSize / (1024 * 1024), 3)


def _measure(name: str, operation: Callable[[], Any], summarize: Callable[[Any], Any] | None = None) -> Any:
    _event({'event': 'start', 'phase': name})
    rss_before = _rss_mb()
    started = time.perf_counter()
    cpu_before = time.process_time()
    try:
        result = operation()
    except Exception as error:
        _event({'event': 'result', 'phase': name, 'status': 'ERROR', 'error_type': type(error).__name__, 'wall_seconds': round(time.perf_counter() - started, 4), 'cpu_seconds': round(time.process_time() - cpu_before, 4), 'rss_mb_after': _rss_mb()})
        raise
    _event({'event': 'result', 'phase': name, 'status': 'PASS', 'wall_seconds': round(time.perf_counter() - started, 4), 'cpu_seconds': round(time.process_time() - cpu_before, 4), 'rss_mb_before': rss_before, 'rss_mb_after': _rss_mb(), 'detail': summarize(result) if summarize else None})
    return result


def _settings(model: str, root: Path):
    from src.config import StorageSettings
    return StorageSettings(sqlite_path=root/'policies.sqlite3', chroma_path=root/'chroma', chroma_collection='rag_performance_diagnostic', embedding_model=model, embedding_device='cpu', chunk_size_chars=400, chunk_overlap_chars=50, rag_top_k=3)


def _chunks():
    from src.schemas.storage import EvidenceChunk
    return [EvidenceChunk(chunk_id=f'diagnostic:clause-{index}:p1:c0', document_id='diagnostic', clause_id=f'clause-{index}', title='Diagnostico', category='outras', page_number=1, chunk_index=0, text=text) for index, text in enumerate(TEXTS)]


def _fake_cache_probe(root: Path) -> dict[str, Any]:
    from src.storage.vector_store import ChromaVectorStore
    from tests.test_storage_rag import FakeChromaClient, FakeCollection
    from tests.test_vector_cache import CountingEncoder
    encoder = CountingEncoder()
    collection = FakeCollection()
    store = ChromaVectorStore(_settings(encoder.model_name, root), encoder=encoder, client=FakeChromaClient(collection))
    for _ in range(2):
        store.upsert(_chunks(), source_name='synthetic-diagnostic')
    store.search('consulta diagnostica alpha', document_id='diagnostic')
    store.search('consulta diagnostica alpha', document_id='diagnostic')
    return {'index_operations':2, 'query_operations':2, 'encoder_calls':len(encoder.calls), 'texts_encoded':sum(map(len, encoder.calls)), 'index_chunks':4, 'stored_ids':len(collection.items), 'expected_repeated_work_without_cache':{'encoder_calls':4, 'texts_encoded':10}}


def _worker(phase: str, model: str, root: Path) -> int:
    if phase in {'chromadb', 'sentence_transformers', 'src.storage.vector_store'}:
        _measure('import_' + phase, lambda: importlib.import_module(phase))
        return 0
    if phase == 'fake_cache':
        _measure('fake_cache_behavior', lambda: _fake_cache_probe(root), lambda result: result)
        return 0
    module = _measure('model_process_import_sentence_transformers', lambda: importlib.import_module('sentence_transformers'))
    model_instance = _measure('first_model_download_and_load', lambda: module.SentenceTransformer(model, device='cpu'), lambda value: {'device':str(value.device), 'model_name':model})
    from src.storage.vector_store import ChromaVectorStore, SentenceTransformerEncoder
    encoder = SentenceTransformerEncoder(model, device='cpu')
    encoder._model = model_instance
    dimensions = lambda values: {'texts':len(values), 'dimensions':len(values[0]) if values else 0}
    _measure('encode_four_chunks', lambda: encoder.encode(TEXTS), dimensions)
    _measure('encode_four_chunks_repeat_without_vector_cache', lambda: encoder.encode(TEXTS), dimensions)
    chromadb = _measure('model_process_import_chromadb', lambda: importlib.import_module('chromadb'))
    settings = _settings(model, root)
    client = _measure('chroma_local_startup', lambda: chromadb.PersistentClient(path=str(settings.chroma_path), settings=chromadb.config.Settings(anonymized_telemetry=False)))
    store = ChromaVectorStore(settings, encoder=encoder, client=client)
    _measure('index_four_chunks_first', lambda: store.upsert(_chunks(), source_name='synthetic-diagnostic'), lambda value:{'indexed_chunks':value})
    _measure('index_four_chunks_repeat_cached', lambda: store.upsert(_chunks(), source_name='synthetic-diagnostic'), lambda value:{'indexed_chunks':value})
    summarize_search = lambda values:{'retrieved_chunks':len(values), 'document_filter_preserved':all(value.document_id == 'diagnostic' for value in values)}
    _measure('query_first', lambda: store.search('recuperar trecho da pagina', document_id='diagnostic'), summarize_search)
    _measure('query_repeat_cached', lambda: store.search('recuperar trecho da pagina', document_id='diagnostic'), summarize_search)
    cold_encoder = SentenceTransformerEncoder(model, device='cpu')
    second_store = ChromaVectorStore(settings, encoder=cold_encoder, client=client)
    _measure('new_encoder_index_repeat_persistent_cache', lambda: second_store.upsert(_chunks(), source_name='synthetic-diagnostic'), lambda value:{'indexed_chunks':value, 'model_loaded':cold_encoder._model is not None})
    return 0


def _supervise(phase: str, model: str, root: Path, timeout: float, env: dict[str, str]) -> dict[str, Any]:
    command = [sys.executable, '-u', str(Path(__file__).resolve()), '--worker', phase, '--model', model, '--workdir', str(root)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    lines: queue.Queue[str | None] = queue.Queue()
    stderr_count = [0]
    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)
    def drain_stderr() -> None:
        assert process.stderr is not None
        for _ in process.stderr:
            stderr_count[0] += 1
    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=drain_stderr, daemon=True).start()
    metrics = []
    active = 'startup_' + phase
    deadline = time.monotonic() + timeout
    status = 'PASS'
    while True:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise queue.Empty
            line = lines.get(timeout=remaining)
        except queue.Empty:
            process.kill()
            process.wait(timeout=10)
            metrics.append({'phase':active, 'status':'TIMEOUT', 'external_timeout_seconds':timeout})
            status = 'TIMEOUT'
            break
        if line is None:
            break
        if not line.startswith(PREFIX):
            continue
        event = json.loads(line[len(PREFIX):])
        if event['event'] == 'start':
            active = event['phase']
            deadline = time.monotonic() + timeout
            print('Medindo: ' + active, flush=True)
        else:
            metrics.append({key:value for key,value in event.items() if key != 'event'})
            deadline = time.monotonic() + timeout
            if event['status'] != 'PASS':
                status = event['status']
    if process.poll() is None:
        process.wait(timeout=10)
    if process.returncode and status == 'PASS':
        status = 'ERROR'
    return {'worker':phase, 'status':status, 'exit_code':process.returncode, 'stderr_line_count':stderr_count[0], 'phases':metrics}


def _cache_size(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob('*') if path.is_file()) if directory.exists() else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--model')
    parser.add_argument('--worker', choices=['chromadb','sentence_transformers','src.storage.vector_store','fake_cache','model_flow'])
    parser.add_argument('--workdir', type=Path)
    parser.add_argument('--skip-model', action='store_true')
    arguments = parser.parse_args()
    if arguments.timeout <= 0:
        parser.error("--timeout deve ser positivo")
    if arguments.worker:
        try:
            return _worker(arguments.worker, arguments.model or '', arguments.workdir or PROJECT_ROOT/'data/processed/rag_benchmark/worker')
        except Exception:
            return 1
    from src.config import get_storage_settings
    model = arguments.model or get_storage_settings().embedding_model
    benchmark_root = PROJECT_ROOT/'data/processed/rag_benchmark'
    run_root = benchmark_root/datetime.now(timezone.utc).strftime('run_%Y%m%dT%H%M%S')
    run_root.mkdir(parents=True, exist_ok=True)
    hf_cache = benchmark_root/'huggingface_cache'
    env = dict(os.environ)
    env.update({'HF_HOME':str(hf_cache), 'HF_HUB_ETAG_TIMEOUT':'10', 'HF_HUB_DOWNLOAD_TIMEOUT':'10', 'HF_HUB_DISABLE_TELEMETRY':'1', 'HF_HUB_DISABLE_PROGRESS_BARS':'1', 'ANONYMIZED_TELEMETRY':'False', 'TOKENIZERS_PARALLELISM':'false', 'PYTHONUNBUFFERED':'1'})
    report = {'started_at_utc':datetime.now(timezone.utc).isoformat(), 'python':sys.version.split()[0], 'cpu_logical_count':os.cpu_count(), 'device':'cpu', 'model':model, 'phase_timeout_seconds':arguments.timeout, 'model_cache_bytes_before':_cache_size(hf_cache), 'model_cache_path':str(hf_cache), 'synthetic_chunk_count':len(TEXTS), 'llm_calls':0, 'workers':[]}
    phases = ['src.storage.vector_store','chromadb','sentence_transformers','fake_cache']
    if not arguments.skip_model:
        phases.append('model_flow')
    for phase in phases:
        result = _supervise(phase, model, run_root/phase.replace('.','_'), arguments.timeout, env)
        report['workers'].append(result)
        (run_root/'rag_benchmark_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    report['model_cache_bytes_after'] = _cache_size(hf_cache)
    report['embedding_cache_bytes'] = _cache_size(run_root/'model_flow/embedding_cache')
    report['embedding_cache_files'] = len(list((run_root/'model_flow/embedding_cache').glob('*.json')))
    report['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    report['status'] = 'PASS' if all(worker['status']=='PASS' for worker in report['workers']) else 'PARTIAL'
    output = run_root/'rag_benchmark_report.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report_path':str(output), **report},ensure_ascii=False,indent=2))
    return 0 if report['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
