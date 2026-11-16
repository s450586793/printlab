"""HA device selection, scoped project writes and read-only asset responses."""
import pytest
from fastapi.testclient import TestClient

from printlab import core, devices, homeassistant, studio_bridge
from printlab.api import app


PRINTERS = [{'id': 'printer-a', 'name': 'A1', 'model': 'A1'},
            {'id': 'printer-p', 'name': 'P1P', 'model': 'P1P'}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    core.write_json(tmp_path / 'Runtime/config.json', {'device_provider': 'homeassistant'})
    monkeypatch.setattr(devices, 'device_list', lambda: {'devices': PRINTERS, 'state': 'online'})
    monkeypatch.setattr(devices, 'management_url', lambda: 'http://ha.example:8123')
    monkeypatch.setattr(devices, 'inventory', lambda: {'state': 'unavailable', 'items': []})
    def selected(printer_id=None):
        printer = next((row for row in PRINTERS if row['id'] == (printer_id or 'printer-a')), None)
        if printer is None:
            raise core.WorkflowError('Unknown printer')
        return {'state': 'online', 'printer': printer, 'message': 'HA 设备', 'stale': False}
    monkeypatch.setattr(devices, 'device', selected)
    return TestClient(app, base_url='http://127.0.0.1:8018')


def headers(client):
    token = client.get('/api/prepared/session').json()['csrf_token']
    return {'Origin': 'http://127.0.0.1:8018', 'X-PrintLab-CSRF': token}


def test_selecting_printer_changes_overview_without_changing_default(client):
    before = core.config()
    first = client.get('/api/overview?printer_id=printer-a').json()
    second = client.get('/api/overview?printer_id=printer-p').json()
    assert first['selected_printer_id'] == 'printer-a'
    assert second['device']['printer']['model'] == 'P1P'
    assert second['provider'] == 'homeassistant'
    assert second['management_url'] == 'http://ha.example:8123'
    assert second['devices'] == PRINTERS
    assert core.config() == before
    assert client.get('/api/device?printer_id=printer-p').json()['printer']['id'] == 'printer-p'
    assert client.get('/api/devices').json()['devices'] == PRINTERS
    assert client.get('/api/device?printer_id=missing').status_code == 404


def test_history_passes_device_selection_to_provider(client, monkeypatch):
    calls = []
    monkeypatch.setattr(devices, 'history_view', lambda printer_id=None: calls.append(printer_id) or {'items': [], 'source': 'homeassistant_cache'})
    assert client.get('/api/history?printer_id=printer-p').json()['source'] == 'homeassistant_cache'
    assert client.get('/api/history').status_code == 200
    assert calls == ['printer-p', None]


def test_project_binding_needs_same_origin_csrf_and_does_not_retarget_job(client):
    core.new_project('Device binding')
    p = core.project_path('P001')
    project = core.read_json(p / 'project.json')
    project['current_revision'] = 'v001'
    core.write_json(p / 'project.json', project)
    core.write_json(p / 'Exports/v001/build.json', {'verified': True, 'dimensions_mm': [20, 20, 20], 'checks': {'finite': True}})
    job = {'id': 'J1', 'printer_id': 'old-printer', 'artifact_sha256': 'approved-hash'}
    core.write_json(p / 'Print/J1/job.json', job)
    url = '/api/projects/P001/printer'
    assert client.post(url, json={'printer_id': 'printer-a'}).status_code == 403
    h = headers(client)
    assert client.post(url, headers={**h, 'Origin': 'http://evil.example'}, json={'printer_id': 'printer-a'}).status_code == 403
    result = client.post(url, headers=h, json={'printer_id': 'printer-p'})
    assert result.status_code == 200
    assert result.json()['target_printer']['id'] == 'printer-p'
    assert client.get('/api/projects/P001').json()['target_printer']['name'] == 'P1P'
    assert core.read_json(p / 'Print/J1/job.json') == job
    assert client.get('/api/projects').json()['projects'][0]['target_printer']['provider'] == 'homeassistant'


@pytest.mark.parametrize('payload', [{'printer_id': 12}, {'printer_id': ''}, {'printer_id': 'x' * 129}, {'printer_id': 'printer-a', 'execute': True}])
def test_project_binding_rejects_ambiguous_input(client, payload):
    core.new_project('Strict binding')
    assert client.post('/api/projects/P001/printer', headers=headers(client), json=payload).status_code == 422


def test_missing_printer_does_not_write_binding(client):
    core.new_project('Known device only')
    response = client.post('/api/projects/P001/printer', headers=headers(client), json={'printer_id': 'missing'})
    assert response.status_code == 409
    assert 'target_printer' not in core.read_json(core.project_path('P001') / 'project.json')


def test_ha_files_stay_local_and_use_safe_download_headers(client, monkeypatch):
    calls = []
    monkeypatch.setattr(homeassistant, 'asset', lambda serial, relative: calls.append((serial, relative)) or {
        'content': b'PK\x03\x04', 'content_type': 'application/octet-stream', 'filename': '中文模型.3mf'})
    response = client.get('/api/ha/files/example-serial/prints/model.3mf')
    assert response.status_code == 200
    assert response.content == b'PK\x03\x04'
    assert calls == [('example-serial', 'prints/model.3mf')]
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['content-disposition'].startswith("attachment; filename*=UTF-8''")
    assert 'Authorization' not in response.headers
    assert 'location' not in response.headers


def test_camera_proxy_and_provider_isolation(client, monkeypatch):
    monkeypatch.setattr(homeassistant, 'camera_image', lambda printer_id: {
        'content': b'image', 'content_type': 'image/jpeg', 'filename': 'camera.jpg'})
    response = client.get('/api/ha/camera/printer-a')
    assert response.status_code == 200
    assert response.headers['content-disposition'].startswith('inline;')
    core.write_json(core.ROOT / 'Runtime/config.json', {'device_provider': 'bambuddy'})
    assert client.get('/api/ha/camera/printer-a').status_code == 404
    assert client.get('/api/ha/files/example/prints/model.3mf').status_code == 404


def test_ha_sender_cannot_request_permissions_or_start(client, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('HA read connection must not call the Studio sender')
    monkeypatch.setattr(studio_bridge, 'capability', forbidden)
    monkeypatch.setattr(studio_bridge, 'setup_permissions', forbidden, raising=False)
    result = client.get('/api/prepared/sender/capability').json()
    assert result['available'] is False
    assert result['setup_required'] is False
    assert result['provider'] == 'homeassistant'
    assert client.post('/api/prepared/sender/enable', headers=headers(client), json={'request_permissions': True}).status_code == 409


def test_health_names_active_provider(client):
    data = client.get('/api/health').json()
    assert data['device_provider'] == 'homeassistant'
    assert 'home-assistant-read-v1' in data['features']
