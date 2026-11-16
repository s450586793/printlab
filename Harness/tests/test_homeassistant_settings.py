"""HA settings validate candidates without exposing or replacing saved secrets."""
import copy
import json
import os
import threading
import time

import httpx
import pytest

from printlab import core, homeassistant as ha


TOKEN = 'candidate-private-token'
OLD_TOKEN = 'existing-private-token'
REFRESH = 'existing-private-refresh'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    ha.clear_cache()
    yield tmp_path
    ha.clear_cache()


@pytest.fixture
def server(monkeypatch):
    state = {'requests': [], 'commands': [], 'hosts': [], 'status': 200,
             'auth': True, 'fail': False, 'denied': None, 'token': TOKEN,
             'snapshot': {
                 'entries': [{'entry_id': 'entry', 'domain': 'bambu_lab'}],
                 'manifest': {'version': 'test'},
                 'devices': [
                     {'id': 'one', 'name': '打印机一', 'model': 'A1', 'config_entries': ['entry'],
                      'identifiers': [['bambu_lab', 'SERIAL-ONE']]},
                     {'id': 'two', 'name': '打印机二', 'model': 'Other', 'config_entries': ['entry'],
                      'identifiers': [['bambu_lab', 'SERIAL-TWO']]},
                     {'id': 'ams', 'name': 'AMS', 'via_device_id': 'one', 'config_entries': ['entry'],
                      'identifiers': [['bambu_lab', 'SERIAL-AMS']]},
                 ], 'registry': [], 'states': [],
             }}

    def transport(request):
        state['requests'].append(request)
        if state['fail']:
            raise httpx.ConnectError(TOKEN + REFRESH)
        if request.url.path == '/auth/token':
            return httpx.Response(200, json={'access_token': 'refreshed-private-token',
                                           'refresh_token': 'refreshed-private-refresh', 'expires_in': 1800})
        assert request.url.path == '/api/' and request.method == 'GET'
        assert request.headers['Authorization'] == 'Bearer ' + state['token']
        return httpx.Response(state['status'], json={'message': 'API running.'}, text=None)

    monkeypatch.setattr(ha, '_http', lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(transport)))

    class Socket:
        def __init__(self):
            self.frames = [state.get('frame', {'type': 'auth_required'})]

        def recv(self):
            return json.dumps(self.frames.pop(0))

        def send(self, raw):
            command = json.loads(raw)
            state['commands'].append(command)
            if command['type'] == 'auth':
                assert command['access_token'] == state['token']
                self.frames.append({'type': 'auth_ok' if state['auth'] else 'auth_invalid', 'message': TOKEN})
                return
            names = {'config_entries/get': 'entries', 'manifest/get': 'manifest',
                     'config/device_registry/list': 'devices', 'config/entity_registry/list': 'registry',
                     'get_states': 'states'}
            name = names[command['type']]
            self.frames.append({'id': command['id'], 'success': state['denied'] != name,
                                'result': copy.deepcopy(state['snapshot'][name]),
                                'error': {'message': TOKEN + REFRESH}})

        def settimeout(self, timeout):
            pass

        def close(self):
            pass

    def socket(url, **kwargs):
        state['hosts'].append(url)
        return Socket()

    monkeypatch.setattr(ha.websocket, 'create_connection', socket)
    return state


def saved_credentials(workspace, **changes):
    value = {'base_url': 'http://old.test:8123', 'access_token': OLD_TOKEN,
             'refresh_token': REFRESH, 'client_id': 'http://old.test:8123/',
             'expires_at': time.time() + 1800, **changes}
    ha._save_credentials(value)
    return workspace / 'Runtime/private/homeassistant.json'


def test_new_install_status_is_unconfigured_without_network_or_credentials(workspace, server):
    from printlab import homeassistant_settings as settings
    result = settings.status()
    assert result == {'configured': False, 'connected': False, 'base_url': None,
                      'devices': [], 'error_code': None, 'message': '尚未连接 Home Assistant。'}
    assert not server['requests'] and not server['commands']
    assert not (workspace / 'Runtime/private/homeassistant.json').exists()


def test_legacy_oauth_status_lists_only_public_printer_fields(workspace, server):
    from printlab import homeassistant_settings as settings
    path = saved_credentials(workspace)
    server['token'] = OLD_TOKEN
    before = path.read_bytes()
    result = settings.status()
    assert result['configured'] is True and result['connected'] is True
    assert result['base_url'] == 'http://old.test:8123'
    assert result['devices'] == [{'id': 'one', 'name': '打印机一', 'model': 'A1'},
                                 {'id': 'two', 'name': '打印机二', 'model': 'Other'}]
    assert result['error_code'] is None
    assert OLD_TOKEN not in json.dumps(result) and REFRESH not in json.dumps(result)
    assert path.read_bytes() == before


@pytest.mark.parametrize('operation', ['test_connection', 'save_settings'])
@pytest.mark.parametrize('empty', [False, True])
def test_zero_devices_and_missing_optional_features_are_successful(workspace, server, operation, empty):
    from printlab import homeassistant_settings as settings
    if empty:
        server['snapshot']['devices'] = []
    result = getattr(settings, operation)({'base_url': 'http://new.test:8123/', 'token': TOKEN})
    assert result['connected'] is True
    assert result['error_code'] == ('no_devices' if empty else None)
    assert result['devices'] == ([] if empty else [{'id': 'one', 'name': '打印机一', 'model': 'A1'},
                                                 {'id': 'two', 'name': '打印机二', 'model': 'Other'}])
    assert (workspace / 'Runtime/private/homeassistant.json').exists() is (operation == 'save_settings')


def test_missing_bambu_integration_does_not_reject_valid_ha_connection(workspace, server):
    from printlab import homeassistant_settings as settings
    server['snapshot'].update(entries=[], manifest={}, devices=[])
    result = settings.save_settings({'base_url': 'http://new.test', 'token': TOKEN})
    assert result['configured'] is True and result['connected'] is True
    assert result['error_code'] == 'integration_missing' and result['devices'] == []


def test_successful_save_replaces_credentials_privately_and_keeps_other_configuration(workspace, server):
    from printlab import homeassistant_settings as settings
    path = saved_credentials(workspace)
    core.write_json(workspace / 'Runtime/config.json', {'device_provider': 'bambuddy', 'keep': True, 'ha_printer_id': 'old'})
    ha._snapshots['old'] = (0, {})
    ha._file_lists['old'] = (0, {})
    historical = workspace / 'Project/P001-sample/project.json'
    core.write_json(historical, {'target_printer': {'id': 'old'}, 'job': 'unchanged'})
    before = historical.read_bytes()
    result = settings.save_settings({'base_url': 'http://new.test:8123/', 'token': TOKEN})
    assert result['connected'] is True and result['configured'] is True
    saved = json.loads(path.read_text())
    assert saved == {'base_url': 'http://new.test:8123', 'access_token': TOKEN}
    assert core.config() == {'device_provider': 'homeassistant', 'keep': True}
    assert ha._snapshots == {} and ha._file_lists == {}
    assert historical.read_bytes() == before
    assert TOKEN not in json.dumps(result) and REFRESH not in json.dumps(result)
    if os.name != 'nt':
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('mode,code', [('network', 'network'), ('auth', 'authentication'),
                                    ('permission', 'permission'), ('socket_auth', 'authentication')])
@pytest.mark.parametrize('operation', ['test_connection', 'save_settings'])
def test_failed_candidate_preserves_old_credentials_config_and_cache(workspace, server, mode, code, operation):
    from printlab import homeassistant_settings as settings
    path = saved_credentials(workspace)
    core.write_json(workspace / 'Runtime/config.json', {'device_provider': 'bambuddy', 'keep': True})
    if mode == 'network': server['fail'] = True
    if mode == 'auth': server['status'] = 401
    if mode == 'permission': server['denied'] = 'registry'
    if mode == 'socket_auth': server['auth'] = False
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    ha._snapshots['old'] = (0, {'old': True})
    result = getattr(settings, operation)({'base_url': 'http://new.test', 'token': TOKEN})
    assert result['connected'] is False and result['error_code'] == code
    assert path.read_bytes() == before and path.stat().st_mtime_ns == modified
    assert core.config() == {'device_provider': 'bambuddy', 'keep': True}
    assert ha._snapshots == {'old': (0, {'old': True})}
    assert TOKEN not in json.dumps(result) and REFRESH not in json.dumps(result)


def test_successful_candidate_test_preserves_existing_files_and_cache(workspace, server):
    from printlab import homeassistant_settings as settings
    path = saved_credentials(workspace)
    core.write_json(workspace / 'Runtime/config.json', {'device_provider': 'bambuddy'})
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    ha._file_lists['old'] = (0, {'old': True})
    result = settings.test_connection({'base_url': 'http://new.test', 'token': TOKEN})
    assert result['connected'] is True
    assert path.read_bytes() == before and path.stat().st_mtime_ns == modified
    assert core.config() == {'device_provider': 'bambuddy'}
    assert ha._file_lists == {'old': (0, {'old': True})}


@pytest.mark.parametrize('token', [None, '', '   '])
def test_blank_token_can_reuse_same_normalized_address(workspace, server, token):
    from printlab import homeassistant_settings as settings
    saved_credentials(workspace)
    server['token'] = OLD_TOKEN
    result = settings.test_connection({'base_url': 'http://old.test:8123/', 'token': token})
    assert result['connected'] is True


@pytest.mark.parametrize('base', ['https://old.test:8123', 'http://other.test:8123', 'http://old.test:8124'])
def test_blank_token_never_sends_old_secret_to_changed_address(workspace, server, base):
    from printlab import homeassistant_settings as settings
    path = saved_credentials(workspace)
    before = path.read_bytes()
    with pytest.raises(settings.SettingsError) as error:
        settings.save_settings({'base_url': base, 'token': ''})
    assert error.value.status_code == 422
    assert not server['requests'] and not server['commands'] and path.read_bytes() == before


def test_candidate_oauth_refresh_is_only_in_memory(workspace, server):
    from printlab import homeassistant_settings as settings
    path = saved_credentials(workspace, expires_at=0)
    before = path.read_bytes()
    server['token'] = 'refreshed-private-token'
    result = settings.test_connection({'base_url': 'http://old.test:8123'})
    assert result['connected'] is True and path.read_bytes() == before
    assert server['requests'][0].url.path == '/auth/token'


@pytest.mark.parametrize('frame', [[], TOKEN, None])
def test_malformed_websocket_data_returns_safe_failure(workspace, server, frame):
    from printlab import homeassistant_settings as settings
    server['frame'] = frame
    result = settings.test_connection({'base_url': 'http://new.test', 'token': TOKEN})
    assert result['connected'] is False and result['error_code'] == 'invalid_response'
    assert TOKEN not in json.dumps(result)


def test_status_refresh_preserves_legacy_oauth_renewal(workspace, server):
    from printlab import homeassistant_settings as settings
    path = saved_credentials(workspace, expires_at=0)
    server['token'] = 'refreshed-private-token'
    result = settings.status()
    assert result['connected'] is True
    saved = json.loads(path.read_text())
    assert saved['access_token'] == 'refreshed-private-token'
    assert saved['refresh_token'] == 'refreshed-private-refresh'


def test_disconnect_is_idempotent_local_and_does_not_switch_to_another_provider(workspace, server):
    from printlab import homeassistant_settings as settings
    path = saved_credentials(workspace)
    core.write_json(workspace / 'Runtime/config.json', {'device_provider': 'homeassistant', 'keep': True, 'ha_printer_id': 'old'})
    historical = workspace / 'Project/P001-sample/project.json'
    core.write_json(historical, {'target_printer': {'id': 'old'}, 'job': 'unchanged'})
    before = historical.read_bytes()
    ha._snapshots['old'] = (0, {})
    result = settings.disconnect()
    assert result['configured'] is False and result['connected'] is False
    assert settings.disconnect()['configured'] is False
    assert not path.exists() and not server['requests'] and not server['commands']
    assert core.config() == {'device_provider': 'homeassistant', 'keep': True}
    assert ha._snapshots == {} and historical.read_bytes() == before


@pytest.mark.parametrize('operation', ['save_settings', 'disconnect'])
def test_cache_invalidation_allows_inflight_credential_reads(workspace, server, monkeypatch, operation):
    from printlab import homeassistant_settings as settings
    saved_credentials(workspace)
    original = ha.clear_cache
    finished = threading.Event()

    def invalidate():
        def read_credentials():
            try:
                ha._authenticated_settings()
            except core.WorkflowError:
                pass
            finally:
                finished.set()
        reader = threading.Thread(target=read_credentials, daemon=True)
        reader.start()
        assert finished.wait(1), 'Cache invalidation must allow an in-flight credential read to complete'
        original()
        reader.join(timeout=1)

    monkeypatch.setattr(ha, 'clear_cache', invalidate)
    if operation == 'save_settings':
        settings.save_settings({'base_url': 'http://new.test', 'token': TOKEN})
    else:
        settings.disconnect()


def test_failed_credential_write_rolls_back_provider_and_preserves_existing_secret(workspace, server, monkeypatch):
    from printlab import homeassistant_settings as settings
    path = saved_credentials(workspace)
    before = path.read_bytes()
    core.write_json(workspace / 'Runtime/config.json', {'device_provider': 'bambuddy', 'keep': True})
    def fail(value):
        raise OSError(TOKEN)
    monkeypatch.setattr(ha, '_save_credentials', fail)
    with pytest.raises(settings.SettingsError) as error:
        settings.save_settings({'base_url': 'http://new.test', 'token': TOKEN})
    assert error.value.status_code == 500 and TOKEN not in str(error.value)
    assert core.config() == {'device_provider': 'bambuddy', 'keep': True}
    assert path.read_bytes() == before


@pytest.mark.parametrize('content', ['[]', '{', '{"base_url":"http://ha.test"}'])
def test_invalid_saved_configuration_returns_safe_status(workspace, server, content):
    from printlab import homeassistant_settings as settings
    path = workspace / 'Runtime/private/homeassistant.json'
    path.parent.mkdir(parents=True)
    path.write_text(content)
    result = settings.status()
    assert result['connected'] is False and result['configured'] is False
    assert not server['requests'] and not server['commands']
