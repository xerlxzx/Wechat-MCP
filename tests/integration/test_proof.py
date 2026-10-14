from dataclasses import replace
import hashlib
import json
import pytest
from tests.support.proof import installed_build,proof_case


def test_complete_synthetic_account_passes_without_public_content(tmp_path):
    from wechat_bridge.proof import run_proof
    case=proof_case(tmp_path);report=run_proof(case.config,case.selection)
    assert report.status=='passed'
    assert all(check.status=='passed' for check in report.checks.values())
    public=json.dumps(report.public_dict())
    assert 'Known message' not in public
    assert 'fictional' not in public
    assert 'Msg_' not in public
    assert str(case.config.account_root) not in public
    assert report.versions['sqlcipher'].startswith('4.')
    assert report.observations
    assert report.evidence_path.stat().st_mode & 0o777==0o600


def test_missing_original_and_key_are_blocked_never_skipped_pass(tmp_path):
    from wechat_bridge.proof import run_proof
    case=proof_case(tmp_path);case.image_path.unlink()
    report=run_proof(case.config,case.selection)
    assert report.status=='blocked'
    assert report.checks['original_image'].status=='blocked'
    assert report.checks['ordinary_file'].status=='passed'
    (case.config.state_dir/'keys.json').unlink()
    assert run_proof(case.config,case.selection).checks['key_coverage'].status=='blocked'


def test_wrong_expected_message_and_bytes_fail(tmp_path):
    from wechat_bridge.proof import run_proof
    case=proof_case(tmp_path)
    samples=dict(case.selection.samples)
    samples['message']=samples['message']|{'expected':samples['message']['expected']|{'text':'Wrong'}}
    samples['ordinary_file']=samples['ordinary_file']|{'expected':{'source_sha256':'0'*64,'size':4}}
    report=run_proof(case.config,replace(case.selection,samples=samples))
    assert report.status=='failed'
    assert report.checks['message'].status=='failed'
    assert report.checks['ordinary_file'].status=='failed'


def test_unconfirmed_or_empty_expectations_cannot_pass(tmp_path):
    from wechat_bridge.proof import run_proof
    case=proof_case(tmp_path)
    assert run_proof(case.config,replace(case.selection,expectation_basis='pending_human_confirmation')).status=='blocked'
    samples=case.selection.samples|{'message':case.selection.samples['message']|{'expected':{}}}
    assert run_proof(case.config,replace(case.selection,samples=samples)).checks['message'].status=='blocked'


def test_missing_selected_message_is_blocked(tmp_path):
    from wechat_bridge.proof import run_proof
    case=proof_case(tmp_path);samples=dict(case.selection.samples)
    samples['message']=samples['message']|{'source':samples['message']['source']|{'local_id':'999'}}
    report=run_proof(case.config,replace(case.selection,samples=samples))
    assert report.checks['message'].status=='blocked'


def test_cancellation_publishes_no_partial_proof_and_changes_no_sources(tmp_path,monkeypatch):
    from wechat_bridge import proof
    case=proof_case(tmp_path)
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (case.config.account_root/'db_storage').rglob('*.db')}
    def interrupt(*args): raise KeyboardInterrupt
    monkeypatch.setattr(proof,'read_selected_message',interrupt)
    with pytest.raises(KeyboardInterrupt):proof.run_proof(case.config,case.selection)
    assert not list((case.config.state_dir/'proofs').glob('*.json'))
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==digest for p,digest in before.items())


def test_cli_proof_exit_codes_and_private_selection(tmp_path,capsys,monkeypatch):
    installed_build(monkeypatch,tmp_path)
    from wechat_bridge.core.config import write_private_json
    from wechat_bridge.cli import main
    case=proof_case(tmp_path);path=case.config.state_dir/'selection.json'
    case.selection=replace(case.selection,expectation_basis='user_confirmed')
    write_private_json(path,case.selection.private_dict())
    assert main(['--state-dir',str(case.config.state_dir),'prove','--selection',str(path)])==0
    assert json.loads(capsys.readouterr().out)['status']=='passed'
    case.image_path.unlink()
    assert main(['--state-dir',str(case.config.state_dir),'prove','--selection',str(path)])==2
    capsys.readouterr()
    samples=case.selection.samples|{'message':case.selection.samples['message']|{'expected':case.selection.samples['message']['expected']|{'text':'Wrong'}}}
    write_private_json(path,replace(case.selection,samples=samples).private_dict())
    assert main(['--state-dir',str(case.config.state_dir),'prove','--selection',str(path)])==1


def test_null_source_timestamp_cannot_satisfy_known_time_gate(tmp_path):
    from wechat_bridge.proof import run_proof
    from tests.support.encrypted import Engine
    from tests.support.schemas import TABLE
    case=proof_case(tmp_path)
    with Engine(case.config.account_root/'db_storage/message/message_0.db') as w:w.run(f'UPDATE {TABLE} SET create_time=NULL WHERE local_id=1')
    samples=case.selection.samples|{'message':case.selection.samples['message']|{'expected':case.selection.samples['message']['expected']|{'utc_timestamp':None}}}
    report=run_proof(case.config,replace(case.selection,samples=samples))
    assert report.status!='passed'
    assert report.checks['message'].status=='blocked'


@pytest.mark.parametrize('field,value',[('text',None),('sender_username',None),('sender_username',''),('utc_timestamp',False),('utc_timestamp','not-time')])
def test_required_expected_assertion_domains_cannot_be_waived(tmp_path,field,value):
    from wechat_bridge.proof import run_proof
    case=proof_case(tmp_path)
    samples=case.selection.samples|{'message':case.selection.samples['message']|{'expected':case.selection.samples['message']['expected']|{field:value}}}
    report=run_proof(case.config,replace(case.selection,samples=samples))
    assert report.checks['message'].status=='blocked'
    assert 'expected_values_invalid' in report.checks['message'].diagnostic_codes


def test_production_cli_rejects_fixture_basis_and_observes_actual_build(tmp_path,monkeypatch,capsys):
    from wechat_bridge.core.config import write_private_json
    from wechat_bridge.cli import main
    from wechat_bridge.adapters import wechat_4_1
    case=proof_case(tmp_path);path=case.config.state_dir/'selection.json'
    write_private_json(path,case.selection.private_dict())
    assert main(['--state-dir',str(case.config.state_dir),'prove','--selection',str(path)])==2
    out=json.loads(capsys.readouterr().out)
    assert out['error']=='synthetic_selection_not_allowed'
    selection=replace(case.selection,expectation_basis='user_confirmed')
    write_private_json(path,selection.private_dict())
    monkeypatch.setattr(wechat_4_1.plistlib,'load',lambda _: {'CFBundleShortVersionString':'9.9.9','CFBundleVersion':'999999'})
    assert main(['--state-dir',str(case.config.state_dir),'prove','--selection',str(path)])==2
    report=json.loads(capsys.readouterr().out)
    assert report['checks']['schema']['status']=='blocked'
    assert report['proof_scope']=='live'


def test_synthetic_proof_provenance_is_visible(tmp_path):
    from wechat_bridge.proof import run_proof
    case=proof_case(tmp_path);public=run_proof(case.config,case.selection).public_dict()
    assert public['proof_scope']=='synthetic'
    assert public['expectation_basis']=='synthetic_fixture'


def test_corrupt_png_does_not_abort_proof_or_print_traceback(tmp_path,capsys,monkeypatch):
    installed_build(monkeypatch,tmp_path)
    from tests.support.images import corrupt_png
    from wechat_bridge.proof import run_proof
    from wechat_bridge.core.config import write_private_json
    from wechat_bridge.cli import main
    case=proof_case(tmp_path);case.image_path.write_bytes(corrupt_png())
    report=run_proof(case.config,case.selection)
    assert report.checks['original_image'].status=='blocked'
    assert report.checks['original_image'].diagnostic_codes==('malformed_image',)
    assert report.checks['ordinary_file'].status=='passed'
    path=case.config.state_dir/'selection.json'
    write_private_json(path,replace(case.selection,expectation_basis='user_confirmed').private_dict())
    assert main(['--state-dir',str(case.config.state_dir),'prove','--selection',str(path)])==2
    captured=capsys.readouterr()
    assert json.loads(captured.out)['checks']['original_image']['diagnostic_codes']==['malformed_image']
    assert str(case.config.account_root) not in captured.out+captured.err
    assert 'Traceback' not in captured.out+captured.err
