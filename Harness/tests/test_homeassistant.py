import copy
import io
import json
import os
import time

import httpx
from PIL import Image
import pytest

from printlab import core, homeassistant as ha


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    core.write_json(tmp_path / 'Runtime/config.json', {})
    ha._save_credentials({'base_url': 'http://ha.test:8123', 'access_token': 'PRIVATE-ACCESS',
                          'refresh_token': 'PRIVATE-REFRESH', 'client_id': 'http://ha.test:8123/',
                          'expires_at': time.time() + 1800})
    ha.clear_cache()
    yield tmp_path
    ha.clear_cache()


@pytest.fixture
def snapshot():
    data = {
        'entries': [{'entry_id': 'entry-a', 'domain': 'bambu_lab'}, {'entry_id': 'entry-b', 'domain': 'bambu_lab'}],
        'manifest': {'version': '2.2.26'},
        'devices': [
            {'id': 'printer-a', 'name': 'A1', 'model': 'A1', 'sw_version': '1.08', 'config_entries': ['entry-a'], 'identifiers': [['bambu_lab', 'SERIALA123']]},
            {'id': 'ams-a', 'name': 'AMS Lite', 'model': 'AMS Lite', 'via_device_id': 'printer-a', 'config_entries': ['entry-a'], 'identifiers': [['bambu_lab', 'SERIALA123_AMS']]},
            {'id': 'printer-b', 'name': 'P1P', 'model': 'P1P', 'sw_version': '1.10', 'config_entries': ['entry-b'], 'identifiers': [['bambu_lab', 'SERIALB456']]},
            {'id': 'unrelated', 'name': 'PRIVATE OTHER DEVICE', 'model': 'Switch', 'config_entries': ['other'], 'identifiers': [['other', 'private']]},
        ], 'registry': [], 'states': [], 'updated_at': '2026-11-12T00:00:00+00:00',
    }
    def add(device, key, value, attrs=None, domain='sensor', disabled=None, entity_id=None):
        entity_id = entity_id or f'{domain}.{device.replace("-", "_")}_{key}'
        data['registry'].append({'entity_id': entity_id, 'unique_id': device + '_' + key, 'device_id': device, 'disabled_by': disabled})
        data['states'].append({'entity_id': entity_id, 'state': value, 'attributes': attrs or {}, 'last_reported': '2026-11-09T00:00:00+00:00'})
    for printer in ('printer-a', 'printer-b'):
        for key, value in {'online': 'on', 'print_status': 'finish', 'current_stage': 'idle', 'print_progress': '100',
                           'task_name': '测试模型', 'nozzle_temperature': '18', 'remaining_time': '1.5',
                           'current_layer': '4', 'total_layer_count': '4', 'developer_lan_mode': 'on',
                           'mqtt_connection_mode': 'local', 'nozzle_size': '0.4', 'nozzle_type': 'hardened_steel'}.items():
            add(printer, key, value, {'unit_of_measurement': 'h'} if key == 'remaining_time' else {})
        add(printer, 'bed_temperature', '15', entity_id=f'sensor.renamed_{printer.replace("-", "_")}')
        add(printer, 'camera', 'streaming', {'access_token': 'PRIVATE-CAMERA', 'entity_picture': '?token=PRIVATE'}, domain='camera')
    add('ams-a', 'tray_1', 'Bambu PLA Matte', {'slot': 1, 'type': 'PLA', 'color': '#FFF144FF', 'remain': 100, 'remain_enabled': True})
    add('ams-a', 'tray_2', 'Generic PETG', {'slot': 2, 'type': 'PETG', 'color': '#FFFFFF', 'remain': -1, 'remain_enabled': True})
    add('ams-a', 'tray_3', 'Empty', {'slot': 3, 'type': 'Empty', 'remain': 100, 'remain_enabled': True})
    add('ams-a', 'tray_4', 'unavailable', {'slot': 4, 'type': 'PLA', 'remain': 100, 'remain_enabled': True})
    add('ams-a', 'tray_5', 'PRIVATE DISABLED', {'slot': 5, 'type': 'PLA'}, disabled='user')
    return data


def png():
    output = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(output, format='PNG')
    return output.getvalue()


def fake_client(monkeypatch, files=None, handler=None):
    files = files if files is not None else {
        'SERIALA123': [{'filename': '样件.3mf', 'path': 'SERIALA123/prints/cache/样件.3mf', 'size': 100,
                        'modified': '2026-11-10T01:00:00', 'thumbnail_path': 'SERIALA123/prints/cache/样件.png'}],
        'SERIALB456': [{'filename': 'part.3mf', 'path': 'SERIALB456/prints/part.3mf', 'size': 200,
                        'modified': '2026-11-11T01:00:00', 'thumbnail_path': None}],
    }
    calls = []
    def transport(request):
        calls.append(request)
        if handler:
            return handler(request)
        if request.url.path.endswith('.3mf'):
            return httpx.Response(200, content=b'PK\x03\x04model')
        return httpx.Response(200, content=png())
    class FakeClient:
        settings = {'base_url': 'http://ha.test:8123', 'access_token': 'PRIVATE-ACCESS', 'refresh_token': 'PRIVATE-REFRESH'}
        def __init__(self):
            self.http = httpx.Client(base_url=self.settings['base_url'], transport=httpx.MockTransport(transport))
        def get(self, path, **kwargs):
            calls.append((path, kwargs))
            assert path == '/api/bambu_lab/print_history'
            return {'files': files[kwargs['params']['serial']]}
        def close(self): self.http.close()
    monkeypatch.setattr(ha, 'Client', FakeClient)
    return calls


@pytest.mark.parametrize('value', ['ftp://ha.test', 'http://user:password@ha.test', 'http://ha.test/path',
                                  'http://ha.test?token=PRIVATE', 'https://ha.test#PRIVATE', 'http://ha.test:999999', None])
def test_invalid_management_urls_are_rejected_without_echoing_credentials(value):
    with pytest.raises(core.WorkflowError) as error:
        ha._url(value)
    assert 'PRIVATE' not in str(error.value) and 'password' not in str(error.value)


def test_connect_uses_normal_oauth_and_never_stores_password(workspace, monkeypatch):
    requests = []
    def transport(request):
        requests.append(request)
        response = {'flow_id': 'flow'} if request.url.path == '/auth/login_flow' else {'type': 'create_entry', 'result': 'code'} if '/auth/login_flow/' in request.url.path else {'access_token': 'NEW-ACCESS', 'refresh_token': 'NEW-REFRESH', 'expires_in': 1800}
        return httpx.Response(200, json=response)
    monkeypatch.setattr(ha, '_http', lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(transport)))
    result = ha.connect('http://ha.test:8123/', 'user', 'PASSWORD-PRIVATE')
    saved = json.loads((workspace / 'Runtime/private/homeassistant.json').read_text())
    assert result == {'connected': True, 'base_url': 'http://ha.test:8123', 'provider': 'homeassistant'}
    assert saved['refresh_token'] == 'NEW-REFRESH' and saved['expires_at'] > time.time()
    assert 'PASSWORD-PRIVATE' not in json.dumps(saved) + json.dumps(result)
    if os.name != 'nt':
        assert (workspace / 'Runtime/private/homeassistant.json').stat().st_mode & 0o777 == 0o600
    assert [r.url.path for r in requests] == ['/auth/login_flow', '/auth/login_flow/flow', '/auth/token']


@pytest.mark.parametrize('login,expected', [({'type': 'form', 'step_id': 'mfa'}, '额外登录验证'), ({'type': 'form', 'step_id': 'init'}, '核对账户')])
def test_incomplete_login_reports_action_without_secrets(workspace, monkeypatch, login, expected):
    def transport(request):
        return httpx.Response(200, json={'flow_id': 'flow'} if request.url.path == '/auth/login_flow' else login)
    monkeypatch.setattr(ha, '_http', lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(transport)))
    with pytest.raises(core.WorkflowError, match=expected):
        ha.connect('http://ha.test', 'user', 'PASSWORD-PRIVATE')


def test_login_transport_failure_is_redacted(workspace, monkeypatch):
    def fail(base): raise httpx.ConnectError('PRIVATE transport address')
    monkeypatch.setattr(ha, '_http', fail)
    with pytest.raises(core.WorkflowError) as error:
        ha.connect('http://ha.test', 'user', 'PASSWORD-PRIVATE')
    assert 'PRIVATE' not in str(error.value)


def test_token_refresh_is_private_and_failures_are_redacted(workspace, monkeypatch):
    saved = ha._settings(); saved['expires_at'] = 0; ha._save_credentials(saved)
    def transport(request):
        assert request.url.path == '/auth/token' and b'grant_type=refresh_token' in request.content
        return httpx.Response(200, json={'access_token': 'REFRESHED', 'expires_in': 1800})
    monkeypatch.setattr(ha, '_http', lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(transport)))
    assert ha._authenticated_settings()['access_token'] == 'REFRESHED'
    saved = ha._settings(); saved['expires_at'] = 0; ha._save_credentials(saved)
    monkeypatch.setattr(ha, '_http', lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(lambda req: httpx.Response(401, text='PRIVATE REFRESH'))))
    with pytest.raises(core.WorkflowError) as error:
        ha._authenticated_settings()
    assert 'PRIVATE' not in str(error.value)
    saved.pop('refresh_token'); ha._save_credentials(saved)
    with pytest.raises(core.WorkflowError, match='过期'):
        ha._authenticated_settings()


@pytest.mark.parametrize('payload', [[], None, 'PRIVATE malformed refresh'])
def test_malformed_refresh_response_is_reported_without_raw_upstream_data(workspace, monkeypatch, payload):
    saved = ha._settings(); saved['expires_at'] = 0; ha._save_credentials(saved)
    monkeypatch.setattr(ha, '_http', lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload))))
    with pytest.raises(core.WorkflowError) as error:
        ha._authenticated_settings()
    assert 'PRIVATE' not in str(error.value)


def test_real_client_keeps_authorization_in_headers_and_only_reads(workspace, monkeypatch):
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={'files': []})
    monkeypatch.setattr(ha, '_http', lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(transport)))
    client = ha.Client()
    try:
        assert client.get('/api/bambu_lab/print_history', params={'serial': 'SERIALA123'}) == {'files': []}
    finally:
        client.close()
    assert requests[0].headers['Authorization'] == 'Bearer PRIVATE-ACCESS'
    assert 'PRIVATE' not in str(requests[0].url)


def test_unconfigured_settings_and_missing_access_token_are_graceful(workspace):
    path = workspace / 'Runtime/private/homeassistant.json'
    path.unlink()
    assert ha.management_url() is None
    assert ha.device_list()['state'] == 'unconfigured'
    assert ha.device()['dispatch_available'] is False
    path.write_text('[]')
    assert ha.management_url() is None
    path.write_text('{"base_url":"http://ha.test"}')
    with pytest.raises(core.WorkflowError, match='凭据'):
        ha._authenticated_settings()


def test_websocket_only_requests_read_metadata_and_states(workspace, snapshot, monkeypatch):
    commands, closed = [], []
    class Socket:
        frames = [{'type': 'auth_required'}, {'type': 'auth_ok'}] + [
            {'id': index, 'success': True, 'result': snapshot[name]} for index, name in enumerate(('entries', 'manifest', 'devices', 'registry', 'states'), 1)]
        def recv(self): return json.dumps(self.frames.pop(0))
        def send(self, data): commands.append(json.loads(data))
        def settimeout(self, value): assert 0 < value <= 20
        def close(self): closed.append(True)
    def create(url, **kwargs):
        assert url == 'ws://ha.test:8123/api/websocket'
        assert kwargs['redirect_limit'] == 0 and kwargs['http_no_proxy'] == ['*']
        return Socket()
    monkeypatch.setattr(ha.websocket, 'create_connection', create)
    class Client:
        settings = ha._settings()
    result = ha._read_snapshot(Client())
    assert result['states'] == snapshot['states'] and closed == [True]
    assert [row['type'] for row in commands] == ['auth', 'config_entries/get', 'manifest/get', 'config/device_registry/list', 'config/entity_registry/list', 'get_states']
    assert all(row['type'] != 'call_service' for row in commands)


def test_socket_auth_failure_and_timeout_close_connection(workspace, monkeypatch):
    closed = []
    class Socket:
        def recv(self): return json.dumps({'type': 'auth_invalid'})
        def send(self, data): pass
        def settimeout(self, value): pass
        def close(self): closed.append(True)
    monkeypatch.setattr(ha.websocket, 'create_connection', lambda *args, **kwargs: Socket())
    class Client: settings = ha._settings()
    with pytest.raises(ValueError): ha._read_snapshot(Client())
    stamps = iter([0, 25])
    monkeypatch.setattr(ha.time, 'monotonic', lambda: next(stamps))
    with pytest.raises(core.WorkflowError, match='超时'): ha._read_snapshot(Client())
    assert len(closed) == 2


def test_snapshot_cache_is_private_deep_copied_and_marks_network_failure(workspace, snapshot, monkeypatch):
    calls = []
    class Client:
        def __init__(self): self.settings = ha._settings()
        def close(self): pass
    monkeypatch.setattr(ha, 'Client', Client)
    def read(client): calls.append(True); return copy.deepcopy(snapshot)
    monkeypatch.setattr(ha, '_read_snapshot', read)
    first = ha._snapshot(); first['devices'].clear()
    assert len(ha._snapshot()['devices']) == 4 and len(calls) == 1
    key = ha._identity(ha._settings()); ha._snapshots[key] = (time.monotonic() - 20, snapshot)
    monkeypatch.setattr(ha, '_read_snapshot', lambda client: (_ for _ in ()).throw(OSError('PRIVATE')))
    assert ha._snapshot()['_stale'] is True
    ha.clear_cache()
    with pytest.raises(core.WorkflowError) as error: ha._snapshot()
    assert 'PRIVATE' not in str(error.value)


def test_devices_and_ams_use_real_entity_values_without_x2d_routes(workspace, snapshot, monkeypatch):
    monkeypatch.setattr(ha, '_snapshot', lambda: copy.deepcopy(snapshot))
    listing = ha.device_list()
    assert [row['name'] for row in listing['devices']] == ['A1', 'P1P']
    assert all(row['connected'] for row in listing['devices'])
    result = ha.device('printer-a')
    assert result['printer']['remaining_time'] == 90
    assert result['printer']['temperatures'] == {'nozzle': 18, 'bed': 15}
    assert result['printer']['state'] == 'FINISH' and result['dispatch_available'] is False
    assert result['telemetry_age_seconds'] is None
    assert result['camera_image_url'] == '/api/ha/camera/printer-a'
    slots = result['material_slots']['modules'][0]['slots']
    assert len(slots) == 4 and [s['state'] for s in slots] == ['loaded', 'loaded', 'empty', 'unknown']
    assert [s['reported_remaining_percent'] for s in slots] == [100, None, None, None]
    assert result['material_slots']['fresh'] is False and result['material_slots']['source_timestamp'] is None
    assert all('左' not in s['route']['text'] and '右' not in s['route']['text'] for s in slots)
    assert 'PRIVATE' not in json.dumps(result) + json.dumps(listing)
    assert ha.device('printer-b')['material_slots']['modules'] == []
    assert ha.inventory()['items'] == [] and ha.inventory()['state'] == 'unavailable'


def test_selection_disabled_states_stale_and_unknown_values(workspace, snapshot, monkeypatch):
    core.write_json(workspace / 'Runtime/config.json', {'ha_printer_id': 'printer-b'})
    snapshot['_stale'] = True
    for row in snapshot['states']:
        if row['entity_id'].endswith('_remaining_time'):
            row['state'] = '120'; row['attributes']['unit_of_measurement'] = 's'
    monkeypatch.setattr(ha, '_snapshot', lambda: snapshot)
    assert ha.device()['printer']['id'] == 'printer-b'
    assert ha.device()['printer']['remaining_time'] == 2
    assert ha.device()['state'] == 'offline' and ha.device()['stale'] is True
    assert ha.device_list()['state'] == 'unavailable'
    with pytest.raises(core.WorkflowError): ha.device('missing')
    assert ha._number(True) is None and ha._number('nan') is None and ha._number('bad') is None


def test_current_temperatures_never_resolve_to_targets_registered_first(workspace, snapshot, monkeypatch):
    for printer in ('printer-a', 'printer-b'):
        for name in ('nozzle_temperature', 'bed_temperature'):
            entity_id = f'sensor.{printer.replace("-", "_")}_target_{name}'
            snapshot['registry'].insert(0, {'entity_id': entity_id, 'unique_id': f'{printer}_sensor_target_{name}', 'device_id': printer})
            snapshot['states'].insert(0, {'entity_id': entity_id, 'state': '0', 'attributes': {}})
        # The other conventional target naming order must also stay separate.
        entity_id = f'sensor.{printer.replace("-", "_")}_nozzle_target_temperature'
        snapshot['registry'].insert(0, {'entity_id': entity_id, 'unique_id': f'{printer}_nozzle_target_temperature', 'device_id': printer})
        snapshot['states'].insert(0, {'entity_id': entity_id, 'state': '0', 'attributes': {}})
    monkeypatch.setattr(ha, '_snapshot', lambda: snapshot)
    assert ha.device('printer-a')['printer']['temperatures'] == {'nozzle': 18, 'bed': 15}
    assert ha.device('printer-b')['printer']['temperatures'] == {'nozzle': 18, 'bed': 15}
    target = ({'entity_id': 'sensor.device_target_bed_temperature', 'unique_id': 'serial_sensor_target_bed_temperature'}, {'state': '0'})
    assert ha._find([target], 'bed_temperature') == {}
    assert ha._find([({'unique_id': 'target_nozzle_temperature'}, {'state': '0'})], 'nozzle_temperature') == {}


def test_malformed_entity_attributes_are_unknown_not_invented(workspace, snapshot, monkeypatch):
    for row in snapshot['states']:
        if row['entity_id'].endswith('_online'):
            row['state'] = {'not': 'a state'}
        if row['entity_id'].endswith('_tray_1'):
            row['attributes'] = []
    monkeypatch.setattr(ha, '_snapshot', lambda: snapshot)
    result = ha.device('printer-a')
    assert result['state'] == 'offline'
    assert len(result['material_slots']['modules'][0]['slots']) == 3


def test_cached_files_are_not_print_runs_and_urls_do_not_contain_tokens(workspace, snapshot, monkeypatch):
    monkeypatch.setattr(ha, '_snapshot', lambda: snapshot)
    calls = fake_client(monkeypatch)
    result = ha.history_view()
    assert result['cache_count'] == 2 and result['source_kind'] == 'file_cache'
    assert result['counts']['prints'] == result['counts']['completed'] == 0
    assert all(row['status'] == 'archived' and row['is_print_event'] is False for row in result['items'])
    assert result['items'][0]['printer_name'] == 'P1P'
    assert ha.history_view('printer-a')['cache_count'] == 1
    assert len(calls) == 2  # The printer cache lists are reused for 15 seconds.
    assert 'PRIVATE' not in json.dumps(result) and 'http://ha.test' not in json.dumps(result)
    row = result['items'][1]
    assert '%E6%A0%B7%E4%BB%B6' in row['download_url']
    assert row['model_preview']['kind'] == 'cached_model'


def test_history_skips_malformed_metadata_and_reports_failure(workspace, snapshot, monkeypatch):
    monkeypatch.setattr(ha, '_snapshot', lambda: snapshot)
    fake_client(monkeypatch, {'SERIALA123': [None, {'path': 'SERIALA123/../PRIVATE.3mf'},
        {'path': 'OTHER/prints/private.3mf'}, {'path': 'SERIALA123/prints/image.png'},
        {'path': 'SERIALA123/prints/file.3mf', 'thumbnail_path': 'OTHER/prints/private.png', 'modified': 'invalid'}]})
    result = ha.history_view('printer-a')
    assert len(result['items']) == 1 and result['items'][0]['model_preview'] is None
    assert result['items'][0]['created_at'] is None
    class Failing:
        def __init__(self): raise httpx.ConnectError('PRIVATE')
    monkeypatch.setattr(ha, 'Client', Failing)
    result = ha.history_view()
    assert result['state'] == 'unavailable' and 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('relative', ['../private.json', '/prints/file.3mf', 'prints/../private.3mf',
    'prints/%2e%2e/private.3mf', 'prints/%252e%252e/private.3mf', 'prints//file.3mf',
    'prints/a%2fb.3mf', 'prints/a%252fb.3mf', 'prints/a%5cb.3mf', 'prints/a%255cb.3mf',
    'prints/%00file.3mf', 'prints/%2500file.3mf', 'prints/%25252e%25252e/private.3mf',
    'prints/file.3mf?token=PRIVATE', 'prints/file.svg', 'prints\\file.3mf', 'prints/a\x00.3mf'])
def test_asset_paths_reject_traversal_and_active_content(relative):
    with pytest.raises(core.WorkflowError): ha.asset('SERIALA123', relative)


def test_assets_only_serve_registered_cache_files_and_validate_images(workspace, snapshot, monkeypatch):
    monkeypatch.setattr(ha, '_snapshot', lambda: snapshot)
    calls = fake_client(monkeypatch)
    model = ha.asset('SERIALA123', 'prints/cache/样件.3mf')
    assert model['content'].startswith(b'PK') and model['filename'] == '样件.3mf'
    assert ha.asset('SERIALA123', 'prints/cache/样件.png')['content_type'] == 'image/png'
    with pytest.raises(core.WorkflowError): ha.asset('SERIALA123', 'prints/unknown.3mf')
    with pytest.raises(core.WorkflowError): ha.asset('NOTREGISTERED', 'prints/cache/样件.3mf')
    requests = [request for request in calls if isinstance(request, httpx.Request)]
    assert all(request.method == 'GET' for request in requests)
    assert '%E6%A0%B7%E4%BB%B6' in str(requests[0].url)


def test_literal_percent_in_chinese_slicer_filename_is_listed_and_encoded_once(workspace, snapshot, monkeypatch):
    monkeypatch.setattr(ha, '_snapshot', lambda: snapshot)
    name = '2136396-0.2mm 层高, 2 层墙, 15% 填充.3mf'
    relative = 'prints/cache/' + name
    calls = fake_client(monkeypatch, {'SERIALA123': [{'filename': name, 'path': 'SERIALA123/' + relative,
        'size': 100, 'modified': '2026-11-10T01:00:00', 'thumbnail_path': None}]})
    history = ha.history_view('printer-a')
    assert history['cache_count'] == 1
    assert '15%25%20' in history['items'][0]['download_url']
    assert '%2525' not in history['items'][0]['download_url']
    assert ha.asset('SERIALA123', relative)['filename'] == name
    request = next(row for row in calls if isinstance(row, httpx.Request))
    assert b'15%25%20' in request.url.raw_path and b'%2525' not in request.url.raw_path


@pytest.mark.parametrize('body,headers,limit', [(b'not a model', {}, 1500000), (b'PK\x03\x04long', {}, 5), (b'PK\x03\x04', {'content-length': '9999999'}, 5)])
def test_bad_or_oversized_model_downloads_are_rejected(workspace, snapshot, monkeypatch, body, headers, limit):
    monkeypatch.setattr(ha, '_snapshot', lambda: snapshot)
    monkeypatch.setattr(ha, 'MAX_FILE_BYTES', limit)
    fake_client(monkeypatch, handler=lambda request: httpx.Response(200, content=body, headers=headers))
    with pytest.raises(core.WorkflowError): ha.asset('SERIALA123', 'prints/cache/样件.3mf')


def test_camera_snapshot_uses_authenticated_proxy_without_exposing_camera_token(workspace, snapshot, monkeypatch):
    monkeypatch.setattr(ha, '_snapshot', lambda: snapshot)
    calls = fake_client(monkeypatch)
    result = ha.camera_image('printer-a')
    assert result['content_type'] == 'image/png' and result['content'].startswith(b'\x89PNG')
    requests = [request for request in calls if isinstance(request, httpx.Request)]
    assert requests[0].url.path == '/api/camera_proxy/camera.printer_a_camera'
    assert 'token=' not in str(requests[0].url)
    for state in snapshot['states']:
        if state['entity_id'].startswith('camera.'): state['state'] = 'unavailable'
    with pytest.raises(core.WorkflowError): ha.camera_image('printer-a')
    with pytest.raises(core.WorkflowError): ha._image_type(b'<svg onload="PRIVATE"/>')
