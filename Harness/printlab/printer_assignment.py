"""Choose a printer after a verified model exists, without sending device commands."""
from __future__ import annotations

import math
import re

import httpx

from . import core, devices


# Manufacturer specifications checked 2026-11-14. These are planning bounds,
# not slicer plate masks, installed nozzle declarations, or print permission.
SPECIFICATIONS = {
    'A1': {
        'volume': [256, 256, 256],
        'url': 'https://store.bblcdn.com/8137fad1525a4454ac8a28502edbc919.pdf',
        'materials': {'PLA', 'PETG', 'TPU', 'PVA'},
    },
    'P1P': {
        'volume': [256, 256, 256],
        'default_height': 250,
        'url': 'https://bambu-lab-global.myshopify.com/products/p1p',
        'materials': {'PLA', 'PETG', 'TPU', 'PVA', 'PET'},
    },
}

PENDING_CHECKS = '材料、实际喷嘴、打印板、摆放与支撑仍需在切片时核对；此处不代表可以直接开打。'


def _model(project_path, project):
    revision = project.get('current_revision')
    if not isinstance(revision, str) or not re.fullmatch(r'v\d{3,}', revision):
        return None
    try:
        path = core.safe_path(project_path, f'Exports/{revision}/build.json')
        build = core.read_json(path)
        if not isinstance(build, dict) or build.get('verified') is not True:
            return None
        checks = build.get('checks')
        if not isinstance(checks, dict) or not checks or any(value is not True for value in checks.values()):
            return None
        if build.get('revision', revision) != revision:
            return None
        dimensions = build.get('dimensions_mm')
        if (not isinstance(dimensions, list) or len(dimensions) != 3
                or any(type(value) not in {int, float} or not math.isfinite(value) or value <= 0 for value in dimensions)):
            return None
        return [float(value) for value in dimensions]
    except (core.WorkflowError, OSError, ValueError, TypeError, OverflowError):
        return None


def _material(project_path, revision):
    """Only a structured model parameter is a material declaration; never guess from prose."""
    try:
        path = core.safe_path(project_path, f'Design/{revision}/params.json')
        params = core.read_json(path)
        value = params.get('material') if isinstance(params, dict) else None
        return value.strip().upper() if isinstance(value, str) and value.strip() else None
    except (core.WorkflowError, OSError, ValueError, TypeError):
        return None


def _option(printer, dimensions, material, stale):
    model = printer.get('model')
    normalized = re.sub(r'^BAMBU\s+LAB\s+', '', model.strip().upper()) if isinstance(model, str) else ''
    specification = SPECIFICATIONS.get(normalized)
    connected = printer.get('connected')
    online = connected if type(connected) is bool and not stale else None
    option = {
        'printer_id': printer['id'],
        'name': printer.get('name') or model or str(printer['id']),
        'model': model, 'fit': 'unknown', 'reasons': [],
        'dimensions_mm': dimensions, 'build_volume_mm': None,
        'online': online, 'selection_available': False,
        'specification_url': None, 'material': material, 'material_fit': 'unknown',
    }
    if specification is None:
        option['reasons'].append('尚无已核实的此机型成形范围，暂不能据此分配。')
    else:
        volume = specification['volume']
        option.update(build_volume_mm=list(volume), specification_url=specification['url'])
        if any(size > limit for size, limit in zip(dimensions, volume)):
            option['fit'] = 'too_large'
            option['reasons'].append('模型当前轴向包围盒超出标称成形范围；可调整摆放或拆件后重新核对。')
        elif dimensions[2] > specification.get('default_height', volume[2]):
            option['reasons'].append('模型当前高度超过此机型默认切片高度 250 mm；未核实额外空间的安全设置，需调整或核对后再分配。')
        else:
            option.update(fit='fits', selection_available=True)
            option['reasons'].append('当前轴向包围盒在标称范围内，尺寸初筛通过。')
        if material in specification['materials']:
            option['material_fit'] = 'manufacturer_recommended'
            option['reasons'].append(f'模型参数材料 {material} 在此机型原厂配置推荐范围内；实际装料与喷嘴尚待核对。')
        elif material:
            option.update(selection_available=False, material_fit='needs_review')
            if option['fit'] == 'fits':
                option['fit'] = 'unknown'
            option['reasons'].append(f'模型参数材料 {material} 需要核对机型限制、围框或硬件配置，暂不据此自动分配。')
        if normalized == 'P1P':
            option['reasons'].append('P1P 的切刀挡块占用部分板面，实际可用区域仍须以切片检查为准。')
    if online is False:
        option['reasons'].append('设备当前离线；可以安排后续制作，开始制作前需重新核对状态。')
    elif online is None:
        option['reasons'].append('设备当前在线状态尚未核实。')
    option['reasons'].append(PENDING_CHECKS)
    return option


def options(pid):
    """Return model-bound planning candidates, never an implicit first-device assignment."""
    path = core.project_path(pid)
    project = core.read_json(path / 'project.json')
    target = project.get('target_printer') or None
    revision = project.get('current_revision')
    dimensions = _model(path, project)
    result = {
        'ready': dimensions is not None,
        'reason': '先完成模型并通过尺寸检查，再根据模型选择打印机。',
        'revision': revision, 'dimensions_mm': dimensions,
        'target_printer': target, 'selected_printer_id': target.get('id') if isinstance(target, dict) else None,
        'assignment_current': bool(isinstance(target, dict) and target.get('assigned_revision') == revision and revision),
        'options': [],
    }
    if dimensions is None:
        return result
    try:
        listing = devices.device_list()
        if not isinstance(listing, dict) or not isinstance(listing.get('devices', []), list):
            raise core.WorkflowError('Invalid printer list')
        material = _material(path, revision)
        result['options'] = [
            _option(printer, dimensions, material, bool(listing.get('stale')))
            for printer in listing.get('devices', [])
            if isinstance(printer, dict) and type(printer.get('id')) in {str, int} and str(printer['id']).strip()
        ]
    except (core.WorkflowError, httpx.HTTPError, OSError, ValueError, TypeError):
        result['reason'] = '模型已完成，暂时无法读取打印机资料，请刷新后重试。'
        return result
    result['reason'] = ('按当前模型做尺寸初筛，选择后仍需核对实际材料、喷嘴与切片摆放。'
                        if result['options'] else '模型已完成，尚未读取到已配置的打印机。')
    return result


def select(pid, printer_id):
    """Bind a current eligible model to a printer; existing sliced jobs stay unchanged."""
    result = options(pid)
    if not result['ready']:
        raise core.WorkflowError(result['reason'])
    candidate = next((row for row in result['options'] if str(row['printer_id']) == str(printer_id)), None)
    if candidate is None:
        raise core.WorkflowError('未找到此打印机，请刷新设备列表。')
    if not candidate['selection_available']:
        raise core.WorkflowError('当前模型与此打印机的适配情况尚未通过初筛，请先处理尺寸或材料限制。')
    return core.bind_printer(pid, printer_id, revision=result['revision'])
