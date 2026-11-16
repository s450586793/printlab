import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from printlab import bambuddy, cli, core, devices, official, slicer_guard, slicer_runtime
from test_confirmation_retry_3mf import controlled


@pytest.fixture
def offline_slice(tmp_path, monkeypatch, request):
    """隔离的模型、官方配置和切片进程边界，不接触任何打印机。"""
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    binary = tmp_path / 'slicer'
    binary.write_text('synthetic external tool boundary; never executed')
    official = tmp_path / 'official'
    core.write_json(tmp_path / 'Runtime/config.json', {
        'device_provider': 'homeassistant', 'slicer_path': str(binary),
        'slicer_profiles_path': str(official),
    })
    monkeypatch.setattr(devices, 'management_url', lambda: 'https://ha.invalid')
    core.new_project('Offline slice fixture')
    project = core.project_path('P001')
    source = project / 'Design/v001/model.py'
    source.parent.mkdir()
    source.write_text('# Synthetic design; never printed\n')
    artifact = project / 'Exports/v001/model.stl'
    artifact.parent.mkdir()
    artifact.write_text('synthetic geometry passed at external slicer boundary')
    core.write_json(artifact.parent / 'build.json', {
        'verified': True, 'checks': {'watertight': True},
        'source_sha256': {'Design/v001/model.py': hashlib.sha256(source.read_bytes()).hexdigest()},
        'artifacts': {'Exports/v001/model.stl': hashlib.sha256(artifact.read_bytes()).hexdigest()},
    })
    data = core.read_json(project / 'project.json')
    data.update(current_revision='v001', target_printer={
        'provider': 'homeassistant', 'id': 'test-printer', 'model': 'A1',
        'assigned_revision': 'v001',
    })
    core.write_json(project / 'project.json', data)
    model = getattr(request, 'param', 'A1')
    data['target_printer']['model'] = model
    core.write_json(project / 'project.json', data)
    templates = {key: 'synthetic ' + model + ' ' + key for key in slicer_guard.FIELDS}
    machine = {**templates, 'type': 'machine', 'name': f'Bambu Lab {model} 0.4 nozzle',
               'printer_model': f'Bambu Lab {model}', 'printer_variant': '0.4',
               'nozzle_diameter': ['0.4'], 'printable_area': ['0x0', '256x0', '256x256', '0x256']}
    process = {'type': 'process', 'name': f'0.20mm Standard @BBL {model}',
               'layer_height': '0.2', 'curr_bed_type': 'Textured PEI Plate',
               'compatible_printers': [machine['name']]}
    filament = {'type': 'filament', 'name': f'Generic PLA @BBL {model}', 'filament_type': ['PLA'],
                'compatible_printers': [machine['name']],
                'nozzle_temperature_initial_layer': ['220'], 'nozzle_temperature': ['220'],
                'textured_plate_temp_initial_layer': ['65'], 'textured_plate_temp': ['65']}
    profiles = []
    for kind, values in [('machine', machine), ('process', process), ('filament', filament)]:
        source_file = official / 'BBL' / kind / (values['name'] + '.json')
        core.write_json(source_file, values)
        candidate = tmp_path / 'resolved' / (kind + '.json')
        core.write_json(candidate, values)
        core.write_json(candidate.with_suffix('.sources.json'), {
            'output_sha256': core.digest(candidate),
            'source_files': [{'path': str(source_file), 'sha256': core.digest(source_file)}],
        })
        profiles.append(candidate)

    def external_slicer(command, **kwargs):
        settings = {}
        for name in command[command.index('--load-settings') + 1].split(';'):
            settings.update(core.read_json(Path(name)))
        for name in command[command.index('--load-filaments') + 1].split(';'):
            settings.update(core.read_json(Path(name)))
        output = Path(command[command.index('--export-3mf') + 1])
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('Metadata/project_settings.config', json.dumps(settings))
            archive.writestr('Metadata/plate_1.gcode',
                            '; SYNTHETIC OFFLINE TEST — NEVER PRINT\nM109 S220\nM190 S65\n; CHANGE_LAYER\nG1 X10 E1\n')
            archive.writestr('Metadata/slice_info.config', '<config><plate>'
                            '<metadata key="index" value="1"/><metadata key="prediction" value="123"/>'
                            '<metadata key="weight" value="1.25"/><filament id="1" used_g="1.25"/>'
                            '</plate></config>')
        return SimpleNamespace(returncode=0, stdout='synthetic slicer output', stderr='')

    monkeypatch.setattr(core.subprocess, 'run', external_slicer)
    return project, profiles, official


def run_slice(profiles, **kwargs):
    return core.slice_project('P001', profiles[0], profiles[1], [profiles[2]],
                              bed_type='Textured PEI Plate', **kwargs)


@pytest.mark.parametrize('offline_slice', ['A1', 'P1P'], indirect=True)
def test_offline_slice_delivers_download_without_granting_print_permission(offline_slice):
    project, profiles, _ = offline_slice
    job = run_slice(profiles)
    assert job['offline_ready'] is True and job['offline_only'] is True
    assert job['hardware_verified'] is False and job['dispatch_available'] is False
    assert job['estimates']['prediction_seconds'] == 123
    assert job['estimates']['weight_grams'] == 1.25
    assert job['startup_audit']['ok'] is True
    assert job['configuration_issues'] == [devices.HA_DISPATCH_MESSAGE]
    assert core.public_project_file('P001', f"Print/{job['id']}/job.gcode.3mf").is_file()
    with pytest.raises(core.WorkflowError, match='HA'):
        core.approve('P001', job['id'], '确认打印', True)
    with pytest.raises(core.WorkflowError, match='HA'):
        bambuddy.submit('P001', job['id'], execute=True)
    with pytest.raises(core.WorkflowError, match='HA'):
        official.submit('P001', job['id'], execute=True)
    assert not (project / 'Print' / job['id'] / 'dispatch.json').exists()


def test_offline_profile_requires_binding_to_the_same_model_revision(offline_slice):
    project, profiles, _ = offline_slice
    data = core.read_json(project / 'project.json')
    data['target_printer']['assigned_revision'] = 'v000'
    core.write_json(project / 'project.json', data)
    with pytest.raises(core.WorkflowError, match='revision|版本'):
        run_slice(profiles)
    assert list((project / 'Print').iterdir()) == []


def test_offline_slice_rejects_unproven_manual_profiles(offline_slice):
    project, profiles, _ = offline_slice
    profiles[1].with_suffix('.sources.json').unlink()
    with pytest.raises(core.WorkflowError, match='provenance|来源'):
        run_slice(profiles)
    assert list((project / 'Print').iterdir()) == []


def test_slicer_material_fallback_is_recorded_as_failure(offline_slice, monkeypatch):
    project, profiles, _ = offline_slice
    external_slicer = core.subprocess.run

    def wrong_material(command, **kwargs):
        result = external_slicer(command, **kwargs)
        output = Path(command[command.index('--export-3mf') + 1])
        with zipfile.ZipFile(output) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        settings = json.loads(members['Metadata/project_settings.config'])
        settings['filament_type'] = ['PETG']
        members['Metadata/project_settings.config'] = json.dumps(settings).encode()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, value in members.items():
                archive.writestr(name, value)
        return result

    monkeypatch.setattr(core.subprocess, 'run', wrong_material)
    with pytest.raises(core.WorkflowError, match='material|filament'):
        run_slice(profiles)
    job = core.read_json(next((project / 'Print').glob('*/job.json')))
    assert job['state'] == 'slice_failed' and not job.get('offline_ready')


def test_no_model_extrusion_is_not_an_offline_ready_slice(monkeypatch):
    templates = {key: 'official fixture ' + key for key in slicer_guard.FIELDS}
    monkeypatch.setattr(slicer_guard, 'official_templates', lambda selected=None: templates)
    settings = {**templates, 'printer_model': 'Bambu Lab A1', 'nozzle_diameter': ['0.4'],
                'curr_bed_type': 'Textured PEI Plate', 'filament_type': ['PLA'],
                'nozzle_temperature_initial_layer': ['220'], 'nozzle_temperature': ['220'],
                'textured_plate_temp_initial_layer': ['65'], 'textured_plate_temp': ['65']}
    assert not slicer_guard.audit('M109 S220\nM190 S65\n; CHANGE_LAYER\nG1 X10', settings)['ok']


def test_cli_automatic_profiles_offer_explicit_material_nozzle_and_bed():
    args = cli.parser().parse_args(['slice', 'P001', '--material', 'PETG',
                                    '--nozzle', '0.4', '--bed-type', 'Textured PEI Plate'])
    assert args.material == 'PETG' and args.nozzle == '0.4'
    assert args.machine is None and args.process is None and args.filament is None


@pytest.mark.parametrize('offline_slice', ['A1', 'P1P'], indirect=True)
def test_cli_automatic_profiles_produce_self_contained_offline_job(offline_slice, capsys):
    project, _, official = offline_slice
    assert cli.main(['slice', 'P001', '--material', 'PLA']) == 0
    job = json.loads(capsys.readouterr().out)
    assert job['offline_ready'] is True
    profiles = project / 'Print' / job['id'] / 'Profiles'
    for copied in profiles.glob('*.json'):
        if copied.name.endswith('.sources.json'):
            continue
        receipt = core.read_json(copied.with_suffix('.sources.json'))
        assert receipt['output_sha256'] == core.digest(copied)
        assert all(Path(source['path']).is_relative_to(official) for source in receipt['source_files'])
    assert len(list(profiles.glob('*.sources.json'))) == 3


def test_controlled_input_without_profiles_reports_workflow_error(offline_slice):
    project, _, _ = offline_slice
    source = project / 'Exports/v001/controlled.3mf'
    source.write_bytes(b'Not parsed before the missing-profile validation')
    manifest = core.read_json(source.parent / 'build.json')
    manifest['artifacts']['Exports/v001/controlled.3mf'] = core.digest(source)
    core.write_json(source.parent / 'build.json', manifest)
    with pytest.raises(core.WorkflowError, match='complete machine/process/filament'):
        core.slice_project('P001', input_3mf='Exports/v001/controlled.3mf')


def test_changed_controlled_input_snapshot_cannot_be_delivered(offline_slice, monkeypatch):
    project, profiles, _ = offline_slice
    source = project / 'Exports/v001/controlled.3mf'
    controlled(source)
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members['Metadata/model_settings.config'] = members['Metadata/model_settings.config'].replace(
        b'value="2"', b'value="1"')
    with zipfile.ZipFile(source, 'w') as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    manifest = core.read_json(source.parent / 'build.json')
    manifest['artifacts']['Exports/v001/controlled.3mf'] = core.digest(source)
    core.write_json(source.parent / 'build.json', manifest)
    external_slicer = core.subprocess.run

    def changed_snapshot(command, **kwargs):
        result = external_slicer(command, **kwargs)
        Path(command[-1]).write_bytes(b'changed while the external slicer was running')
        return result

    monkeypatch.setattr(core.subprocess, 'run', changed_snapshot)
    with pytest.raises(core.WorkflowError, match='snapshot changed'):
        run_slice(profiles, input_3mf='Exports/v001/controlled.3mf')
    job = core.read_json(next((project / 'Print').glob('*/job.json')))
    assert job['state'] == 'slice_failed' and not job.get('offline_ready')


@pytest.mark.parametrize('change', ['target', 'revision', 'profiles', 'wrapper'])
def test_changing_slice_inputs_during_execution_prevents_delivery(offline_slice, monkeypatch, change):
    project, profiles, _ = offline_slice
    external_slicer = core.subprocess.run

    def changed_during_run(command, **kwargs):
        result = external_slicer(command, **kwargs)
        if change == 'profiles':
            process = Path(command[command.index('--load-settings') + 1].split(';')[1])
            values = core.read_json(process)
            values['layer_height'] = '0.3'
            core.write_json(process, values)
        elif change == 'wrapper':
            Path(command[0]).write_text('changed external slicer wrapper')
        else:
            values = core.read_json(project / 'project.json')
            if change == 'target':
                values['target_printer']['id'] = 'another-printer'
            else:
                values['current_revision'] = 'v002'
            core.write_json(project / 'project.json', values)
        return result

    monkeypatch.setattr(core.subprocess, 'run', changed_during_run)
    with pytest.raises(core.WorkflowError, match='changed|revision'):
        run_slice(profiles)
    job = core.read_json(next((project / 'Print').glob('*/job.json')))
    assert job['state'] == 'slice_failed' and not job.get('offline_ready')


def test_builtin_engine_identity_is_recorded_and_required_for_existing_jobs(offline_slice, monkeypatch):
    project, profiles, _ = offline_slice
    identity = {'receipt_sha256': '1' * 64, 'engine_sha256': '2' * 64}
    monkeypatch.setattr(slicer_runtime, 'fingerprint', lambda path: dict(identity))
    job = run_slice(profiles)
    assert job['slicer_engine'] == identity
    directory = project / 'Print' / job['id']
    core.job_fingerprint(project, directory, job)
    identity['engine_sha256'] = '3' * 64
    with pytest.raises(core.WorkflowError, match='Slicer|slicer|engine'):
        core.job_fingerprint(project, directory, job)


def test_builtin_engine_change_during_slicing_prevents_delivery(offline_slice, monkeypatch):
    project, profiles, _ = offline_slice
    identity = {'receipt_sha256': '1' * 64, 'engine_sha256': '2' * 64}
    monkeypatch.setattr(slicer_runtime, 'fingerprint', lambda path: dict(identity))
    external_slicer = core.subprocess.run

    def changed_engine(command, **kwargs):
        result = external_slicer(command, **kwargs)
        identity['engine_sha256'] = '3' * 64
        return result

    monkeypatch.setattr(core.subprocess, 'run', changed_engine)
    with pytest.raises(core.WorkflowError, match='Slicer|slicer|engine'):
        run_slice(profiles)
    job = core.read_json(next((project / 'Print').glob('*/job.json')))
    assert job['state'] == 'slice_failed' and not job.get('offline_ready')


@pytest.mark.parametrize('change', ['model', 'assigned_revision'])
def test_existing_slice_identity_rejects_same_device_with_changed_binding(offline_slice, change):
    project, profiles, _ = offline_slice
    job = run_slice(profiles)
    values = core.read_json(project / 'project.json')
    values['target_printer'][change] = 'P1P' if change == 'model' else 'v002'
    core.write_json(project / 'project.json', values)
    with pytest.raises(core.WorkflowError, match='changed|stale'):
        core.job_fingerprint(project, project / 'Print' / job['id'], job)


@pytest.mark.parametrize('model', ['A1', 'P1P'])
def test_single_nozzle_guard_accepts_offline_reference_without_ams(model, monkeypatch):
    templates = {key: 'official fixture ' + model + key for key in slicer_guard.FIELDS}
    monkeypatch.setattr(slicer_guard, 'official_templates', lambda selected=None: templates)
    settings = {**templates, 'printer_model': 'Bambu Lab ' + model, 'nozzle_diameter': ['0.4'],
                'curr_bed_type': 'Textured PEI Plate', 'filament_type': ['PLA'],
                'nozzle_temperature_initial_layer': ['220'], 'nozzle_temperature': ['220'],
                'textured_plate_temp_initial_layer': ['65'], 'textured_plate_temp': ['65']}
    gcode = 'M109 S220\nM190 S65\n; CHANGE_LAYER\nG1 X10 E1\n'
    result = slicer_guard.audit(gcode, settings, [1])
    assert result['ok'] is True
    assert not slicer_guard.audit(gcode.replace('S220', 'S205'), settings, [1])['ok']
    assert not slicer_guard.audit(gcode.replace('S65', 'S45'), settings, [1])['ok']


@pytest.mark.parametrize('change', [
    {'nozzle_diameter': ['0.6']}, {'filament_type': ['ABS']},
    {'filament_type': ['PLA', 'PETG']}, {'curr_bed_type': 'Cool Plate'},
])
def test_single_nozzle_guard_rejects_unvalidated_combinations(change, monkeypatch):
    templates = {key: 'official fixture ' + key for key in slicer_guard.FIELDS}
    monkeypatch.setattr(slicer_guard, 'official_templates', lambda selected=None: templates)
    settings = {**templates, 'printer_model': 'Bambu Lab A1', 'nozzle_diameter': ['0.4'],
                'curr_bed_type': 'Textured PEI Plate', 'filament_type': ['PLA'],
                'nozzle_temperature_initial_layer': ['220'], 'nozzle_temperature': ['220'],
                'textured_plate_temp_initial_layer': ['65'], 'textured_plate_temp': ['65'], **change}
    assert not slicer_guard.audit('M109 S220\nM190 S65\n; CHANGE_LAYER\nG1 X10 E1', settings)['ok']


@pytest.mark.parametrize('gcode', [
    'M82\nG92 E100\nM109 S220\nM190 S65\n; CHANGE_LAYER\nG1 X10 E99\n',
    'M83\nM109 T1 S220\nM190 S65\n; CHANGE_LAYER\nG1 X10 E1\n',
    'M83\nM109 N1 S220\nM190 S65\n; CHANGE_LAYER\nG1 X10 E1\n',
])
def test_single_nozzle_guard_rejects_retraction_or_wrong_physical_heater(gcode, monkeypatch):
    templates = {key: 'official fixture ' + key for key in slicer_guard.FIELDS}
    monkeypatch.setattr(slicer_guard, 'official_templates', lambda selected=None: templates)
    settings = {**templates, 'printer_model': 'Bambu Lab A1', 'nozzle_diameter': ['0.4'],
                'curr_bed_type': 'Textured PEI Plate', 'filament_type': ['PLA'],
                'nozzle_temperature_initial_layer': ['220'], 'nozzle_temperature': ['220'],
                'textured_plate_temp_initial_layer': ['65'], 'textured_plate_temp': ['65']}
    assert not slicer_guard.audit(gcode, settings)['ok']
