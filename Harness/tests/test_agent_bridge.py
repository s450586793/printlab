import copy
import hashlib
import io
import json
import os
import queue
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image

from printlab import agent_bridge as bridge, core, project_files, project_images


def wait_for(function, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = function()
        if result:
            return result
        time.sleep(0.01)
    pytest.fail('Timed out waiting for mocked Codex work')


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    home = tmp_path / 'Runtime/codex'
    monkeypatch.setenv('CODEX_HOME', str(home))
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'PRINTLAB_PROVIDER_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    home.mkdir(parents=True)
    (home / 'config.toml').write_text('model = "configured-model"\n', encoding='utf-8')
    core.write_json(tmp_path / 'Runtime/config.json', {})
    core.new_project('Agent project')
    core.new_project('Other project')
    yield tmp_path
    bridge.shutdown()
    for worker in list(bridge._worker_threads):
        worker.join(timeout=1)


@pytest.fixture
def runtime(workspace, monkeypatch):
    instances = []
    class FakeServer:
        def __init__(self, on_message, on_exit, spec=None):
            self.on_message, self.on_exit = on_message, on_exit
            self.spec = spec
            self.closed, self.calls, self.responses = False, [], []
            self.thread_count = self.turn_count = 0
            instances.append(self)
        def request(self, method, params, timeout=60):
            self.calls.append((method, copy.deepcopy(params)))
            if method == 'thread/start':
                self.thread_count += 1
                return {'thread': {'id': f'thread-{self.thread_count}'}, 'model': 'effective-model',
                        'modelProvider': 'configured-provider', 'serviceTier': 'default'}
            if method == 'thread/resume':
                return {'thread': {'id': params['threadId']}, 'model': 'effective-model',
                        'modelProvider': 'configured-provider', 'serviceTier': 'default'}
            if method == 'turn/start':
                self.turn_count += 1
                return {'turn': {'id': f'turn-{len(instances)}-{self.turn_count}', 'status': 'inProgress'}}
            if method == 'turn/interrupt':
                self.finish(params['threadId'], 'interrupted')
                return {}
            if method == 'model/list':
                return {'data': [{'id': 'configured-model', 'displayName': 'Configured model', 'isDefault': True}]}
            raise AssertionError(method)
        def notify(self, method, params): self.calls.append((method, params))
        def respond(self, identity, result): self.responses.append((identity, result))
        def _write(self, message): self.responses.append(message)
        def emit(self, method, tid, **params):
            self.on_message({'method': method, 'params': {'threadId': tid, **params}})
        def finish(self, tid, status='completed', text='Created the requested model.', error=None):
            if text:
                self.emit('item/agentMessage/delta', tid, itemId='message-1', delta=text[:8])
                self.emit('item/completed', tid, item={'id': 'message-1', 'type': 'agentMessage', 'text': text})
            self.emit('turn/completed', tid, turn={'id': 'turn-final', 'status': status, 'error': error})
        def stop(self):
            self.closed = True
            self.on_exit()
    monkeypatch.setattr(bridge, 'AppServer', FakeServer)
    return instances


@pytest.fixture
def provider_registry(workspace, monkeypatch):
    snapshots = {}
    current = {'alpha': 1, 'beta': 1}
    default = ['alpha']
    for identity, model in [('alpha', 'alpha-model'), ('beta', 'beta-model')]:
        snapshots[(identity, 1)] = {'id': identity, 'revision': 1, 'name': identity.title(),
            'model': model, 'models': [model, model + '-small'], 'base_url': f'https://{identity}.example/v1',
            'api_key': identity + '-nonstandard-private-sentinel',
            'home': workspace / 'Runtime/codex-providers' / identity / '1', 'legacy': False}

    def resolve(provider_id=None, model=None, revision=None):
        identity = provider_id or default[0]
        if identity == 'legacy':
            return {'id': 'legacy', 'revision': 0, 'name': 'Legacy', 'model': model,
                    'base_url': None, 'api_key': None, 'home': workspace / 'Runtime/codex', 'legacy': True}
        selected = snapshots.get((identity, revision if revision is not None else current.get(identity)))
        if selected is None:
            raise core.WorkflowError('Unknown provider')
        return {**selected, 'model': model or selected['model']}

    store = SimpleNamespace(resolve=resolve,
        session_home=lambda provider_id=None, revision=None: resolve(provider_id, revision=revision)['home'],
        secret_values=lambda: tuple(spec['api_key'] for spec in snapshots.values()),
        settings=lambda: {'default_provider_id': default[0], 'providers': [
            {**snapshots[(identity, revision)], 'default_model': snapshots[(identity, revision)]['model']}
            for identity, revision in current.items()]})
    monkeypatch.setattr(bridge, 'model_providers', store, raising=False)
    return SimpleNamespace(snapshots=snapshots, current=current, default=default, store=store)


def running(pid, rid):
    return wait_for(lambda: bridge._read_run(pid, rid) if bridge._read_run(pid, rid)['status'] == 'running' else None)


def finished(pid, rid):
    return wait_for(lambda: bridge._read_run(pid, rid) if bridge._read_run(pid, rid)['status'] in bridge.TERMINAL else None)


def response_usage(input_tokens=80, cached_tokens=20, output_tokens=20):
    return {'inputTokens': input_tokens, 'cachedInputTokens': cached_tokens, 'cacheWriteInputTokens': 0,
            'outputTokens': output_tokens, 'reasoningOutputTokens': 5, 'totalTokens': input_tokens + output_tokens}


def emit_response(server, row, response_id='response-1', usage=None, **params):
    server.emit('rawResponse/completed', row['thread_id'], turnId=row['turn_id'], responseId=response_id,
                usage=usage, **params)


def test_usage_counts_exact_responses_once_and_preserves_requested_model(workspace, runtime):
    identity = str(uuid.uuid4())
    task = bridge.submit('P001', 'Build a model', request_id=identity)
    row = running('P001', task['id'])
    assert row['model'] is None and row['effective_model'] == 'effective-model'
    assert 'usage' not in row
    server = runtime[0]
    emit_response(server, row, usage=response_usage(), usageMetadata={'private': 'not-public'})
    emit_response(server, row, usage=response_usage())
    server.emit('thread/tokenUsage/updated', row['thread_id'], turnId=row['turn_id'],
                tokenUsage={'total': response_usage(99999, 500, 500), 'last': response_usage()})
    emit_response(server, row, 'response-2', response_usage(40, 10, 10))
    usage = bridge.run('P001', row['id'])['usage']
    assert usage['source'] == 'raw_response' and usage['response_count'] == 2
    assert usage['total_tokens'] == 150 and usage['input_tokens'] == 120 and usage['output_tokens'] == 30
    assert usage['cached_input_tokens'] == 30 and usage['reasoning_output_tokens'] == 10
    assert usage['missing_responses'] == 0 and usage['model_changed'] is False
    assert usage['models'] == [{'model': 'effective-model', 'service_tier': 'default', 'response_count': 2,
                                'tokens': {key: usage[key] for key in ('input_tokens', 'cached_input_tokens',
                                          'cache_write_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')}}]
    assert bridge.submit('P001', 'Build a model', request_id=identity)['id'] == row['id']
    assert '_usage_responses' not in bridge.run('P001', row['id'])
    assert 'not-public' not in json.dumps(bridge._read_run('P001', row['id']))
    server.finish(row['thread_id'])
    assert finished('P001', row['id'])['usage'] == usage


@pytest.mark.parametrize('invalid', [None, [], {}, {'inputTokens': True},
    {**response_usage(), 'cachedInputTokens': 81}, {**response_usage(), 'outputTokens': -1}])
def test_usage_missing_and_invalid_responses_remain_unknown(workspace, runtime, invalid):
    task = bridge.submit('P001', 'Build a model')
    row = running('P001', task['id'])
    emit_response(runtime[0], row, usage=invalid)
    emit_response(runtime[0], row, usage=invalid)
    usage = bridge.run('P001', row['id'])['usage']
    assert usage['response_count'] == 0 and usage['missing_responses'] == 1 and usage['models'] == []
    emit_response(runtime[0], row, 'response-zero', {key: 0 for key in response_usage()})
    usage = bridge.run('P001', row['id'])['usage']
    assert usage['response_count'] == 1 and usage['missing_responses'] == 1 and usage['total_tokens'] == 0
    runtime[0].finish(row['thread_id'])
    finished('P001', row['id'])


def test_usage_buffers_notifications_before_turn_start_response(workspace, runtime, monkeypatch):
    bridge.models()
    server = runtime[0]
    request = server.request
    def early_response(method, params, timeout=60):
        result = request(method, params, timeout)
        if method == 'turn/start':
            server.emit('rawResponse/completed', params['threadId'], turnId=result['turn']['id'],
                        responseId='early-response', usage=response_usage())
            server.emit('rawResponse/completed', params['threadId'], turnId='unrelated-turn',
                        responseId='wrong-response', usage=response_usage())
        return result
    monkeypatch.setattr(server, 'request', early_response)
    task = bridge.submit('P001', 'Build a model')
    row = running('P001', task['id'])
    usage = wait_for(lambda: bridge.run('P001', row['id']).get('usage'))
    assert usage['response_count'] == 1 and usage['total_tokens'] == 100
    server.finish(row['thread_id'])
    assert finished('P001', row['id'])['status'] == 'succeeded'


def test_usage_isolates_projects_and_ignores_unrelated_or_malformed_notifications(workspace, runtime):
    first = bridge.submit('P001', 'One')
    a = running('P001', first['id'])
    second = bridge.submit('P002', 'Two')
    b = running('P002', second['id'])
    server = runtime[0]
    emit_response(server, a, usage=response_usage())
    for params in [None, [], {'threadId': [], 'turnId': a['turn_id']},
                   {'threadId': 'unknown-thread', 'turnId': a['turn_id'], 'responseId': 'x', 'usage': response_usage()},
                   {'threadId': a['thread_id'], 'turnId': 'wrong-turn', 'responseId': 'x', 'usage': response_usage()},
                   {'threadId': a['thread_id'], 'turnId': a['turn_id'], 'responseId': [], 'usage': response_usage()}]:
        server.on_message({'method': 'rawResponse/completed', 'params': params})
    assert bridge.run('P001', a['id'])['usage']['total_tokens'] == 100
    assert 'usage' not in bridge.run('P002', b['id'])
    emit_response(server, b, usage=response_usage(40, 0, 10))
    assert bridge.run('P002', b['id'])['usage']['total_tokens'] == 50
    server.finish(a['thread_id'])
    server.finish(b['thread_id'])
    finished('P001', a['id'])
    finished('P002', b['id'])


def test_usage_late_responses_and_restart_never_count_previous_turn_in_followup(workspace, runtime):
    task = bridge.submit('P001', 'First')
    first = running('P001', task['id'])
    server = runtime[0]
    emit_response(server, first, usage=response_usage())
    server.finish(first['thread_id'])
    finished('P001', first['id'])
    wait_for(lambda: not bridge._manager().running)
    emit_response(server, first, 'late-response', response_usage(40, 0, 10))
    assert bridge.run('P001', first['id'])['usage']['total_tokens'] == 150
    bridge.shutdown()
    followup = bridge.submit('P001', 'Second')
    second = running('P001', followup['id'])
    server = runtime[-1]
    assert second['thread_id'] == first['thread_id']
    emit_response(server, first, usage=response_usage())
    assert bridge.run('P001', first['id'])['usage']['response_count'] == 2
    assert 'usage' not in bridge.run('P001', second['id'])
    emit_response(server, second, usage=response_usage(40, 0, 10))
    assert bridge.run('P001', second['id'])['usage']['total_tokens'] == 50
    server.finish(second['thread_id'])
    finished('P001', second['id'])


@pytest.mark.parametrize('status', ['failed', 'interrupted', 'cancelled'])
def test_usage_survives_failed_or_interrupted_turn(workspace, runtime, status):
    task = bridge.submit('P001', 'Build a model')
    row = running('P001', task['id'])
    emit_response(runtime[0], row, usage=response_usage())
    if status == 'cancelled':
        bridge.cancel('P001', row['id'])
    else:
        runtime[0].finish(row['thread_id'], status)
    result = finished('P001', row['id'])
    assert result['status'] == status and result['usage']['total_tokens'] == 100


def test_usage_rerouted_model_is_not_priced_as_the_selected_model(workspace, runtime):
    task = bridge.submit('P001', 'Build a model')
    row = running('P001', task['id'])
    emit_response(runtime[0], row, usage=response_usage())
    runtime[0].emit('model/rerouted', row['thread_id'], turnId=row['turn_id'], fromModel='effective-model',
                    toModel='another-model', reason='fallback')
    emit_response(runtime[0], row, 'response-2', response_usage())
    result = bridge.run('P001', row['id'])
    assert result['effective_model'] == 'effective-model'
    assert result['usage']['model_changed'] is True and result['usage']['total_tokens'] == 200
    assert result['usage']['models'][0]['model'] is None
    runtime[0].finish(row['thread_id'])
    finished('P001', row['id'])


def test_usage_never_uses_requested_or_secret_model_when_runtime_model_is_unavailable(workspace, runtime, monkeypatch):
    bridge.models()
    server = runtime[0]
    request = server.request
    monkeypatch.setenv('PRINTLAB_TEST_SECRET', 'sk-model-secret-sentinel')
    def without_model(method, params, timeout=60):
        result = request(method, params, timeout)
        if method == 'thread/start':
            result['model'] = 'sk-model-secret-sentinel'
            result['modelProvider'] = {'invalid': 'provider'}
        return result
    monkeypatch.setattr(server, 'request', without_model)
    task = bridge.submit('P001', 'Build a model', model='requested-model')
    row = running('P001', task['id'])
    emit_response(server, row, usage=response_usage())
    result = bridge.run('P001', row['id'])
    assert result['model'] == 'requested-model' and result['effective_model'] is None
    assert result['model_provider'] is None and result['usage']['models'][0]['model'] is None
    assert 'sk-model-secret-sentinel' not in json.dumps(result)
    server.finish(row['thread_id'])
    finished('P001', row['id'])


def test_usage_storage_error_never_stops_model_work(workspace, runtime, monkeypatch):
    task = bridge.submit('P001', 'Build a model')
    row = running('P001', task['id'])
    def fail(*args, **kwargs): raise OSError('storage error')
    monkeypatch.setattr(bridge, '_record_usage', fail)
    emit_response(runtime[0], row, usage=response_usage())
    assert not runtime[0].closed
    runtime[0].finish(row['thread_id'])
    assert finished('P001', row['id'])['status'] == 'succeeded'


def test_real_turn_protocol_saves_messages_artifacts_and_handoff(workspace, runtime):
    prompt = 'Build a hook; literal shell text $(echo not-executed) stays user input.'
    task = bridge.submit('P001', prompt, model='configured-model', request_id=str(uuid.uuid4()), owner='browser')
    row = running('P001', task['id'])
    server = runtime[0]
    params = next(params for method, params in server.calls if method == 'thread/start')
    assert params['cwd'] == str(core.project_path('P001'))
    assert 'printlab context P001' in params['developerInstructions']
    assert 'Runtime/private' in params['developerInstructions']
    assert params['sandbox'] == 'workspace-write'
    turn = next(params for method, params in server.calls if method == 'turn/start')
    assert turn['input'] == [{'type': 'text', 'text': prompt}]
    # Codex tool subprocesses can call core project functions without deadlocking.
    core.handoff('P001', 'A tool can take the project short lock while the agent runs.')
    output = core.project_path('P001') / 'Exports/v001/model.stl'
    output.parent.mkdir(parents=True)
    output.write_text('solid hook\nendsolid hook\n')
    server.emit('item/started', row['thread_id'], item={'id': 'command', 'type': 'commandExecution', 'command': 'printlab build P001 --help'})
    server.finish(row['thread_id'])
    result = finished('P001', task['id'])
    assert result['status'] == 'succeeded' and result['result'] == 'Created the requested model.'
    assert any(file['path'] == 'Exports/v001/model.stl' for file in result['artifacts'])
    assert (core.project_path('P001') / result['handoff_path']).is_file()
    events = bridge.run('P001', task['id'])['events']
    assert [event['seq'] for event in events] == list(range(1, len(events) + 1))
    assert {'assistant_delta', 'assistant_message', 'tool', 'completed'}.issubset({event['type'] for event in events})
    conversation = bridge.conversation('P001')
    assert conversation['messages'][0]['content'] == prompt
    assert conversation['messages'][-1]['role'] == 'assistant'
    assert conversation['active_run'] is None and conversation['latest_run']['id'] == task['id']
    assert 'owner' not in json.dumps(bridge.run('P001', task['id']))


def reference_image(pid='P001'):
    data = io.BytesIO()
    Image.new('RGB', (20, 12), 'blue').save(data, format='PNG')
    return project_images.save_image(pid, data.getvalue(), 'reference.png')


def test_uploaded_image_reaches_native_turn_and_remains_in_conversation(workspace, runtime):
    image = reference_image()
    identity = str(uuid.uuid4())
    task = bridge.submit('P001', 'Use this reference', attachment_ids=[image['id']], request_id=identity, owner='browser')
    row = running('P001', task['id'])
    server = runtime[0]
    turn = next(params for method, params in server.calls if method == 'turn/start')
    expected = project_images.resolve_image('P001', image['id'])[1]
    assert turn['input'] == [{'type':'text', 'text':'Use this reference'}, {'type':'localImage', 'path':str(expected)}]
    assert turn['input'][1]['path'].startswith(str(core.project_path('P001')))
    assert bridge.conversation('P001')['messages'][0]['attachments'] == [image]
    assert bridge.submit('P001', 'Use this reference', attachment_ids=[image['id']], request_id=identity, owner='browser')['id'] == task['id']
    with pytest.raises(bridge.BridgeError):
        bridge.submit('P001', 'Use this reference', request_id=identity, owner='browser')
    server.finish(row['thread_id'])
    assert finished('P001', task['id'])['attachments'] == [image]
    assert bridge.conversation('P001')['messages'][0]['attachments'] == [image]


def test_images_changed_while_queued_are_not_silently_sent_to_codex(workspace, runtime, monkeypatch):
    image = reference_image()
    manager = bridge._manager()
    original_launch = manager.launch
    monkeypatch.setattr(manager, 'launch', lambda *args: None)
    task = bridge.submit('P001', '', attachment_ids=[image['id']])
    _, path = project_images.resolve_image('P001', image['id'])
    path.write_bytes(b'changed after upload')
    original_launch('P001', task['id'])
    result = finished('P001', task['id'])
    assert result['status'] == 'failed' and '参考图' in result['error']
    assert not any(method == 'turn/start' for method, _ in runtime[0].calls)


def test_replacing_blob_and_manifest_together_cannot_change_a_queued_reference(workspace, runtime, monkeypatch):
    image = reference_image()
    manager = bridge._manager()
    original_launch = manager.launch
    monkeypatch.setattr(manager, 'launch', lambda *args: None)
    task = bridge.submit('P001', 'Read the original image', attachment_ids=[image['id']])
    _, path = project_images.resolve_image('P001', image['id'])
    replacement = io.BytesIO()
    Image.new('RGB', (20, 12), 'red').save(replacement, format='PNG')
    path.write_bytes(replacement.getvalue())
    metadata = core.read_json(path.with_suffix('.json'))
    metadata.update(size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    core.write_json(path.with_suffix('.json'), metadata)
    assert project_images.resolve_image('P001', image['id'])[0]['sha256'] != image['sha256']
    original_launch('P001', task['id'])
    result = finished('P001', task['id'])
    assert result['status'] == 'failed' and '排队期间发生变化' in result['error']
    assert not any(method == 'turn/start' for method, _ in runtime[0].calls)


def test_cross_project_or_path_like_image_is_rejected_before_launch(workspace, runtime):
    other = reference_image('P002')
    for image_ids in [[other['id']], ['../private'], ['a' * 32], ['a' * 32] * 7]:
        with pytest.raises(bridge.BridgeError):
            bridge.submit('P001', 'Review image', attachment_ids=image_ids)
    assert runtime == [] and bridge.conversation('P001')['messages'] == []


def test_native_turn_and_resume_receive_every_uploaded_reference_without_a_count_cap(workspace, runtime):
    uploaded = [reference_image() for _ in range(7)]
    identities = [image['id'] for image in uploaded]
    task = bridge.submit('P001', 'Use every reference', attachment_ids=identities)
    row = running('P001', task['id'])
    server = runtime[0]
    expected = [{'type':'localImage', 'path':str(project_images.resolve_image('P001', identity)[1])} for identity in identities]
    turn = next(params for method, params in server.calls if method == 'turn/start')
    assert turn['input'][1:] == expected
    server.finish(row['thread_id']); finished('P001', task['id'])
    resumed = bridge.resume('P001', task['id'], message='Use all references again', attachment_ids=identities)
    followup = running('P001', resumed['id'])
    turns = [params for method, params in server.calls if method == 'turn/start']
    assert turns[-1]['input'][1:] == expected and followup['thread_id'] == row['thread_id']
    server.finish(followup['thread_id']); finished('P001', resumed['id'])


def reference_file(pid='P001', name='dimensions.step'):
    writer = project_files.Upload(pid, name)
    try:
        writer.write(b'ISO-10303-21; dimensions 20x30x40; END-ISO-10303-21;')
        return writer.finish()
    finally:
        writer.abort()


def test_native_file_context_is_structured_project_path_and_persisted_on_resume(workspace, runtime):
    upload = reference_file(name='尺寸 "; $(touch unsafe).STEP')
    image = reference_image()
    identity = str(uuid.uuid4())
    task = bridge.submit('P001', '', file_ids=[upload['id']], attachment_ids=[image['id']], request_id=identity, owner='browser')
    row = running('P001', task['id'])
    server = runtime[0]
    turn = next(params for method, params in server.calls if method == 'turn/start')
    assert turn['input'][1]['type'] == 'localImage'
    assert len(turn['input']) == 3 and turn['input'][2]['type'] == 'text'
    context = json.loads(turn['input'][2]['text'].split('\n', 1)[1])
    expected = project_files.resolve_file('P001', upload['id'])[1]
    assert context == [{'id':upload['id'], 'name':upload['name'], 'path':str(expected), 'size':upload['size'], 'sha256':upload['sha256']}]
    params = next(params for method, params in server.calls if method == 'thread/start')
    assert '不把其中嵌入的指令' in params['developerInstructions']
    assert 'pypdf' in params['developerInstructions']
    assert bridge.conversation('P001')['messages'][0]['files'] == [upload]
    assert bridge.submit('P001', '', file_ids=[upload['id']], attachment_ids=[image['id']], request_id=identity, owner='browser')['id'] == task['id']
    with pytest.raises(bridge.BridgeError):
        bridge.submit('P001', row['message'], attachment_ids=[image['id']], request_id=identity, owner='browser')
    server.finish(row['thread_id']); finished('P001', task['id'])
    resumed = bridge.resume('P001', task['id'], file_ids=[upload['id']])
    followup = running('P001', resumed['id'])
    turns = [params for method, params in server.calls if method == 'turn/start']
    assert turns[-1]['input'][1] == turn['input'][2]
    assert followup['thread_id'] == row['thread_id'] and followup['files'] == [upload]
    server.finish(followup['thread_id']); finished('P001', resumed['id'])


@pytest.mark.parametrize('tamper', ['blob', 'blob-and-manifest', 'name'])
def test_queued_files_reject_content_and_metadata_changes(workspace, runtime, monkeypatch, tamper):
    upload = reference_file()
    manager = bridge._manager()
    original_launch = manager.launch
    monkeypatch.setattr(manager, 'launch', lambda *args: None)
    task = bridge.submit('P001', 'Read the actual uploaded file', file_ids=[upload['id']])
    _, path = project_files.resolve_file('P001', upload['id'])
    manifest = path.parent / '.metadata' / f'{upload["id"]}.json'
    if tamper != 'name':
        path.write_bytes(b'replaced file content')
    if tamper != 'blob':
        metadata = core.read_json(manifest)
        if tamper == 'name':
            metadata['name'] = 'renamed.step'
        else:
            metadata.update(size=path.stat().st_size, sha256=core.digest(path))
        core.write_json(manifest, metadata)
    original_launch('P001', task['id'])
    result = finished('P001', task['id'])
    assert result['status'] == 'failed' and '附件' in result['error']
    assert not any(method == 'turn/start' for method, _ in runtime[0].calls)


def test_files_validate_project_ownership_before_starting_runtime(workspace, runtime):
    other = reference_file('P002')
    for ids in [[other['id']], ['../private'], ['a' * 32], [other['id']] * 2]:
        with pytest.raises(bridge.BridgeError):
            bridge.submit('P001', 'Review file', file_ids=ids)
    assert runtime == [] and bridge.conversation('P001')['messages'] == []


def test_followup_resumes_exact_project_thread_and_deduplicates_requests(workspace, runtime):
    request_id = str(uuid.uuid4())
    task = bridge.submit('P001', 'First message', request_id=request_id, owner='browser')
    row = running('P001', task['id'])
    assert bridge.submit('P001', 'First message', request_id=request_id, owner='browser')['id'] == task['id']
    with pytest.raises(bridge.BridgeError): bridge.submit('P001', 'Different message', request_id=request_id, owner='browser')
    with pytest.raises(bridge.BridgeError): bridge.submit('P001', 'Another simultaneous message')
    runtime[0].finish(row['thread_id'])
    finished('P001', task['id'])
    followup = bridge.submit('P001', 'Make it thicker')
    second = running('P001', followup['id'])
    assert second['thread_id'] == row['thread_id']
    assert sum(method == 'thread/start' for method, _ in runtime[0].calls) == 1
    assert any(method == 'thread/resume' for method, _ in runtime[0].calls)
    runtime[0].finish(second['thread_id'])
    finished('P001', followup['id'])
    assert len(bridge.conversation('P001')['messages']) == 4


def test_projects_have_independent_threads_and_cancellation(workspace, runtime):
    first = bridge.submit('P001', 'Project one')
    a = running('P001', first['id'])
    second = bridge.submit('P002', 'Project two')
    b = running('P002', second['id'])
    assert a['thread_id'] != b['thread_id'] and len(runtime) == 1
    bridge.cancel('P001', first['id'])
    assert finished('P001', first['id'])['status'] == 'cancelled'
    assert bridge._read_run('P002', second['id'])['status'] == 'running'
    runtime[0].finish(b['thread_id'])
    assert finished('P002', second['id'])['status'] == 'succeeded'
    with pytest.raises(bridge.BridgeError): bridge.run('P002', first['id'])
    assert bridge.cancel('P002', second['id'])['status'] == 'succeeded'


def test_queued_cancel_and_recovery_never_launch_or_replay(workspace, monkeypatch):
    launches = []
    monkeypatch.setattr(bridge.Manager, 'launch', lambda self, pid, rid: launches.append((pid, rid)))
    task = bridge.submit('P001', 'Queued work')
    assert bridge.cancel('P001', task['id'])['status'] == 'cancelled'
    next_task = bridge.submit('P001', 'Saved work')
    bridge._change('P001', next_task['id'], status='awaiting_input', pending_request={'kind': 'user_input'})
    bridge.recover()
    row = bridge._read_run('P001', next_task['id'])
    assert row['status'] == 'interrupted' and row['pending_request'] is None
    assert len(launches) == 2
    resumed = bridge.resume('P001', next_task['id'])
    assert resumed['id'] not in {task['id'], next_task['id']} and len(launches) == 3
    with pytest.raises(bridge.BridgeError): bridge.resume('P001', resumed['id'])


def test_question_reply_and_approval_are_scoped_to_native_request(workspace, runtime):
    task = bridge.submit('P001', 'Ask for a dimension')
    row = running('P001', task['id']); server = runtime[0]
    server.on_message({'id': 77, 'method': 'item/tool/requestUserInput', 'params': {'threadId': row['thread_id'], 'questions': [
        {'id': 'width', 'header': 'Width', 'question': 'How wide?', 'options': [{'label': '20mm', 'description': 'Small'}]}]}})
    assert bridge.run('P001', task['id'])['pending_request']['kind'] == 'user_input'
    bridge.reply('P001', task['id'], message='20mm')
    assert server.responses[-1] == (77, {'answers': {'width': {'answers': ['20mm']}}})
    server.on_message({'id': 78, 'method': 'item/commandExecution/requestApproval', 'params': {'threadId': row['thread_id'], 'command': 'printlab build --help'}})
    with pytest.raises(bridge.BridgeError): bridge.reply('P001', task['id'], decision='acceptForSession')
    bridge.reply('P001', task['id'], decision='decline')
    assert server.responses[-1] == (78, {'decision': 'decline'})
    server.finish(row['thread_id'])
    finished('P001', task['id'])
    assert any(message['content'] == '20mm' for message in bridge.conversation('P001')['messages'])
    with pytest.raises(bridge.BridgeError): bridge.reply('P001', task['id'], message='stale answer')


def test_failed_turn_and_process_exit_are_not_success(workspace, runtime, monkeypatch):
    monkeypatch.setenv('TEST_API_KEY', 'private-key-sentinel')
    task = bridge.submit('P001', 'Fail safely')
    row = running('P001', task['id'])
    runtime[0].finish(row['thread_id'], status='failed', text='', error={'message': 'provider error private-key-sentinel'})
    result = finished('P001', task['id'])
    assert result['status'] == 'failed' and 'private-key-sentinel' not in json.dumps(bridge.run('P001', task['id']))
    next_task = bridge.submit('P001', 'Interrupted work')
    running('P001', next_task['id'])
    runtime[0].stop()
    assert finished('P001', next_task['id'])['status'] == 'interrupted'


def test_event_cursor_pagination_and_invalid_ids(workspace, monkeypatch):
    monkeypatch.setattr(bridge.Manager, 'launch', lambda *args: None)
    task = bridge.submit('P001', 'Saved events')
    path = bridge._events_path('P001', task['id'])
    path.write_text('not JSON\n' + ''.join(json.dumps({'seq': index, 'type': 'tool', 'text': 'step', 'at': core.now()}) + '\n' for index in range(1, 701)))
    bridge._change('P001', task['id'], last_seq=700)
    first = bridge.run('P001', task['id'], after=0)
    second = bridge.run('P001', task['id'], after=first['last_seq'])
    assert len(first['events']) == 500 and len(second['events']) == 200
    assert first['last_seq'] == 500 and second['last_seq'] == 700
    with pytest.raises(bridge.BridgeError): bridge.run('P001', task['id'], after=-1)
    for pid, rid in [('../Runtime', task['id']), ('P001', '../../private'), ('P999', task['id'])]:
        with pytest.raises(core.WorkflowError): bridge.run(pid, rid)


def test_project_creation_is_real_and_idempotent(workspace):
    identity = str(uuid.uuid4())
    project = bridge.create_project('真实中文作品', brief='桌面挂钩', request_id=identity, owner='browser')
    assert project['id'] == 'P003' and '桌面挂钩' in project['brief']
    assert bridge.create_project('真实中文作品', brief='桌面挂钩', request_id=identity, owner='browser')['id'] == 'P003'
    assert len(core.list_projects()) == 3
    with pytest.raises(bridge.BridgeError): bridge.create_project('另一件作品', request_id=identity, owner='browser')
    with pytest.raises(bridge.BridgeError): bridge.create_project('  ')


@pytest.mark.parametrize('message,model,request_id', [('', None, None), ('x' * 24001, None, None), ('Hello', '../bad model', None), ('Hello', None, 'bad')])
def test_invalid_submit_never_launches(workspace, message, model, request_id):
    with pytest.raises(bridge.BridgeError): bridge.submit('P001', message, model=model, request_id=request_id)
    assert bridge.conversation('P001')['messages'] == []


def test_models_are_read_from_runtime_and_missing_binary_is_clear(workspace, runtime, monkeypatch):
    assert bridge.models() == {'models': [{'id': 'configured-model', 'name': 'Configured model', 'default': True}]}
    monkeypatch.setenv('PRINTLAB_CODEX_BINARY', '/does/not/exist/codex')
    assert bridge.capability()['available'] is False


def test_worker_launch_failure_does_not_leave_project_permanently_busy(workspace, monkeypatch):
    def fail(*args): raise RuntimeError('thread not available')
    monkeypatch.setattr(bridge.Manager, 'launch', fail)
    task = bridge.submit('P001', 'Try to start')
    assert task['status'] == 'failed'
    assert bridge.conversation('P001')['active_run'] is None


def test_unexpected_host_error_stops_active_tools_before_releasing_project(workspace, runtime, monkeypatch):
    monkeypatch.setenv('PRINTLAB_AGENT_TIMEOUT', 'not-a-duration')
    task = bridge.submit('P001', 'Start and encounter invalid host configuration')
    result = finished('P001', task['id'])
    assert result['status'] == 'failed' and runtime[0].closed is True
    assert bridge.conversation('P001')['active_run'] is None


def test_broken_native_binary_is_reported_without_raw_process_error(workspace, monkeypatch):
    def fail(*args): raise FileNotFoundError('private binary installation path')
    monkeypatch.setattr(bridge, 'AppServer', fail)
    task = bridge.submit('P001', 'Actual runtime check')
    row = finished('P001', task['id'])
    assert row['status'] == 'failed' and '无法启动 Codex' in row['error']
    assert 'private binary installation path' not in row['error']


def test_queued_provider_binding_and_model_survive_settings_edits(workspace, runtime, provider_registry, monkeypatch):
    scheduled = []
    original = bridge.Manager.launch
    monkeypatch.setattr(bridge.Manager, 'launch', lambda self, pid, rid: scheduled.append((self, pid, rid)))
    task = bridge.submit('P001', 'Build with frozen settings')
    saved = bridge._read_run('P001', task['id'])
    assert (saved['provider_id'], saved['provider_revision'], saved['selected_model']) == ('alpha', 1, 'alpha-model')
    assert saved['model'] is None and saved['requested_provider_id'] is None
    provider_registry.snapshots[('alpha', 2)] = {**provider_registry.snapshots[('alpha', 1)],
        'revision': 2, 'model': 'changed-default', 'api_key': 'replacement-private-sentinel',
        'home': workspace / 'Runtime/codex-providers/alpha/2'}
    provider_registry.current['alpha'] = 2
    monkeypatch.setattr(bridge.Manager, 'launch', original)
    manager, pid, rid = scheduled[0]
    manager.launch(pid, rid)
    row = running(pid, rid)
    assert runtime[0].spec['revision'] == 1
    assert runtime[0].spec['api_key'] == 'alpha-nonstandard-private-sentinel'
    assert next(params for method, params in runtime[0].calls if method == 'thread/start')['model'] == 'alpha-model'
    runtime[0].finish(row['thread_id'])
    finished(pid, rid)


def test_omitted_provider_and_model_idempotency_ignores_new_defaults(workspace, provider_registry, monkeypatch):
    monkeypatch.setattr(bridge.Manager, 'launch', lambda *args: None)
    request_id = str(uuid.uuid4())
    first = bridge.submit('P001', 'One saved request', request_id=request_id)
    provider_registry.default[0] = 'beta'
    duplicate = bridge.submit('P001', 'One saved request', request_id=request_id)
    assert duplicate['id'] == first['id'] and duplicate['provider_id'] == 'alpha'
    with pytest.raises(bridge.BridgeError):
        bridge.submit('P001', 'One saved request', provider_id='beta', request_id=request_id)


def test_resume_uses_original_provider_revision_after_default_changes(workspace, runtime, provider_registry):
    task = bridge.submit('P001', 'First provider work', provider_id='alpha')
    first = running('P001', task['id'])
    runtime[0].finish(first['thread_id'])
    finished('P001', task['id'])
    provider_registry.default[0] = 'beta'
    resumed = bridge.resume('P001', task['id'])
    row = running('P001', resumed['id'])
    assert (row['provider_id'], row['provider_revision'], row['selected_model']) == ('alpha', 1, 'alpha-model')
    assert row['thread_id'] == first['thread_id'] and len(runtime) == 1
    runtime[0].finish(row['thread_id'])
    finished('P001', row['id'])


def test_resume_retry_returns_saved_request_before_resolving_settings(workspace, runtime, provider_registry, monkeypatch):
    task = bridge.submit('P001', 'First provider work', provider_id='alpha')
    first = running('P001', task['id'])
    runtime[0].finish(first['thread_id'])
    finished('P001', task['id'])
    monkeypatch.setattr(bridge.Manager, 'launch', lambda *args: None)
    request_id = str(uuid.uuid4())
    resumed = bridge.resume('P001', task['id'], request_id=request_id)
    def unavailable(*args, **kwargs):
        raise core.WorkflowError('Settings temporarily unavailable')
    monkeypatch.setattr(provider_registry.store, 'resolve', unavailable)
    assert bridge.resume('P001', task['id'], request_id=request_id)['id'] == resumed['id']


def test_resume_request_id_cannot_be_reused_for_another_original_run(workspace, runtime, provider_registry, monkeypatch):
    originals = []
    for provider, instance in [('alpha', 0), ('beta', 1)]:
        provider_registry.default[0] = provider
        task = bridge.submit('P001', 'Work under defaults')
        row = running('P001', task['id'])
        runtime[instance].finish(row['thread_id'])
        finished('P001', row['id'])
        originals.append(row['id'])
    monkeypatch.setattr(bridge.Manager, 'launch', lambda *args: None)
    request_id = str(uuid.uuid4())
    resumed = bridge.resume('P001', originals[0], request_id=request_id)
    with pytest.raises(bridge.BridgeError):
        bridge.resume('P001', originals[1], request_id=request_id)
    assert bridge.resume('P001', originals[0], request_id=request_id)['id'] == resumed['id']


def test_pre_provider_run_resumes_legacy_after_custom_becomes_default(workspace, runtime, provider_registry):
    task = bridge.submit('P001', 'Existing legacy task', provider_id='legacy')
    first = running('P001', task['id'])
    runtime[0].finish(first['thread_id'])
    old = finished('P001', task['id'])
    for key in ('provider_id', 'provider_revision', 'provider_name', 'selected_model', 'requested_provider_id'):
        old.pop(key, None)
    core.write_json(bridge._run_path('P001', task['id']), old)
    resumed = bridge.resume('P001', task['id'])
    second = running('P001', resumed['id'])
    assert (second['provider_id'], second['provider_revision']) == ('legacy', 0)
    assert second['thread_id'] == first['thread_id'] and len(runtime) == 1
    runtime[0].finish(second['thread_id'])
    finished('P001', second['id'])


def test_new_revision_uses_new_runtime_without_stopping_active_old_revision(workspace, runtime, provider_registry):
    first = bridge.submit('P001', 'Old settings', provider_id='alpha')
    a = running('P001', first['id'])
    provider_registry.snapshots[('alpha', 2)] = {**provider_registry.snapshots[('alpha', 1)],
        'revision': 2, 'api_key': 'new-revision-key-sentinel', 'home': workspace / 'Runtime/codex-providers/alpha/2'}
    provider_registry.current['alpha'] = 2
    second = bridge.submit('P002', 'New settings', provider_id='alpha')
    b = running('P002', second['id'])
    assert [server.spec['revision'] for server in runtime] == [1, 2]
    assert bridge.run('P001', a['id'])['status'] == 'running' and not runtime[0].closed
    runtime[1].stop()
    assert finished('P002', b['id'])['status'] == 'interrupted'
    assert bridge.run('P001', a['id'])['status'] == 'running'
    runtime[0].finish(a['thread_id'])
    finished('P001', a['id'])


def test_late_usage_with_matching_native_ids_keeps_original_provider(workspace, runtime, provider_registry, monkeypatch):
    original = bridge.AppServer.request
    def same_turn(self, method, params, timeout=60):
        if method == 'turn/start':
            return {'turn': {'id': 'same-turn', 'status': 'inProgress'}}
        return original(self, method, params, timeout)
    monkeypatch.setattr(bridge.AppServer, 'request', same_turn)
    first = bridge.submit('P001', 'First provider', provider_id='alpha')
    a = running('P001', first['id'])
    emit_response(runtime[0], a, usage=response_usage())
    runtime[0].finish(a['thread_id'])
    finished('P001', a['id'])
    second = bridge.submit('P001', 'Second provider', provider_id='beta')
    b = running('P001', second['id'])
    assert (a['thread_id'], a['turn_id']) == (b['thread_id'], b['turn_id'])
    emit_response(runtime[1], b, usage=response_usage(input_tokens=180))
    emit_response(runtime[0], a, response_id='late', usage=response_usage(input_tokens=30))
    assert bridge.run('P001', a['id'])['usage']['total_tokens'] == 150
    assert bridge.run('P001', b['id'])['usage']['total_tokens'] == 200
    runtime[1].finish(b['thread_id'])
    finished('P001', b['id'])


def test_provider_sessions_return_to_original_thread_without_overwriting_legacy(workspace, runtime, provider_registry):
    core.write_json(bridge._state_path('P001'), {'project_id': 'P001', 'thread_id': 'legacy-thread', 'old': 'preserved'})
    for provider, instance in [('alpha', 0), ('beta', 1), ('alpha', 0)]:
        task = bridge.submit('P001', provider + ' request', provider_id=provider)
        row = running('P001', task['id'])
        runtime[instance].finish(row['thread_id'])
        finished('P001', row['id'])
    assert len(runtime) == 2
    assert sum(method == 'thread/start' for method, _ in runtime[0].calls) == 1
    assert sum(method == 'thread/resume' for method, _ in runtime[0].calls) == 1
    state = core.read_json(bridge._state_path('P001'))
    assert state['thread_id'] == 'legacy-thread' and state['old'] == 'preserved'
    assert len(state['sessions']) == 2
    conversation = bridge.conversation('P001')
    assert len(conversation['messages']) == 6
    assert conversation['selection'] == {'provider_id': 'alpha', 'model': 'alpha-model'}


def test_runtime_binding_isolates_matching_native_ids_questions_and_cancellation(workspace, runtime, provider_registry):
    first = bridge.submit('P001', 'First service', provider_id='alpha')
    a = running('P001', first['id'])
    second = bridge.submit('P002', 'Second service', provider_id='beta')
    b = running('P002', second['id'])
    assert a['thread_id'] == b['thread_id']
    for server, row in zip(runtime, (a, b)):
        server.on_message({'id': 77, 'method': 'item/tool/requestUserInput', 'params': {
            'threadId': row['thread_id'], 'questions': [{'id': 'width', 'question': 'Width?'}]}})
    bridge.reply('P001', a['id'], message='20mm')
    assert runtime[0].responses == [(77, {'answers': {'width': {'answers': ['20mm']}}})]
    assert runtime[1].responses == []
    assert bridge.run('P002', b['id'])['status'] == 'awaiting_input'
    bridge.reply('P002', b['id'], message='40mm')
    assert runtime[1].responses == [(77, {'answers': {'width': {'answers': ['40mm']}}})]
    bridge.cancel('P001', a['id'])
    assert finished('P001', a['id'])['status'] == 'cancelled'
    assert bridge.run('P002', b['id'])['status'] == 'running'
    runtime[1].finish(b['thread_id'])
    finished('P002', b['id'])


def test_pending_reply_uses_live_frozen_manager_without_rereading_settings(workspace, runtime, provider_registry, monkeypatch):
    task = bridge.submit('P001', 'Ask a dimension', provider_id='alpha')
    row = running('P001', task['id'])
    runtime[0].on_message({'id': 77, 'method': 'item/tool/requestUserInput', 'params': {
        'threadId': row['thread_id'], 'questions': [{'id': 'width', 'question': 'Width?'}]}})
    def unavailable(*args, **kwargs):
        raise core.WorkflowError('Settings temporarily unavailable')
    monkeypatch.setattr(provider_registry.store, 'resolve', unavailable)
    bridge.reply('P001', row['id'], message='20mm')
    assert runtime[0].responses == [(77, {'answers': {'width': {'answers': ['20mm']}}})]
    runtime[0].finish(row['thread_id'])
    finished('P001', row['id'])


def test_custom_models_do_not_start_a_runtime(workspace, runtime, provider_registry):
    assert bridge.models(provider_id='beta') == {'models': [
        {'id': 'beta-model', 'name': 'beta-model', 'default': True},
        {'id': 'beta-model-small', 'name': 'beta-model-small', 'default': False}]}
    assert runtime == []


def test_provider_model_identifiers_are_accepted_by_turn_and_usage(workspace, runtime, provider_registry, monkeypatch):
    model = 'vendor/model@2026+fast'
    provider_registry.snapshots[('alpha', 1)]['models'].append(model)
    server = bridge._manager('alpha', 1)._runtime()
    request = server.request
    def with_model(method, params, timeout=60):
        result = request(method, params, timeout)
        if method == 'thread/start':
            result['model'] = model
        return result
    monkeypatch.setattr(server, 'request', with_model)
    task = bridge.submit('P001', 'Build a model', provider_id='alpha', model=model)
    row = running('P001', task['id'])
    assert row['selected_model'] == row['effective_model'] == model
    assert next(params for method, params in server.calls if method == 'thread/start')['model'] == model
    emit_response(server, row, usage=response_usage())
    assert bridge.run('P001', task['id'])['usage']['models'][0]['model'] == model
    server.finish(row['thread_id'])
    finished('P001', row['id'])


def test_private_provider_key_is_redacted_without_global_environment(workspace, runtime, provider_registry):
    task = bridge.submit('P001', 'Handle provider errors', provider_id='alpha')
    row = running('P001', task['id'])
    secret = 'alpha-nonstandard-private-sentinel'
    assert secret not in os.environ.values()
    runtime[0].finish(row['thread_id'], status='failed', text='', error={'message': 'Gateway failed ' + secret})
    finished('P001', row['id'])
    assert secret not in json.dumps(bridge.run('P001', row['id']))


def test_provider_key_is_redacted_before_output_is_truncated(workspace, provider_registry):
    secret = 'alpha-nonstandard-private-sentinel'
    result = bridge._safe_text('prefix-' + secret + '-end', maximum=14)
    assert result == 'prefix-[redact'


def test_active_key_suffix_is_not_exposed_when_registry_falls_back_to_shorter_key(workspace, monkeypatch):
    monkeypatch.setattr(bridge.model_providers, 'secret_values', lambda: ('provider-private-key',))
    monkeypatch.setattr(bridge, '_runtime_secrets', {'provider-private-key-rotated'})
    assert bridge._safe_text('Gateway failed provider-private-key-rotated') == 'Gateway failed [redacted]'


class LinePipe:
    def __init__(self): self.lines = queue.Queue()
    def push(self, value): self.lines.put(value)
    def readline(self, size=-1): return self.lines.get(timeout=3)
    def __iter__(self):
        while True:
            line = self.readline()
            if not line: return
            yield line


@pytest.mark.parametrize('custom', [False, True, 'managed-legacy'])
def test_app_server_uses_fixed_stdio_process_and_json_rpc(workspace, monkeypatch, provider_registry, custom):
    created, sent, signals, notifications = [], [], [], []
    class Process:
        pid = 987654321
        def __init__(self, args, **kwargs):
            self.stdout, self.stderr, self.returncode = LinePipe(), LinePipe(), None
            created.append((args, kwargs))
            self.stdin = self
        def write(self, line):
            message = json.loads(line); sent.append(message)
            if message.get('method') == 'initialize':
                self.stdout.push(json.dumps({'id': message['id'], 'result': {'serverInfo': 'mock'}}) + '\n')
            elif message.get('method') == 'model/list':
                self.stdout.push(json.dumps({'id': message['id'], 'result': {'data': []}}) + '\n')
            elif message.get('method') == 'bad/request':
                self.stdout.push(json.dumps({'id': message['id'], 'error': {'message': 'Bearer private-token-sentinel'}}) + '\n')
        def flush(self): pass
        def poll(self): return self.returncode
        def close(self):
            self.returncode = 0; self.stdout.push(''); self.stderr.push('')
        def wait(self, timeout=None): return self.returncode
    monkeypatch.setattr(bridge, '_binary', lambda: '/mock/codex')
    monkeypatch.setattr(bridge.subprocess, 'Popen', Process)
    monkeypatch.setattr(bridge.os, 'killpg', lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setenv('CODEX_API_KEY', 'legacy-key-sentinel')
    monkeypatch.setenv('OPENAI_API_KEY', 'another-key-sentinel')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://other-provider.example/v1')
    monkeypatch.setenv('ARBITRARY_AUTH', 'beta-nonstandard-private-sentinel')
    spec = provider_registry.store.resolve('alpha') if custom else None
    if custom == 'managed-legacy':
        spec.update(legacy=True, managed=True, id='legacy', revision=0, home=workspace / 'Runtime/codex')
    server = bridge.AppServer(notifications.append, lambda: None, spec=spec)
    assert server.request('model/list', {}) == {'data': []}
    with pytest.raises(bridge.BridgeError) as error: server.request('bad/request', {})
    assert 'private-token-sentinel' not in str(error.value)
    server.process.stdout.push(json.dumps({'id': 55, 'method': 'item/tool/requestUserInput', 'params': {'threadId': 'thread'}}) + '\n')
    wait_for(lambda: notifications)
    server.respond(55, {'answers': {}})
    server.stop()
    args, options = created[0]
    assert args == ['/mock/codex', 'app-server'] and 'shell' not in options
    assert options['cwd'] == str(workspace)
    if custom:
        expected_home = 'Runtime/codex' if custom == 'managed-legacy' else 'Runtime/codex-providers/alpha/1'
        assert options['env']['CODEX_HOME'].endswith(expected_home)
        assert options['env']['PRINTLAB_PROVIDER_API_KEY'] == 'alpha-nonstandard-private-sentinel'
        assert not {'CODEX_API_KEY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'ARBITRARY_AUTH'} & options['env'].keys()
    else:
        assert options['env']['CODEX_HOME'].endswith('Runtime/codex')
        assert options['env']['CODEX_API_KEY'] == 'legacy-key-sentinel'
    assert options['start_new_session'] is True
    assert [message.get('method') for message in sent[:2]] == ['initialize', 'initialized']
    assert sent[-1] == {'id': 55, 'result': {'answers': {}}}
    assert signals
