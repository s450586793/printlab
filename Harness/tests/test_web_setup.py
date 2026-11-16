"""First-run setup must not expose or replace an existing administrator."""
import json
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from printlab import core
from printlab.web_setup import WebSetup


VALUES = {'username': 'owner', 'password': 'setup-test-password'}
HEADERS = {'Origin': 'http://testserver'}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    monkeypatch.setattr(core, 'config', lambda: {'studio_url': 'http://testserver'})
    monkeypatch.delenv('PRINTLAB_WEB_AUTH_FILE', raising=False)
    return tmp_path


def browser():
    app = FastAPI()

    @app.get('/api/projects')
    @app.get('/api/health')
    def endpoint():
        return {'ok': True}

    return TestClient(WebSetup(app), follow_redirects=False)


def token(root):
    return (root / 'Runtime/private/setup-token').read_text().strip()


def test_first_run_is_locked_until_valid_setup_and_survives_restart(workspace):
    with browser() as client:
        assert client.get('/').headers['location'] == '/setup'
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/projects').status_code == 401
        assert client.get('/api/auth/session').json()['setup_required'] is True
        secret = token(workspace)
        assert secret not in client.get('/setup').text
        assert (workspace / 'Runtime/private/setup-token').stat().st_mode & 0o777 == 0o600
        response = client.post('/api/auth/setup', headers=HEADERS,
                               json={'setup_token': secret, **VALUES})
        assert response.status_code == 200
        assert response.json() == {'ok': True, 'next': '/#/overview'}
        assert client.get('/api/projects').status_code == 200
        cookie = client.cookies.get('printlab_auth')
        auth = workspace / 'Runtime/private/web-auth.json'
        assert auth.stat().st_mode & 0o777 == 0o600
        assert not (workspace / 'Runtime/private/setup-token').exists()
        assert secret not in response.text and VALUES['password'] not in response.text
    with browser() as client:
        assert client.get('/api/projects', headers={'Cookie': 'printlab_auth=' + cookie}).status_code == 200
        assert client.post('/api/auth/setup', headers=HEADERS, json={}).status_code == 409
        assert client.get('/api/auth/session').json().get('setup_required', False) is False
        assert client.get('/setup').headers['location'] == '/login'


@pytest.mark.parametrize('patch,headers,status', [
    ({'setup_token': 'wrong-code-sentinel'}, HEADERS, 403),
    ({'password': 'too-short'}, HEADERS, 400),
    ({'username': ''}, HEADERS, 400),
    ({'password': 12345}, HEADERS, 400),
    ({'extra': 'never-echo-sentinel'}, HEADERS, 400),
    ({}, {}, 403),
    ({}, {'Origin': 'https://evil.test'}, 403),
])
def test_bad_setup_preserves_uninitialized_state(workspace, patch, headers, status):
    with browser() as client:
        payload = {'setup_token': token(workspace), **VALUES, **patch}
        response = client.post('/api/auth/setup', headers=headers, json=payload)
        assert response.status_code == status
        assert response.headers['cache-control'] == 'no-store'
        assert not (workspace / 'Runtime/private/web-auth.json').exists()
        for secret in [VALUES['password'], payload['setup_token'], 'never-echo-sentinel']:
            assert secret not in response.text


def test_malformed_and_oversized_setup_are_rejected(workspace):
    with browser() as client:
        for body, expected in [('not-json', 400), ('x' * 8193, 413)]:
            response = client.post('/api/auth/setup', content=body,
                headers={**HEADERS, 'Content-Type': 'application/json'})
            assert response.status_code == expected
        assert client.post('/api/auth/setup', content='form', headers=HEADERS).status_code == 415


def test_two_instances_can_only_initialize_once(workspace):
    first, second = browser(), browser()
    payload = {'setup_token': token(workspace), **VALUES}
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda client: client.post('/api/auth/setup', headers=HEADERS, json=payload),
                                  [first, second]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert json.loads((workspace / 'Runtime/private/web-auth.json').read_text()) == VALUES


def test_concurrent_request_never_observes_incomplete_session_key(workspace, monkeypatch):
    from printlab import web_setup, container_runtime
    first, second = browser(), browser()
    writing, resume = threading.Event(), threading.Event()
    original = container_runtime.os.fdopen

    def delayed(descriptor, mode, *args, **kwargs):
        if mode == 'wb':
            writing.set()
            assert resume.wait(5)
        return original(descriptor, mode, *args, **kwargs)

    monkeypatch.setattr(container_runtime.os, 'fdopen', delayed)
    payload = {'setup_token': token(workspace), **VALUES}
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(first.post, '/api/auth/setup', headers=HEADERS, json=payload)
        assert writing.wait(5)
        try:
            response = second.get('/setup')
            assert response.status_code == 200
            assert not (workspace / 'Runtime/private/web-auth.json').exists()
        finally:
            resume.set()
        assert future.result(timeout=5).status_code == 200
    assert second.get('/setup').status_code == 303


def test_existing_credentials_are_never_reset(workspace, monkeypatch):
    auth = workspace / 'existing-auth.json'
    auth.write_text(json.dumps(VALUES))
    monkeypatch.setenv('PRINTLAB_WEB_AUTH_FILE', str(auth))
    with browser() as client:
        assert not (workspace / 'Runtime/private/setup-token').exists()
        before = auth.read_bytes()
        assert client.post('/api/auth/setup', headers=HEADERS, json={}).status_code == 409
        assert auth.read_bytes() == before
        assert client.get('/api/projects', auth=(VALUES['username'], VALUES['password'])).status_code == 200


def test_setup_rejects_symlinked_private_directory(workspace):
    (workspace / 'Runtime').mkdir()
    outside = workspace / 'outside'
    outside.mkdir()
    (workspace / 'Runtime/private').symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError):
        browser()
    assert list(outside.iterdir()) == []
