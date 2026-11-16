import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from printlab import container_runtime as runtime


TEST_KEY = b't' * 32
LOGIN = {'username': 'ace', 'password': 'a-long-test-password'}
ORIGIN = {'Origin': 'http://testserver'}


def client(*, session_key=TEST_KEY, session_key_file=None, password=LOGIN['password'], base_url='http://testserver'):
    app = FastAPI()

    @app.get('/api/health')
    @app.get('/api/ha/camera/printer')
    @app.get('/api/projects')
    def private():
        return {'ok': True}

    return TestClient(runtime.WebLogin(app, 'ace', password, session_key=session_key,
                                      session_key_file=session_key_file),
                      base_url=base_url, follow_redirects=False)


def test_private_routes_require_login_and_health_remains_available():
    with client() as browser:
        assert browser.get('/api/health').status_code == 200
        response = browser.get('/')
        assert response.status_code == 303 and response.headers['location'] == '/login'
        assert browser.head('/').status_code == 303
        for path in ['/api/projects', '/api/ha/camera/printer']:
            response = browser.get(path)
            assert response.status_code == 401
            assert response.headers['cache-control'] == 'no-store'
            assert 'www-authenticate' not in response.headers
            assert response.json() == {'detail': '请先登录 PrintLab'}


@pytest.mark.parametrize('header', ['Basic invalid!', 'Bearer token', 'Basic YWJj', ''])
def test_malformed_login_is_rejected(header):
    with client() as browser:
        assert browser.get('/api/projects', headers={'Authorization': header}).status_code == 401


def test_valid_login_passes_through_and_wrong_password_does_not():
    with client() as browser:
        assert browser.get('/api/projects', auth=('ace', 'a-long-test-password')).status_code == 200
        assert browser.get('/api/projects', auth=('ace', 'wrong')).status_code == 401


@pytest.fixture
def login_origin(monkeypatch):
    monkeypatch.setattr(runtime.core, 'config', lambda: {'studio_url': 'http://testserver'})


def test_login_page_and_session_status_are_public_without_credentials(login_origin):
    with client() as browser:
        response = browser.get('/login')
        assert response.status_code == 200 and 'text/html' in response.headers['content-type']
        assert 'PrintLab' in response.text and LOGIN['password'] not in response.text
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-frame-options'] == 'DENY'
        assert browser.head('/login').content == b''
        assert browser.get('/api/auth/session').json() == {'enabled': True, 'authenticated': False}


def test_cookie_login_protects_all_routes_and_logout_clears_session(login_origin):
    with client() as browser:
        response = browser.post('/api/auth/login', json=LOGIN, headers=ORIGIN)
        assert response.status_code == 200
        assert response.json() == {'ok': True, 'next': '/#/overview'}
        cookie = response.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'samesite=lax' in cookie and 'max-age=43200' in cookie
        assert 'secure' not in cookie
        assert LOGIN['password'] not in cookie
        for path in ['/api/projects', '/api/ha/camera/printer']:
            assert browser.get(path).status_code == 200
        assert browser.get('/api/auth/session').json() == {'enabled': True, 'authenticated': True}
        assert browser.get('/login').headers['location'] == '/#/overview'
        browser.cookies.set('printlab_session', 'old-workbench-session')
        response = browser.post('/api/auth/logout', headers=ORIGIN)
        assert response.status_code == 200 and response.json() == {'ok': True}
        assert any('printlab_session=' in value for value in response.headers.get_list('set-cookie'))
        assert browser.get('/api/auth/session').json()['authenticated'] is False
        assert browser.get('/api/projects').status_code == 401
        assert browser.get('/').headers['location'] == '/login'


def test_https_login_cookie_is_secure(monkeypatch):
    monkeypatch.setattr(runtime.core, 'config', lambda: {'studio_url': 'https://testserver'})
    with client(base_url='https://testserver') as browser:
        response = browser.post('/api/auth/login', json=LOGIN, headers={'Origin': 'https://testserver'})
        assert response.status_code == 200
        assert 'Secure' in response.headers['set-cookie']
        assert browser.get('/api/projects').status_code == 200
        response = browser.post('/api/auth/logout', headers={'Origin': 'https://testserver'})
        assert 'Secure' in response.headers['set-cookie']


@pytest.mark.parametrize('headers', [
    {}, {'Origin': 'http://other.test'}, {'Origin': 'null'},
    {'Origin': 'http://testserver', 'Sec-Fetch-Site': 'cross-site'},
    {'Origin': 'http://evil.test', 'Host': 'evil.test'},
])
def test_login_and_logout_require_the_configured_origin(login_origin, headers):
    with client() as browser:
        for path in ['/api/auth/login', '/api/auth/logout']:
            response = browser.post(path, json=LOGIN, headers=headers)
            assert response.status_code == 403
            assert 'set-cookie' not in response.headers
            assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('values', [
    {'username': 'ace', 'password': 'wrong-password'},
    {'username': 'wrong-user', 'password': LOGIN['password']},
    {'username': '用户名', 'password': '密码不匹配'},
])
def test_wrong_credentials_return_fixed_error_without_echoing_input(login_origin, values):
    with client() as browser:
        response = browser.post('/api/auth/login', json=values, headers=ORIGIN)
        assert response.status_code == 401
        assert response.json() == {'detail': '账号或密码不正确'}
        assert 'www-authenticate' not in response.headers and 'set-cookie' not in response.headers


@pytest.mark.parametrize('values', [
    [], None, {}, {'username': 'ace'}, {**LOGIN, 'extra': True},
    {'username': '', 'password': 'value'}, {'username': 'ace', 'password': ''},
    {'username': 123, 'password': 'value'}, {'username': 'ace', 'password': False},
    {'username': 'x' * 201, 'password': 'value'}, {'username': 'ace', 'password': 'x' * 1025},
    {'username': '\ud800', 'password': 'value'},
])
def test_invalid_login_input_has_fixed_validation_error(login_origin, values):
    with client() as browser:
        response = browser.post('/api/auth/login', content=json.dumps(values),
                                headers={**ORIGIN, 'Content-Type': 'application/json'})
        assert response.status_code == 400
        assert response.json() == {'detail': '请输入有效的账号和密码'}


@pytest.mark.parametrize('body', [b'{broken', b'\xff', b'[' * 1100 + b']' * 1100])
def test_malformed_login_json_is_handled_without_parser_details(login_origin, body):
    with client() as browser:
        response = browser.post('/api/auth/login', content=body,
                                headers={**ORIGIN, 'Content-Type': 'application/json'})
        assert response.status_code == 400
        assert response.json() == {'detail': '请输入有效的账号和密码'}


def test_login_content_type_and_length_are_bounded(login_origin):
    with client() as browser:
        assert browser.post('/api/auth/login', content='text', headers=ORIGIN).status_code == 415
        for length in ['-1', 'invalid']:
            response = browser.post('/api/auth/login', content='{}',
                headers={**ORIGIN, 'Content-Type': 'application/json', 'Content-Length': length})
            assert response.status_code == 400
        response = browser.post('/api/auth/login', content=b'x' * 8193,
            headers={**ORIGIN, 'Content-Type': 'application/json'})
        assert response.status_code == 413
        response = browser.post('/api/auth/login', content=iter([b'x' * 4096, b'x' * 4097]),
            headers={**ORIGIN, 'Content-Type': 'application/json'})
        assert response.status_code == 413


def test_expired_and_tampered_sessions_are_rejected(login_origin, monkeypatch):
    with client() as browser:
        browser.post('/api/auth/login', json=LOGIN, headers=ORIGIN)
        token = browser.cookies.get(runtime.WebLogin.cookie_name)
        browser.cookies.clear()
        for invalid in [token[:-1] + ('a' if token[-1] != 'a' else 'b'), 'invalid', 'x' * 1025]:
            response = browser.get('/api/projects', headers={'Cookie': f'printlab_auth={invalid}'})
            assert response.status_code == 401
        assert browser.get('/api/projects', headers={b'Cookie': b'printlab_auth=\xe9.invalid'}).status_code == 401
        now = runtime.time.time()
        monkeypatch.setattr(runtime.time, 'time', lambda: now + 43201)
        assert browser.get('/api/projects', headers={'Cookie': f'printlab_auth={token}'}).status_code == 401


def test_sessions_survive_restart_and_password_changes_invalidate_them(tmp_path, login_origin):
    path = tmp_path / 'private/web-session.key'
    with client(session_key=None, session_key_file=path) as browser:
        browser.post('/api/auth/login', json=LOGIN, headers=ORIGIN)
        token = browser.cookies.get(runtime.WebLogin.cookie_name)
    with client(session_key=None, session_key_file=path) as browser:
        assert browser.get('/api/projects', headers={'Cookie': f'printlab_auth={token}'}).status_code == 200
    with client(session_key=None, session_key_file=path, password='new-test-password') as browser:
        assert browser.get('/api/projects', headers={'Cookie': f'printlab_auth={token}'}).status_code == 401
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_session_key_is_private_and_stable(tmp_path):
    path = tmp_path / 'private/web-session.key'
    first = runtime.load_session_key(path)
    assert len(first) == 32 and first == runtime.load_session_key(path)
    path.chmod(0o644)
    assert runtime.load_session_key(path) == first
    assert path.stat().st_mode & 0o777 == 0o600


def test_session_key_rejects_invalid_files_and_links(tmp_path):
    path = tmp_path / 'private/web-session.key'
    path.parent.mkdir()
    path.write_bytes(b'short')
    with pytest.raises(RuntimeError, match='private PrintLab login session key'):
        runtime.load_session_key(path)
    outside = tmp_path / 'outside'
    outside.write_bytes(b'outside-file-must-not-be-modified')
    outside.chmod(0o640)
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(RuntimeError, match='private PrintLab login session key'):
        runtime.load_session_key(path)
    assert outside.stat().st_mode & 0o777 == 0o640
    path.unlink()
    path.parent.rmdir()
    path.parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match='private PrintLab login session key'):
        runtime.load_session_key(path)


def test_new_session_key_restricts_inherited_nas_permissions(tmp_path, monkeypatch):
    original_open = runtime.os.open

    def nas_open(path, flags, mode=0o777):
        descriptor = original_open(path, flags, mode)
        if flags & runtime.os.O_CREAT:
            runtime.os.fchmod(descriptor, 0o700)
        return descriptor

    monkeypatch.setattr(runtime.os, 'open', nas_open)
    path = tmp_path / 'private/web-session.key'
    assert len(runtime.load_session_key(path)) == 32
    assert path.stat().st_mode & 0o777 == 0o600


def test_invalid_injected_session_key_is_rejected():
    with pytest.raises(ValueError, match='32 bytes'):
        client(session_key=b'too-short')


def test_private_credentials_are_validated_without_exposing_values(tmp_path):
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps({'username': 'ace', 'password': 'a-long-test-password'}))
    assert runtime.read_private_json(path) == ('ace', 'a-long-test-password')
    path.write_text('{invalid')
    with pytest.raises(RuntimeError, match='web login'):
        runtime.read_private_json(path)


def test_startup_allows_first_run_without_deployment_auth(tmp_path, monkeypatch):
    monkeypatch.delenv('PRINTLAB_WEB_AUTH_FILE', raising=False)
    monkeypatch.setattr(runtime.core, 'ROOT', tmp_path)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'Runtime/codex'))
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *args, **kwargs: None)
    assert runtime.configure() is None


def test_configuration_preserves_user_settings_without_gateway_secret(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'Runtime').mkdir()
    runtime.core.write_json(data / 'Runtime/config.json', {'device_provider': 'homeassistant', 'custom': 42,
                                                       'cad_python': '/old/mac/python',
                                                       'slicer_path': '/Applications/BambuStudio.app/Contents/MacOS/BambuStudio',
                                                       'slicer_profiles_path': '/Applications/BambuStudio.app/Contents/Resources/profiles'})
    auth = tmp_path / 'auth.json'
    auth.write_text(json.dumps({'username': 'ace', 'password': 'a-long-test-password'}))
    monkeypatch.setattr(runtime.core, 'ROOT', data)
    monkeypatch.setenv('PRINTLAB_WEB_AUTH_FILE', str(auth))
    monkeypatch.setenv('PRINTLAB_CAD_PYTHON', '/opt/venv/bin/python')
    monkeypatch.setenv('PRINTLAB_STUDIO_URL', 'http://nas.example:8018')
    monkeypatch.setenv('PRINTLAB_SLICER_PATH', '/usr/local/bin/printlab-slicer')
    monkeypatch.setenv('PRINTLAB_SLICER_PROFILES', '/opt/bambu-studio/resources/profiles')
    monkeypatch.setenv('CODEX_HOME', str(data / 'Runtime/codex'))
    calls = []
    monkeypatch.setattr(runtime.subprocess, 'run', lambda command, **kwargs: calls.append((command, kwargs)))
    assert runtime.configure() == ('ace', 'a-long-test-password')
    config = runtime.core.read_json(data / 'Runtime/config.json')
    assert config['custom'] == 42 and config['device_provider'] == 'homeassistant'
    assert config['cad_python'] == '/opt/venv/bin/python'
    assert config['studio_url'] == 'http://nas.example:8018'
    assert config['slicer_path'] == '/usr/local/bin/printlab-slicer'
    assert config['slicer_profiles_path'] == '/opt/bambu-studio/resources/profiles'
    assert calls[0][0][-1].endswith('scripts/init_workspace.py')
    assert calls[0][1] == {'check': True}


def test_stored_web_credentials_are_preserved(tmp_path, monkeypatch):
    auth = tmp_path / 'auth.json'
    auth.write_text(json.dumps(LOGIN))
    monkeypatch.setattr(runtime.core, 'ROOT', tmp_path)
    monkeypatch.setenv('PRINTLAB_WEB_AUTH_FILE', str(auth))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'Runtime/codex'))
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *args, **kwargs: None)
    assert runtime.configure() == (LOGIN['username'], LOGIN['password'])


def test_explicit_missing_auth_cannot_reset_admin(tmp_path, monkeypatch):
    monkeypatch.setenv('PRINTLAB_WEB_AUTH_FILE', str(tmp_path / 'missing.json'))
    with pytest.raises(RuntimeError, match='web login'):
        runtime.configure()


def test_configuration_keeps_custom_slicer_paths_without_container_overrides(tmp_path, monkeypatch):
    (tmp_path / 'Runtime').mkdir()
    config = {'slicer_path': '/custom/slicer', 'slicer_profiles_path': '/custom/profiles', 'custom': 42}
    runtime.core.write_json(tmp_path / 'Runtime/config.json', config)
    auth = tmp_path / 'auth.json'
    auth.write_text(json.dumps(LOGIN))
    monkeypatch.setattr(runtime.core, 'ROOT', tmp_path)
    monkeypatch.setenv('PRINTLAB_WEB_AUTH_FILE', str(auth))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'Runtime/codex'))
    for variable in ('PRINTLAB_SLICER_PATH', 'PRINTLAB_SLICER_PROFILES',
                     'PRINTLAB_CAD_PYTHON', 'PRINTLAB_BLENDER_PATH', 'PRINTLAB_STUDIO_URL'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *args, **kwargs: None)
    runtime.configure()
    assert runtime.core.read_json(tmp_path / 'Runtime/config.json') == config


def test_startup_reports_slicer_failure_and_still_serves_the_workbench(monkeypatch, capsys):
    import sys
    from types import SimpleNamespace
    import uvicorn
    from printlab import slicer_runtime, web_setup
    app, calls = object(), []
    monkeypatch.setattr(runtime, 'configure', lambda: ('ace', LOGIN['password']))
    monkeypatch.setattr(slicer_runtime, 'capability', lambda: {
        'state': 'unavailable', 'message': 'Slicer runtime unavailable'})
    monkeypatch.setitem(sys.modules, 'printlab.api', SimpleNamespace(app=app))
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(web_setup, 'WebSetup', lambda application, **kwargs: application)
    runtime.main()
    assert calls[0][0] == (app,)
    captured = capsys.readouterr()
    assert 'unavailable' in captured.err
    assert LOGIN['password'] not in captured.err


@pytest.mark.skipif(runtime.os.name == 'nt', reason='Linux deployment permissions')
def test_prepare_mounts_owns_only_given_data_and_protects_credentials(tmp_path, monkeypatch):
    data, secret = tmp_path / 'data', tmp_path / 'secret'
    (data / 'Runtime/private').mkdir(parents=True)
    provider_home = data / 'Runtime/codex-providers/test-provider/1'
    provider_home.mkdir(parents=True)
    (provider_home / 'config.toml').write_text('model = "test-model"\n')
    secret.mkdir()
    (data / 'Runtime/private/ha.json').write_text('{}')
    (secret / 'key').write_text('private-test')
    changed = []
    monkeypatch.setattr(runtime.os, 'chown', lambda path, uid, gid, **kwargs: changed.append(Path(path)))
    runtime.prepare_data(data, secret)
    assert changed and all(path.is_relative_to(data) or path.is_relative_to(secret) for path in changed)
    assert data.stat().st_mode & 0o777 == 0o700
    assert (secret / 'key').stat().st_mode & 0o777 == 0o600
    assert (data / 'Runtime/private/ha.json').stat().st_mode & 0o777 == 0o600
    assert provider_home.stat().st_mode & 0o777 == 0o700
    assert (provider_home / 'config.toml').stat().st_mode & 0o777 == 0o600


def test_prepare_mounts_preserves_helper_links_without_following_them(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('preserve me')
    outside.chmod(0o640)
    (data / 'link').symlink_to(outside)
    changed = []
    monkeypatch.setattr(runtime.os, 'chown', lambda path, *args, **kwargs: changed.append(Path(path)))
    runtime.prepare_data(data, tmp_path / 'secret')
    assert outside.read_text() == 'preserve me'
    assert outside.stat().st_mode & 0o777 == 0o640
    assert outside not in changed and data / 'link' not in changed
    assert (data / 'link').is_symlink()


def test_prepare_mounts_still_rejects_private_directory_links(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    (data / 'Runtime').mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (data / 'Runtime/private').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(runtime.os, 'chown', lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match='Private PrintLab directories'):
        runtime.prepare_data(data, tmp_path / 'secret')
