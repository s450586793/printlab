"""Device data routing; HA observation never enables an unverified send path."""
from __future__ import annotations

from urllib.parse import urlparse

from . import core

HA_DISPATCH_MESSAGE = 'HA 连接已接入设备与模型资料；离线切片不构成打印许可，自动发送尚未开放。请在官方 Bambu Studio 中核对并打印。工作台未发送打印命令。'


def provider():
    value = core.config().get('device_provider', 'bambuddy')
    if not isinstance(value, str) or value not in {'bambuddy', 'homeassistant'}:
        raise core.WorkflowError('Unsupported device provider')
    return value


def device_list():
    if provider() == 'homeassistant':
        from . import homeassistant
        return homeassistant.device_list()
    from . import bambuddy
    observed = bambuddy.device()
    printer = observed.get('printer') or {}
    pid = core.config().get('printer_id')
    rows = [] if pid is None else [{
        'id': pid, 'name': printer.get('name') or '拓竹打印机',
        'model': printer.get('model'), 'firmware_version': printer.get('firmware_version'),
    }]
    return {'provider': 'bambuddy', 'state': observed.get('state'), 'devices': rows,
            'updated_at': observed.get('updated_at'), 'message': observed.get('message')}


def device(printer_id=None):
    if provider() == 'homeassistant':
        from . import homeassistant
        return homeassistant.device(printer_id)
    configured = core.config().get('printer_id')
    if printer_id is not None and str(printer_id) != str(configured):
        raise core.WorkflowError('Unknown printer')
    from . import bambuddy
    return bambuddy.device()


def inventory():
    if provider() == 'homeassistant':
        from . import homeassistant
        return homeassistant.inventory()
    from . import bambuddy
    return bambuddy.inventory()


def history_view(printer_id=None, *, force=False):
    if provider() == 'homeassistant':
        from . import homeassistant
        return homeassistant.history_view(printer_id)
    configured = core.config().get('printer_id')
    if printer_id is not None and str(printer_id) != str(configured):
        raise core.WorkflowError('Unknown printer')
    from . import history
    return history.view(force=True) if force else history.view()


def management_url():
    if provider() == 'homeassistant':
        from . import homeassistant
        return homeassistant.management_url()
    value = core.config().get('bambuddy_url') or 'http://127.0.0.1:8017'
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise core.WorkflowError('Invalid printer management URL')
    return value.rstrip('/')


def require_dispatch_supported():
    if provider() == 'homeassistant':
        raise core.WorkflowError(HA_DISPATCH_MESSAGE)


def slice_identity(project):
    """Snapshot a project's target without selecting a global device for other jobs."""
    if provider() != 'homeassistant':
        config = core.config()
        return {'printer_id': config.get('printer_id'), 'bambuddy_url': config.get('bambuddy_url')}
    target = project.get('target_printer') or {}
    if target.get('provider') != 'homeassistant' or not isinstance(target.get('id'), str) or not target['id']:
        raise core.WorkflowError('请先为此项目选择 HA 打印机，再准备对应机型的切片')
    return {'device_provider': 'homeassistant', 'printer_id': target['id'],
            'ha_url': management_url(), 'bambuddy_url': None,
            'target_printer': dict(target), 'dispatch_available': False}
