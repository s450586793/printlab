import json
import uuid

import pytest
from fastapi.testclient import TestClient

from printlab import agent_bridge, agent_usage as usage, container_runtime, core
from printlab.api import app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    monkeypatch.delenv('CODEX_HOME', raising=False)
    core.new_project('用量测试')
    core.new_project('其他项目')
    yield tmp_path
    usage._SESSION_CACHE.clear()


def tokens(input=100, cached=30, output=20, reasoning=10, write=0):
    return dict(zip(usage.TOKEN_KEYS, (input, cached, write, output, reasoning, input + output)))


def row(counts=None, model='gpt-6.1-sol', **updates):
    counts = counts if counts is not None else tokens()
    return {'id': uuid.uuid4().hex, 'project_id': 'P001', 'status': 'succeeded',
            'created_at': core.now(), 'usage': {**counts, 'response_count': 1, 'missing_responses': 0,
            'models': [{'model': model, 'service_tier': None, 'tokens': counts}]}, **updates}


def test_normalize_native_and_rollout_preserves_actual_reported_total():
    snake = tokens()
    snake['total_tokens'] = 123
    assert usage.normalize_tokens(snake) == snake
    camel = dict(zip(usage._CAMEL_KEYS, snake.values()))
    del camel['cacheWriteInputTokens']
    assert usage.normalize_tokens(camel) == snake


@pytest.mark.parametrize('bad', [None, [], {}, {'input_tokens': 0}])
def test_missing_usage_never_becomes_zero(bad):
    assert usage.normalize_tokens(bad) is None


@pytest.mark.parametrize('key,value', [('input_tokens', True), ('cached_input_tokens', -1),
    ('output_tokens', '10'), ('total_tokens', 2 ** 53), ('reasoning_output_tokens', float('nan')),
    ('cache_write_input_tokens', None), ('cached_input_tokens', 101), ('reasoning_output_tokens', 21)])
def test_invalid_usage_is_not_billable(key, value):
    assert usage.normalize_tokens({**tokens(), key: value}) is None


def test_reference_cost_subtracts_cache_and_never_adds_reasoning_twice(workspace):
    result = usage.summarize('P001', [row(tokens(input=1000000, cached=400000, output=200000, reasoning=100000))])
    assert result['tokens']['total_tokens'] == 1200000
    assert result['cost'] == {'amount': '3.24', 'currency': 'USD', 'basis': 'official_reference',
                               'partial': False, 'reason': None}
    assert result['models'][0]['pricing']['source_url'].startswith('https://developers.openai.com/')


def test_custom_provider_model_never_inherits_official_reference_price(workspace):
    result = usage.summarize('P001', [row(provider_id='custom', provider_revision=2)])
    assert result['tokens']['total_tokens'] == 120
    assert result['cost']['amount'] is None
    assert result['models'][0]['provider_id'] == 'custom'


def test_same_named_models_are_grouped_and_priced_by_provider(workspace):
    core.write_json(workspace / 'Runtime/agent-pricing.json', {'providers': {
        'alpha': {'currency': 'USD', 'models': {'shared-model': {
            'input_per_million': '1', 'cached_input_per_million': '0', 'output_per_million': '2'}}},
        'beta': {'currency': 'USD', 'models': {'shared-model': {
            'input_per_million': '5', 'cached_input_per_million': '0', 'output_per_million': '10'}}}}})
    result = usage.summarize('P001', [row(model='shared-model', provider_id='alpha', provider_name='Alpha gateway'),
                                    row(model='shared-model', provider_id='beta', provider_name='Beta gateway')])
    assert result['tokens']['total_tokens'] == 240
    assert len(result['models']) == 2
    costs = {item['provider_id']: item['cost']['amount'] for item in result['models']}
    assert costs == {'alpha': '0.00011', 'beta': '0.00055'}
    assert {item['provider_name'] for item in result['models']} == {'Alpha gateway', 'Beta gateway'}
    assert result['cost']['amount'] == '0.00066'


def test_provider_history_is_read_from_its_frozen_home(workspace, monkeypatch):
    path, entries, run = session(workspace)
    target = workspace / 'Runtime/codex-providers/custom/3/sessions/history' / path.name
    target.parent.mkdir(parents=True)
    run.update(provider_id='custom', provider_revision=3)
    write_session(target, entries + [record(run)])
    monkeypatch.setattr(agent_bridge.model_providers, 'session_home',
        lambda provider_id=None, revision=None: workspace / f'Runtime/codex-providers/{provider_id}/{revision}')
    result = usage.summarize('P001', [run])
    assert result['tokens']['total_tokens'] == 120
    assert result['cost']['amount'] is None


def test_same_native_ids_in_distinct_provider_homes_do_not_suppress_usage(workspace, monkeypatch):
    path, entries, first = session(workspace)
    second = {**first, 'id': uuid.uuid4().hex, 'provider_id': 'beta', 'provider_revision': 1}
    first.update(provider_id='alpha', provider_revision=1)
    for provider, count in [('alpha', 100), ('beta', 200)]:
        target = workspace / f'Runtime/codex-providers/{provider}/1/sessions/history' / path.name
        target.parent.mkdir(parents=True)
        write_session(target, entries + [record(first, tokens(input=count))])
    monkeypatch.setattr(agent_bridge.model_providers, 'session_home',
        lambda provider_id=None, revision=None: workspace / f'Runtime/codex-providers/{provider_id}/{revision}')
    result = usage.summarize('P001', [first, second])
    assert result['tokens']['total_tokens'] == 340
    assert len(result['models']) == 2


def test_unknown_zero_and_partial_history_have_different_coverage(workspace):
    assert usage.summarize('P001', [])['tokens'] is None
    missing = {'id': uuid.uuid4().hex, 'project_id': 'P001'}
    result = usage.summarize('P001', [missing])
    assert result['unrecorded_runs'] == 1 and result['cost']['amount'] is None
    result = usage.summarize('P001', [row(tokens(0, 0, 0, 0)), missing])
    assert result['recorded_runs'] == 1 and result['unrecorded_runs'] == 1 and result['partial']
    assert result['cost']['amount'] == '0.0' and result['cost']['partial']
    assert result['tokens']['total_tokens'] == 0
    malformed = row()
    malformed['usage'].update(tokens(0, 0, 0, 0), response_count=0, missing_responses=1)
    assert usage.summarize('P001', [malformed])['tokens'] is None


def test_partial_provider_response_and_unpriced_models_do_not_look_free(workspace):
    counts = row()
    counts['usage']['missing_responses'] = 1
    result = usage.summarize('P001', [counts, row(model='unknown')])
    assert result['partial'] and result['cost']['partial']
    assert result['cost']['amount'] == '0.000343'  # 只有已定价的已知部分。
    unknown = usage.summarize('P001', [row(model='unknown')])
    assert unknown['tokens']['total_tokens'] == 120 and unknown['cost']['amount'] is None


def test_invalid_model_breakdown_keeps_total_but_not_a_guessed_price(workspace):
    run = row()
    run['usage']['models'][0]['tokens'] = tokens(1, 0, 0, 0)
    result = usage.summarize('P001', [run])
    assert result['tokens'] == tokens() and result['models'][0]['model'] == '未知模型'
    assert result['cost']['amount'] is None


def test_custom_currency_and_prices_are_decimal_and_read_only(workspace):
    path = workspace / 'Runtime/agent-pricing.json'
    core.write_json(path, {'currency': 'CNY', 'models': {'gpt-6.1-sol': {
        'input_per_million': '1.25', 'cached_input_per_million': '0', 'output_per_million': '6'}}})
    before = path.read_bytes()
    result = usage.summarize('P001', [row(tokens(1000000, 400000, 200000, 100000))])
    assert result['cost']['amount'] == '1.95' and result['cost']['currency'] == 'CNY'
    assert result['cost']['basis'] == 'configured' and path.read_bytes() == before


@pytest.mark.parametrize('config', [None, False, {}, {'currency': '¥', 'models': {}},
    {'currency': 'USD', 'models': {'gpt-6.1-sol': {'input_per_million': '-1'}}},
    {'currency': 'USD', 'models': {'gpt-6.1-sol': {'input_per_million': 'NaN'}}}])
def test_bad_custom_prices_do_not_silently_fall_back_to_official(workspace, config):
    core.write_json(workspace / 'Runtime/agent-pricing.json', config)
    assert usage.summarize('P001', [row()])['cost']['amount'] is None


def test_bad_json_and_symlink_pricing_are_unpriced(workspace):
    path = workspace / 'Runtime/agent-pricing.json'
    path.write_text('not JSON')
    assert usage.summarize('P001', [row()])['cost']['amount'] is None
    path.unlink()
    path.symlink_to(workspace / 'Runtime/private/secret')
    assert usage.summarize('P001', [row()])['cost']['amount'] is None


def test_custom_small_rates_stay_decimal_and_excessive_precision_is_rejected(workspace):
    path = workspace / 'Runtime/agent-pricing.json'
    config = {'currency': 'USD', 'models': {'gpt-6.1-sol': {
        'input_per_million': '1e-8', 'cached_input_per_million': 0, 'output_per_million': 0}}}
    core.write_json(path, config)
    result = usage.summarize('P001', [row()])
    assert result['models'][0]['pricing']['input_per_million'] == '0.00000001'
    assert result['cost']['amount'] == '0.0000000000007'
    config['models']['gpt-6.1-sol']['input_per_million'] = '1e-999999'
    core.write_json(path, config)
    assert usage.summarize('P001', [row()])['cost']['amount'] is None


def test_nonstandard_tier_or_cache_write_requires_known_prices(workspace):
    run = row()
    run['usage']['models'][0]['service_tier'] = 'priority'
    assert usage.summarize('P001', [run])['cost']['amount'] is None
    result = usage.summarize('P001', [row(tokens(write=10))])
    assert result['models'][0]['cost']['reason'] == 'cache_write_pricing_unknown'
    assert result['cost']['amount'] is None


def session(workspace, metadata=None):
    tid, turn = str(uuid.uuid4()), str(uuid.uuid4())
    entries = [
        {'type': 'session_meta', 'payload': metadata or {'id': tid, 'cwd': str(core.project_path('P001'))}},
        {'type': 'turn_context', 'payload': {'turn_id': turn, 'model': 'gpt-6.1-sol'}},
    ]
    path = workspace / f'Runtime/codex/sessions/2026/11/15/rollout-2026-11-15-{tid}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    run = {'id': uuid.uuid4().hex, 'project_id': 'P001', 'status': 'succeeded', 'created_at': core.now(),
           'thread_id': tid, 'turn_id': turn}
    return path, entries, run


def record(run, counts=None, response='response-1', **updates):
    return {'type': 'token_usage_record', 'payload': {'thread_id': run['thread_id'],
        'session_id': run['thread_id'], 'root_turn_id': run['turn_id'], 'turn_id': run['turn_id'],
        'response_id': response, 'usage': counts if counts is not None else tokens(), **updates}}


def write_session(path, entries):
    path.write_text(''.join(json.dumps(entry) + '\n' for entry in entries))


def test_exact_history_backfills_matching_runs_without_writing(workspace):
    path, entries, run = session(workspace)
    entries += [record(run), record(run), record(run, response='second'),
                {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_tokens': 999999999}}}]
    write_session(path, entries)
    before = path.read_bytes()
    result = usage.summarize('P001', [run])
    assert result['recorded_runs'] == 1 and result['tokens']['total_tokens'] == 240
    assert result['cost']['amount'] == '0.000686'
    assert path.read_bytes() == before and 'usage' not in run


def test_history_and_live_share_identity_even_when_rollout_appends(workspace):
    path, entries, run = session(workspace)
    run['_usage_responses'] = {'response-1': {'tokens': tokens(), 'model': 'gpt-6.1-sol', 'service_tier': None}}
    run['usage'] = agent_bridge._usage_summary(run['_usage_responses'])
    write_session(path, entries + [record(run)])
    assert usage.summarize('P001', [run])['tokens']['total_tokens'] == 120
    write_session(path, entries + [record(run), record(run, response='second')])
    assert usage.summarize('P001', [run])['tokens']['total_tokens'] == 240


@pytest.mark.parametrize('identity', ['thread_id', 'session_id', 'root_turn_id', 'turn_id'])
def test_cross_thread_or_turn_history_is_excluded(workspace, identity):
    path, entries, run = session(workspace)
    write_session(path, entries + [record(run, **{identity: str(uuid.uuid4())})])
    assert usage.summarize('P001', [run])['tokens'] is None


@pytest.mark.parametrize('wrong', ['id', 'cwd'])
def test_history_metadata_must_match_project_and_thread(workspace, wrong):
    path, entries, run = session(workspace)
    entries[0]['payload'][wrong] = str(uuid.uuid4()) if wrong == 'id' else str(core.project_path('P002'))
    write_session(path, entries + [record(run)])
    assert usage.summarize('P001', [run])['tokens'] is None


def test_history_symlink_and_duplicate_session_files_are_rejected(workspace):
    path, entries, run = session(workspace)
    write_session(path, entries + [record(run)])
    external = workspace / 'private-rollout.jsonl'
    path.rename(external)
    path.symlink_to(external)
    assert usage.summarize('P001', [run])['tokens'] is None
    path.unlink()
    path.write_bytes(external.read_bytes())
    duplicate = path.parent / ('rollout-other-' + run['thread_id'] + '.jsonl')
    duplicate.write_bytes(path.read_bytes())
    assert usage.summarize('P001', [run])['tokens'] is None


def test_malformed_truncated_and_oversized_history_lines_degrade(workspace, monkeypatch):
    path, entries, run = session(workspace)
    write_session(path, entries + [record(run)])
    with path.open('a') as stream:
        stream.write('not JSON\n' + 'x' * 3000 + '\n' + '{"unfinished":')
    monkeypatch.setattr(usage, '_MAX_LINE', 2000)
    assert usage.summarize('P001', [run])['tokens']['total_tokens'] == 120


def test_conflicting_history_and_missing_model_do_not_get_false_price(workspace):
    path, entries, run = session(workspace)
    write_session(path, entries + [record(run), record(run, counts=tokens(200))])
    assert usage.summarize('P001', [run])['tokens'] is None
    entries[1]['payload'].pop('model')
    write_session(path, entries + [record(run)])
    result = usage.summarize('P001', [run])
    assert result['tokens']['total_tokens'] == 120 and result['cost']['amount'] is None


def test_historical_model_reroute_keeps_tokens_but_does_not_use_initial_price(workspace):
    path, entries, run = session(workspace)
    write_session(path, entries + [record(run), {'type': 'event_msg', 'payload': {
        'type': 'model_reroute', 'from_model': 'gpt-6.1-sol', 'to_model': 'other-model'}}])
    result = usage.summarize('P001', [run])
    assert result['tokens']['total_tokens'] == 120 and result['cost']['amount'] is None
    assert usage.with_history('P001', [run])[0]['usage']['model_changed']


def test_unknown_history_and_duplicate_run_turn_are_not_added(workspace):
    path, entries, run = session(workspace)
    write_session(path, entries + [record(run)])
    duplicate = {**run, 'id': uuid.uuid4().hex}
    assert usage.summarize('P001', [run, duplicate])['tokens'] is None
    assert usage.summarize('P002', [{**run, 'project_id': 'P002'}])['tokens'] is None


def test_duplicate_native_turn_with_live_usage_is_unknown_not_double_billed(workspace):
    run = row(thread_id=str(uuid.uuid4()), turn_id=str(uuid.uuid4()))
    duplicate = {**run, 'id': uuid.uuid4().hex}
    result = usage.summarize('P001', [run, duplicate])
    assert result['tokens'] is None and result['unrecorded_runs'] == 2
    assert result['cost']['amount'] is None


def test_unreadable_usage_does_not_hide_project_or_imply_no_tasks(workspace):
    file = workspace / f'Runtime/agent/runs/P001/{uuid.uuid4().hex}.json'
    file.parent.mkdir(parents=True)
    file.write_text('invalid JSON')
    result = core.summary(core.project_path('P001'))
    assert result['id'] == 'P001' and result['agent_usage']['partial']
    assert result['agent_usage']['cost']['reason'] == 'usage_unavailable'


def test_usage_summary_endpoints_are_authenticated_read_only_and_do_not_start_codex(workspace, monkeypatch):
    run = row()
    core.write_json(workspace / f'Runtime/agent/runs/P001/{run["id"]}.json', run)
    monkeypatch.setattr(agent_bridge, '_manager', lambda: pytest.fail('Reading usage must not start Codex'))
    protected = container_runtime.WebLogin(app, 'test', 'test-long-password', session_key=b't' * 32)
    client = TestClient(protected, base_url='http://127.0.0.1:8018')
    assert client.get('/api/overview').status_code == 401
    client.auth = ('test', 'test-long-password')
    client.get('/api/prepared/session')
    overview = client.get('/api/overview').json()
    assert next(p for p in overview['projects'] if p['id'] == 'P001')['agent_usage']['tokens'] == tokens()
    detail = client.get('/api/projects/P001').json()
    conversation = client.get('/api/projects/P001/agent').json()
    current = client.get(f'/api/projects/P001/agent/runs/{run["id"]}').json()
    assert detail['agent_usage'] == conversation['agent_usage'] == current['agent_usage']
    assert 'owner' not in json.dumps(current) and '_usage_responses' not in json.dumps(current)
    assert client.get(f'/api/projects/P002/agent/runs/{run["id"]}').status_code == 409
