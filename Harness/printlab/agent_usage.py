"""项目模型用量与费用估算；只读取精确请求记录，不用上下文占用推算账单。"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import threading

from . import core, model_providers

TOKEN_KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
              'output_tokens', 'reasoning_output_tokens', 'total_tokens')
_CAMEL_KEYS = ('inputTokens', 'cachedInputTokens', 'cacheWriteInputTokens',
               'outputTokens', 'reasoningOutputTokens', 'totalTokens')
_MAX_INTEGER = 2 ** 53 - 1
_PRICING_URL = 'https://developers.openai.com/api/docs/pricing'
_REFERENCE = {'gpt-6.1-sol': {'input_per_million': '2', 'cached_input_per_million': '0.1',
                            'output_per_million': '10'}}
_SESSION_CACHE = {}
_SESSION_LOCK = threading.RLock()
_MAX_LINE = 2 * 1024 * 1024


def normalize_tokens(raw):
    """校验 app-server camelCase 或 rollout snake_case 的原始用量。"""
    if not isinstance(raw, dict):
        return None
    result = {}
    for key, camel in zip(TOKEN_KEYS, _CAMEL_KEYS):
        value = raw.get(key, raw.get(camel, 0 if key == 'cache_write_input_tokens' else None))
        if type(value) is not int or not 0 <= value <= _MAX_INTEGER:
            return None
        result[key] = value
    if (result['cached_input_tokens'] > result['input_tokens']
            or result['reasoning_output_tokens'] > result['output_tokens']):
        return None
    return result


def _add(target, tokens):
    for key in TOKEN_KEYS:
        target[key] += tokens[key]


def _empty_tokens():
    return dict.fromkeys(TOKEN_KEYS, 0)


def _session_file(thread_id, provider_id='legacy', revision=0):
    if not isinstance(thread_id, str) or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', thread_id):
        return None
    home = Path(model_providers.session_home(provider_id, revision=revision)).expanduser().resolve()
    paths = []
    for directory in ('sessions', 'archived_sessions'):
        root = core.safe_path(home, directory, require_file=False)
        for candidate in root.glob(f'**/rollout-*-{thread_id}.jsonl'):
            try:
                paths.append(core.safe_path(home, candidate.relative_to(home).as_posix()))
            except (OSError, ValueError):
                continue
    return paths[0] if len(paths) == 1 else None


def _read_session(path, thread_id, project):
    """只缓存数值和模型归属，不保留或向 API 暴露会话内容。"""
    stat = path.stat()
    fingerprint = (stat.st_ino, stat.st_mtime_ns, stat.st_size, thread_id, str(project))
    with _SESSION_LOCK:
        cached = _SESSION_CACHE.get(str(path))
        if cached and cached[0] == fingerprint:
            return cached[1]
    verified, contexts, records, rerouted = False, {}, {}, set()
    current_turn = None
    with path.open(encoding='utf-8') as stream:
        while line := stream.readline(_MAX_LINE + 1):
            if len(line) > _MAX_LINE:
                while line and not line.endswith('\n'):
                    line = stream.readline(_MAX_LINE + 1)
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict) or not isinstance(item.get('payload'), dict):
                continue
            payload = item['payload']
            if item.get('type') == 'session_meta':
                cwd = payload.get('cwd')
                if payload.get('id') != thread_id or not isinstance(cwd, str) or Path(cwd).resolve() != project:
                    return {}
                verified = True
            if not verified:
                continue
            if item.get('type') == 'event_msg' and payload.get('type') == 'model_reroute':
                identity = payload.get('turn_id') or current_turn
                if isinstance(identity, str):
                    rerouted.add(identity)
                continue
            turn = payload.get('turn_id')
            if not isinstance(turn, str) or not turn or len(turn) > 200:
                continue
            if item.get('type') == 'turn_context':
                current_turn = turn
                from .agent_bridge import _model_name
                model = _model_name(payload.get('model'))
                previous = contexts.get(turn)
                contexts[turn] = model if turn not in contexts or previous == model else None
            elif item.get('type') == 'token_usage_record':
                if (payload.get('thread_id') != thread_id or payload.get('session_id') != thread_id
                        or payload.get('root_turn_id') != turn):
                    continue
                response_id = payload.get('response_id')
                if not isinstance(response_id, str) or not 0 < len(response_id) <= 256:
                    continue
                responses = records.setdefault(turn, {})
                tokens = normalize_tokens(payload.get('usage'))
                if response_id in responses and responses[response_id] != tokens:
                    # 相同 identity 的冲突记录保持未知，不能任选一份估价。
                    tokens = None
                responses[response_id] = tokens
    result = {turn: {'model': contexts.get(turn), 'responses': responses, 'model_changed': turn in rerouted}
              for turn, responses in records.items()} if verified else {}
    with _SESSION_LOCK:
        if len(_SESSION_CACHE) >= 32:
            _SESSION_CACHE.pop(next(iter(_SESSION_CACHE)))
        _SESSION_CACHE[str(path)] = (fingerprint, result)
    return result


def with_history(pid, rows):
    """将已核对归属的原始请求记录合并为只读视图，不改历史任务文件。"""
    project = core.project_path(pid)
    sessions = {}
    identities = Counter((row.get('provider_id', 'legacy'), row.get('provider_revision', 0),
                          row.get('thread_id'), row.get('turn_id')) for row in rows)
    result = []
    for row in rows:
        thread_id, turn = row.get('thread_id'), row.get('turn_id')
        provider_id, revision = row.get('provider_id', 'legacy'), row.get('provider_revision', 0)
        identity = (provider_id, revision, thread_id, turn)
        if not isinstance(thread_id, str) or not isinstance(turn, str):
            result.append(row)
            continue
        if identities[identity] != 1:
            # 恢复或导入的重复 native turn 归属不明确，不能将实时记录算两遍。
            result.append({**row, 'usage': None})
            continue
        session_identity = (provider_id, revision, thread_id)
        if session_identity not in sessions:
            try:
                path = _session_file(thread_id, provider_id, revision)
                sessions[session_identity] = _read_session(path, thread_id, project) if path else {}
            except (core.WorkflowError, OSError, ValueError):
                sessions[session_identity] = {}
        historical = sessions[session_identity].get(turn)
        if not historical:
            result.append(row)
            continue
        # 实时与历史共用 response identity；迟到的落盘记录不会重复累计。
        responses = {identity: {'tokens': tokens, 'model': historical['model'],
                               'service_tier': row.get('service_tier')}
                     for identity, tokens in historical['responses'].items()}
        live = row.get('_usage_responses')
        if isinstance(live, dict):
            for identity, entry in live.items():
                if not isinstance(entry, dict):
                    continue
                previous = responses.get(identity)
                if previous and previous['tokens'] != entry.get('tokens'):
                    responses[identity] = {**entry, 'tokens': None}
                else:
                    responses[identity] = entry
        elif normalize_tokens(row.get('usage')) is not None:
            # 没有请求 identity 的旧聚合不能与历史逐请求计数直接相加。
            result.append(row)
            continue
        from .agent_bridge import _usage_summary
        usage = _usage_summary(responses, bool(row.get('_usage_model_changed') or historical['model_changed']))
        result.append({**row, 'usage': usage})
    return result


def _rate(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        text = str(value)
        number = Decimal(text) if len(text) <= 64 else Decimal('NaN')
        return number if (number.is_finite() and 0 <= number <= 1000000
                          and number.as_tuple().exponent >= -12) else None
    except InvalidOperation:
        return None


def _pricing_config():
    try:
        path = core.safe_path(core.ROOT, 'Runtime/agent-pricing.json', require_file=False)
        if not path.exists():
            return None
        config = core.read_json(path)
        return config if isinstance(config, dict) else False
    except (OSError, ValueError):
        # 无效配置不悄悄退回参考价，避免把错误配置看成实际单价。
        return False


def _pricing(model, service_tier, config, provider_id='legacy'):
    if isinstance(config, dict) and isinstance(config.get('providers'), dict):
        scoped = config['providers'].get(provider_id)
        if scoped is not None or provider_id != 'legacy':
            config = scoped if isinstance(scoped, dict) else False
    elif provider_id != 'legacy':
        config = False
    if config is None:
        rates = _REFERENCE.get(model) if service_tier in {None, 'default', 'standard'} else None
        if rates is None:
            return None
        return {**rates, 'currency': 'USD', 'source': 'official_reference',
                'source_url': _PRICING_URL, 'as_of': '2026-11-15'}
    if not isinstance(config, dict):
        return None
    currency = config.get('currency')
    models = config.get('models')
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency) or not isinstance(models, dict):
        return None
    # 未知的服务等级不能套用默认等级单价。
    key = model if service_tier in {None, 'default', 'standard'} else f'{model}:{service_tier}'
    rates = models.get(key)
    if not isinstance(rates, dict):
        return None
    result = {key: _rate(rates.get(key)) for key in
              ('input_per_million', 'cached_input_per_million', 'output_per_million')}
    if any(value is None for value in result.values()):
        return None
    return {key: format(value, 'f') for key, value in result.items()} | {'currency': currency, 'source': 'configured'}


def _cost(tokens, pricing):
    if not pricing:
        return {'amount': None, 'currency': None, 'basis': None, 'partial': True,
                'reason': 'pricing_not_configured'}
    if tokens['cache_write_input_tokens']:
        return {'amount': None, 'currency': pricing['currency'], 'basis': pricing['source'],
                'partial': True, 'reason': 'cache_write_pricing_unknown'}
    amount = (Decimal(tokens['input_tokens'] - tokens['cached_input_tokens']) * Decimal(pricing['input_per_million'])
              + Decimal(tokens['cached_input_tokens']) * Decimal(pricing['cached_input_per_million'])
              + Decimal(tokens['output_tokens']) * Decimal(pricing['output_per_million'])) / 1000000
    return {'amount': format(amount, 'f'), 'currency': pricing['currency'], 'basis': pricing['source'],
            'partial': False, 'reason': None}


def summarize(pid, rows=None):
    """返回同口径项目合计；缺失的任务保持未知，不补成免费用量。"""
    core.project_path(pid)
    if rows is None:
        from . import agent_bridge
        try:
            rows = agent_bridge._runs(pid)
        except (OSError, ValueError, TypeError, AttributeError):
            # 统计不可用不应导致项目从图册消失，也不应呈现为无任务。
            return {'tokens': None, 'recorded_runs': 0, 'unrecorded_runs': 0, 'partial': True,
                    'cost': {'amount': None, 'currency': None, 'basis': None, 'partial': True,
                             'reason': 'usage_unavailable'}, 'models': []}
    rows = with_history(pid, rows)
    config = _pricing_config()
    groups = {}
    provider_names = {}
    recorded = unrecorded = 0
    partial = False
    for row in rows:
        usage = row.get('usage')
        tokens = normalize_tokens(usage)
        if tokens is None or usage.get('response_count') == 0:
            unrecorded += 1
            continue
        recorded += 1
        partial = partial or bool(usage.get('missing_responses'))
        models = usage.get('models') or []
        valid = []
        for model in models:
            if not isinstance(model, dict):
                continue
            counts = normalize_tokens(model.get('tokens'))
            if counts is not None:
                valid.append((model, counts))
        totals = _empty_tokens()
        for _, counts in valid:
            _add(totals, counts)
        if totals != tokens:
            # 模型明细缺失时保留 Token，但不给未确认的模型套价格。
            valid = [({'model': None, 'service_tier': None}, tokens)]
        for model, counts in valid:
            identity = (row.get('provider_id', 'legacy'), model.get('model'), model.get('service_tier'))
            if not all(value is None or isinstance(value, str) for value in identity):
                identity = ('legacy', None, None)
            target = groups.setdefault(identity, _empty_tokens())
            provider_names[identity[0]] = row.get('provider_name') or identity[0]
            _add(target, counts)
    total = _empty_tokens()
    models = []
    for (provider_id, model, tier), tokens in sorted(groups.items(), key=lambda item: str(item[0])):
        _add(total, tokens)
        pricing = _pricing(model, tier, config, provider_id)
        models.append({'provider_id': provider_id, 'provider_name': provider_names[provider_id],
                       'model': model or '未知模型', 'service_tier': tier, 'tokens': tokens,
                       'pricing': pricing, 'cost': _cost(tokens, pricing)})
    known = [model['cost'] for model in models if model['cost']['amount'] is not None]
    currencies = {cost['currency'] for cost in known}
    bases = {cost['basis'] for cost in known}
    incomplete = partial or unrecorded > 0 or len(known) < len(models)
    if len(currencies) == 1:
        cost = {'amount': format(sum((Decimal(item['amount']) for item in known), Decimal(0)), 'f'),
                'currency': next(iter(currencies)), 'basis': next(iter(bases)) if len(bases) == 1 else 'mixed',
                'partial': incomplete, 'reason': 'missing_usage' if partial or unrecorded else
                ('pricing_not_configured' if len(known) < len(models) else None)}
    else:
        cost = {'amount': None, 'currency': None, 'basis': None, 'partial': incomplete,
                'reason': 'mixed_currency' if currencies else ('missing_usage' if not models else 'pricing_not_configured')}
    return {'tokens': total if recorded else None, 'recorded_runs': recorded, 'unrecorded_runs': unrecorded,
            'partial': partial or unrecorded > 0, 'cost': cost, 'models': models}
