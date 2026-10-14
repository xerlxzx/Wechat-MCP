import json

import pytest

from tests.integration.test_search import repo_case
from wechat_bridge.core.errors import BridgeError


def test_benchmark_reports_aggregate_warm_distribution_and_scope(tmp_path):
    from benchmarks.search import benchmark
    cfg, _ = repo_case(tmp_path)
    result = benchmark(cfg, samples=200, corpus_scope='synthetic_fixture')
    assert result['message_count'] == 3
    assert result['sample_count'] == 200
    assert result['warm_query_ms']['p95'] >= result['warm_query_ms']['p50'] >= 0
    assert result['archive_bytes'] > 0
    assert result['corpus_scope'] == 'synthetic_fixture'
    assert not result['representative_large_archive_claim']
    assert 'Known message' not in json.dumps(result)
    assert 'fictional' not in json.dumps(result)
    with pytest.raises(BridgeError, match='invalid_benchmark_samples'):
        benchmark(cfg, samples=199)
