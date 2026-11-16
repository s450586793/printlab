"""Select and resolve the narrow built-in A1/P1P offline slicing presets."""
import importlib.util
import json
from pathlib import Path

from . import core, platforms


BED_TYPE = 'Textured PEI Plate'


def _arguments(model, material):
    if not isinstance(model, str) or not isinstance(material, str):
        raise core.WorkflowError('Only A1/P1P and PLA/PETG are supported by built-in slicing')
    model = model.strip().upper()
    if model.startswith('BAMBU LAB '):
        model = model[len('BAMBU LAB '):]
    material = material.strip().upper()
    if model not in {'A1', 'P1P'} or material not in {'PLA', 'PETG'}:
        raise core.WorkflowError('Only A1/P1P and PLA/PETG are supported by built-in slicing')
    return model, material


def _resolver(directory):
    spec = importlib.util.spec_from_file_location(
        'printlab_builtin_profile_resolver', Path(__file__).parents[1] / 'resolve_bambu_profiles.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('Missing or symbolic-link official profile category')
    directories = {directory}
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symbolic links are not allowed in official profile categories')
        if path.suffix == '.json' and path.is_file():
            if not path.resolve().is_relative_to(directory):
                raise ValueError('Official profile escaped its category')
            directories.add(path.parent)
    result = module.ProfileResolver(directory)
    # P1P filament presets inherit common profiles from the parent category.
    for child in sorted(directories - {directory}):
        extra = module.ProfileResolver(child)
        for name, path in extra.files.items():
            if name in result.files and result.files[name] != path:
                raise ValueError('Ambiguous official profile name: ' + name)
            result.files[name] = path
    return result


def _selection(model, material):
    model, material = _arguments(model, material)
    try:
        root = platforms.profile_directory(core.config()).resolve()
        bundle = root / 'BBL'
        if bundle.is_symlink():
            raise ValueError('Symbolic-link official profile bundle')
        resolvers = {kind: _resolver(bundle / kind) for kind in ('machine', 'process', 'filament')}
        names = {'machine': f'Bambu Lab {model} 0.4 nozzle',
                 'process': f'0.20mm Standard @BBL {model}',
                 'filament': f'Generic {material} @BBL {model}'}
        values = {kind: resolvers[kind].resolve(name) for kind, name in names.items()}
        for kind, value in values.items():
            if value.get('type') != kind or value.get('name') != names[kind]:
                raise ValueError('Official preset type or identity differs from selection')
        machine = values['machine']
        if (machine.get('printer_model') != f'Bambu Lab {model}'
                or str(machine.get('printer_variant')) != '0.4'
                or machine.get('nozzle_diameter') != ['0.4']):
            raise ValueError('Official machine preset must match the selected model and 0.4 nozzle')
        if float(values['process'].get('layer_height', 0)) != 0.2:
            raise ValueError('Official process must use 0.20 mm layers')
        for kind in ('process', 'filament'):
            compatible = values[kind].get('compatible_printers')
            if not isinstance(compatible, list) or names['machine'] not in compatible:
                raise ValueError('Official preset is not compatible with the selected machine')
        if values['filament'].get('filament_type') != [material]:
            raise ValueError('Official filament preset does not match the selected material')
        paths = {kind: resolvers[kind].files[name] for kind, name in names.items()}
        return paths, values, resolvers
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise core.WorkflowError('Unable to resolve official slicer profiles: ' + str(exc)) from exc


def select(model, material):
    """Return the original official preset paths after resolving and checking them."""
    return _selection(model, material)[0]


def resolve(model, material, output_directory):
    """Write complete candidates and source manifests without replacing any file."""
    paths, values, resolvers = _selection(model, material)
    try:
        directory = Path(output_directory)
        outputs = {kind: directory / (kind + '.json') for kind in paths}
        for path in outputs.values():
            for candidate in (path, path.with_suffix('.sources.json')):
                if candidate.exists() or candidate.is_symlink():
                    raise core.WorkflowError('Candidate output already exists; use a new version')
        root = platforms.profile_directory(core.config()).resolve()
        if directory.resolve().is_relative_to(root) or directory.is_symlink():
            raise core.WorkflowError('Candidate output must be outside the official profile directory')
        values['process']['curr_bed_type'] = BED_TYPE
        directory.mkdir(parents=True, exist_ok=True)
        for kind, path in outputs.items():
            with path.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(values[kind], ensure_ascii=False, indent=2) + '\n')
            receipt = {'schema_version': 1, 'profile': str(paths[kind]),
                       'output_sha256': core.digest(path),
                       'merge_order': 'inherits -> ordered includes -> own explicit values',
                       'source_files': list(resolvers[kind].sources.values()),
                       'scope': 'configuration candidate, not print approval or physical validation'}
            if kind == 'process':
                receipt['overrides'] = {'curr_bed_type': BED_TYPE}
            with path.with_suffix('.sources.json').open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        return outputs
    except (OSError, ValueError, TypeError) as exc:
        raise core.WorkflowError('Unable to write official slicer profile candidates: ' + str(exc)) from exc
