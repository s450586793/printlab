import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from printlab import slicer_runtime


@pytest.fixture
def installed(tmp_path, monkeypatch):
    executable = tmp_path / 'printlab-slicer'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    profiles = tmp_path / 'profiles'
    for kind in ('machine', 'process', 'filament'):
        (profiles / 'BBL' / kind).mkdir(parents=True)
    for model in ('A1', 'P1P'):
        (profiles / 'BBL/machine' / f'Bambu Lab {model} 0.4 nozzle.json').write_text('{}')
        (profiles / 'BBL/process' / f'0.20mm Standard @BBL {model}.json').write_text('{}')
        filament = profiles / 'BBL/filament' / ('P1P' if model == 'P1P' else '')
        filament.mkdir(exist_ok=True)
        for material in ('PLA', 'PETG'):
            (filament / f'Generic {material} @BBL {model}.json').write_text('{}')
    # 临时目录可能位于 noexec tmpfs；这里只模拟操作系统执行权限检查。
    monkeypatch.setattr(slicer_runtime.os, 'access', lambda path, mode: bool(Path(path).stat().st_mode & 0o111))
    runner = Mock(return_value=subprocess.CompletedProcess([], 0,
        'BambuStudio-02.09.01.70: --load-settings --load-filaments --slice --export-3mf', ''))
    monkeypatch.setattr(slicer_runtime.subprocess, 'run', runner)
    return {'slicer_path': str(executable), 'slicer_profiles_path': str(profiles)}, runner


def test_ready_means_the_program_actually_starts_with_bundled_profiles(installed):
    config, runner = installed
    result = slicer_runtime.capability(config)
    assert result['available'] is True and result['state'] == 'ready'
    assert result['path'] == config['slicer_path']
    assert result['profiles_path'] == config['slicer_profiles_path']
    assert result['missing_libraries'] == []
    command = runner.call_args.args[0]
    assert command == [config['slicer_path'], '--help']
    assert 0 < runner.call_args.kwargs['timeout'] <= 30


@pytest.mark.parametrize('failure', ['missing', 'directory', 'not_executable'])
def test_missing_or_unexecutable_program_is_reported_without_starting_it(installed, failure):
    config, runner = installed
    executable = Path(config['slicer_path'])
    if failure == 'missing':
        executable.unlink()
    elif failure == 'directory':
        executable.unlink()
        executable.mkdir()
    else:
        executable.chmod(0o644)
    result = slicer_runtime.capability(config)
    assert result['available'] is False and result['state'] == 'missing'
    runner.assert_not_called()


@pytest.mark.parametrize('missing', ['BBL/filament', 'BBL/process',
                                     'BBL/machine/Bambu Lab A1 0.4 nozzle.json',
                                     'BBL/machine/Bambu Lab P1P 0.4 nozzle.json',
                                     'BBL/process/0.20mm Standard @BBL A1.json',
                                     'BBL/process/0.20mm Standard @BBL P1P.json',
                                     'BBL/filament/Generic PLA @BBL A1.json',
                                     'BBL/filament/Generic PETG @BBL A1.json',
                                     'BBL/filament/P1P/Generic PLA @BBL P1P.json',
                                     'BBL/filament/P1P/Generic PETG @BBL P1P.json'])
def test_missing_bundled_profiles_prevent_a_ready_status(installed, missing):
    config, runner = installed
    target = Path(config['slicer_profiles_path']) / missing
    if target.is_dir():
        import shutil
        shutil.rmtree(target)
    else:
        target.unlink()
    result = slicer_runtime.capability(config)
    assert result['available'] is False and result['state'] == 'missing_profiles'
    runner.assert_not_called()


def test_loader_errors_only_expose_library_names(installed):
    config, runner = installed
    runner.return_value = subprocess.CompletedProcess([], 127, 'private-api-key',
        '/opt/bin/bambu: error while loading shared libraries: libgtk-3.so.0: cannot open shared object file\n'
        'private-api-key http://user:password@example.test libwebkit2gtk-4.1.so.0')
    result = slicer_runtime.capability(config)
    assert result['available'] is False and result['state'] == 'unavailable'
    assert result['missing_libraries'] == ['libgtk-3.so.0', 'libwebkit2gtk-4.1.so.0']
    assert 'private-api-key' not in str(result) and 'password' not in str(result)


def test_success_exit_without_slicing_commands_is_not_ready(installed):
    _, runner = installed
    runner.return_value = subprocess.CompletedProcess([], 0, 'unrelated tool', '')
    assert slicer_runtime.capability(installed[0])['available'] is False


@pytest.mark.parametrize('failure', ['timeout', 'os_error', 'nonzero'])
def test_startup_failure_degrades_without_blocking_the_workbench(installed, failure):
    config, runner = installed
    if failure == 'timeout':
        runner.side_effect = subprocess.TimeoutExpired('private-api-key', 20)
    elif failure == 'os_error':
        runner.side_effect = OSError('private-api-key')
    else:
        runner.return_value = subprocess.CompletedProcess([], 1, '', 'private-api-key')
    result = slicer_runtime.capability(config)
    assert result['available'] is False and result['state'] == 'unavailable'
    assert 'private-api-key' not in str(result)


def test_default_configuration_is_loaded_lazily(installed, monkeypatch):
    from printlab import core
    config, _ = installed
    monkeypatch.setattr(core, 'config', lambda: config)
    assert slicer_runtime.capability()['available'] is True


def test_custom_slicer_does_not_claim_a_bundled_engine_fingerprint(tmp_path):
    assert slicer_runtime.fingerprint(tmp_path / 'custom-slicer') is None


def test_bundled_engine_identity_changes_with_engine_or_source_receipt(tmp_path, monkeypatch):
    wrapper = tmp_path / 'printlab-slicer'
    receipt, engine = tmp_path / 'PRINTLAB-SOURCE.json', tmp_path / 'bambu-studio'
    monkeypatch.setattr(slicer_runtime, 'BUNDLED_WRAPPER', wrapper)
    monkeypatch.setattr(slicer_runtime, 'BUNDLED_RECEIPT', receipt)
    monkeypatch.setattr(slicer_runtime, 'BUNDLED_ENGINE', engine)
    receipt.write_text('{"version":"1"}')
    engine.write_bytes(b'engine-1')
    before = slicer_runtime.fingerprint(wrapper)
    assert set(before) == {'receipt_sha256', 'engine_sha256'}
    engine.write_bytes(b'engine-2')
    changed = slicer_runtime.fingerprint(wrapper)
    assert changed['engine_sha256'] != before['engine_sha256']
    assert changed['receipt_sha256'] == before['receipt_sha256']
    receipt.write_text('{"version":"2"}')
    assert slicer_runtime.fingerprint(wrapper)['receipt_sha256'] != before['receipt_sha256']


def test_missing_bundled_engine_receipt_fails_without_disclosing_raw_errors(tmp_path, monkeypatch):
    from printlab import core
    wrapper = tmp_path / 'printlab-slicer'
    monkeypatch.setattr(slicer_runtime, 'BUNDLED_WRAPPER', wrapper)
    monkeypatch.setattr(slicer_runtime, 'BUNDLED_RECEIPT', tmp_path / 'private-token')
    with pytest.raises(core.WorkflowError, match='Built-in slicer identity is unavailable') as error:
        slicer_runtime.fingerprint(wrapper)
    assert 'private-token' not in str(error.value)


@pytest.mark.parametrize('link', ['symbolic', 'hard'])
def test_alias_of_the_bundled_wrapper_retains_engine_identity(tmp_path, monkeypatch, link):
    wrapper, alias = tmp_path / 'printlab-slicer', tmp_path / 'alias'
    receipt, engine = tmp_path / 'PRINTLAB-SOURCE.json', tmp_path / 'bambu-studio'
    wrapper.write_text('#!/bin/sh\n')
    alias.symlink_to(wrapper) if link == 'symbolic' else alias.hardlink_to(wrapper)
    receipt.write_text('{}')
    engine.write_bytes(b'engine')
    monkeypatch.setattr(slicer_runtime, 'BUNDLED_WRAPPER', wrapper)
    monkeypatch.setattr(slicer_runtime, 'BUNDLED_RECEIPT', receipt)
    monkeypatch.setattr(slicer_runtime, 'BUNDLED_ENGINE', engine)
    assert slicer_runtime.fingerprint(alias) == slicer_runtime.fingerprint(wrapper)
