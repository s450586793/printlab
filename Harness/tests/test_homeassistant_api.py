"""HA settings routes require a same-origin session and never reflect secrets."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from printlab import core
from printlab.prepared_api import router as prepared_router


TOKEN = 'never-echo-private-token'
URL = '/api/settings/home-assistant'


@pytest.fixture
def client(tmp_path, monkeypatch):
    from printlab.homeassistant_api import router
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    app = FastAPI()
    app.include_router(prepared_router)
    app.include_router(router)
    return TestClient(app, base_url='http://127.0.0.1:8018')


def headers(client):
    token = client.get('/api/prepared/session').json()['csrf_token']
    return {'Origin': 'http://127.0.0.1:8018', 'X-PrintLab-CSRF': token}


def test_read_requires_session_and_all_responses_are_private(client):
    response = client.get(URL)
    assert response.status_code == 403 and response.headers['cache-control'] == 'no-store'
    headers(client)
    response = client.get(URL)
    assert response.status_code == 200 and response.json()['configured'] is False
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('suffix,body', [('', {'base_url': 'http://ha.test', 'token': TOKEN}),
                                       ('/test', {'base_url': 'http://ha.test', 'token': TOKEN}),
                                       ('/disconnect', {})])
def test_writes_require_origin_csrf_and_session_before_contacting_ha(client, monkeypatch, suffix, body):
    from printlab import homeassistant as ha
    monkeypatch.setattr(ha, '_http', lambda base: pytest.fail('Unauthorized request contacted HA'))
    assert client.post(URL + suffix, json=body).status_code == 403
    valid = headers(client)
    for invalid in [{**valid, 'Origin': 'https://other.test'}, {**valid, 'X-PrintLab-CSRF': 'wrong'},
                    {'X-PrintLab-CSRF': valid['X-PrintLab-CSRF']}]:
        response = client.post(URL + suffix, json=body, headers=invalid)
        assert response.status_code == 403 and response.headers['cache-control'] == 'no-store'
        assert TOKEN not in response.text


@pytest.mark.parametrize('suffix,body', [
    ('', {'base_url': 'http://ha.test', 'token': [TOKEN]}),
    ('/test', {'base_url': 'http://ha.test', 'token': TOKEN, 'extra': TOKEN}),
    ('', {'base_url': 'https://user:' + TOKEN + '@ha.test', 'token': TOKEN}),
    ('/test', {'base_url': 'http://ha.test?secret=' + TOKEN, 'token': TOKEN}),
    ('', {'base_url': 'http://ha.test', 'token': TOKEN + '\nInjected: x'}),
    ('/disconnect', {'token': TOKEN}),
])
def test_validation_is_fixed_422_without_input_or_secret_echo(client, suffix, body):
    response = client.post(URL + suffix, json=body, headers=headers(client))
    assert response.status_code == 422
    assert TOKEN not in response.text and 'input' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_malformed_json_and_missing_token_do_not_echo_input(client):
    response = client.post(URL, content='{"token":"' + TOKEN + '",',
                           headers={**headers(client), 'Content-Type': 'application/json'})
    assert response.status_code == 422 and TOKEN not in response.text
    assert response.headers['cache-control'] == 'no-store'
    response = client.post(URL, json={'base_url': 'http://ha.test'}, headers=headers(client))
    assert response.status_code == 422 and TOKEN not in response.text


def test_upstream_connection_failure_returns_sanitized_state_and_no_saved_credentials(client, monkeypatch, tmp_path):
    import httpx
    from printlab import homeassistant as ha
    def fail(base):
        raise httpx.ConnectError(TOKEN)
    monkeypatch.setattr(ha, '_http', fail)
    for suffix in ['', '/test']:
        response = client.post(URL + suffix, json={'base_url': 'http://ha.test', 'token': TOKEN}, headers=headers(client))
        assert response.status_code == 200 and response.json()['error_code'] == 'network'
        assert response.json()['connected'] is False and TOKEN not in response.text
        assert response.headers['cache-control'] == 'no-store'
    assert not (tmp_path / 'Runtime/private/homeassistant.json').exists()


def test_disconnect_returns_unconfigured_state(client):
    response = client.post(URL + '/disconnect', json={}, headers=headers(client))
    assert response.status_code == 200 and response.json()['configured'] is False
    assert response.headers['cache-control'] == 'no-store'
