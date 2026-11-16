"""Connection setup saves the provider choice only after devices are readable."""
import importlib.util
from pathlib import Path

import pytest
from printlab import core, homeassistant


@pytest.fixture
def setup_module(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/connect_homeassistant.py'
    spec = importlib.util.spec_from_file_location('connect_homeassistant', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    monkeypatch.setattr(module.getpass, 'getpass', lambda prompt: 'private-password-sentinel')
    return module


def test_setup_activates_read_connection_without_password_in_config(setup_module, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(homeassistant, 'connect', lambda *args: calls.append(args) or {'connected': True})
    monkeypatch.setattr(homeassistant, 'device_list', lambda: {'devices': [{'id': 'printer-a', 'name': 'A1'}]})
    core.write_json(core.ROOT / 'Runtime/config.json', {'printer_id': 42, 'other_setting': True})
    assert setup_module.main(['--url', 'http://ha.example:8123', '--username', 'user']) == 0
    assert calls == [('http://ha.example:8123', 'user', 'private-password-sentinel')]
    config = core.config()
    assert config == {'device_provider': 'homeassistant', 'printer_id': 42, 'other_setting': True}
    output = capsys.readouterr()
    assert 'private-password-sentinel' not in output.out + output.err
    assert '"dispatch_available": false' in output.out


def test_setup_failure_does_not_activate_provider(setup_module, monkeypatch, capsys):
    def fail(*args):
        raise core.WorkflowError('登录未完成')
    monkeypatch.setattr(homeassistant, 'connect', fail)
    assert setup_module.main(['--url', 'http://ha.example:8123', '--username', 'user']) == 1
    assert core.config() == {}
    assert '登录未完成' in capsys.readouterr().err


def test_setup_requires_readable_bambu_devices(setup_module, monkeypatch):
    monkeypatch.setattr(homeassistant, 'connect', lambda *args: {'connected': True})
    monkeypatch.setattr(homeassistant, 'device_list', lambda: {'devices': []})
    assert setup_module.main(['--url', 'http://ha.example:8123', '--username', 'user']) == 1
    assert core.config() == {}
