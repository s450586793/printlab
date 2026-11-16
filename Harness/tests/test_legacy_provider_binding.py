"""Migrated native threads keep their original home and frozen provider credentials."""
import json

import pytest

from printlab import core, model_providers as providers


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    home = tmp_path / 'Runtime/codex'
    home.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    for name in providers._AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    (home / 'config.toml').write_text('model="old-model"\nmodel_provider="gateway"\n'
        '[model_providers.gateway]\nbase_url="https://legacy.example/v1"\n'
        'env_key="PRINTLAB_PROVIDER_API_KEY"\nwire_api="responses"\n')
    (home / 'thread-sentinel').write_text('keep-native-thread')
    saved = providers.save_settings({'version': 0, 'provider': {
        'name': '模型服务', 'base_url': 'https://legacy.example/v1',
        'api_key': 'original-private-sentinel', 'models': ['old-model'], 'default_model': 'old-model'},
        'make_default': True})
    pid = saved['default_provider_id']
    return tmp_path, home, pid


def test_legacy_binding_keeps_native_home_and_frozen_revision(legacy):
    root, home, pid = legacy
    before = (home / 'config.toml').read_bytes()
    providers.bind_legacy(pid, 1)
    spec = providers.resolve('legacy', revision=0)
    assert spec['api_key'] == 'original-private-sentinel'
    assert spec['home'] == home and spec['managed'] and spec['legacy']
    assert providers.session_home('legacy', 0) == home
    assert providers.settings()['default_provider_id'] == pid
    assert any(row['id'] == 'legacy' for row in providers.settings()['providers'])
    providers.save_settings({'version': 1, 'provider': {'id': pid, 'api_key': 'rotated-sentinel'}})
    assert providers.resolve('legacy')['api_key'] == 'original-private-sentinel'
    assert (home / 'config.toml').read_bytes() == before
    assert (home / 'thread-sentinel').read_text() == 'keep-native-thread'
    binding = root / 'Runtime/private/legacy-provider.json'
    assert binding.stat().st_mode & 0o777 == 0o600
    assert 'original-private-sentinel' not in binding.read_text()
    assert providers.resolve(pid)['api_key'] == 'rotated-sentinel'


def test_binding_cannot_be_silently_replaced(legacy):
    _, _, pid = legacy
    providers.bind_legacy(pid, 1)
    providers.bind_legacy(pid, 1)
    providers.save_settings({'version': 1, 'provider': {'id': pid, 'api_key': 'rotated-sentinel'}})
    with pytest.raises(providers.ProviderError):
        providers.bind_legacy(pid, 2)
    assert providers.resolve('legacy')['api_key'] == 'original-private-sentinel'


@pytest.mark.parametrize('change', ['base', 'env', 'corrupt', 'symlink'])
def test_invalid_binding_fails_closed_without_falling_back_to_default(legacy, change):
    root, home, pid = legacy
    providers.bind_legacy(pid, 1)
    binding = root / 'Runtime/private/legacy-provider.json'
    if change == 'base':
        (home / 'config.toml').write_text((home / 'config.toml').read_text().replace('legacy.example', 'other.example'))
    elif change == 'env':
        (home / 'config.toml').write_text((home / 'config.toml').read_text().replace('PRINTLAB_PROVIDER_API_KEY', 'PATH'))
    elif change == 'corrupt':
        binding.write_text(json.dumps({'provider_id': pid, 'revision': 999}))
    else:
        binding.unlink()
        binding.symlink_to(home / 'thread-sentinel')
    with pytest.raises(providers.ProviderError):
        providers.resolve('legacy')
    assert providers.resolve(pid)['api_key'] == 'original-private-sentinel'
    assert providers.settings()['default_provider_id'] == pid
    assert all(not row['legacy'] for row in providers.settings()['providers'])
