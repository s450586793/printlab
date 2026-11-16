"""Model provider administration stays private, same-origin and CSRF protected."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from printlab import core
from printlab.prepared_api import router as prepared_router


KEY = 'candidate-sentinel-private-not-returned'


def candidate(**changes):
    return {'name': '服务', 'base_url': 'https://provider.example/v1', 'api_key': KEY,
            'models': ['model-a'], 'default_model': 'model-a', 'enabled': True, **changes}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from printlab.model_provider_api import router
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'no-legacy'))
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'PRINTLAB_PROVIDER_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    app = FastAPI()
    app.include_router(prepared_router)
    app.include_router(router)
    return TestClient(app, base_url='http://127.0.0.1:8018')


def headers(client):
    token = client.get('/api/prepared/session').json()['csrf_token']
    return {'Origin': 'http://127.0.0.1:8018', 'X-PrintLab-CSRF': token}


def test_settings_read_requires_session_and_never_creates_provider_files(client, tmp_path):
    assert client.get('/api/agent/settings').status_code == 403
    headers(client)
    response = client.get('/api/agent/settings')
    assert response.status_code == 200 and response.json()['providers'] == []
    assert response.headers['cache-control'] == 'no-store'
    assert not (tmp_path / 'Runtime/private/model-providers.json').exists()
    assert not (tmp_path / 'Runtime/codex-providers').exists()


def test_settings_save_requires_origin_csrf_and_returns_only_public_metadata(client):
    body = {'version': 0, 'provider': candidate()}
    assert client.post('/api/agent/settings', json=body).status_code == 403
    valid = headers(client)
    for invalid in [{**valid, 'Origin': 'https://evil.example'}, {**valid, 'X-PrintLab-CSRF': 'wrong'}]:
        assert client.post('/api/agent/settings', json=body, headers=invalid).status_code == 403
    response = client.post('/api/agent/settings', json=body, headers=valid)
    assert response.status_code == 200 and response.json()['version'] == 1
    assert response.json()['providers'][0]['key_configured'] is True
    assert KEY not in response.text and 'api_key' not in response.text and 'home' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    stale = client.post('/api/agent/settings', json=body, headers=valid)
    assert stale.status_code == 409 and KEY not in stale.text


@pytest.mark.parametrize('body', [
    {'version': 'wrong', 'provider': candidate()},
    {'version': 0, 'provider': candidate(api_key=[KEY])},
    {'version': 0, 'provider': candidate(env={'SECRET': KEY})},
    {'version': 0, 'provider': candidate(), 'executable': KEY},
    {'version': 0, 'provider': candidate(base_url='https://user:' + KEY + '@example.com/v1')},
])
def test_invalid_settings_do_not_echo_any_original_input(client, body):
    response = client.post('/api/agent/settings', json=body, headers=headers(client))
    assert response.status_code == 422
    assert KEY not in response.text and 'input' not in response.text


def test_malformed_json_does_not_echo_candidate_secret(client):
    response = client.post('/api/agent/settings', content='{"api_key":"' + KEY + '",',
                           headers={**headers(client), 'Content-Type': 'application/json'})
    assert response.status_code == 422 and KEY not in response.text


def test_probe_is_csrf_protected_and_does_not_save(client, monkeypatch, tmp_path):
    from printlab import model_providers
    calls = []
    monkeypatch.setattr(model_providers, 'test_connection', lambda body: calls.append(body) or {
        'ok': False, 'models': [], 'tool_supported': False, 'message': '模型服务验证失败。'})
    body = {k: v for k, v in candidate().items() if k != 'enabled'}
    assert client.post('/api/agent/settings/test', json=body).status_code == 403
    response = client.post('/api/agent/settings/test', json=body, headers=headers(client))
    assert response.status_code == 200 and response.json()['ok'] is False
    assert calls[0]['api_key'] == KEY and KEY not in response.text
    assert not (tmp_path / 'Runtime/private/model-providers.json').exists()
