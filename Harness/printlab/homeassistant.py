"""Read-only Home Assistant printer data and authenticated cache-file access.

HA entity timestamps are not printer heartbeats. Cache files are not print runs.
No device service, file upload or print command is issued by this adapter.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import PurePosixPath
import re
import tempfile
import threading
import time
from urllib.parse import quote, unquote, urlparse, urlunparse

import httpx
from PIL import Image, UnidentifiedImageError
import websocket

from . import core

PROVIDER = 'homeassistant'
CACHE_SECONDS = 15
MAX_FILE_BYTES = 150 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_credentials_lock = threading.RLock()
_snapshot_lock = threading.Lock()
_files_lock = threading.Lock()
_snapshots = {}
_file_lists = {}


class HAConnectionError(core.WorkflowError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _url(value):
    if not isinstance(value, str):
        raise core.WorkflowError('请先连接 Home Assistant。')
    parsed = urlparse(value.strip())
    try:
        valid_port = parsed.port is None or 0 < parsed.port < 65536
    except ValueError:
        valid_port = False
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in {'', '/'} or not valid_port):
        raise core.WorkflowError('Home Assistant 地址无效；请填写完整的 http 或 https 地址。')
    return urlunparse((parsed.scheme, parsed.netloc, '', '', '', ''))


def _credential_path():
    return core.safe_path(core.ROOT, 'Runtime/private/homeassistant.json', require_file=False)


def _settings():
    try:
        value = core.read_json(_credential_path(), {})
        if not isinstance(value, dict):
            raise ValueError
        return {**value, 'base_url': _url(value.get('base_url'))}
    except (OSError, ValueError, TypeError):
        raise core.WorkflowError('请先连接 Home Assistant。') from None


def _save_credentials(value):
    path = _credential_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.ha-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _http(base):
    return httpx.Client(base_url=base, timeout=15, trust_env=False, follow_redirects=False)


def connect(base_url, username, password):
    """Save an ordinary HA OAuth session; never save the account password."""
    base = _url(base_url)
    client_id = base + '/'
    try:
        with _credentials_lock, _http(base) as client:
            response = client.post('/auth/login_flow', json={
                'client_id': client_id, 'handler': ['homeassistant', None],
                'redirect_uri': client_id + '?auth_callback=1',
            })
            response.raise_for_status()
            flow = response.json()
            response = client.post('/auth/login_flow/' + flow['flow_id'], json={
                'client_id': client_id, 'username': username, 'password': password,
            })
            response.raise_for_status()
            login = response.json()
            if login.get('type') != 'create_entry':
                if login.get('step_id') not in {'init', None}:
                    raise core.WorkflowError('Home Assistant 需要额外登录验证，请在登录页面完成后重试。')
                raise core.WorkflowError('Home Assistant 登录未完成，请核对账户信息。')
            response = client.post('/auth/token', data={
                'grant_type': 'authorization_code', 'code': login['result'], 'client_id': client_id,
            })
            response.raise_for_status()
            auth = response.json()
            if not auth.get('access_token') or not auth.get('refresh_token'):
                raise ValueError
            _save_credentials({
                'base_url': base, 'client_id': client_id,
                'access_token': auth['access_token'], 'refresh_token': auth['refresh_token'],
                'expires_at': time.time() + float(auth.get('expires_in', 1800)),
            })
        clear_cache()
        return {'connected': True, 'base_url': base, 'provider': PROVIDER}
    except core.WorkflowError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError):
        raise core.WorkflowError('无法登录 Home Assistant，请检查地址、账户及网络连接。') from None


def _refresh_settings(settings):
    """Refresh an in-memory candidate; persistence belongs to its caller."""
    settings = copy.deepcopy(settings)
    if not isinstance(settings.get('access_token'), str) or not settings['access_token']:
        raise HAConnectionError('authentication', 'Home Assistant 登录凭据尚未配置。')
    expiry = _number(settings.get('expires_at'))
    if expiry is not None and expiry <= time.time() + 60:
        if not settings.get('refresh_token'):
            raise HAConnectionError('authentication', 'Home Assistant 登录已过期，请重新连接。')
        try:
            with _http(settings['base_url']) as client:
                response = client.post('/auth/token', data={
                    'grant_type': 'refresh_token', 'refresh_token': settings['refresh_token'],
                    'client_id': settings.get('client_id', settings['base_url'] + '/'),
                })
                response.raise_for_status()
                auth = response.json()
            if not isinstance(auth, dict) or not isinstance(auth.get('access_token'), str) or not auth['access_token']:
                raise ValueError
            settings.update(access_token=auth['access_token'], expires_at=time.time() + float(auth.get('expires_in', 1800)))
            if auth.get('refresh_token'):
                settings['refresh_token'] = auth['refresh_token']
        except httpx.HTTPStatusError as exc:
            code = 'authentication' if exc.response.status_code in {400, 401} else 'permission' if exc.response.status_code == 403 else 'network'
            raise HAConnectionError(code, 'Home Assistant 登录续期失败，请重新连接。') from None
        except (httpx.HTTPError, OSError):
            raise HAConnectionError('network', 'Home Assistant 登录续期失败，请检查网络连接。') from None
        except (ValueError, KeyError, TypeError):
            raise HAConnectionError('invalid_response', 'Home Assistant 登录续期失败，请重新连接。') from None
    return settings


def _authenticated_settings():
    with _credentials_lock:
        original = _settings()
        settings = _refresh_settings(original)
        if settings != original:
            _save_credentials(settings)
        return settings


class Client:
    def __init__(self, settings=None):
        self.settings = _authenticated_settings() if settings is None else copy.deepcopy(settings)
        self.http = _http(self.settings['base_url'])
        self.http.headers['Authorization'] = 'Bearer ' + self.settings['access_token']

    def close(self):
        self.http.close()

    def get(self, path, **kwargs):
        response = self.http.get(path, **kwargs)
        response.raise_for_status()
        return response.json()


def management_url():
    try:
        return _settings()['base_url']
    except core.WorkflowError:
        return None


def clear_cache():
    with _snapshot_lock:
        _snapshots.clear()
    with _files_lock:
        _file_lists.clear()


def _identity(settings):
    digest = hashlib.sha256((settings.get('access_token', '') + '\0' + settings.get('refresh_token', '')).encode()).hexdigest()
    return str(core.ROOT), settings['base_url'], digest


def _read_snapshot(client):
    parsed = urlparse(client.settings['base_url'])
    url = urlunparse(('wss' if parsed.scheme == 'https' else 'ws', parsed.netloc, '/api/websocket', '', '', ''))
    deadline = time.monotonic() + 20
    socket = websocket.create_connection(url, timeout=15, http_no_proxy=['*'], redirect_limit=0)
    try:
        def receive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HAConnectionError('network', 'Home Assistant 数据读取超时。')
            socket.settimeout(remaining)
            frame = socket.recv()
            if not isinstance(frame, str) or len(frame) > 16 * 1024 * 1024:
                raise ValueError('Invalid HA response')
            value = json.loads(frame)
            if not isinstance(value, dict):
                raise ValueError('Invalid HA response')
            return value
        if receive().get('type') != 'auth_required':
            raise ValueError('Unexpected authentication flow')
        socket.send(json.dumps({'type': 'auth', 'access_token': client.settings['access_token']}))
        if receive().get('type') != 'auth_ok':
            raise HAConnectionError('authentication', 'Home Assistant 登录已失效，请重新连接。')
        commands = [
            ('entries', {'type': 'config_entries/get', 'domain': 'bambu_lab'}),
            ('manifest', {'type': 'manifest/get', 'integration': 'bambu_lab'}),
            ('devices', {'type': 'config/device_registry/list'}),
            ('registry', {'type': 'config/entity_registry/list'}),
            ('states', {'type': 'get_states'}),
        ]
        snapshot = {}
        for index, (name, command) in enumerate(commands, 1):
            socket.send(json.dumps({'id': index, **command}))
            response = receive()
            if response.get('id') != index or response.get('success') is not True:
                if name == 'manifest':
                    snapshot[name] = {}
                    continue
                raise HAConnectionError('permission', 'Home Assistant 未允许读取拓竹设备资料。')
            snapshot[name] = response.get('result')
        snapshot['updated_at'] = core.now()
        return snapshot
    finally:
        socket.close()


def _snapshot():
    settings = _authenticated_settings()
    key = _identity(settings)
    with _snapshot_lock:
        cached = _snapshots.get(key)
        if cached and 0 <= time.monotonic() - cached[0] < CACHE_SECONDS:
            return copy.deepcopy(cached[1])
        client = Client()
        try:
            value = _read_snapshot(client)
            _snapshots.clear()
            _snapshots[_identity(client.settings)] = (time.monotonic(), value)
            return copy.deepcopy(value)
        except (websocket.WebSocketException, httpx.HTTPError, OSError, ValueError, TypeError, KeyError):
            if cached:
                return {**copy.deepcopy(cached[1]), '_stale': True}
            raise core.WorkflowError('无法读取 Home Assistant 的拓竹设备，请检查连接。') from None
        finally:
            client.close()


def _list(value):
    return value if isinstance(value, list) else []


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _text(value, maximum=160):
    return value[:maximum] if isinstance(value, str) else None


def _printers(snapshot):
    entries = snapshot.get('entries')
    entries = entries.get('entries', []) if isinstance(entries, dict) else _list(entries)
    ids = {e.get('entry_id') for e in entries if isinstance(e, dict) and e.get('domain') == 'bambu_lab'}
    result = []
    for row in _list(snapshot.get('devices')):
        if not isinstance(row, dict) or row.get('via_device_id'):
            continue
        identifiers = [pair[1] for pair in _list(row.get('identifiers'))
                       if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] == 'bambu_lab']
        if not (ids.intersection(row.get('config_entries') or []) or identifiers):
            continue
        # ha-bambulab 同时登记 MakerWorld 作者资料，它们不是打印机。
        if ((_text(row.get('manufacturer')) or '').strip().casefold() == 'makerworld'
                or (_text(row.get('model')) or '').strip().casefold() == 'creator profile'
                or any(isinstance(value, str) and value.casefold().startswith('makerworld_account_')
                       for value in identifiers)):
            continue
        if not identifiers or not isinstance(identifiers[0], str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,80}', identifiers[0]):
            continue
        if not isinstance(row.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', row['id']):
            continue
        result.append({'id': row['id'], 'name': _text(row.get('name_by_user') or row.get('name')) or '拓竹打印机',
                       'model': _text(row.get('model')), 'firmware_version': _text(row.get('sw_version')),
                       '_serial': identifiers[0]})
    return sorted(result, key=lambda d: (d['name'], d['id']))


def _printer(snapshot, printer_id=None):
    printers = _printers(snapshot)
    selected = printer_id or core.config().get('ha_printer_id')
    if selected:
        match = next((row for row in printers if row['id'] == selected), None)
        if not match:
            raise core.WorkflowError('未找到选择的 Home Assistant 打印机。')
        return match
    if not printers:
        raise core.WorkflowError('Home Assistant 中尚未找到已接入的拓竹打印机。')
    return printers[0]


def _entities(snapshot, printer):
    allowed = {printer['id']}
    for _ in range(8):
        children = {row.get('id') for row in _list(snapshot.get('devices'))
                    if isinstance(row, dict) and row.get('via_device_id') in allowed}
        if children.issubset(allowed):
            break
        allowed.update(children)
    states = {row.get('entity_id'): row for row in _list(snapshot.get('states')) if isinstance(row, dict)}
    return [(row, states[row['entity_id']]) for row in _list(snapshot.get('registry'))
            if isinstance(row, dict) and row.get('device_id') in allowed and
            not row.get('disabled_by') and row.get('entity_id') in states]


def _find(entities, name):
    """Match an exact HA field tail, excluding its target-temperature sibling."""
    suffix = '_' + name
    candidates = []
    for position, (row, state) in enumerate(entities):
        for priority, key in enumerate(('entity_id', 'unique_id')):
            value = row.get(key)
            if not isinstance(value, str):
                continue
            value = value.rsplit('.', 1)[-1].lower()
            # Bambu exposes both target_nozzle_temperature and nozzle_temperature.
            # A plain suffix search would choose the former when it occurs first.
            if value == 'target' + suffix or value.endswith('_target' + suffix):
                continue
            if value == name or value.endswith(suffix):
                candidates.append((priority, position, state))
    return min(candidates, key=lambda value: value[:2])[2] if candidates else {}


def _value(entities, name):
    value = _find(entities, name).get('state')
    return value if isinstance(value, str) and value not in {'unknown', 'unavailable'} else None


def _attributes(state):
    value = state.get('attributes')
    return value if isinstance(value, dict) else {}


def _public_printer(printer):
    return {key: value for key, value in printer.items() if not key.startswith('_')}


def device_list():
    try:
        snapshot = _snapshot()
        printers = _printers(snapshot)
        return {'state': 'unavailable' if snapshot.get('_stale') else 'online', 'provider': PROVIDER,
                'devices': [{**_public_printer(row), 'connected': _value(_entities(snapshot, row), 'online') == 'on'} for row in printers],
                'integration_version': _text((snapshot.get('manifest') or {}).get('version')),
                'updated_at': snapshot.get('updated_at'), 'stale': bool(snapshot.get('_stale')),
                'message': 'HA 连接暂不可用，显示上次读取资料。' if snapshot.get('_stale') else '设备资料来自 Home Assistant。'}
    except (core.WorkflowError, httpx.HTTPError, OSError, ValueError, TypeError):
        return {'state': 'unconfigured' if not management_url() else 'unavailable', 'provider': PROVIDER,
                'devices': [], 'updated_at': None, 'message': '暂时无法读取 Home Assistant 拓竹设备。'}


def _slots(snapshot, printer, entities):
    modules = {}
    devices = {row.get('id'): row for row in _list(snapshot.get('devices')) if isinstance(row, dict)}
    for registry, state in entities:
        attrs = _attributes(state)
        slot = attrs.get('slot')
        if type(slot) is not int or not 1 <= slot <= 16 or not registry['entity_id'].startswith('sensor.'):
            continue
        unit = devices.get(registry.get('device_id'), {})
        name = _text(unit.get('name_by_user') or unit.get('name')) or 'AMS'
        kind = _text(attrs.get('type'))
        value = _text(state.get('state'))
        status = 'unknown' if value in {'unknown', 'unavailable', None} else 'empty' if kind == 'Empty' or value == 'Empty' else 'loaded' if kind not in {None, '', '?'} else 'unidentified'
        remain = _number(attrs.get('remain')) if attrs.get('remain_enabled') is True and status == 'loaded' else None
        remain = remain if remain is not None and 0 <= remain <= 100 else None
        color = attrs.get('color')
        color = color if isinstance(color, str) and re.fullmatch(r'#?[A-Fa-f0-9]{6}(?:[A-Fa-f0-9]{2})?', color) else None
        modules.setdefault(registry.get('device_id'), {'name': name, 'slots': []})['slots'].append({
            'slot_number': slot, 'state': status, 'english_label': _text(value) if status == 'loaded' else None,
            'material': _text(kind), 'rgba': color, 'spool_id': None, 'assignment_matches': False,
            'material_number': None, 'reported_remaining_percent': remain,
            'remaining_basis': 'device_report' if remain is not None else 'unknown',
            'actual_remaining_weight': None, 'source_timestamp': state.get('last_reported') or state.get('last_updated'),
            'fresh': False, 'route': {'text': f'{name} → 单喷嘴；发送路线待核验', 'nozzle_number': 1, 'firmware_extruder_id': None},
            'diagnostics': {'ams_id': None, 'tray_id': slot - 1, 'physical_extruder_id': None, 'reported_state': _text(value)},
        })
    for module in modules.values():
        module['slots'].sort(key=lambda row: row['slot_number'])
    return {'modules': list(modules.values()), 'observed_at': snapshot.get('updated_at'),
            'source_timestamp': None, 'age_seconds': None, 'fresh': False,
            'note': 'HA 实体时间不代表打印机心跳；余量是设备估算，100%不代表实称满卷。'}


def device(printer_id=None):
    try:
        snapshot = _snapshot()
        selected = _printer(snapshot, printer_id)
        entities = _entities(snapshot, selected)
        online = _value(entities, 'online') == 'on' and not snapshot.get('_stale')
        remaining_state = _find(entities, 'remaining_time')
        remaining = _number(remaining_state.get('state'))
        unit = _attributes(remaining_state).get('unit_of_measurement')
        remaining = remaining * 60 if remaining is not None and unit == 'h' else remaining / 60 if remaining is not None and unit == 's' else remaining
        cameras = [(row, state) for row, state in entities if row['entity_id'].startswith('camera.')]
        camera = cameras[0] if cameras else None
        status = _value(entities, 'print_status')
        printer = {**_public_printer(selected), 'connected': online,
                   'state': status.upper() if isinstance(status, str) else 'UNKNOWN',
                   'current_stage': _value(entities, 'current_stage'), 'current_print': _value(entities, 'task_name'),
                   'progress': _number(_value(entities, 'print_progress')), 'remaining_time': remaining,
                   'layer_num': _number(_value(entities, 'current_layer')), 'total_layers': _number(_value(entities, 'total_layer_count')),
                   'temperatures': {'nozzle': _number(_value(entities, 'nozzle_temperature')), 'bed': _number(_value(entities, 'bed_temperature'))},
                   'developer_mode': _value(entities, 'developer_lan_mode') == 'on',
                   'connection_mode': _value(entities, 'mqtt_connection_mode'),
                   'nozzles': [{'nozzle_diameter': _number(_value(entities, 'nozzle_size')), 'nozzle_type': _value(entities, 'nozzle_type')}],
                   'hms_errors': _value(entities, 'hms_errors') == 'on'}
        return {'state': 'online' if online else 'offline', 'provider': PROVIDER, 'printer': printer,
                'updated_at': snapshot.get('updated_at'), 'stale': bool(snapshot.get('_stale')),
                'telemetry_age_seconds': None, 'telemetry_source': 'Home Assistant entity states; printer heartbeat not verified',
                'dispatch_available': False, 'dispatch_message': 'HA 已接入设备资料；自动发送尚未启用。',
                'message': '已连接 Home Assistant，自动发送尚未启用。' if online else 'HA 或打印机连接不可用；当前值不用于开始打印。',
                'material_slots': _slots(snapshot, selected, entities),
                'camera': {'entity_id': camera[0]['entity_id'], 'state': camera[1].get('state')} if camera else None,
                'camera_image_url': f'/api/ha/camera/{selected["id"]}' if camera else None,
                'routing_evidence': {'validated_for_dispatch': False, 'source': 'Home Assistant entity states', 'message': '需要按 A1/P1P 另行核对发送路线。'}}
    except core.WorkflowError:
        if printer_id:
            raise
        return {'state': 'unavailable', 'provider': PROVIDER, 'printer': None, 'updated_at': None,
                'dispatch_available': False, 'message': '暂时无法读取 Home Assistant 打印机。'}


def inventory():
    return {'state': 'unavailable', 'provider': PROVIDER, 'kind': 'inventory_ledger', 'items': [], 'updated_at': None,
            'quantity_note': 'HA 当前装料与独立料卷库存不同；当前没有接入库存台账。'}


def _relative(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise core.WorkflowError('模型文件路径无效。')
    decoded = value
    for _ in range(8):
        if (any(ord(c) < 32 for c in decoded) or any(c in decoded for c in ('\\', '?', '#')) or
                any(part in {'', '.', '..'} for part in decoded.split('/'))):
            raise core.WorkflowError('模型文件路径无效。')
        path = PurePosixPath(decoded)
        if path.is_absolute() or path.parts[0] != 'prints' or path.suffix.lower() not in {'.3mf', '.jpg', '.jpeg', '.png', '.webp'}:
            raise core.WorkflowError('模型文件路径无效。')
        try:
            candidate = unquote(decoded, errors='strict')
        except UnicodeDecodeError:
            raise core.WorkflowError('模型文件路径无效。') from None
        if candidate == decoded:
            break
        # Literal '%' is common in slicer names (for example "15% 填充").
        # Inspect decoded forms without changing the actual allowlisted name.
        if candidate.count('/') != decoded.count('/'):
            raise core.WorkflowError('模型文件路径无效。')
        decoded = candidate
    else:
        raise core.WorkflowError('模型文件路径无效。')
    return value


def _file_path(value, serial):
    if not isinstance(value, str) or not value.startswith(serial + '/'):
        raise core.WorkflowError('模型文件不属于选择的打印机。')
    return _relative(value[len(serial) + 1:])


def _asset_url(serial, relative):
    return '/api/ha/files/' + quote(serial, safe='') + '/' + quote(relative, safe='/')


def _files(client, printer):
    key = (*_identity(client.settings), printer['_serial'])
    with _files_lock:
        cached = _file_lists.get(key)
        if cached and 0 <= time.monotonic() - cached[0] < CACHE_SECONDS:
            return copy.deepcopy(cached[1])
        data = client.get('/api/bambu_lab/print_history', params={'serial': printer['_serial']})
        rows = data.get('files') if isinstance(data, dict) else None
        if not isinstance(rows, list) or len(rows) > 10000:
            raise core.WorkflowError('HA 模型缓存列表无效。')
        _file_lists[key] = (time.monotonic(), rows)
        if len(_file_lists) > 16:
            oldest = min(_file_lists, key=lambda entry: _file_lists[entry][0])
            _file_lists.pop(oldest, None)
        return copy.deepcopy(rows)


def history_view(printer_id=None):
    client = None
    try:
        snapshot = _snapshot()
        printers = [_printer(snapshot, printer_id)] if printer_id else _printers(snapshot)
        client = Client()
        rows = []
        for printer in printers:
            for file in _files(client, printer):
                if not isinstance(file, dict):
                    continue
                try:
                    relative = _file_path(file.get('path'), printer['_serial'])
                    if PurePosixPath(relative).suffix.lower() != '.3mf':
                        continue
                except core.WorkflowError:
                    continue
                thumbnail = None
                if file.get('thumbnail_path'):
                    try:
                        thumb_rel = _file_path(file['thumbnail_path'], printer['_serial'])
                        if PurePosixPath(thumb_rel).suffix.lower() != '.3mf':
                            thumbnail = _asset_url(printer['_serial'], thumb_rel)
                    except core.WorkflowError:
                        pass
                stamp = _timestamp(file.get('modified'))
                identity = hashlib.sha256((printer['id'] + '\0' + relative).encode()).hexdigest()[:24]
                rows.append({'id': 'ha-cache-' + identity, 'archive_id': None, 'printer_id': printer['id'], 'printer_name': printer['name'],
                             'name': _text(file.get('filename'), 512) or PurePosixPath(relative).name,
                             'status': 'archived', 'status_source': 'ha_file_cache', 'origin': 'external',
                             'project_id': None, 'project_name': None, 'started_at': None, 'completed_at': None,
                             'created_at': stamp, 'duration_seconds': None, 'reported_weight_grams': None,
                             'is_print_event': False, 'physical_review': 'not_verified', 'has_3mf': True,
                             'has_source_3mf': False, 'archive_photo_url': thumbnail,
                             'model_preview': {'url': thumbnail, 'kind': 'cached_model'} if thumbnail else None,
                             'download_url': _asset_url(printer['_serial'], relative), 'makerworld_url': None,
                             'file_size': _number(file.get('size')), 'cached_file': True})
        rows.sort(key=lambda row: row['created_at'] or '', reverse=True)
        return {'state': 'current', 'provider': PROVIDER, 'source_kind': 'file_cache', 'stale': bool(snapshot.get('_stale')),
                'updated_at': core.now(), 'items': rows, 'cache_count': len(rows), 'archive_count': len(rows), 'run_count': 0,
                'printers': [_public_printer(printer) for printer in printers],
                'counts': {'records': len(rows), 'prints': 0, 'external': 0, 'linked': 0, 'completed': 0, 'cached_files': len(rows)},
                'coverage': {'from': min((row['created_at'] for row in rows if row['created_at']), default=None),
                             'all_lifetime_prints_verified': False, 'cloud_history_synced': False},
                'message': 'HA 保存的模型文件缓存；文件数量不代表打印次数，未核实完整任务历史。'}
    except (core.WorkflowError, httpx.HTTPError, OSError, ValueError, TypeError, KeyError):
        if printer_id and client is None:
            raise
        return {'state': 'unavailable', 'provider': PROVIDER, 'source_kind': 'file_cache', 'stale': True,
                'items': [], 'updated_at': None, 'cache_count': 0,
                'message': '暂时无法读取 HA 模型缓存；没有生成示例记录。'}
    finally:
        if client:
            client.close()


def _timestamp(value):
    try:
        stamp = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp.replace(tzinfo=stamp.tzinfo or dt.timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _read_bytes(client, path, limit):
    with client.http.stream('GET', path) as response:
        response.raise_for_status()
        length = response.headers.get('content-length')
        if length and (not length.isdigit() or int(length) > limit):
            raise core.WorkflowError('文件超过允许的下载大小。')
        chunks, total = [], 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > limit:
                raise core.WorkflowError('文件超过允许的下载大小。')
            chunks.append(chunk)
        return b''.join(chunks)


def _image_type(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 20000000:
                raise ValueError
            content_type = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}[image.format]
            image.verify()
        return content_type
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise core.WorkflowError('图像内容无法读取。') from None


def asset(serial, relative):
    relative = _relative(relative)
    client = None
    try:
        printer = next((row for row in _printers(_snapshot()) if row['_serial'] == serial), None)
        if printer is None:
            raise core.WorkflowError('未找到文件所属打印机。')
        client = Client()
        allowed = set()
        for row in _files(client, printer):
            if not isinstance(row, dict):
                continue
            for key in ('path', 'thumbnail_path'):
                if row.get(key):
                    try:
                        allowed.add(_file_path(row[key], serial))
                    except core.WorkflowError:
                        pass
        if relative not in allowed:
            raise core.WorkflowError('文件不在 HA 已登记的模型缓存中。')
        model = PurePosixPath(relative).suffix.lower() == '.3mf'
        data = _read_bytes(client, '/api/bambu_lab/file_cache/' + quote(serial, safe='') + '/' + quote(relative, safe='/'), MAX_FILE_BYTES if model else MAX_IMAGE_BYTES)
        if model:
            if not data.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')):
                raise core.WorkflowError('模型文件不是可读取的 3MF 包。')
            content_type = 'application/vnd.ms-package.3dmanufacturing-3dmodel+xml'
        else:
            content_type = _image_type(data)
        return {'content': data, 'content_type': content_type, 'filename': PurePosixPath(relative).name}
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError):
        raise core.WorkflowError('暂时无法读取 HA 缓存文件。') from None
    finally:
        if client:
            client.close()


def camera_image(printer_id):
    client = None
    try:
        snapshot = _snapshot()
        printer = _printer(snapshot, printer_id)
        cameras = [(row, state) for row, state in _entities(snapshot, printer)
                   if re.fullmatch(r'camera\.[A-Za-z0-9_]+', row.get('entity_id', '')) and state.get('state') not in {'unknown', 'unavailable'}]
        if not cameras:
            raise core.WorkflowError('这台打印机暂无可读取的摄像头图像。')
        client = Client()
        data = _read_bytes(client, '/api/camera_proxy/' + quote(cameras[0][0]['entity_id'], safe='.'), MAX_IMAGE_BYTES)
        return {'content': data, 'content_type': _image_type(data), 'filename': 'printer-camera.jpg'}
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError):
        raise core.WorkflowError('暂时无法读取打印机摄像头。') from None
    finally:
        if client:
            client.close()
