"""Private, versioned Responses providers without changing existing Codex homes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import core

_AUTH_ENV = ('OPENAI_API_KEY', 'CODEX_API_KEY', 'PRINTLAB_PROVIDER_API_KEY')
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}')
_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}')


class ProviderError(core.WorkflowError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def _path(relative):
    try:
        return core.safe_path(core.ROOT, relative, require_file=False)
    except core.WorkflowError:
        raise ProviderError('模型服务私有目录不可用。', 409) from None


def _registry():
    path = _path('Runtime/private/model-providers.json')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return {'schema_version': 1, 'version': 0, 'default_provider_id': None, 'providers': {}}
    except OSError:
        raise ProviderError('无法读取模型服务私有配置。', 409) from None
    try:
        with os.fdopen(descriptor, 'r', encoding='utf-8') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            value = json.load(stream)
        if (not isinstance(value, dict) or value.get('schema_version') != 1
                or type(value.get('version')) is not int or value['version'] < 0
                or not isinstance(value.get('providers'), dict)
                or (value.get('default_provider_id') is not None and not _valid_id(value['default_provider_id']))):
            raise ValueError
        for pid, row in value['providers'].items():
            if (not _valid_id(pid) or pid == 'legacy' or not isinstance(row, dict)
                    or type(row.get('revision')) is not int or row['revision'] < 1
                    or type(row.get('enabled')) is not bool or not isinstance(row.get('revisions'), dict)
                    or str(row['revision']) not in row['revisions']):
                raise ValueError
            for revision, version in row['revisions'].items():
                if (not isinstance(revision, str) or not revision.isdecimal()
                        or str(int(revision)) != revision or not 1 <= int(revision) <= row['revision']
                        or not isinstance(version, dict)):
                    raise ValueError
                normalized = _candidate(version, probe=True)
                normalized.pop('enabled')
                if normalized != version:
                    raise ValueError
        return value
    except (OSError, ValueError, TypeError, ProviderError):
        raise ProviderError('模型服务私有配置无效，请检查服务端数据。', 409) from None


def _valid_id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ProviderError('请填写有效的模型服务 Base URL。')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or (parsed.port is not None and not 0 < parsed.port < 65536)):
            raise ValueError
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))
    except ValueError:
        raise ProviderError('请填写有效的模型服务 Base URL。') from None


def _model(value):
    if not isinstance(value, str) or not _MODEL.fullmatch(value):
        raise ProviderError('模型 ID 无效，请填写服务商提供的模型名称。')
    return value


def _candidate(payload, previous=None, *, probe=False):
    if not isinstance(payload, dict):
        raise ProviderError('模型服务配置格式无效。')
    previous = previous or {}
    name = payload.get('name', previous.get('name', '模型服务' if probe else ''))
    if (not isinstance(name, str) or not name.strip() or len(name) > 120
            or any(ord(c) < 32 for c in name)):
        raise ProviderError('请填写有效的模型服务名称。')
    base = _url(payload.get('base_url', previous.get('base_url')))
    key = payload.get('api_key')
    if key is not None and not isinstance(key, str):
        raise ProviderError('API Key 格式无效。')
    if not (key or '').strip() and previous and base != previous.get('base_url'):
        raise ProviderError('更换服务地址时请重新填写 API Key。')
    key = (key or '').strip() or previous.get('api_key', '')
    if not key or len(key) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ProviderError('请填写有效的 API Key。')
    models = payload.get('models', previous.get('models', []))
    if not isinstance(models, list) or len(models) > 256:
        raise ProviderError('模型列表格式无效。')
    models = list(dict.fromkeys(_model(item) for item in models))
    default = payload.get('default_model', '' if probe else previous.get('default_model', ''))
    if default is None:
        default = ''
    if default:
        default = _model(default)
        if default not in models:
            models.append(default)
    elif not isinstance(default, str):
        raise ProviderError('默认模型格式无效。')
    if not default and models and not probe:
        default = models[0]
    enabled = payload.get('enabled', previous.get('enabled', True))
    if type(enabled) is not bool:
        raise ProviderError('启用状态无效。')
    if any(key in text for text in [name, base, *models]):
        raise ProviderError('请勿在公开配置字段中填写 API Key。')
    return {'name': name.strip(), 'base_url': base, 'api_key': key, 'models': models,
            'default_model': default or None, 'enabled': enabled}


def _legacy_home():
    return Path(os.environ.get('CODEX_HOME', str(core.ROOT / 'Runtime/codex'))).expanduser()


def _legacy_settings(*, strict=False):
    path = _legacy_home() / 'config.toml'
    available = path.is_file() or (_legacy_home() / 'auth.json').is_file() or any(os.environ.get(name) for name in _AUTH_ENV)
    try:
        value = tomllib.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        definitions = value.get('model_providers', {})
        provider = definitions.get(value.get('model_provider'), {}) if isinstance(definitions, dict) else {}
        if not isinstance(provider, dict):
            provider = {}
    except (OSError, ValueError, TypeError):
        value, provider = {}, {}
    model = value.get('model')
    model = model if isinstance(model, str) and _MODEL.fullmatch(model) else None
    env_key = provider.get('env_key', '')
    key = os.environ.get(env_key, '') if isinstance(env_key, str) else ''
    key = key or next((os.environ[name] for name in _AUTH_ENV if os.environ.get(name)), '')
    try:
        base = _url(provider.get('base_url')) if provider.get('base_url') else None
    except ProviderError:
        base = None
    if key:
        model = None if model and key in model else model
        base = None if base and key in base else base
    legacy = {'id': 'legacy', 'revision': 0, 'name': '现有 Codex 配置', 'model': model,
              'base_url': base, 'api_key': key, 'home': _legacy_home(), 'legacy': True}
    try:
        binding = _legacy_binding()
        if binding:
            _, version = _record(_registry(), binding['provider_id'], binding['revision'])
            if (base != version['base_url'] or model not in version['models']
                    or env_key != 'PRINTLAB_PROVIDER_API_KEY'):
                raise ProviderError('历史对话运行配置与迁移服务不一致，无法接续。', 409)
            legacy.update(api_key=version['api_key'], managed=True, name=version['name'])
    except ProviderError:
        if strict:
            raise
        return False, legacy
    return available and not legacy.get('managed'), legacy


def _legacy_binding():
    path = _path('Runtime/private/legacy-provider.json')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return None
    except OSError:
        raise ProviderError('无法读取历史对话服务绑定。', 409) from None
    try:
        with os.fdopen(descriptor, 'r', encoding='utf-8') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            value = json.loads(stream.read(4097))
        if (not isinstance(value, dict) or set(value) != {'provider_id', 'revision'}
                or not _valid_id(value['provider_id']) or value['provider_id'] == 'legacy'
                or type(value['revision']) is not int or value['revision'] < 1):
            raise ValueError
        return value
    except (ValueError, TypeError, OSError):
        raise ProviderError('历史对话服务绑定无效，无法接续。', 409) from None


def bind_legacy(provider_id, revision):
    """Bind existing native threads once; never silently redirect them to a new default."""
    with core.lock('model-providers'):
        _, version = _record(_registry(), provider_id, revision)
        binding = {'provider_id': provider_id, 'revision': revision}
        previous = _legacy_binding()
        if previous and previous != binding:
            raise ProviderError('历史对话已绑定其他服务版本。', 409)
        available, spec = _legacy_settings(strict=True)
        try:
            value = tomllib.loads((_legacy_home() / 'config.toml').read_text(encoding='utf-8'))
            env_key = value['model_providers'][value['model_provider']]['env_key']
        except (OSError, ValueError, TypeError, KeyError):
            raise ProviderError('请先准备历史对话的通用运行配置。', 409) from None
        if ((not available and not spec.get('managed')) or spec['base_url'] != version['base_url']
                or spec['model'] not in version['models'] or env_key != 'PRINTLAB_PROVIDER_API_KEY'):
            raise ProviderError('历史对话运行配置与迁移服务不一致。', 409)
        if not previous:
            _save(binding, relative='Runtime/private/legacy-provider.json')
        return binding


def _default(registry, legacy_available):
    pid = registry.get('default_provider_id')
    if pid == 'legacy' and legacy_available:
        return pid
    if pid in registry['providers'] and registry['providers'][pid]['enabled']:
        return pid
    if legacy_available:
        return 'legacy'
    return next((pid for pid, row in registry['providers'].items() if row['enabled']), None)


def _public(registry):
    legacy_available, legacy = _legacy_settings()
    rows = []
    if legacy_available or legacy.get('managed'):
        name = legacy['name'] + ' · 历史对话' if legacy.get('managed') else legacy['name']
        rows.append({'id': 'legacy', 'name': name, 'revision': 0,
                     'base_url': legacy['base_url'], 'models': [legacy['model']] if legacy['model'] else [],
                     'default_model': legacy['model'], 'enabled': True,
                     'key_configured': bool(legacy['api_key']) or (_legacy_home() / 'auth.json').is_file(), 'legacy': True})
    try:
        for pid, record in registry['providers'].items():
            version = record['revisions'][str(record['revision'])]
            rows.append({'id': pid, 'name': version['name'], 'revision': record['revision'],
                         'base_url': version['base_url'], 'models': version['models'],
                         'default_model': version['default_model'], 'enabled': record['enabled'],
                         'key_configured': bool(version['api_key']), 'legacy': False})
    except (KeyError, TypeError):
        raise ProviderError('模型服务私有配置无效，请检查服务端数据。', 409) from None
    return {'version': registry['version'], 'default_provider_id': _default(registry, legacy_available), 'providers': rows}


def settings():
    return _public(_registry())


def _save(registry, *, relative='Runtime/private/model-providers.json'):
    path = _path(relative)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='.model-providers-', dir=path.parent)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(registry, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        _path(relative)
        os.replace(temporary, path)
    except (OSError, ValueError):
        raise ProviderError('保存模型服务失败，原配置已保留。', 409) from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def save_settings(payload):
    if not isinstance(payload, dict) or type(payload.get('version')) is not int:
        raise ProviderError('模型服务配置版本无效。')
    _path('Runtime/private/model-providers.json')
    with core.lock('model-providers'):
        registry = _registry()
        if payload['version'] != registry['version']:
            raise ProviderError('模型服务配置已更新，请刷新后再保存。', 409)
        candidate = payload.get('provider')
        if candidate is None and 'default_provider_id' not in payload:
            raise ProviderError('请提供需要保存的模型服务或默认服务。')
        pid = None
        if candidate is not None:
            if not isinstance(candidate, dict):
                raise ProviderError('模型服务配置格式无效。')
            pid = candidate.get('id')
            if pid is not None and (not _valid_id(pid) or pid == 'legacy' or pid not in registry['providers']):
                raise ProviderError('模型服务编号无效。')
            record = registry['providers'].get(pid)
            previous = {**record['revisions'][str(record['revision'])], 'enabled': record['enabled']} if record else None
            version = _candidate(candidate, previous)
            pid = pid or 'provider-' + uuid.uuid4().hex[:16]
            revision = record['revision'] + 1 if record else 1
            revisions = dict(record['revisions']) if record else {}
            revisions[str(revision)] = {k: v for k, v in version.items() if k != 'enabled'}
            registry['providers'][pid] = {'revision': revision, 'enabled': version['enabled'], 'revisions': revisions}
        if payload.get('make_default'):
            if not pid:
                raise ProviderError('请先选择模型服务。')
            registry['default_provider_id'] = pid
        if 'default_provider_id' in payload:
            registry['default_provider_id'] = payload['default_provider_id']
        default = registry.get('default_provider_id')
        available, _ = _legacy_settings()
        if default is not None and default != 'legacy' and default not in registry['providers']:
            raise ProviderError('默认模型服务不存在。')
        if default == 'legacy' and not available:
            raise ProviderError('现有 Codex 服务尚未配置。')
        if ((payload.get('make_default') or 'default_provider_id' in payload)
                and default != 'legacy' and default is not None and not registry['providers'][default]['enabled']):
            raise ProviderError('不能将已停用的服务设为默认。')
        registry['default_provider_id'] = _default(registry, available)
        registry['version'] += 1
        _save(registry)
        return _public(registry)


def _record(registry, pid, revision):
    if not _valid_id(pid) or pid not in registry['providers']:
        raise ProviderError('模型服务不存在，请在设置中配置。', 409)
    record = registry['providers'][pid]
    if revision is None and not record['enabled']:
        raise ProviderError('此模型服务已停用，请选择其他服务。', 409)
    revision = record['revision'] if revision is None else revision
    if type(revision) is not int or revision < 1:
        raise ProviderError('模型服务版本无效。', 409)
    version = record['revisions'].get(str(revision))
    if not isinstance(version, dict):
        raise ProviderError('原模型服务版本不可用，无法接续此任务。', 409)
    return revision, version


def session_home(provider_id=None, revision=None):
    if provider_id == 'legacy':
        if revision not in (None, 0):
            raise ProviderError('模型服务版本无效。', 409)
        return _legacy_home()
    registry = _registry()
    available, _ = _legacy_settings()
    pid = provider_id or _default(registry, available)
    if pid == 'legacy':
        return _legacy_home()
    if pid is None:
        raise ProviderError('请先在设置中配置模型服务。', 409)
    revision, _ = _record(registry, pid, revision)
    return _path(f'Runtime/codex-providers/{pid}/{revision}')


def _prepare_home(pid, revision, version):
    home = _path(f'Runtime/codex-providers/{pid}/{revision}')
    content = 'model_provider = "printlab"\n'
    if version['default_model']:
        content += 'model = ' + json.dumps(version['default_model']) + '\n'
    content += ('\n[model_providers.printlab]\nname = "PrintLab"\nbase_url = '
                + json.dumps(version['base_url']) + '\nenv_key = "PRINTLAB_PROVIDER_API_KEY"\n'
                'wire_api = "responses"\nrequires_openai_auth = false\n')
    with core.lock(f'provider-home-{pid}-{revision}'):
        try:
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            for path in [home, home.parent, home.parent.parent]:
                os.chmod(path, 0o700)
            config = _path(f'Runtime/codex-providers/{pid}/{revision}/config.toml')
            try:
                descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            except FileExistsError:
                if not config.is_file() or config.read_text(encoding='utf-8') != content:
                    raise ProviderError('模型运行配置与冻结版本不一致。', 409)
                os.chmod(config, 0o600)
            else:
                with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
        except (OSError, ValueError):
            raise ProviderError('无法准备模型服务的私有运行目录。', 409) from None
    return home


def resolve(provider_id=None, model=None, revision=None):
    available, legacy = _legacy_settings(strict=provider_id == 'legacy')
    if provider_id == 'legacy':
        if revision not in (None, 0):
            raise ProviderError('模型服务版本无效。', 409)
        if model is not None:
            legacy['model'] = _model(model)
        return legacy
    registry = _registry()
    pid = provider_id or _default(registry, available)
    if pid == 'legacy':
        return resolve('legacy', model=model, revision=revision)
    if pid is None:
        raise ProviderError('请先在设置中配置模型服务。', 409)
    revision, version = _record(registry, pid, revision)
    selected = _model(model) if model is not None else version.get('default_model')
    if not selected or selected not in version['models']:
        raise ProviderError('请在设置中配置并选择此服务支持的模型。', 409)
    home = _prepare_home(pid, revision, version)
    return {'id': pid, 'revision': revision, 'name': version['name'], 'model': selected,
            'base_url': version['base_url'], 'api_key': version['api_key'], 'home': home, 'legacy': False}


def secret_values():
    values = {value for name, value in os.environ.items() if value and re.search(r'KEY|TOKEN|SECRET|PASSWORD', name, re.I)}
    try:
        rows = _registry()['providers'].values()
    except ProviderError:
        rows = ()
    for row in rows:
        for version in row['revisions'].values():
            key = version.get('api_key') if isinstance(version, dict) else None
            if isinstance(key, str) and key:
                values.add(key)
    return tuple(sorted(values, key=len, reverse=True))


def _http():
    return httpx.Client(timeout=httpx.Timeout(20, connect=5), trust_env=False, follow_redirects=False)


def _response(client, method, url, **kwargs):
    with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > 1024 * 1024:
                raise ValueError
        return json.loads(content)


def test_connection(payload):
    previous = None
    pid = payload.get('id') if isinstance(payload, dict) else None
    if pid:
        registry = _registry()
        if not _valid_id(pid) or pid not in registry['providers']:
            raise ProviderError('模型服务不存在，请在设置中配置。', 409)
        _, previous = _record(registry, pid, registry['providers'][pid]['revision'])
    candidate = _candidate(payload, previous, probe=True)
    headers = {'Authorization': 'Bearer ' + candidate['api_key'], 'Content-Type': 'application/json'}
    models = []
    try:
        with _http() as client:
            try:
                catalogue = _response(client, 'GET', candidate['base_url'] + '/models', headers=headers)
                rows = catalogue.get('data') if isinstance(catalogue, dict) else None
                if not isinstance(rows, list):
                    raise ValueError
                secrets = (*secret_values(), candidate['api_key'])
                for row in rows[:4096]:
                    mid = row.get('id') if isinstance(row, dict) else None
                    if (isinstance(mid, str) and _MODEL.fullmatch(mid) and mid not in models
                            and not any(secret in mid for secret in secrets)):
                        models.append(mid)
            except (httpx.HTTPError, ValueError, TypeError, OSError):
                if not candidate['default_model']:
                    return {'ok': False, 'models': [], 'tool_supported': None,
                            'message': '无法读取模型列表，请检查地址与 API Key，或手动填写模型后测试工具调用。'}
            model = candidate['default_model']
            if not model:
                return {'ok': True, 'models': models, 'tool_supported': None,
                        'message': '已读取模型列表，选择模型后可验证建模工具能力。'}
            body = {'model': model, 'store': False, 'max_output_tokens': 128,
                    'input': 'Call printlab_probe with value 1. This only verifies the API; no code is executed.',
                    'tools': [{'type': 'function', 'name': 'printlab_probe', 'description': 'Verify tool calls only.',
                               'strict': True, 'parameters': {'type': 'object', 'properties': {'value': {'type': 'integer'}},
                                                              'required': ['value'], 'additionalProperties': False}}],
                    'tool_choice': {'type': 'function', 'name': 'printlab_probe'}}
            result = _response(client, 'POST', candidate['base_url'] + '/responses', headers=headers, json=body)
            output = result.get('output', []) if isinstance(result, dict) else []
            supported = any(isinstance(item, dict) and item.get('type') == 'function_call'
                            and item.get('name') == 'printlab_probe'
                            and json.loads(item.get('arguments', '{}')) == {'value': 1}
                            for item in output)
            return {'ok': supported, 'models': models, 'tool_supported': supported,
                    'message': 'Responses 工具调用验证成功。' if supported else '接口可连接，但尚未验证 Responses 工具调用。'}
    except (httpx.HTTPError, ValueError, TypeError, OSError):
        return {'ok': False, 'models': models, 'tool_supported': False if candidate['default_model'] else None,
                'message': '模型服务验证失败，请检查地址、API Key、模型与 Responses 工具支持。'}
