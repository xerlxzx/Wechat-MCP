import json


def test_missing_manifest_returns_setup_required(tmp_path, source_root, capsys):
    from wechat_bridge.core.config import create_config
    from wechat_bridge.cli import main
    cfg = create_config(tmp_path / "state", source_root)
    code = main(["--state-dir", str(cfg.state_dir), "verify-keys"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "setup_required"


def test_inspect_schema_prints_aggregates_only(tmp_path, capsys):
    from tests.support.schemas import account_case
    from wechat_bridge.cli import main
    cfg = account_case(tmp_path)
    assert main(["--state-dir", str(cfg.state_dir), "inspect-schema"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["schema_count"] == 6
    assert "fictional" not in output
    assert "Msg_" not in output
    assert str(cfg.account_root) not in output


def test_archive_cli_refresh_and_status_are_redacted(tmp_path, capsys):
    from tests.integration.test_importer import import_case
    from wechat_bridge.cli import main
    cfg = import_case(tmp_path)
    args = ['--state-dir', str(cfg.state_dir)]
    assert main(args + ['archive-status']) == 2
    assert json.loads(capsys.readouterr().out)['ready_for_local_queries'] is False
    assert main(args + ['archive-refresh']) == 0
    output = capsys.readouterr().out
    assert json.loads(output)['counts']['messages'] == 3
    assert 'missing_conversation_ids' not in output
    for private in ('fictional', '发票', 'Msg_', str(cfg.account_root), 'source_fingerprint'):
        assert private not in output
    assert main(args + ['archive-status']) == 0
    status = json.loads(capsys.readouterr().out)
    assert status['ready_for_local_queries']
    assert not status['stale']


def test_archive_cli_failure_preserves_prior_generation(tmp_path, capsys):
    from tests.integration.test_importer import import_case
    from wechat_bridge.cli import main
    cfg = import_case(tmp_path); args = ['--state-dir', str(cfg.state_dir)]
    assert main(args + ['archive-refresh']) == 0
    first = json.loads(capsys.readouterr().out)
    (cfg.state_dir / 'keys.json').unlink()
    assert main(args + ['archive-refresh']) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed['status'] == 'failed'
    assert failed['active_generation'] == first['active_generation']


def test_archive_watch_cancellation_is_redacted(tmp_path, capsys, monkeypatch):
    from tests.integration.test_importer import import_case, factory
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.cli import main
    cfg = import_case(tmp_path); refresh_archive(cfg, adapter_factory=factory)
    def interrupt(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr('wechat_bridge.archive.watcher.ArchiveWatcher.run', interrupt)
    assert main(['--state-dir', str(cfg.state_dir), 'archive-watch']) == 130
    assert json.loads(capsys.readouterr().out) == {'error': 'operation_cancelled'}


def test_search_cli_is_explicit_private_content_with_limit_validation(tmp_path, capsys):
    from tests.integration.test_importer import import_case
    from wechat_bridge.cli import main
    cfg = import_case(tmp_path); args = ['--state-dir', str(cfg.state_dir)]
    assert main(args + ['archive-refresh']) == 0; capsys.readouterr()
    assert main(args + ['search-index']) == 0; capsys.readouterr()
    assert main(args + ['search', '发票', '--limit', '1']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['items'][0]['excerpt'] == '发票 invoice'
    assert main(args + ['search', '发票', '--limit', '201']) == 2
    assert json.loads(capsys.readouterr().out) == {'error': 'invalid_limit'}


def test_server_startup_failure_never_prints_nonprotocol_stdout(tmp_path, capsys):
    from wechat_bridge.cli import main
    assert main(['--state-dir', str(tmp_path.resolve() / 'missing'), 'serve']) == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert json.loads(output.err) == {'error': 'invalid_private_state'}


def test_unexpected_exception_prints_redacted_internal_error(tmp_path, capsys, monkeypatch):
    from wechat_bridge.cli import main
    private = str(tmp_path / "private-account-folder")

    def fail(*args, **kwargs):
        raise PermissionError(13, "denied", private)
    monkeypatch.setattr("wechat_bridge.cli.discover", fail)
    assert main(["--state-dir", str(tmp_path / "state"), "discover"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error": "internal_error"}
    assert private not in captured.out + captured.err
