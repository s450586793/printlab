"""Provider selection and target isolation use mocked data, never printer actions."""
import json
import sys
import types

import pytest

import printlab
from printlab import bambuddy, cli, core, devices, history, native, official, workbench


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    core.write_json(tmp_path / 'Runtime/config.json', {})
    return tmp_path


@pytest.fixture
def ha(workspace, monkeypatch):
    core.write_json(workspace / 'Runtime/config.json', {'device_provider': 'homeassistant'})
    calls = []
    listing = {'provider': 'homeassistant', 'state': 'online', 'devices': [
        {'id': 'printer-a', 'name': 'A1', 'model': 'A1'},
        {'id': 'printer-p', 'name': 'P1P', 'model': 'P1P'},
    ]}
    module = types.ModuleType('printlab.homeassistant')
    module.device_list = lambda: calls.append(('list',)) or listing
    module.device = lambda printer_id=None: calls.append(('device', printer_id)) or {'printer': {'id': printer_id}}
    module.inventory = lambda: calls.append(('inventory',)) or {'state': 'unavailable', 'items': []}
    module.history_view = lambda printer_id=None: calls.append(('history', printer_id)) or {'items': []}
    module.management_url = lambda: 'https://ha.example.invalid'
    monkeypatch.setitem(sys.modules, 'printlab.homeassistant', module)
    monkeypatch.setattr(printlab, 'homeassistant', module, raising=False)
    return module, calls


def test_provider_default_and_unknown_configuration(workspace):
    assert devices.provider() == 'bambuddy'
    devices.require_dispatch_supported()
    for value in ['unknown', '', None, [], True]:
        core.write_json(workspace / 'Runtime/config.json', {'device_provider': value})
        with pytest.raises(core.WorkflowError, match='Unsupported device provider'):
            devices.provider()


def test_bambuddy_facade_preserves_existing_data_and_refresh(workspace, monkeypatch):
    core.write_json(workspace / 'Runtime/config.json', {'printer_id': 7, 'bambuddy_url': 'http://buddy.example/'})
    observation = {'state': 'online', 'printer': {'name': 'Workshop', 'model': 'X2D', 'firmware_version': 'test'}, 'updated_at': 'stamp'}
    monkeypatch.setattr(bambuddy, 'device', lambda: observation)
    monkeypatch.setattr(bambuddy, 'inventory', lambda: {'items': ['spool']})
    refresh = []
    monkeypatch.setattr(history, 'view', lambda **kw: refresh.append(kw) or {'items': ['archive']})
    assert devices.device('7') is observation
    assert devices.inventory() == {'items': ['spool']}
    assert devices.history_view('7', force=True) == {'items': ['archive']}
    assert refresh == [{'force': True}]
    assert devices.management_url() == 'http://buddy.example'
    assert devices.device_list()['devices'] == [{'id': 7, 'name': 'Workshop', 'model': 'X2D', 'firmware_version': 'test'}]
    assert devices.slice_identity({}) == {'printer_id': 7, 'bambuddy_url': 'http://buddy.example/'}
    for function in [devices.device, devices.history_view]:
        with pytest.raises(core.WorkflowError, match='Unknown printer'):
            function('another-printer')


def test_unconfigured_bambuddy_list_does_not_invent_a_device(workspace, monkeypatch):
    monkeypatch.setattr(bambuddy, 'device', lambda: {'state': 'unconfigured', 'printer': None})
    assert devices.device_list()['devices'] == []
    assert devices.management_url() == 'http://127.0.0.1:8017'
    core.write_json(workspace / 'Runtime/config.json', {'printer_id': 2})
    assert devices.device_list()['devices'][0]['name'] == '拓竹打印机'


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'relative/path', 'http://', 'https://user:secret@buddy.example'])
def test_management_url_rejects_unusable_or_credential_urls(workspace, url):
    core.write_json(workspace / 'Runtime/config.json', {'bambuddy_url': url})
    with pytest.raises(core.WorkflowError, match='Invalid printer management URL'):
        devices.management_url()


def test_homeassistant_facade_routes_without_bambuddy(ha, monkeypatch):
    module, calls = ha
    monkeypatch.setattr(bambuddy, 'Client', lambda *a, **kw: pytest.fail('HA must not open Bambuddy'))
    assert devices.provider() == 'homeassistant'
    assert len(devices.device_list()['devices']) == 2
    assert devices.device('printer-p')['printer']['id'] == 'printer-p'
    assert devices.inventory()['state'] == 'unavailable'
    assert devices.history_view('printer-a', force=True) == {'items': []}
    assert devices.management_url() == 'https://ha.example.invalid'
    assert calls == [('list',), ('device', 'printer-p'), ('inventory',), ('history', 'printer-a')]
    with pytest.raises(core.WorkflowError, match='HA 连接'):
        devices.require_dispatch_supported()


def test_bind_project_sets_only_default_and_preserves_existing_job(ha, workspace):
    core.new_project('Two printer project')
    path = core.project_path('P001')
    old_job = path / 'Print/Jold/job.json'
    approval = path / 'Print/Jold/approval.json'
    core.write_json(old_job, {'id': 'Jold', 'printer_id': 'original', 'device_provider': 'bambuddy', 'state': 'approved'})
    core.write_json(approval, {'fingerprint': 'old-proof'})
    original_job, original_approval = old_job.read_bytes(), approval.read_bytes()
    before_config = (workspace / 'Runtime/config.json').read_bytes()
    chosen = core.bind_printer('P001', 'printer-a')
    assert chosen == core.detail('P001')['target_printer']
    assert chosen['name'] == 'A1' and chosen['provider'] == 'homeassistant'
    assert chosen['assigned_at']
    identity = devices.slice_identity(core.read_json(path / 'project.json'))
    core.bind_printer('P001', 'printer-p')
    assert core.detail('P001')['target_printer']['id'] == 'printer-p'
    assert identity['printer_id'] == 'printer-a'
    assert identity['target_printer']['id'] == 'printer-a'
    assert identity['ha_url'] == 'https://ha.example.invalid'
    assert identity['dispatch_available'] is False
    assert old_job.read_bytes() == original_job and approval.read_bytes() == original_approval
    assert (workspace / 'Runtime/config.json').read_bytes() == before_config


@pytest.mark.parametrize('printer_id', [None, '', True, [], {}, 'not-registered'])
def test_binding_rejects_invalid_or_unknown_device_without_changes(ha, printer_id):
    core.new_project('Binding errors')
    project_file = core.project_path('P001') / 'project.json'
    before = project_file.read_bytes()
    with pytest.raises(core.WorkflowError):
        core.bind_printer('P001', printer_id)
    assert project_file.read_bytes() == before


def test_binding_missing_project_fails_before_device_query(ha):
    _, calls = ha
    with pytest.raises(core.WorkflowError):
        core.bind_printer('P999', 'printer-a')
    assert calls == []


@pytest.mark.parametrize('target', [None, {}, {'provider': 'bambuddy', 'id': 'printer-a'}, {'provider': 'homeassistant', 'id': 7}])
def test_ha_slice_identity_requires_project_binding(ha, target):
    with pytest.raises(core.WorkflowError, match='选择 HA 打印机'):
        devices.slice_identity({'target_printer': target})


@pytest.mark.parametrize('entry', ['bambuddy', 'official', 'native', 'approve', 'native-check', 'native-run', 'start'])
def test_ha_dispatch_entries_stop_before_any_job_or_transport_access(ha, monkeypatch, entry):
    monkeypatch.setattr(core, 'job_path', lambda *a: pytest.fail('Must reject before touching task or transport'))
    functions = {
        'bambuddy': lambda: bambuddy.submit('P001', 'Jold', execute=True),
        'official': lambda: official.submit('P001', 'Jold', execute=True),
        'native': lambda: native.submit('P001', 'Jold', execute=True),
        'approve': lambda: core.approve('P001', 'Jold', '确认打印', True),
        'native-check': lambda: native.check('P001', 'Jold'),
        'native-run': lambda: native.run_client(core.ROOT, {}, 'print'),
        'start': lambda: workbench.start('P001', 'Jold', owner='browser', check_token='token', plate_clear=True, request_id='id'),
    }
    with pytest.raises(core.WorkflowError, match='HA 连接'):
        functions[entry]()


def test_ha_prepared_checks_are_blocked_without_old_preflight_or_ticket(ha, workspace, monkeypatch):
    monkeypatch.setattr(workbench.prepared, 'check', lambda *a: pytest.fail('Do not run X2D preflight for HA'))
    result = workbench.check('P001', 'Jold', owner='browser')
    assert not result['ready'] and not result['executed']
    assert result['blockers'][0]['id'] == 'ha_dispatch'
    assert not result['transport']['available']
    assert 'check_token' not in result
    assert not (workspace / 'Runtime/workbench/checks').exists()


def test_cli_reads_and_binding_use_selected_provider(ha, monkeypatch, capsys):
    _, calls = ha
    core.new_project('CLI selected target')
    assert cli.main(['devices']) == 0
    assert len(json.loads(capsys.readouterr().out)['devices']) == 2
    assert cli.main(['bind-printer', 'P001', 'printer-p']) == 0
    assert json.loads(capsys.readouterr().out)['model'] == 'P1P'
    assert cli.main(['status', '--printer', 'printer-p']) == 0
    assert json.loads(capsys.readouterr().out)['device']['printer']['id'] == 'printer-p'
    assert cli.main(['materials']) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'unavailable'
    assert cli.main(['history', '--printer', 'printer-a', '--refresh']) == 0
    assert json.loads(capsys.readouterr().out)['items'] == []
    assert ('history', 'printer-a') in calls
    monkeypatch.setattr(devices, 'inventory', lambda: {'state': 'unavailable', 'items': [], 'provider': 'homeassistant'})
    assert core.context('P001')['materials']['provider'] == 'homeassistant'


def test_ha_job_is_always_marked_unadapted_for_approval(ha):
    job = {'device_provider': 'homeassistant', 'printer_id': 'printer-a'}
    assert devices.HA_DISPATCH_MESSAGE in core.print_configuration_issues(job)


def test_provider_change_invalidates_job_before_file_inspection(ha, tmp_path):
    with pytest.raises(core.WorkflowError, match='Device provider changed'):
        core.job_fingerprint(tmp_path, tmp_path, {'device_provider': 'bambuddy'})


def test_ha_fingerprint_tracks_project_target_and_endpoint(ha, monkeypatch):
    module, _ = ha
    core.new_project('HA task identity')
    path = core.project_path('P001')
    core.bind_printer('P001', 'printer-a')
    project = core.read_json(path / 'project.json')
    project['current_revision'] = 'v001'
    core.write_json(path / 'project.json', project)
    directory = path / 'Print/Jha'
    directory.mkdir()
    artifact = directory / 'fixture.3mf'
    artifact.write_bytes(b'offline identity fixture; not sent to a printer')
    monkeypatch.setattr(core, 'validate_3mf', lambda *a: None)
    monkeypatch.setattr(core, 'revision_files', lambda *a: {})
    job = {**devices.slice_identity(project), 'id': 'Jha', 'revision': 'v001', 'artifact': artifact.name,
           'artifact_sha256': core.digest(artifact), 'source_hashes': {}, 'profile_hashes': {}, 'mapping': {}, 'plate': 1}
    fingerprint = core.job_fingerprint(path, directory, job)
    assert len(fingerprint) == 64
    assert core.job_fingerprint(path, directory, job) == fingerprint
    monkeypatch.setattr(module, 'management_url', lambda: 'https://different-ha.example.invalid')
    with pytest.raises(core.WorkflowError, match='HA endpoint changed'):
        core.job_fingerprint(path, directory, job)
    monkeypatch.setattr(module, 'management_url', lambda: 'https://ha.example.invalid')
    core.bind_printer('P001', 'printer-p')
    with pytest.raises(core.WorkflowError, match='Project target printer changed'):
        core.job_fingerprint(path, directory, job)


def test_bambuddy_binding_preserves_numeric_identifier(workspace, monkeypatch):
    core.write_json(workspace / 'Runtime/config.json', {'printer_id': 7})
    monkeypatch.setattr(bambuddy, 'device', lambda: {'state': 'online', 'printer': {'id': 7, 'name': 'Local', 'model': 'X2D'}})
    core.new_project('Legacy default target')
    target = core.bind_printer('P001', '7')
    assert target['id'] == 7 and target['provider'] == 'bambuddy'


def test_ha_context_and_doctor_report_configured_roster(ha):
    core.new_project('Context target')
    core.bind_printer('P001', 'printer-p')
    context = core.context('P001')
    assert context['device_provider'] == 'homeassistant'
    assert context['target_printer']['id'] == 'printer-p'
    assert len(context['devices']['devices']) == 2
    report = core.doctor()
    assert report['device_provider'] == 'homeassistant'
    assert report['printer_configured'] is True


def test_cli_rejects_unknown_binding_without_retargeting(ha, capsys):
    core.new_project('CLI invalid binding')
    assert cli.main(['bind-printer', 'P001', 'not-registered']) == 2
    assert 'Unknown printer' in json.loads(capsys.readouterr().err)['error']
    assert 'target_printer' not in core.summary(core.project_path('P001'))
