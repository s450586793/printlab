"""Private HA connection administration. Candidate checks never persist secrets."""
from contextlib import contextmanager

import httpx
import websocket

from . import core, homeassistant as ha


class SettingsError(core.WorkflowError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


@contextmanager
def _lock():
    with core.lock('homeassistant-settings'), ha._credentials_lock:
        yield


def _saved():
    try:
        value = ha._settings()
        if not isinstance(value.get('access_token'), str) or not value['access_token']:
            return None
        return value
    except (core.WorkflowError, OSError, ValueError, TypeError):
        return None


def _empty():
    return {'configured': False, 'connected': False, 'base_url': None,
            'devices': [], 'error_code': None, 'message': '尚未连接 Home Assistant。'}


def _candidate(body, saved):
    try:
        base = ha._url(body.get('base_url'))
    except (core.WorkflowError, ValueError, TypeError):
        raise SettingsError('Home Assistant 配置格式无效，请检查填写内容。') from None
    token = body.get('token')
    if token is not None and not isinstance(token, str):
        raise SettingsError('Home Assistant 配置格式无效，请检查填写内容。')
    token = token.strip() if token is not None else ''
    if not token:
        if saved and base == saved['base_url']:
            return dict(saved)
        raise SettingsError('请为此 Home Assistant 地址填写访问令牌。')
    if len(token) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise SettingsError('Home Assistant 配置格式无效，请检查填写内容。')
    return {'base_url': base, 'access_token': token}


def _failure(configured, base, code):
    messages = {
        'authentication': 'Home Assistant 认证失败，请检查访问令牌或重新连接。',
        'permission': 'Home Assistant 未允许读取设备资料，请检查此账户的权限。',
        'network': '无法连接 Home Assistant，请检查地址及网络连接。',
        'invalid_response': 'Home Assistant 返回了无法识别的数据，请检查地址和集成版本。',
    }
    return {'configured': configured, 'connected': False, 'base_url': base,
            'devices': [], 'error_code': code, 'message': messages[code]}


def _probe(candidate, configured, *, persist_refresh=False):
    base = candidate['base_url']
    try:
        refreshed = ha._refresh_settings(candidate)
        # Runtime reads keep the existing OAuth renewal behavior; tests only use memory.
        if persist_refresh and refreshed != candidate:
            ha._save_credentials(refreshed)
        client = ha.Client(refreshed)
        try:
            response = client.get('/api/')
            if not isinstance(response, dict) or response.get('message') != 'API running.':
                raise ValueError
            snapshot = ha._read_snapshot(client)
        finally:
            client.close()
        printers = [{key: row[key] for key in ('id', 'name', 'model')} for row in ha._printers(snapshot)]
        code, message = None, 'Home Assistant 已连接。'
        if not printers:
            entries = snapshot.get('entries')
            entries = entries.get('entries', []) if isinstance(entries, dict) else ha._list(entries)
            installed = any(isinstance(row, dict) and row.get('domain') == 'bambu_lab' for row in entries)
            installed = installed or bool(snapshot.get('manifest'))
            code = 'no_devices' if installed else 'integration_missing'
            message = ('Home Assistant 已连接，尚未发现拓竹打印机。' if installed else
                       'Home Assistant 已连接，请安装并配置 ha-bambulab 集成。')
        return {'configured': configured, 'connected': True, 'base_url': base,
                'devices': printers, 'error_code': code, 'message': message}, refreshed
    except ha.HAConnectionError as exc:
        code = exc.code
    except httpx.HTTPStatusError as exc:
        code = 'authentication' if exc.response.status_code == 401 else 'permission' if exc.response.status_code == 403 else 'network'
    except websocket.WebSocketBadStatusException as exc:
        code = 'authentication' if exc.status_code == 401 else 'permission' if exc.status_code == 403 else 'network'
    except (httpx.HTTPError, websocket.WebSocketException, OSError):
        code = 'network'
    except (ValueError, TypeError, KeyError):
        code = 'invalid_response'
    return _failure(configured, base, code), None


def status():
    with _lock():
        saved = _saved()
        if not saved:
            return _empty()
        return _probe(saved, True, persist_refresh=True)[0]


def test_connection(body):
    with _lock():
        saved = _saved()
        return _probe(_candidate(body, saved), bool(saved))[0]


def save_settings(body):
    with _lock():
        saved = _saved()
        result, candidate = _probe(_candidate(body, saved), bool(saved))
        if not result['connected']:
            return result
        config = core.config()
        updated = {**config, 'device_provider': ha.PROVIDER}
        updated.pop('ha_printer_id', None)
        try:
            core.write_json(core.ROOT / 'Runtime/config.json', updated)
            try:
                ha._save_credentials(candidate)
            except OSError:
                core.write_json(core.ROOT / 'Runtime/config.json', config)
                raise
        except OSError:
            raise SettingsError('无法保存 Home Assistant 配置，请检查数据目录权限。', 500) from None
    ha.clear_cache()
    return {**result, 'configured': True}


def disconnect():
    with _lock():
        try:
            ha._credential_path().unlink(missing_ok=True)
            config = core.config()
            if 'ha_printer_id' in config:
                config.pop('ha_printer_id')
                core.write_json(core.ROOT / 'Runtime/config.json', config)
        except OSError:
            raise SettingsError('无法断开 Home Assistant，请检查数据目录权限。', 500) from None
    ha.clear_cache()
    return _empty()
