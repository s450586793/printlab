"""Printer planning uses verified model data and mocked devices, with no printer actions."""
import copy
import httpx
import pytest
from fastapi.testclient import TestClient

from printlab import agent_bridge, core, devices, printer_assignment
from printlab.api import app


PRINTERS = [
    {'id': 'a1', 'name': 'Studio A1', 'model': 'A1', 'connected': True},
    {'id': 'p1p', 'name': 'Studio P1P', 'model': 'P1P', 'connected': False},
]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    core.write_json(tmp_path / 'Runtime/config.json', {'device_provider': 'homeassistant'})
    monkeypatch.setattr(devices, 'device_list', lambda: {'devices': copy.deepcopy(PRINTERS), 'state': 'online'})
    core.new_project('Model before printer')
    return core.project_path('P001')


def built(path, dimensions=None, revision='v001', **changes):
    project = core.read_json(path / 'project.json')
    project['current_revision'] = revision
    core.write_json(path / 'project.json', project)
    manifest = {'revision': revision, 'verified': True, 'dimensions_mm': dimensions or [20, 30, 40],
                'checks': {'finite': True, 'watertight': True}}
    manifest.update(changes)
    core.write_json(path / 'Exports' / revision / 'build.json', manifest)
    return manifest


def headers(client):
    token = client.get('/api/prepared/session').json()['csrf_token']
    return {'Origin': 'http://127.0.0.1:8018', 'X-PrintLab-CSRF': token}


def test_new_project_does_not_inherit_current_or_first_printer(workspace, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Creating an idea must not inspect or select a printer')
    monkeypatch.setattr(devices, 'device_list', forbidden)
    monkeypatch.setattr(core, 'bind_printer', forbidden)
    project = agent_bridge.create_project('A new unassigned idea')
    assert project['id'] == 'P002'
    assert 'target_printer' not in project
    assert core.read_json(workspace / 'project.json').get('target_printer') is None


def test_legacy_explicit_creation_still_preserves_chosen_device(workspace):
    result = agent_bridge.create_project('Explicit historical client', printer_id='p1p')
    assert result['target_printer']['id'] == 'p1p'
    assert not result.get('current_revision')


def test_unassigned_project_context_does_not_claim_the_first_device_is_selected(workspace, monkeypatch):
    monkeypatch.setattr(devices, 'inventory', lambda: {'state': 'unknown', 'items': []})
    context = core.context('P001')
    assert context['target_printer'] is None
    assert context['devices']['devices'][0]['id'] == 'a1'
    assert '此项目尚未分配打印机' in context['printer_selection_note']
    assert '保守构建包络不代表已选择' in context['printer_selection_note']


def test_unbuilt_model_does_not_offer_a_first_device(workspace, monkeypatch):
    monkeypatch.setattr(devices, 'device_list', lambda: pytest.fail('Unbuilt models do not need device data'))
    result = printer_assignment.options('P001')
    assert result == {
        'ready': False, 'reason': '先完成模型并通过尺寸检查，再根据模型选择打印机。',
        'revision': None, 'dimensions_mm': None, 'target_printer': None,
        'selected_printer_id': None, 'assignment_current': False, 'options': [],
    }
    with pytest.raises(core.WorkflowError, match='先完成模型'):
        printer_assignment.select('P001', 'a1')


@pytest.mark.parametrize('changes', [
    {'verified': False}, {'verified': 1}, {'checks': {}}, {'checks': {'finite': False}},
    {'checks': []}, {'checks': {'finite': 1}},
    {'dimensions_mm': [20, 30]}, {'dimensions_mm': [0, 10, 10]},
    {'dimensions_mm': [-1, 10, 10]}, {'dimensions_mm': [True, 10, 10]},
    {'dimensions_mm': ['20', 10, 10]}, {'dimensions_mm': [float('inf'), 10, 10]},
    {'dimensions_mm': [float('nan'), 10, 10]}, {'dimensions_mm': {}},
    {'dimensions_mm': [10 ** 400, 10, 10]},
])
def test_unverified_or_invalid_dimensions_do_not_offer_devices(workspace, changes):
    built(workspace, **changes)
    result = printer_assignment.options('P001')
    assert result['ready'] is False and result['options'] == []


@pytest.mark.parametrize('value', ['../private', 'v1', 42, None])
def test_invalid_project_revision_is_not_used_as_a_path(workspace, value):
    data = core.read_json(workspace / 'project.json')
    data['current_revision'] = value
    core.write_json(workspace / 'project.json', data)
    assert printer_assignment.options('P001')['ready'] is False


def test_missing_malformed_or_linked_manifest_cannot_become_ready(workspace, tmp_path):
    built(workspace)
    manifest = workspace / 'Exports/v001/build.json'
    manifest.write_text('invalid json')
    assert printer_assignment.options('P001')['ready'] is False
    manifest.unlink()
    assert printer_assignment.options('P001')['ready'] is False
    secret = tmp_path / 'private-manifest.json'
    core.write_json(secret, {'verified': True, 'dimensions_mm': [1, 2, 3], 'checks': {'finite': True}})
    manifest.symlink_to(secret)
    assert printer_assignment.options('P001')['ready'] is False


def test_manifest_revision_must_match_current_project_revision(workspace):
    manifest = built(workspace)
    manifest['revision'] = 'v002'
    core.write_json(workspace / 'Exports/v001/build.json', manifest)
    assert printer_assignment.options('P001')['ready'] is False
    core.write_json(workspace / 'Exports/v001/build.json', [])
    assert printer_assignment.options('P001')['ready'] is False


def test_candidates_are_current_model_size_checks_not_print_permission(workspace):
    built(workspace)
    before = (workspace / 'project.json').read_bytes()
    result = printer_assignment.options('P001')
    assert result['ready'] is True and result['revision'] == 'v001'
    assert result['selected_printer_id'] is None
    assert len(result['options']) == 2
    first, second = result['options']
    assert first['fit'] == second['fit'] == 'fits'
    assert first['online'] is True and second['online'] is False
    assert first['dimensions_mm'] == [20, 30, 40]
    assert first['build_volume_mm'] == [256, 256, 256]
    assert first['material_fit'] == 'unknown'
    assert all(option['selection_available'] for option in result['options'])
    assert any('实际喷嘴' in reason for reason in first['reasons'])
    assert any('切刀挡块' in reason for reason in second['reasons'])
    assert all(option['specification_url'].startswith('https://') for option in result['options'])
    assert (workspace / 'project.json').read_bytes() == before


def test_model_dimensions_must_come_from_current_revision(workspace):
    built(workspace, [500, 20, 20], revision='v001')
    built(workspace, [20, 30, 40], revision='v002')
    assert all(row['fit'] == 'fits' for row in printer_assignment.options('P001')['options'])
    (workspace / 'Exports/v002/build.json').unlink()
    assert printer_assignment.options('P001')['ready'] is False


def test_current_orientation_over_limit_needs_review_not_an_absolute_print_claim(workspace):
    built(workspace, [257, 20, 20])
    result = printer_assignment.options('P001')
    assert all(row['fit'] == 'too_large' and not row['selection_available'] for row in result['options'])
    assert all(any('当前轴向' in reason and '调整摆放' in reason for reason in row['reasons']) for row in result['options'])
    with pytest.raises(core.WorkflowError, match='初筛'):
        printer_assignment.select('P001', 'a1')
    assert 'target_printer' not in core.read_json(workspace / 'project.json')


def test_p1p_nominal_height_does_not_override_default_slicer_height(workspace):
    built(workspace, [256, 256, 256])
    a1, p1p = printer_assignment.options('P001')['options']
    assert a1['fit'] == 'fits'
    assert p1p['fit'] == 'unknown' and not p1p['selection_available']
    assert any('250 mm' in reason for reason in p1p['reasons'])


def test_material_checks_only_use_structured_current_model_parameters(workspace):
    built(workspace)
    (workspace / 'brief.md').write_text('ABS PLA-CF and PETG appear in a reference, not a material choice.')
    assert all(row['material'] is None for row in printer_assignment.options('P001')['options'])
    core.write_json(workspace / 'Design/v001/params.json', {'material': 'PET'})
    a1, p1p = printer_assignment.options('P001')['options']
    assert not a1['selection_available'] and a1['material_fit'] == 'needs_review'
    assert p1p['selection_available'] and p1p['material_fit'] == 'manufacturer_recommended'
    core.write_json(workspace / 'Design/v001/params.json', {'material': 'ABS'})
    assert all(not row['selection_available'] for row in printer_assignment.options('P001')['options'])


@pytest.mark.parametrize('params', [[], {'material': 42}, {'material': ''}, {'material': None}])
def test_non_string_material_is_unknown(workspace, params):
    built(workspace)
    core.write_json(workspace / 'Design/v001/params.json', params)
    assert all(row['material'] is None for row in printer_assignment.options('P001')['options'])


def test_unknown_models_and_stale_connection_data_stay_unknown(workspace, monkeypatch):
    built(workspace)
    monkeypatch.setattr(devices, 'device_list', lambda: {'stale': True, 'devices': [
        {'id': 'new', 'model': 'Unknown', 'connected': True},
        {'id': 'a1', 'model': 'Bambu Lab A1', 'connected': True},
        {'id': None}, {'id': True}, 'bad row',
    ]})
    first, second = printer_assignment.options('P001')['options']
    assert first['fit'] == 'unknown' and not first['selection_available']
    assert second['fit'] == 'fits'
    assert first['online'] is second['online'] is None
    with pytest.raises(core.WorkflowError, match='未找到'):
        printer_assignment.select('P001', 'missing')


@pytest.mark.parametrize('error', [core.WorkflowError('no provider'), httpx.ConnectError('offline'), OSError('unavailable')])
def test_device_failures_preserve_model_readiness_without_choosing_a_printer(workspace, monkeypatch, error):
    built(workspace)
    def failed():
        raise error
    monkeypatch.setattr(devices, 'device_list', failed)
    result = printer_assignment.options('P001')
    assert result['ready'] is True and result['options'] == []
    assert '暂时无法读取' in result['reason']


def test_no_configured_devices_is_distinct_from_no_verified_model(workspace, monkeypatch):
    built(workspace)
    monkeypatch.setattr(devices, 'device_list', lambda: {'devices': []})
    result = printer_assignment.options('P001')
    assert result['ready'] is True and '尚未读取到' in result['reason']


@pytest.mark.parametrize('listing', [None, [], {'devices': None}, {'devices': {}}])
def test_malformed_device_lists_degrade_without_an_assignment(workspace, monkeypatch, listing):
    built(workspace)
    monkeypatch.setattr(devices, 'device_list', lambda: listing)
    result = printer_assignment.options('P001')
    assert result['ready'] is True and not result['options']
    assert '暂时无法读取' in result['reason']


def test_explicit_assignment_binds_revision_without_retargeting_any_existing_job(workspace):
    built(workspace)
    original = {'id': 'Jold', 'printer_id': 'original', 'state': 'approved', 'artifact_sha256': 'fixed'}
    core.write_json(workspace / 'Print/Jold/job.json', original)
    assigned = printer_assignment.select('P001', 'p1p')
    assert assigned['id'] == 'p1p' and assigned['assigned_revision'] == 'v001'
    assert core.read_json(workspace / 'Print/Jold/job.json') == original
    options = printer_assignment.options('P001')
    assert options['selected_printer_id'] == 'p1p' and options['assignment_current'] is True
    built(workspace, revision='v002')
    assert printer_assignment.options('P001')['assignment_current'] is False


def test_model_version_changed_during_assignment_requires_new_check(workspace, monkeypatch):
    built(workspace)
    original_bind = core.bind_printer
    def changed(pid, printer_id, *, revision):
        project = core.read_json(workspace / 'project.json')
        project['current_revision'] = 'v002'
        core.write_json(workspace / 'project.json', project)
        return original_bind(pid, printer_id, revision=revision)
    monkeypatch.setattr(core, 'bind_printer', changed)
    with pytest.raises(core.WorkflowError, match='模型版本已变化'):
        printer_assignment.select('P001', 'a1')
    assert 'target_printer' not in core.read_json(workspace / 'project.json')


def test_browser_entry_rejects_early_binding_then_accepts_verified_candidate(workspace):
    client = TestClient(app, base_url='http://127.0.0.1:8018')
    assert client.get('/api/projects/P001/printer-options').json()['ready'] is False
    assert client.post('/api/projects/P001/printer', json={'printer_id': 'a1'}).status_code == 403
    h = headers(client)
    assert client.post('/api/projects/P001/printer', headers=h, json={'printer_id': 'a1'}).status_code == 409
    built(workspace)
    assert client.get('/api/projects/P001/printer-options').json()['ready'] is True
    response = client.post('/api/projects/P001/printer', headers=h, json={'printer_id': 'a1'})
    assert response.status_code == 200 and response.json()['target_printer']['assigned_revision'] == 'v001'
    assert client.get('/api/projects/P999/printer-options').status_code == 404
