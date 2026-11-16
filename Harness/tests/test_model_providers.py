"""Private model configuration, frozen runtimes and bounded provider probes."""
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading
import tomllib

import httpx
import pytest

from printlab import core


KEY = 'sentinel-provider-credential-never-public'


def service(**changes):
    return {'name': '测试服务', 'base_url': 'https://models.example/gateway/v1/',
            'api_key': KEY, 'models': ['model-a'], 'default_model': 'model-a',
            'enabled': True, **changes}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'legacy-codex'))
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'PRINTLAB_PROVIDER_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_saved_provider_is_private_and_public_view_hides_key(workspace):
    from printlab import model_providers as providers
    assert providers.settings() == {'version': 0, 'default_provider_id': None, 'providers': []}
    saved = providers.save_settings({'version': 0, 'provider': service()})
    row = saved['providers'][0]
    assert saved['version'] == 1 and saved['default_provider_id'] == row['id']
    assert row['key_configured'] and not row['legacy'] and row['revision'] == 1
    assert row['base_url'] == 'https://models.example/gateway/v1'
    assert KEY not in json.dumps(saved) and 'api_key' not in row and 'home' not in row
    path = workspace / 'Runtime/private/model-providers.json'
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_key_rotation_preserves_history_and_stopped_provider_can_resume(workspace):
    from printlab import model_providers as providers
    initial = providers.save_settings({'version': 0, 'provider': service()})
    pid = initial['providers'][0]['id']
    old = providers.resolve(pid)
    second = providers.save_settings({'version': 1, 'provider': service(id=pid, api_key='new-sentinel-key')})
    assert second['providers'][0]['revision'] == 2
    assert providers.resolve(pid, revision=1)['api_key'] == KEY
    assert providers.resolve(pid)['api_key'] == 'new-sentinel-key'
    providers.save_settings({'version': 2, 'provider': service(id=pid, api_key='', enabled=False)})
    with pytest.raises(providers.ProviderError):
        providers.resolve(pid)
    assert providers.resolve(pid, revision=1) == old
    assert {KEY, 'new-sentinel-key'} <= set(providers.secret_values())
    assert providers.session_home(pid, revision=1) == old['home']


@pytest.mark.parametrize('key', ['', None])
def test_omitted_or_empty_key_preserves_current_secret(workspace, key):
    from printlab import model_providers as providers
    saved = providers.save_settings({'version': 0, 'provider': service()})
    pid = saved['providers'][0]['id']
    update = service(id=pid, name='改名', api_key=key)
    if key is None:
        del update['api_key']
    providers.save_settings({'version': 1, 'provider': update})
    assert providers.resolve(pid)['api_key'] == KEY


def test_changed_service_address_cannot_reuse_saved_key(workspace, monkeypatch):
    from printlab import model_providers as providers
    saved = providers.save_settings({'version': 0, 'provider': service()})
    pid = saved['default_provider_id']
    before = (workspace / 'Runtime/private/model-providers.json').read_bytes()
    monkeypatch.setattr(providers, '_http', lambda: pytest.fail('Do not send old keys to a new address'))
    payload = {'id': pid, 'base_url': 'https://other.example/v1'}
    with pytest.raises(providers.ProviderError, match='API Key'):
        providers.test_connection(payload)
    with pytest.raises(providers.ProviderError, match='API Key'):
        providers.save_settings({'version': 1, 'provider': payload})
    assert (workspace / 'Runtime/private/model-providers.json').read_bytes() == before


def test_failed_save_and_stale_version_leave_original_bytes(workspace, monkeypatch):
    from printlab import model_providers as providers
    providers.save_settings({'version': 0, 'provider': service()})
    path = workspace / 'Runtime/private/model-providers.json'
    before = path.read_bytes()
    with pytest.raises(providers.ProviderError):
        providers.save_settings({'version': 0, 'provider': service()})
    assert path.read_bytes() == before
    monkeypatch.setattr(providers.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('fixture failure')))
    with pytest.raises(providers.ProviderError):
        providers.save_settings({'version': 1, 'provider': service(name='第二项')})
    assert path.read_bytes() == before
    assert not list(path.parent.glob('.model-providers-*'))


@pytest.mark.parametrize('bad', ['ftp://models.example/v1', 'https://user:password@models.example/v1',
                               'https://models.example/v1?token=x', 'https://models.example/v1#secret',
                               'https://models.example:99999/v1', 'https://models.example/\nvalue'])
def test_bad_urls_never_create_registry(workspace, bad):
    from printlab import model_providers as providers
    with pytest.raises(providers.ProviderError):
        providers.save_settings({'version': 0, 'provider': service(base_url=bad)})
    assert not (workspace / 'Runtime/private/model-providers.json').exists()


@pytest.mark.parametrize('target', ['Runtime', 'Runtime/private', 'Runtime/private/model-providers.json'])
def test_registry_symlinks_are_rejected_without_touching_target(workspace, target):
    from printlab import model_providers as providers
    outside = workspace / 'outside'
    path = workspace / target
    path.parent.mkdir(parents=True, exist_ok=True)
    if target.endswith('.json'):
        outside.write_text('do not overwrite')
    else:
        outside.mkdir()
    path.symlink_to(outside)
    with pytest.raises(providers.ProviderError):
        providers.save_settings({'version': 0, 'provider': service()})
    assert outside.read_text() == 'do not overwrite' if outside.is_file() else not list(outside.iterdir())


def test_runtime_config_is_private_no_key_and_does_not_change_for_model_selection(workspace):
    from printlab import model_providers as providers
    saved = providers.save_settings({'version': 0, 'provider': service(models=['model-a', 'model-b'])})
    pid = saved['providers'][0]['id']
    first = providers.resolve(pid, model='model-b')
    home = first['home']
    assert home == workspace / f'Runtime/codex-providers/{pid}/1'
    config = home / 'config.toml'
    before = config.read_bytes()
    parsed = tomllib.loads(before.decode())
    assert parsed['model'] == 'model-a' and first['model'] == 'model-b'
    provider = parsed['model_providers']['printlab']
    assert provider['base_url'] == 'https://models.example/gateway/v1'
    assert provider['env_key'] == 'PRINTLAB_PROVIDER_API_KEY' and provider['wire_api'] == 'responses'
    assert KEY not in before.decode() and config.stat().st_mode & 0o777 == 0o600
    assert home.stat().st_mode & 0o777 == 0o700
    providers.resolve(pid, model='model-a')
    assert config.read_bytes() == before
    with pytest.raises(providers.ProviderError):
        providers.resolve(pid, model='unlisted')


def test_concurrent_first_use_waits_for_complete_frozen_runtime(workspace, monkeypatch):
    from printlab import model_providers as providers
    saved = providers.save_settings({'version': 0, 'provider': service()})
    pid = saved['providers'][0]['id']
    writer_opened, release_writer, second_started = threading.Event(), threading.Event(), threading.Event()
    fdopen = providers.os.fdopen

    class PendingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            writer_opened.set()
            if not release_writer.wait(5):
                raise AssertionError('fixture writer was not released')
            return self.stream

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

    def delayed_fdopen(descriptor, mode, **kwargs):
        stream = fdopen(descriptor, mode, **kwargs)
        return PendingWriter(stream) if mode == 'w' else stream

    def second_resolve():
        second_started.set()
        return providers.resolve(pid)

    monkeypatch.setattr(providers.os, 'fdopen', delayed_fdopen)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(providers.resolve, pid)
        try:
            assert writer_opened.wait(5)
            second = pool.submit(second_resolve)
            assert second_started.wait(5)
            try:
                second.result(timeout=0.2)
            except TimeoutError:
                pass
        finally:
            release_writer.set()
        assert first.result(timeout=5) == second.result(timeout=5)
    config = workspace / f'Runtime/codex-providers/{pid}/1/config.toml'
    assert tomllib.loads(config.read_text())['model'] == 'model-a'


def test_legacy_visibility_is_conditional_but_explicit_old_binding_is_always_supported(workspace, monkeypatch):
    from printlab import model_providers as providers
    assert not providers.settings()['providers']
    with pytest.raises(providers.ProviderError):
        providers.resolve()
    legacy = providers.resolve('legacy', revision=0)
    assert legacy['legacy'] and legacy['home'] == workspace / 'legacy-codex'
    home = workspace / 'legacy-codex'
    home.mkdir()
    config = home / 'config.toml'
    config.write_text('model="old-model"\nmodel_provider="gateway"\n[model_providers.gateway]\nbase_url="http://legacy.example/v1"\nenv_key="PRINTLAB_PROVIDER_API_KEY"\n')
    before = config.read_bytes()
    monkeypatch.setenv('PRINTLAB_PROVIDER_API_KEY', 'legacy-sentinel-secret')
    state = providers.settings()
    assert state['default_provider_id'] == 'legacy'
    assert state['providers'][0]['id'] == 'legacy'
    assert providers.resolve()['model'] == 'old-model'
    assert providers.resolve()['api_key'] == 'legacy-sentinel-secret'
    assert config.read_bytes() == before
    assert 'legacy-sentinel-secret' in providers.secret_values()


def test_existing_codex_login_without_config_remains_available(workspace):
    from printlab import model_providers as providers
    home = workspace / 'legacy-codex'
    home.mkdir()
    auth = home / 'auth.json'
    auth.write_text('{"tokens":{"access_token":"sentinel-login-token"}}')
    before = auth.read_bytes()
    state = providers.settings()
    assert state['default_provider_id'] == 'legacy'
    assert state['providers'][0]['key_configured'] is True
    assert providers.resolve()['home'] == home
    assert 'sentinel-login-token' not in json.dumps(state)
    assert auth.read_bytes() == before and not (home / 'config.toml').exists()


def transport_for(monkeypatch, providers, handler):
    original = httpx.Client
    monkeypatch.setattr(providers, '_http', lambda: original(transport=httpx.MockTransport(handler),
                                                          trust_env=False, follow_redirects=False, timeout=20))


def test_probe_checks_responses_function_without_saving_candidate(workspace, monkeypatch):
    from printlab import model_providers as providers
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['authorization'] == f'Bearer {KEY}'
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'model-a'}]})
        body = json.loads(request.content)
        assert body['model'] == 'model-a' and body['tools'][0]['type'] == 'function'
        assert body['tool_choice']['name'] == 'printlab_probe' and body['store'] is False
        return httpx.Response(200, json={'output': [{'type': 'function_call', 'name': 'printlab_probe',
                                                   'call_id': 'test-call', 'arguments': '{"value":1}'}]})
    transport_for(monkeypatch, providers, handler)
    result = providers.test_connection(service())
    assert result['ok'] and result['tool_supported'] is True and result['models'] == ['model-a']
    assert [r.url.path for r in requests] == ['/gateway/v1/models', '/gateway/v1/responses']
    assert KEY not in json.dumps(result)
    assert not (workspace / 'Runtime/private/model-providers.json').exists()


def test_probe_without_model_only_lists_models(workspace, monkeypatch):
    from printlab import model_providers as providers
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'data': [{'id': 'model-a'}, {'id': KEY}, {'id': 'model-a'}]})
    transport_for(monkeypatch, providers, handler)
    result = providers.test_connection(service(default_model='', models=[]))
    assert result['ok'] and result['tool_supported'] is None and result['models'] == ['model-a']
    assert len(requests) == 1 and KEY not in json.dumps(result)


def test_saved_provider_without_candidate_model_only_lists_models(workspace, monkeypatch):
    from printlab import model_providers as providers
    saved = providers.save_settings({'version': 0, 'provider': service()})
    pid = saved['providers'][0]['id']
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'data': [{'id': 'model-a'}]})

    transport_for(monkeypatch, providers, handler)
    result = providers.test_connection({'id': pid, 'base_url': 'https://models.example/gateway/v1'})
    assert result['ok'] and result['tool_supported'] is None
    assert len(requests) == 1 and requests[0].method == 'GET'


@pytest.mark.parametrize('catalogue_failure', [404, 501, 'timeout', 'invalid-json'])
@pytest.mark.parametrize('tool_supported', [True, False])
def test_manual_model_checks_responses_when_catalogue_is_unavailable(workspace, monkeypatch,
                                                                   catalogue_failure, tool_supported):
    from printlab import model_providers as providers
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == 'GET':
            if catalogue_failure == 'timeout':
                raise httpx.ReadTimeout('fixture catalogue unavailable', request=request)
            if catalogue_failure == 'invalid-json':
                return httpx.Response(200, text='not a catalogue')
            return httpx.Response(catalogue_failure)
        output = [{'type': 'function_call', 'name': 'printlab_probe', 'arguments': '{"value":1}'}] if tool_supported else []
        return httpx.Response(200, json={'output': output})

    transport_for(monkeypatch, providers, handler)
    result = providers.test_connection(service())
    assert result['ok'] is tool_supported and result['tool_supported'] is tool_supported
    assert result['models'] == []
    assert [request.method for request in requests] == ['GET', 'POST']
    assert not (workspace / 'Runtime/private/model-providers.json').exists()


def test_catalogue_failure_without_manual_model_returns_safe_error(workspace, monkeypatch):
    from printlab import model_providers as providers
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(404, text=KEY)

    transport_for(monkeypatch, providers, handler)
    result = providers.test_connection(service(default_model='', models=[]))
    assert result['ok'] is False and result['tool_supported'] is None
    assert '手动' in result['message'] and KEY not in json.dumps(result)
    assert len(requests) == 1


@pytest.mark.parametrize('kind', ['auth', 'redirect', 'text-only', 'timeout', 'invalid-json'])
def test_probe_errors_do_not_echo_upstream_or_save_configuration(workspace, monkeypatch, kind):
    from printlab import model_providers as providers
    def handler(request):
        if kind == 'timeout':
            raise httpx.ReadTimeout(KEY, request=request)
        if kind == 'redirect':
            return httpx.Response(302, headers={'location': 'https://elsewhere.example/' + KEY})
        if kind == 'auth':
            return httpx.Response(401, json={'error': KEY})
        if kind == 'invalid-json':
            return httpx.Response(200, text=KEY)
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'model-a'}]})
        return httpx.Response(200, json={'output': [{'type': 'message', 'content': KEY}]})
    transport_for(monkeypatch, providers, handler)
    result = providers.test_connection(service())
    assert result['ok'] is False and KEY not in json.dumps(result)
    assert not (workspace / 'Runtime/private/model-providers.json').exists()


def test_empty_new_key_and_leaked_metadata_are_rejected(workspace):
    from printlab import model_providers as providers
    for candidate in [service(api_key=''), service(name=KEY)]:
        with pytest.raises(providers.ProviderError):
            providers.save_settings({'version': 0, 'provider': candidate})


@pytest.mark.parametrize('content', ['[]', '{invalid', '{"schema_version":1,"version":0,"providers":{},"default_provider_id":{}}'])
def test_corrupt_registry_reports_safe_error_without_breaking_legacy_resume(workspace, content):
    from printlab import model_providers as providers
    path = workspace / 'Runtime/private/model-providers.json'
    path.parent.mkdir(parents=True)
    path.write_text(content)
    with pytest.raises(providers.ProviderError):
        providers.settings()
    assert providers.resolve('legacy', revision=0)['home'] == workspace / 'legacy-codex'


def test_redactor_survives_broken_registry_and_keeps_environment_secrets(workspace, monkeypatch):
    from printlab import model_providers as providers
    path = workspace / 'Runtime/private/model-providers.json'
    path.parent.mkdir(parents=True)
    path.write_text('{invalid')
    monkeypatch.setenv('PRINTLAB_PROVIDER_API_KEY', 'legacy-sentinel')
    assert 'legacy-sentinel' in providers.secret_values()


def test_registry_revision_corruption_is_rejected_before_public_output(workspace):
    from printlab import model_providers as providers
    saved = providers.save_settings({'version': 0, 'provider': service()})
    path = workspace / 'Runtime/private/model-providers.json'
    content = json.loads(path.read_text())
    pid = saved['providers'][0]['id']
    content['providers'][pid]['revisions']['1']['base_url'] = 'https://user:secret@example.com'
    path.write_text(json.dumps(content))
    with pytest.raises(providers.ProviderError):
        providers.settings()


def test_defaults_change_without_mutating_frozen_provider_or_legacy(workspace, monkeypatch):
    from printlab import model_providers as providers
    first = providers.save_settings({'version': 0, 'provider': service()})
    pid = first['providers'][0]['id']
    frozen = providers.resolve(pid, revision=1)
    monkeypatch.setenv('PRINTLAB_PROVIDER_API_KEY', 'legacy-sentinel')
    changed = providers.save_settings({'version': 1, 'default_provider_id': 'legacy'})
    assert changed['default_provider_id'] == 'legacy' and changed['version'] == 2
    assert providers.resolve(pid, revision=1) == frozen
    changed = providers.save_settings({'version': 2, 'default_provider_id': pid})
    assert changed['default_provider_id'] == pid


def test_runtime_config_tamper_or_symlink_is_rejected_without_overwrite(workspace):
    from printlab import model_providers as providers
    saved = providers.save_settings({'version': 0, 'provider': service()})
    pid = saved['providers'][0]['id']
    spec = providers.resolve(pid)
    config = spec['home'] / 'config.toml'
    config.write_text('do not overwrite')
    with pytest.raises(providers.ProviderError):
        providers.resolve(pid)
    assert config.read_text() == 'do not overwrite'
    config.unlink()
    outside = workspace / 'outside.toml'
    outside.write_text('outside')
    config.symlink_to(outside)
    with pytest.raises(providers.ProviderError):
        providers.resolve(pid)
    assert outside.read_text() == 'outside'


def test_probe_reuses_stored_key_and_bounds_upstream_payload(workspace, monkeypatch):
    from printlab import model_providers as providers
    saved = providers.save_settings({'version': 0, 'provider': service()})
    pid = saved['providers'][0]['id']
    path = workspace / 'Runtime/private/model-providers.json'
    before = path.read_bytes()
    def handler(request):
        assert request.headers['authorization'] == 'Bearer ' + KEY
        return httpx.Response(200, content=b'x' * (1024 * 1024 + 1))
    transport_for(monkeypatch, providers, handler)
    probe = service(id=pid, api_key='')
    result = providers.test_connection(probe)
    assert result['ok'] is False and KEY not in json.dumps(result)
    assert path.read_bytes() == before


def test_disabled_service_can_test_candidate_before_reenabling(workspace, monkeypatch):
    from printlab import model_providers as providers
    saved = providers.save_settings({'version': 0, 'provider': service(enabled=False)})
    pid = saved['providers'][0]['id']
    transport_for(monkeypatch, providers, lambda request: httpx.Response(200, json={'data': [{'id': 'model-a'}]}))
    result = providers.test_connection({'id': pid, 'base_url': 'https://models.example/gateway/v1',
                                        'default_model': '', 'models': []})
    assert result['ok'] and result['models'] == ['model-a']
    assert providers.settings()['providers'][0]['enabled'] is False
