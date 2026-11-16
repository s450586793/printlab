import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from printlab import agent_bridge, core, prepared, project_lifecycle, shop
from printlab.api import app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    core.write_json(tmp_path / 'Runtime/config.json', {})
    core.new_project('保留项目')
    core.new_project('删除测试')
    monkeypatch.setattr(agent_bridge.Manager, 'launch', lambda *args: None)
    yield tmp_path
    agent_bridge.shutdown()


def test_delete_preserves_bytes_conversation_and_identity(workspace):
    path = core.project_path('P002')
    file = path / 'Sources/model.stl'
    file.write_bytes(b'original model')
    run = agent_bridge.submit('P002', 'Keep my conversation')
    agent_bridge.cancel('P002', run['id'])
    run_path = workspace / 'Runtime/agent/runs/P002' / (run['id'] + '.json')
    before = run_path.read_bytes()
    other = (core.project_path('P001') / 'project.json').read_bytes()
    result = project_lifecycle.delete_project('P002')
    assert result['id'] == 'P002' and result['deleted_at']
    assert project_lifecycle.delete_project('P002') == result
    assert file.read_bytes() == b'original model' and run_path.read_bytes() == before
    assert (core.project_path('P001') / 'project.json').read_bytes() == other
    assert [p['id'] for p in core.list_projects()] == ['P001']
    assert core.is_project_deleted('P002') and not core.is_project_deleted('P999')
    for identity in ['P002', '删除测试', path.name]:
        with pytest.raises(core.WorkflowError):
            core.detail(identity)
    with pytest.raises(core.WorkflowError):
        core.public_project_file('P002', 'Sources/model.stl')
    with pytest.raises(core.WorkflowError):
        agent_bridge.submit('P002', 'Must not start')
    assert core.new_project('新项目')['id'] == 'P003'


@pytest.mark.parametrize('status', sorted(agent_bridge.ACTIVE) + ['unexpected'])
def test_active_or_unknown_codex_refuses_delete(workspace, status):
    run = agent_bridge.submit('P002', 'Working')
    path = workspace / 'Runtime/agent/runs/P002' / (run['id'] + '.json')
    core.write_json(path, {**core.read_json(path), 'status': status})
    before = (core.project_path('P002') / 'project.json').read_bytes()
    with pytest.raises(project_lifecycle.DeleteError, match='Codex'):
        project_lifecycle.delete_project('P002')
    assert (core.project_path('P002') / 'project.json').read_bytes() == before


@pytest.mark.parametrize('state', ['slicing', 'dispatching', 'submitted', 'printing', 'running', 'preparing', 'paused', 'dispatch_unknown', 'staged_requires_reconciliation', 'unknown', 'unexpected'])
def test_active_or_unknown_print_refuses_delete(workspace, state):
    path = core.project_path('P002') / 'Print/J1/job.json'
    core.write_json(path, {'id': 'J1', 'project_id': 'P002', 'state': state})
    with pytest.raises(project_lifecycle.DeleteError, match='打印|切片'):
        project_lifecycle.delete_project('P002')
    assert not core.is_project_deleted('P002')


@pytest.mark.parametrize('state', ['checking', 'dispatching', 'unknown', 'unexpected'])
def test_unfinished_workbench_operation_refuses_delete(workspace, state):
    core.write_json(workspace / 'Runtime/workbench/operations/op.json', {'project_id': 'P002', 'state': state})
    with pytest.raises(project_lifecycle.DeleteError, match='打印'):
        project_lifecycle.delete_project('P002')


@pytest.mark.parametrize('state', ['awaiting_configuration', 'awaiting_approval', 'approved', 'failed', 'cancelled', 'completed', 'awaiting_physical_review', 'slice_failed', 'superseded'])
def test_inactive_print_allows_delete(workspace, state):
    core.write_json(core.project_path('P002') / 'Print/J1/job.json', {'state': state})
    assert project_lifecycle.delete_project('P002')['deleted_at']


def test_unknown_dispatch_requires_terminal_observation(workspace):
    directory = core.project_path('P002') / 'Print/J1'
    core.write_json(directory / 'job.json', {'state': 'failed'})
    core.write_json(directory / 'dispatch.json', {'state': 'unknown', 'queue_id': 42})
    with pytest.raises(project_lifecycle.DeleteError):
        project_lifecycle.delete_project('P002')
    core.write_json(directory / 'observation.json', {'queue': {'id': 42, 'status': 'failed'}})
    assert project_lifecycle.delete_project('P002')['deleted_at']


@pytest.mark.parametrize('state', ['submitted', 'observed'])
def test_sent_operation_needs_finished_job(workspace, state):
    core.write_json(workspace / 'Runtime/workbench/operations/op.json', {'project_id': 'P002', 'job_id': 'J1', 'state': state})
    with pytest.raises(project_lifecycle.DeleteError):
        project_lifecycle.delete_project('P002')
    core.write_json(core.project_path('P002') / 'Print/J1/job.json', {'state': 'awaiting_physical_review'})
    assert project_lifecycle.delete_project('P002')['deleted_at']


def test_incomplete_send_or_project_status_cannot_bypass_protection(workspace):
    path = core.project_path('P002')
    data = core.read_json(path / 'project.json')
    core.write_json(path / 'project.json', {**data, 'status': 'printing'})
    with pytest.raises(project_lifecycle.DeleteError):
        project_lifecycle.delete_project('P002')
    core.write_json(path / 'project.json', data)
    core.write_json(path / 'Print/J1/job.json', {'state': 'approved'})
    core.write_json(path / 'Print/J1/dispatch.json', {'state': 'start_requested'})
    with pytest.raises(project_lifecycle.DeleteError):
        project_lifecycle.delete_project('P002')


def test_invalid_identifier_is_rejected(workspace):
    for identity in ['删除测试', '../P002', None]:
        with pytest.raises(project_lifecycle.DeleteError):
            project_lifecycle.delete_project(identity)


def test_settled_dispatch_rejection_requires_matching_evidence(workspace):
    directory = core.project_path('P002') / 'Print/J1'
    receipt = {'state': 'unknown', 'error_type': 'signature'}
    core.write_json(directory / 'job.json', {'state': 'dispatch_rejected', 'reconciliation': {'state': 'settled'}})
    core.write_json(directory / 'dispatch.json', receipt)
    with pytest.raises(project_lifecycle.DeleteError):
        project_lifecycle.delete_project('P002')
    core.write_json(directory / 'dispatch-resolution.json', {'kind': 'signature_failure_settled_after_plate_clear', 'original_dispatch': receipt, 'next_job_id': 'J2'})
    assert project_lifecycle.delete_project('P002')['deleted_at']


def test_queued_handoff_rechecks_deleted_project(workspace, monkeypatch):
    original_lock = core.lock

    @contextmanager
    def deleted_before_lock(name='workflow'):
        if name == 'P002':
            path = core.project_path('P002') / 'project.json'
            core.write_json(path, {**core.read_json(path), 'deleted_at': core.now()})
        with original_lock(name):
            yield

    monkeypatch.setattr(core, 'lock', deleted_before_lock)
    with pytest.raises(core.WorkflowError, match='deleted'):
        core.handoff('P002', 'Must not write after deletion')


def test_stale_print_ticket_cannot_start_deleted_project(workspace, monkeypatch):
    from printlab import devices, workbench
    monkeypatch.setattr(devices, 'require_dispatch_supported', lambda: None)
    project_lifecycle.delete_project('P002')
    with pytest.raises(core.WorkflowError, match='deleted'):
        workbench.start('P002', 'J1', owner='test', check_token='x' * 32, plate_clear=True, request_id='a' * 32, launch=lambda operation: pytest.fail('No launch'))
    assert list((workspace / 'Runtime/workbench/operations').glob('*.json')) == []


def test_deleted_entries_hidden_but_inventory_and_history_retained(workspace):
    ledger = {'version': 1, 'items': {'part': {'id': 'part', 'name': '配件', 'existing': 3, 'purchased': 0, 'spec_verified': True}},
              'projects': {'P002': {'requirements': [{'id': 'r1', 'item_id': 'part', 'quantity': 2, 'note': ''}]}}, 'history': []}
    core.write_json(workspace / shop.STORE, ledger)
    core.write_json(workspace / prepared.REGISTRY, {'items': [{'project_id': 'P002', 'job_id': 'J1', 'active': True}], 'schema_version': 1})
    project_lifecycle.delete_project('P002')
    assert prepared.catalogue()['items'] == []
    view = shop.view()
    assert view['projects'] == {} and view['items'][0]['required'] == 0
    assert view['items'][0]['existing'] == 3 and shop.load() == ledger


def test_delete_api_requires_same_origin_session_csrf_and_matching_confirmation(workspace):
    client = TestClient(app, base_url='http://127.0.0.1:8018')
    url = '/api/projects/P002'
    body = {'project_id': 'P002'}
    assert client.request('DELETE', url, json=body).status_code == 403
    token = client.get('/api/prepared/session').json()['csrf_token']
    headers = {'Origin': 'http://127.0.0.1:8018', 'X-PrintLab-CSRF': token}
    for bad in [{'Origin': 'http://evil.example'}, {'X-PrintLab-CSRF': 'wrong'}, {'Sec-Fetch-Site': 'cross-site'}]:
        assert client.request('DELETE', url, headers={**headers, **bad}, json=body).status_code == 403
    assert client.request('DELETE', url, headers=headers, json={'project_id': 'P001'}).status_code == 409
    assert client.request('DELETE', url, headers=headers, json={}).status_code == 422
    assert client.request('DELETE', '/api/projects/P999', headers=headers, json={'project_id': 'P999'}).status_code == 404
    run = agent_bridge.submit('P002', 'Working')
    assert client.request('DELETE', url, headers=headers, json=body).status_code == 409
    agent_bridge.cancel('P002', run['id'])
    result = client.request('DELETE', url, headers=headers, json=body)
    assert result.status_code == 200 and result.json()['id'] == 'P002'
    assert client.request('DELETE', url, headers=headers, json=body).json() == result.json()
    assert client.get(url).status_code == 404
    assert client.post(url + '/agent/runs', headers=headers, json={'message': 'No'}).status_code == 404
    assert client.get('/api/projects/P001').status_code == 200


def test_submission_waiting_on_delete_cannot_restart_project(workspace, monkeypatch):
    original_lock = core.lock
    acquired, release, waiting = threading.Event(), threading.Event(), threading.Event()

    @contextmanager
    def gated_lock(name='workflow'):
        if name == 'agent-schedule-P002' and threading.current_thread().name.endswith('_1'):
            waiting.set()
        with original_lock(name):
            if name == 'P002':
                acquired.set()
                assert release.wait(5)
            yield

    monkeypatch.setattr(core, 'lock', gated_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        delete = pool.submit(project_lifecycle.delete_project, 'P002')
        assert acquired.wait(5)
        submit = pool.submit(agent_bridge.submit, 'P002', 'Too late')
        assert waiting.wait(5)
        release.set()
        assert delete.result(timeout=5)['deleted_at']
        with pytest.raises(core.WorkflowError):
            submit.result(timeout=5)
    assert list((workspace / 'Runtime/agent/runs/P002').glob('*.json')) == []
