"""Warm archive-query timings. Stdout contains aggregate metrics only."""
import argparse
import math
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import tempfile
import time
import json

from wechat_bridge.archive.store import Archive
from wechat_bridge.core.config import load_config
from wechat_bridge.core.errors import BridgeError
from wechat_bridge.search.repository import QueryRepository


def benchmark(config, *, samples=200, corpus_scope='live_observed', import_seconds=None):
    if type(samples) is not int or not 200 <= samples <= 10000:
        raise BridgeError('invalid_benchmark_samples')
    repo = QueryRepository(config); archive = Archive(config)
    with archive.connection() as db:
        conversation = db.execute('SELECT conversation_id FROM messages WHERE generation=(SELECT active_generation FROM archive_state) GROUP BY conversation_id ORDER BY count(*) DESC LIMIT 1').fetchone()
    calls = [('short_chinese', lambda: repo.search_messages('发票')),
             ('latin', lambda: repo.search_messages('invoice')),
             ('emoji', lambda: repo.search_messages('🐈')),
             ('no_match', lambda: repo.search_messages('missing literal 9f826b'))]
    if conversation:
        calls.append(('timeline', lambda: repo.get_messages(conversation[0])))
    for _, call in calls:
        call()
    elapsed = []
    for index in range(samples):
        start = time.perf_counter_ns(); calls[index % len(calls)][1]()
        elapsed.append((time.perf_counter_ns() - start) / 1_000_000)
    ordered = sorted(elapsed)
    percentile = lambda fraction: ordered[math.ceil(fraction * len(ordered)) - 1]
    paths = [archive.path, Path(str(archive.path) + '-wal'), Path(str(archive.path) + '-shm')]
    return {'corpus_scope': corpus_scope, 'message_count': archive.status()['counts'].get('messages', 0),
            'sample_count': samples, 'cache_state': 'warm_after_query_mix', 'query_mix': [name for name, _ in calls],
            'warm_query_ms': {'p50': round(statistics.median(elapsed), 3), 'p95': round(percentile(.95), 3),
                              'p99': round(percentile(.99), 3), 'maximum': round(max(elapsed), 3)},
            'archive_bytes': sum(path.stat().st_size for path in paths if path.exists()),
            'import_seconds': import_seconds, 'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
            'platform': platform.system(), 'architecture': platform.machine(), 'cpu_count': os.cpu_count(),
            'target_p95_ms': 250, 'representative_large_archive_claim': False,
            'includes_attachment_decoding': False, 'includes_model_inference': False,
            'includes_source_import_during_queries': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=200)
    parser.add_argument('--synthetic-records', type=int)
    args = parser.parse_args()
    if args.synthetic_records is None:
        result = benchmark(load_config(), samples=args.samples)
    else:
        if not 1000 <= args.synthetic_records <= 100000:
            raise BridgeError('invalid_synthetic_size')
        from tests.support.schemas import account_case, TABLE
        from tests.support.encrypted import Engine
        from wechat_bridge.adapters.wechat_4_1 import open_adapter
        from wechat_bridge.archive.importer import refresh_archive
        with tempfile.TemporaryDirectory(prefix='wechat-synthetic-benchmark-') as folder:
            config = account_case(Path(folder).resolve())
            with Engine(config.account_root / 'db_storage/message/message_0.db') as writer:
                writer.run('BEGIN')
                for start in range(1, args.synthetic_records + 1, 500):
                    values = ','.join(f"({n},{n},1,1,{1700000000+n},'发票 invoice 🐈 synthetic {n}',0)" for n in range(start, min(start + 500, args.synthetic_records + 1)))
                    writer.run(f'INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES' + values)
                writer.run('COMMIT')
            started = time.perf_counter()
            imported = refresh_archive(config, adapter_factory=lambda cfg: open_adapter(cfg, build='270102'))
            if imported['status'] != 'complete':
                raise BridgeError('synthetic_import_failed')
            result = benchmark(config, samples=args.samples, corpus_scope='synthetic_fixture', import_seconds=time.perf_counter() - started)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
