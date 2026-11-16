import importlib
import json
from pathlib import Path

import pytest

from printlab import core


def put(directory, name, **values):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + '.json')
    path.write_text(json.dumps({'name': name, **values}), encoding='utf-8')
    return path


@pytest.fixture
def official_profiles(tmp_path, monkeypatch):
    root = tmp_path / 'profiles'
    monkeypatch.setattr(core, 'config', lambda: {'slicer_profiles_path': str(root)})
    machine = root / 'BBL/machine'
    process = root / 'BBL/process'
    filament = root / 'BBL/filament'
    put(machine, 'machine_base', type='machine', printable_height='256',
        machine_start_gcode='BASE START', machine_end_gcode='BASE END')
    put(process, 'process_base', type='process', layer_height='0.2', curr_bed_type='Cool Plate')
    for material, temperature in [('PLA', '220'), ('PETG', '255')]:
        put(filament, f'Generic {material} @base', type='filament', filament_type=[material],
            nozzle_temperature=[temperature], nozzle_temperature_initial_layer=[temperature],
            textured_plate_temp=['65' if material == 'PLA' else '80'])
    for model in ('A1', 'P1P'):
        name = f'Bambu Lab {model} 0.4 nozzle'
        put(machine, name + ' start', machine_start_gcode=f'{model} OFFICIAL START')
        put(machine, name, type='machine', instantiation='true',
            inherits='machine_base', include=[name + ' start'], printer_model=f'Bambu Lab {model}',
            printer_variant='0.4', nozzle_diameter=['0.4'])
        put(process, f'0.20mm Standard @BBL {model}', type='process', instantiation='true',
            inherits='process_base', compatible_printers=[name])
        for material in ('PLA', 'PETG'):
            directory = filament / 'P1P' if model == 'P1P' else filament
            put(directory, f'Generic {material} @BBL {model}', type='filament', instantiation='true',
                inherits=f'Generic {material} @base', compatible_printers=[name])
    return root


@pytest.mark.parametrize('model,material', [('A1', 'PLA'), ('A1', 'PETG'), ('P1P', 'PLA'), ('P1P', 'PETG')])
def test_selects_only_matching_official_single_material_presets(official_profiles, model, material):
    profiles = importlib.import_module('printlab.slicer_profiles')
    selected = profiles.select(model, material)
    assert set(selected) == {'machine', 'process', 'filament'}
    assert selected['machine'] == official_profiles / f'BBL/machine/Bambu Lab {model} 0.4 nozzle.json'
    assert selected['process'] == official_profiles / f'BBL/process/0.20mm Standard @BBL {model}.json'
    directory = official_profiles / ('BBL/filament/P1P' if model == 'P1P' else 'BBL/filament')
    assert selected['filament'] == directory / f'Generic {material} @BBL {model}.json'


def test_resolves_nested_inheritance_preserves_templates_and_records_all_sources(official_profiles, tmp_path):
    profiles = importlib.import_module('printlab.slicer_profiles')
    original = {p: p.read_bytes() for p in official_profiles.rglob('*.json')}
    output = tmp_path / 'candidate'
    selected = profiles.resolve('P1P', 'PETG', output)
    assert selected == {kind: output / (kind + '.json') for kind in ('machine', 'process', 'filament')}
    machine = json.loads(selected['machine'].read_text())
    assert machine['machine_start_gcode'] == 'P1P OFFICIAL START'
    assert machine['machine_end_gcode'] == 'BASE END'
    assert machine['nozzle_diameter'] == ['0.4']
    assert json.loads(selected['process'].read_text())['curr_bed_type'] == 'Textured PEI Plate'
    assert json.loads(selected['filament'].read_text())['filament_type'] == ['PETG']
    for kind, path in selected.items():
        data = json.loads(path.read_text())
        assert not data.get('inherits') and not data.get('include')
        receipt = json.loads(path.with_suffix('.sources.json').read_text())
        assert receipt['output_sha256'] == core.digest(path)
        assert receipt['source_files']
        for source in receipt['source_files']:
            assert source['path'] in {str(p) for p in original}
            assert core.digest(Path(source['path'])) == source['sha256']
        if kind == 'filament':
            assert {source['path'] for source in receipt['source_files']} == {
                str(official_profiles / 'BBL/filament/Generic PETG @base.json'),
                str(official_profiles / 'BBL/filament/P1P/Generic PETG @BBL P1P.json'),
            }
    assert all(path.read_bytes() == content for path, content in original.items())


@pytest.mark.parametrize('model,material', [('X2D', 'PLA'), ('A1 mini', 'PLA'), ('A1 0.6', 'PLA'),
                                          ('A1', 'ABS'), ('P1P', 'PETG-CF'), (None, 'PLA'), ('A1', ['PLA'])])
def test_rejects_unsupported_machine_or_material_before_writing(official_profiles, tmp_path, model, material):
    profiles = importlib.import_module('printlab.slicer_profiles')
    output = tmp_path / 'candidate'
    with pytest.raises(core.WorkflowError, match='supported'):
        profiles.resolve(model, material, output)
    assert not output.exists()


def test_accepts_canonical_device_names_and_normalized_material(official_profiles):
    profiles = importlib.import_module('printlab.slicer_profiles')
    assert profiles.select(' Bambu Lab P1P ', ' petg ')['filament'].name == 'Generic PETG @BBL P1P.json'


def test_missing_or_cyclic_official_dependency_is_rejected_before_output(official_profiles, tmp_path):
    profiles = importlib.import_module('printlab.slicer_profiles')
    path = official_profiles / 'BBL/machine/machine_base.json'
    data = json.loads(path.read_text())
    for parent in ('missing_official_base', 'Bambu Lab A1 0.4 nozzle'):
        path.write_text(json.dumps({**data, 'inherits': parent}))
        with pytest.raises(core.WorkflowError, match='official'):
            profiles.resolve('A1', 'PLA', tmp_path / 'candidate')
        assert not (tmp_path / 'candidate').exists()


@pytest.mark.parametrize('kind,changes', [
    ('machine', {'printer_model': 'Bambu Lab A1 mini'}),
    ('machine', {'nozzle_diameter': ['0.6']}),
    ('machine', {'printer_variant': '0.6'}),
    ('machine', {'type': 'filament'}),
    ('process', {'layer_height': '0.16'}),
    ('process', {'compatible_printers': ['Bambu Lab A1 mini 0.4 nozzle']}),
    ('filament', {'filament_type': ['ABS']}),
    ('filament', {'compatible_printers': []}),
])
def test_rejects_official_preset_with_wrong_device_nozzle_or_material(official_profiles, kind, changes):
    profiles = importlib.import_module('printlab.slicer_profiles')
    paths = {'machine': 'BBL/machine/Bambu Lab A1 0.4 nozzle.json',
             'process': 'BBL/process/0.20mm Standard @BBL A1.json',
             'filament': 'BBL/filament/Generic PLA @BBL A1.json'}
    path = official_profiles / paths[kind]
    path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))
    with pytest.raises(core.WorkflowError, match='official'):
        profiles.select('A1', 'PLA')


def test_duplicate_preset_names_and_symlink_sources_are_rejected(official_profiles, tmp_path):
    profiles = importlib.import_module('printlab.slicer_profiles')
    original = official_profiles / 'BBL/filament/Generic PLA @BBL A1.json'
    duplicate = official_profiles / 'BBL/filament/extra' / original.name
    duplicate.parent.mkdir()
    duplicate.write_bytes(original.read_bytes())
    with pytest.raises(core.WorkflowError, match='official'):
        profiles.select('A1', 'PLA')
    duplicate.unlink()
    external = tmp_path / 'external.json'
    external.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(external)
    with pytest.raises(core.WorkflowError, match='official'):
        profiles.select('A1', 'PLA')


@pytest.mark.parametrize('existing', ['machine.json', 'process.sources.json', 'filament.json'])
def test_preserves_existing_candidates_without_partial_writes(official_profiles, tmp_path, existing):
    profiles = importlib.import_module('printlab.slicer_profiles')
    output = tmp_path / 'candidate'
    output.mkdir()
    preserved = output / existing
    preserved.write_bytes(b'preserve')
    with pytest.raises(core.WorkflowError, match='already exists'):
        profiles.resolve('A1', 'PLA', output)
    assert {p.name for p in output.iterdir()} == {existing}
    assert preserved.read_bytes() == b'preserve'


def test_rejects_missing_or_invalid_official_presets(official_profiles):
    profiles = importlib.import_module('printlab.slicer_profiles')
    path = official_profiles / 'BBL/process/0.20mm Standard @BBL A1.json'
    path.write_text('{bad json')
    with pytest.raises(core.WorkflowError, match='official'):
        profiles.select('A1', 'PLA')
    path.unlink()
    with pytest.raises(core.WorkflowError, match='official'):
        profiles.select('A1', 'PLA')


@pytest.mark.parametrize('content', ['null', '[]'])
def test_malformed_official_profile_shapes_fail_with_a_workflow_error(official_profiles, content):
    profiles = importlib.import_module('printlab.slicer_profiles')
    path = official_profiles / 'BBL/machine/machine_base.json'
    path.write_text(content)
    with pytest.raises(core.WorkflowError, match='official'):
        profiles.select('A1', 'PLA')


@pytest.mark.parametrize('kind', ['machine', 'process', 'filament'])
def test_symlink_category_cannot_supply_presets(official_profiles, tmp_path, kind):
    profiles = importlib.import_module('printlab.slicer_profiles')
    directory = official_profiles / 'BBL' / kind
    moved = tmp_path / ('external-' + kind)
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(core.WorkflowError, match='official'):
        profiles.select('P1P', 'PETG')


def test_candidates_cannot_modify_the_official_profile_tree(official_profiles):
    profiles = importlib.import_module('printlab.slicer_profiles')
    output = official_profiles / 'candidates'
    with pytest.raises(core.WorkflowError, match='outside'):
        profiles.resolve('A1', 'PLA', output)
    assert not output.exists()


def test_output_io_error_is_reported_without_replacing_existing_file(official_profiles, tmp_path):
    profiles = importlib.import_module('printlab.slicer_profiles')
    output = tmp_path / 'candidate'
    output.write_bytes(b'preserve')
    with pytest.raises(core.WorkflowError, match='write'):
        profiles.resolve('A1', 'PLA', output)
    assert output.read_bytes() == b'preserve'
