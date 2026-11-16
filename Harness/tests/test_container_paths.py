"""Source/data separation, container listeners and Blender export compatibility."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from printlab import core, image3d, platforms

SOURCE = Path(__file__).resolve().parents[2]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, SOURCE / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_tool_environment(monkeypatch):
    for name in ('PRINTLAB_SOURCE_ROOT', 'PRINTLAB_ROOT', 'PRINTLAB_CAD_PYTHON',
                 'PRINTLAB_SLICER_PATH', 'PRINTLAB_BLENDER_PATH', 'PRINTLAB_SLICER_PROFILES',
                 'PRINTLAB_HOST', 'PRINTLAB_PORT', 'PRINTLAB_STUDIO_PORT', 'PRINTLAB_PROXY_HEADERS', 'PRINTLAB_FORWARDED_ALLOW_IPS'):
        monkeypatch.delenv(name, raising=False)


def test_separate_workspace_uses_shipped_templates_and_preserves_overrides(tmp_path, monkeypatch):
    application, workspace = tmp_path / 'app', tmp_path / 'data'
    monkeypatch.setattr(core, 'SOURCE_ROOT', application)
    monkeypatch.setattr(core, 'ROOT', workspace)
    templates = application / 'Templates/Project'
    (templates / 'Review').mkdir(parents=True)
    (templates / 'Review/team.md').write_text('Shipped team for {{project_name}}')
    assert core.template_root() == templates
    core.new_project('Separated workspace')
    project = core.project_path('P001')
    assert project.is_relative_to(workspace)
    assert (project / 'Review/team.md').read_text() == 'Shipped team for Separated workspace'
    custom = workspace / 'Templates/Project'
    (custom / 'Review').mkdir(parents=True)
    (custom / 'Review/team.md').write_text('Workspace override')
    assert core.template_root() == custom
    core.new_project('Custom rules')
    assert (core.project_path('P002') / 'Review/team.md').read_text() == 'Workspace override'


def test_model_build_invokes_program_source_and_writes_only_workspace(tmp_path, monkeypatch):
    application, workspace = tmp_path / 'app', tmp_path / 'data'
    monkeypatch.setattr(core, 'SOURCE_ROOT', application)
    monkeypatch.setattr(core, 'ROOT', workspace)
    builder = application / 'Harness/build_model.py'
    builder.parent.mkdir(parents=True)
    builder.write_text('# Program image fixture')
    core.write_json(workspace / 'Runtime/config.json', {'cad_python': '/opt/venv/bin/python'})
    core.new_project('Build routing')
    project = core.project_path('P001')
    (project / 'Design/v001').mkdir()
    (project / 'Design/v001/model.py').write_text('# Synthetic geometry is not executed in this path test')
    monkeypatch.setattr(core, 'validate_build_lineage', lambda *args: None)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        output = project / 'Exports/v001'
        output.mkdir()
        (output / 'model.stl').write_bytes(b'path fixture, not real geometry')
        core.write_json(output / 'build.json', {'verified': True, 'checks': {'synthetic': True}})
        return SimpleNamespace(returncode=0, stdout='output', stderr='')
    monkeypatch.setattr(core.subprocess, 'run', run)
    core.build('P001', 'v001', 'cadquery')
    assert commands[0] == ['/opt/venv/bin/python', str(builder), '--project', str(project), '--revision', 'v001', '--engine', 'cadquery']
    assert (workspace / 'Runtime/evidence/P001-v001-build.log').read_text() == 'output\n'
    assert core.doctor()['tools']['builder']['available'] is True
    assert not (application / 'Project').exists()


def test_image_workers_are_program_files_and_weights_are_workspace_files(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'SOURCE_ROOT', tmp_path / 'app')
    monkeypatch.setattr(core, 'ROOT', tmp_path / 'data')
    paths = image3d.toolchain()
    assert paths['worker'] == tmp_path / 'app/Harness/image3d_worker.py'
    assert paths['python'].is_relative_to(tmp_path / 'data/Runtime')
    assert paths['weights'].is_relative_to(tmp_path / 'data/Runtime')


def test_initializer_seeds_data_from_program_without_copying_environment(tmp_path, monkeypatch, clean_tool_environment):
    application, workspace = tmp_path / 'app', tmp_path / 'data'
    monkeypatch.setenv('PRINTLAB_SOURCE_ROOT', str(application))
    monkeypatch.setenv('PRINTLAB_ROOT', str(workspace))
    monkeypatch.setenv('PRINTLAB_BLENDER_PATH', '/usr/local/bin/printlab-blender')
    monkeypatch.setenv('PRINTLAB_CAD_PYTHON', '/opt/venv/bin/python')
    (application / 'config').mkdir(parents=True)
    for path in (SOURCE / 'config').glob('*.example'):
        (application / 'config' / path.name).write_bytes(path.read_bytes())
    (application / 'AGENTS.md').write_text('Program rules')
    (application / 'README.md').write_text('Program documentation')
    (application / 'Context').mkdir()
    (application / 'Context/workflow.md').write_text('Default workflow')
    (application / 'Context/profile.md').write_text('Do not copy source-host personal data')
    (application / 'Library/Materials').mkdir(parents=True)
    (application / 'Library/Materials/README.md').write_text('Material rules')
    initializer = load_module('container_initializer', 'scripts/init_workspace.py')
    initializer.main()
    config_path = workspace / 'Runtime/config.json'
    config = core.read_json(config_path)
    assert config['cad_python'] == '/opt/venv/bin/python'
    assert config['blender_path'] == '/usr/local/bin/printlab-blender'
    assert (workspace / 'Context/workflow.md').read_text() == 'Default workflow'
    assert 'Do not copy' not in (workspace / 'Context/profile.md').read_text()
    assert str(workspace) in (workspace / 'Context/team.md').read_text()
    assert (workspace / 'Library/Materials/README.md').read_text() == 'Material rules'
    assert (workspace / 'AGENTS.md').read_text() == 'Program rules'
    assert not (workspace / 'Harness').exists()
    assert not (workspace / 'Runtime/venv').exists()
    original = '{"custom": true}\n'
    config_path.write_text(original)
    (workspace / 'Context/workflow.md').write_text('My workflow')
    initializer.main()
    assert config_path.read_text() == original
    assert (workspace / 'Context/workflow.md').read_text() == 'My workflow'
    if sys.platform != 'win32':
        assert (workspace / 'Runtime/private').stat().st_mode & 0o777 == 0o700
        assert (workspace / 'Runtime/private/bambuddy.json').stat().st_mode & 0o777 == 0o600


def test_separated_initializer_defaults_to_current_image_python(tmp_path, monkeypatch, clean_tool_environment):
    monkeypatch.setenv('PRINTLAB_ROOT', str(tmp_path))
    initializer = load_module('image_python_initializer', 'scripts/init_workspace.py')
    initializer.main()
    assert core.read_json(tmp_path / 'Runtime/config.json')['cad_python'] == sys.executable


def test_seed_text_missing_source_preserves_existing_destination(tmp_path):
    initializer = load_module('seed_text_initializer', 'scripts/init_workspace.py')
    destination = tmp_path / 'existing.md'
    destination.write_text('Existing')
    initializer.seed_text(tmp_path / 'missing.md', destination)
    assert destination.read_text() == 'Existing'


@pytest.mark.parametrize('system,expected', [('darwin', '/Applications/Blender.app/Contents/MacOS/Blender'), ('linux', '/usr/bin/blender')])
def test_platform_defaults_and_environment_overrides(monkeypatch, clean_tool_environment, system, expected):
    monkeypatch.setattr(platforms.sys, 'platform', system)
    monkeypatch.setattr(platforms.shutil, 'which', lambda _: None)
    defaults = platforms.tool_defaults()
    assert defaults['blender_path'] == expected
    if system == 'linux':
        assert not any('/Applications/' in value for value in defaults.values())
    monkeypatch.setenv('PRINTLAB_BLENDER_PATH', '/custom/blender')
    monkeypatch.setenv('PRINTLAB_SLICER_PATH', '/custom/slicer')
    monkeypatch.setenv('PRINTLAB_SLICER_PROFILES', '/custom/profiles')
    assert platforms.tool_defaults() == {'blender_path': '/custom/blender', 'slicer_path': '/custom/slicer', 'slicer_profiles_path': '/custom/profiles'}


@pytest.fixture
def server(tmp_path, monkeypatch, clean_tool_environment):
    monkeypatch.setenv('PRINTLAB_ROOT', str(tmp_path))
    module = load_module('container_server', 'Harness/serve.py')
    monkeypatch.setattr(module.sys, 'path', list(sys.path))
    observations = {'bindings': [], 'closed': 0}
    class Listener:
        def setsockopt(self, *args):
            pass
        def bind(self, address):
            observations['bindings'].append(address)
        def listen(self, *args):
            pass
        def close(self):
            observations['closed'] += 1
    monkeypatch.setattr(module.socket, 'socket', lambda *args: Listener())
    monkeypatch.setattr(module.uvicorn, 'Config', lambda target, **kwargs: observations.update(target=target, config=kwargs) or kwargs)
    monkeypatch.setattr(module.uvicorn, 'Server', lambda config: SimpleNamespace(run=lambda **kwargs: observations.update(run=True)))
    return module, observations


def test_server_loopback_default_and_data_config_tailscale_listener(server, tmp_path, monkeypatch):
    module, result = server
    core.write_json(tmp_path / 'Runtime/config.json', {'tailscale_ip': '100.70.0.2'})
    module.main(['studio'])
    assert result['bindings'] == [('127.0.0.1', 8018), ('100.70.0.2', 8018)]
    assert result['config']['proxy_headers'] is False
    assert result['closed'] == 2
    assert module.sys.path[0] == str(module.SOURCE_ROOT / 'Harness')
    assert module.os.environ['PRINTLAB_STUDIO_PORT'] == '8018'


def test_server_explicit_container_host_port_and_trusted_proxy(server, tmp_path, monkeypatch):
    module, result = server
    monkeypatch.setenv('PRINTLAB_HOST', '0.0.0.0')
    monkeypatch.setenv('PRINTLAB_PORT', '18018')
    monkeypatch.setenv('PRINTLAB_PROXY_HEADERS', '1')
    monkeypatch.setenv('PRINTLAB_FORWARDED_ALLOW_IPS', '172.20.0.2')
    core.write_json(tmp_path / 'Runtime/config.json', {'tailscale_ip': '100.70.0.2'})
    module.main(['studio'])
    assert result['bindings'] == [('0.0.0.0', 18018)]
    assert result['config']['forwarded_allow_ips'] == '172.20.0.2'
    assert result['config']['proxy_headers'] is True
    assert result['run']


def test_server_cli_options_override_environment(server, monkeypatch):
    module, result = server
    monkeypatch.setenv('PRINTLAB_HOST', '0.0.0.0')
    monkeypatch.setenv('PRINTLAB_PORT', 'bad')
    module.main(['studio', '--host', '127.0.0.2', '--port', '18019'])
    assert result['bindings'] == [('127.0.0.2', 18019)]


@pytest.mark.parametrize('argv,environment', [(['studio', '--port', '0'], {}), (['studio', '--host', 'https://example.com'], {}), (['studio'], {'PRINTLAB_PORT': 'bad'})])
def test_server_rejects_invalid_listener_before_opening_sockets(server, monkeypatch, argv, environment):
    module, result = server
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit) as error:
        module.main(argv)
    assert error.value.code == 2
    assert result['bindings'] == []


@pytest.mark.parametrize('modern,addon', [(True, False), (False, False), (False, True)])
def test_stl_export_keeps_selection_modifiers_and_millimetres(tmp_path, modern, addon):
    builder = load_module('stl_builder', 'Harness/build_model.py')
    calls = []
    wm = SimpleNamespace()
    legacy = SimpleNamespace()
    if modern:
        wm.stl_export = lambda **kwargs: calls.append(('modern', kwargs))
    if not addon:
        legacy.stl = lambda **kwargs: calls.append(('legacy', kwargs))
    def enable(**kwargs):
        calls.append(('addon', kwargs))
        legacy.stl = lambda **options: calls.append(('legacy', options))
    bpy = SimpleNamespace(ops=SimpleNamespace(wm=wm, export_mesh=legacy, preferences=SimpleNamespace(addon_enable=enable)))
    output = tmp_path / 'model.stl'
    builder.export_stl(bpy, output)
    kind, options = calls[-1]
    assert kind == ('modern' if modern else 'legacy')
    assert options['filepath'] == str(output)
    assert options['global_scale'] == 1000.0
    assert options.get('export_selected_objects', options.get('use_selection')) is True
    assert options.get('apply_modifiers', options.get('use_mesh_modifiers')) is True
    if addon:
        assert calls[0] == ('addon', {'module': 'io_mesh_stl'})


def test_failed_modern_export_does_not_silently_try_another_geometry_export(tmp_path):
    builder = load_module('failing_stl_builder', 'Harness/build_model.py')
    def fail(**kwargs):
        raise RuntimeError('export failed')
    bpy = SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(stl_export=fail)))
    with pytest.raises(RuntimeError, match='export failed'):
        builder.export_stl(bpy, tmp_path / 'model.stl')


def test_legacy_gltf_importer_restores_only_the_removed_numpy_alias(monkeypatch):
    builder = load_module('legacy_gltf_builder', 'Harness/build_model.py')
    numpy = SimpleNamespace(bool_=object())
    monkeypatch.setitem(sys.modules, 'numpy', numpy)
    builder._legacy_gltf_numpy_compat(SimpleNamespace(app=SimpleNamespace(version=(3, 4, 1))))
    assert numpy.bool is numpy.bool_
    existing = object()
    numpy.bool = existing
    builder._legacy_gltf_numpy_compat(SimpleNamespace(app=SimpleNamespace(version=(3, 4, 1))))
    assert numpy.bool is existing


def test_modern_blender_does_not_modify_numpy_aliases(monkeypatch):
    builder = load_module('modern_gltf_builder', 'Harness/build_model.py')
    numpy = SimpleNamespace(bool_=object())
    monkeypatch.setitem(sys.modules, 'numpy', numpy)
    builder._legacy_gltf_numpy_compat(SimpleNamespace(app=SimpleNamespace(version=(4, 5, 0))))
    assert not hasattr(numpy, 'bool')
