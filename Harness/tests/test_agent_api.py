import uuid
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from printlab import agent_bridge, core, project_files, project_images
from printlab.api import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    home = tmp_path / 'Runtime/codex'
    monkeypatch.setenv('CODEX_HOME', str(home))
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'PRINTLAB_PROVIDER_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    home.mkdir(parents=True)
    (home / 'config.toml').write_text('model = "configured-model"\n', encoding='utf-8')
    core.write_json(tmp_path / 'Runtime/config.json', {})
    core.new_project('Web conversation')
    monkeypatch.setattr(agent_bridge.Manager, 'launch', lambda *args: None)
    yield TestClient(app, base_url='http://127.0.0.1:8018')
    agent_bridge.shutdown()


def headers(client):
    token = client.get('/api/prepared/session').json()['csrf_token']
    return {'Origin': 'http://127.0.0.1:8018', 'X-PrintLab-CSRF': token}


def test_project_creation_and_message_need_origin_cookie_and_csrf(client):
    assert client.post('/api/projects', json={'name': 'New project'}).status_code == 403
    h = headers(client)
    assert client.post('/api/projects', headers={**h, 'Origin': 'http://evil.example'}, json={'name': 'New project'}).status_code == 403
    assert client.post('/api/projects', headers={**h, 'X-PrintLab-CSRF': 'wrong'}, json={'name': 'New project'}).status_code == 403
    identity = str(uuid.uuid4())
    result = client.post('/api/projects', headers=h, json={'name': '新作品', 'brief': '桌边挂钩', 'request_id': identity})
    assert result.status_code == 201 and result.json()['id'] == 'P002'
    duplicate = client.post('/api/projects', headers=h, json={'name': '新作品', 'brief': '桌边挂钩', 'request_id': identity})
    assert duplicate.json()['id'] == 'P002' and len(core.list_projects()) == 2
    url = '/api/projects/P002/agent/runs'
    assert client.post(url, json={'message': 'Create a model'}).status_code == 403
    assert client.get('/api/projects/P002/agent').status_code == 200
    request = {'message': '创建真实模型', 'request_id': str(uuid.uuid4())}
    run = client.post(url, headers=h, json=request)
    assert run.status_code == 202 and run.json()['project_id'] == 'P002'
    assert client.post(url, headers=h, json=request).json()['id'] == run.json()['id']
    assert client.get('/api/projects/P002/agent').json()['messages'][0]['content'] == '创建真实模型'


@pytest.mark.parametrize('payload', [
    {'message': 'Hello', 'cwd': '/etc'}, {'message': 'Hello', 'executable': 'sh'},
    {'message': 'Hello', 'env': {'KEY': 'secret'}}, {'message': ''},
    {'message': 42}, {'message': 'Hello', 'request_id': 'bad'},
])
def test_browser_cannot_choose_process_or_bad_payload(client, payload):
    response = client.post('/api/projects/P001/agent/runs', headers=headers(client), json=payload)
    assert response.status_code == 422
    assert agent_bridge.conversation('P001')['messages'] == []


def test_cancel_events_resume_and_project_isolation(client):
    h = headers(client)
    task = client.post('/api/projects/P001/agent/runs', headers=h, json={'message': 'Prepare files'}).json()
    url = '/api/projects/P001/agent/runs/' + task['id']
    assert client.get(url).json()['events'][0]['seq'] == 1
    assert client.get(url + '?after=1').json()['events'] == []
    assert client.get(url + '?after=-1').status_code == 422
    assert client.post(url + '/cancel', json={}).status_code == 403
    assert client.post(url + '/cancel', headers=h, json={}).json()['status'] == 'cancelled'
    resumed = client.post(url + '/resume', headers=h, json={'message': 'Continue after inspecting files'})
    assert resumed.status_code == 202 and resumed.json()['id'] != task['id']
    core.new_project('Other project')
    assert client.get('/api/projects/P002/agent/runs/' + task['id']).status_code == 409
    assert client.post(url + '/reply', headers=h, json={'decision': 'acceptForSession'}).status_code == 422


def test_reads_require_session_and_models_are_server_controlled(client, monkeypatch):
    assert client.get('/api/projects/P001/agent').status_code == 403
    headers(client)
    monkeypatch.setattr(agent_bridge, 'models', lambda provider_id=None: {'models': [{'id': 'configured', 'name': 'Configured'}]})
    assert client.get('/api/agent/models').json()['models'][0]['id'] == 'configured'
    response = client.get('/api/projects/P001/agent')
    assert response.headers['cache-control'] == 'no-store'
    assert 'owner' not in response.text and 'request_id' not in response.text


def test_provider_selection_is_persisted_and_models_are_scoped(client, monkeypatch):
    spec = {'id': 'custom', 'revision': 3, 'name': 'Custom', 'model': 'third-party-model', 'legacy': False}
    monkeypatch.setattr(agent_bridge.model_providers, 'resolve', lambda *args, **kwargs: spec)
    monkeypatch.setattr(agent_bridge.model_providers, 'settings', lambda: {'providers': [
        {'id': 'custom', 'models': ['third-party-model']}]})
    response = client.get('/api/agent/models?provider_id=custom')
    assert response.status_code == 403
    h = headers(client)
    assert client.get('/api/agent/models?provider_id=custom').json() == {'models': [
        {'id': 'third-party-model', 'name': 'third-party-model', 'default': True}]}
    response = client.post('/api/projects/P001/agent/runs', headers=h,
        json={'message': 'Build from this service', 'provider_id': 'custom'})
    assert response.status_code == 202
    assert response.json()['provider_id'] == 'custom' and response.json()['provider_revision'] == 3
    conversation = client.get('/api/projects/P001/agent').json()
    assert conversation['selection'] == {'provider_id': 'custom', 'model': 'third-party-model'}


@pytest.mark.parametrize('provider_id', ['../private', '', 3, 'a' * 65])
def test_invalid_provider_selection_never_creates_task(client, provider_id):
    response = client.post('/api/projects/P001/agent/runs', headers=headers(client),
        json={'message': 'Build', 'provider_id': provider_id})
    assert response.status_code == 422
    assert agent_bridge.conversation('P001')['messages'] == []


def test_unconfigured_model_service_does_not_hide_project_conversation(client, monkeypatch):
    def missing(*args, **kwargs):
        raise core.WorkflowError('请先在设置中配置模型服务。')
    monkeypatch.setattr(agent_bridge.model_providers, 'resolve', missing)
    h = headers(client)
    conversation = client.get('/api/projects/P001/agent')
    assert conversation.status_code == 200
    assert conversation.json()['selection'] == {'provider_id': None, 'model': None}
    result = client.post('/api/projects/P001/agent/runs', headers=h, json={'message': 'Build'})
    assert result.status_code == 409 and '配置模型服务' in result.json()['detail']


def image_bytes():
    data = io.BytesIO()
    Image.new('RGB', (20, 12), 'green').save(data, format='PNG')
    return data.getvalue()


def test_image_upload_requires_session_origin_csrf_and_returns_private_project_asset(client):
    url = '/api/projects/P001/images?filename=reference.png'
    assert client.post(url, content=image_bytes()).status_code == 403
    h = headers(client)
    assert client.post(url, headers={**h, 'Origin':'http://evil.example'}, content=image_bytes()).status_code == 403
    assert client.post(url, headers={**h, 'X-PrintLab-CSRF':'wrong'}, content=image_bytes()).status_code == 403
    response = client.post(url, headers=h, content=image_bytes())
    assert response.status_code == 201
    image = response.json()
    assert set(image) == set(project_images.PUBLIC_FIELDS)
    preview = client.get(image['url'])
    assert preview.status_code == 200 and preview.headers['content-type'] == 'image/png'
    assert preview.headers['cache-control'] == 'no-store'
    assert 'Runtime' not in response.text and 'Sources' not in response.text
    assert TestClient(app, base_url='http://127.0.0.1:8018').get(image['url']).status_code == 403
    core.new_project('Another image project')
    assert client.get(image['url'].replace('P001', 'P002')).status_code == 404


@pytest.mark.parametrize('filename,data', [('../outside.png', b'image'), ('bad.png', b'not PNG')])
def test_invalid_image_never_becomes_reference(client, filename, data):
    response = client.post('/api/projects/P001/images', params={'filename':filename}, headers=headers(client), content=data)
    assert response.status_code == 400


def test_image_stream_and_declared_size_are_both_limited(client, monkeypatch):
    h = headers(client)
    monkeypatch.setattr(project_images, 'MAX_BYTES', 128)
    path = '/api/projects/P001/images'
    assert client.post(path, headers={**h, 'Content-Length':'129'}, content=b'x').status_code == 413
    assert client.post(path, headers={**h, 'Content-Length':'0'}, content=b'x' * 129).status_code == 413
    assert client.post(path, headers={**h, 'Content-Length':'-1'}, content=b'x').status_code == 400
    assert client.post(path, headers={**h, 'Content-Length':'invalid'}, content=b'x').status_code == 400


def test_picture_only_turn_is_persisted_and_resumable_without_client_file_paths(client):
    h = headers(client)
    image = client.post('/api/projects/P001/images?filename=reference.png', headers=h, content=image_bytes()).json()
    task = client.post('/api/projects/P001/agent/runs', headers=h, json={'attachment_ids':[image['id']]} )
    assert task.status_code == 202 and task.json()['attachments'] == [image]
    message = client.get('/api/projects/P001/agent').json()['messages'][0]
    assert message['attachments'] == [image] and '参考图' in message['content']
    url = '/api/projects/P001/agent/runs/' + task.json()['id']
    client.post(url + '/cancel', headers=h, json={})
    resumed = client.post(url + '/resume', headers=h, json={'attachment_ids':[image['id']]})
    assert resumed.status_code == 202 and resumed.json()['attachments'] == [image]


@pytest.mark.parametrize('ids', [['../outside.png'], ['http://evil.example/x.png']])
def test_client_cannot_send_attachment_paths(client, ids):
    response = client.post('/api/projects/P001/agent/runs', headers=headers(client), json={'message':'Review', 'attachment_ids':ids})
    assert response.status_code == 422


@pytest.mark.parametrize('count', [7, 12])
def test_submit_and_resume_accept_more_than_six_uploaded_images(client, count):
    h = headers(client)
    uploaded = [client.post('/api/projects/P001/images', headers=h, content=image_bytes()).json() for _ in range(count)]
    payload = {'message':'Use all uploaded reference images', 'attachment_ids':[image['id'] for image in uploaded]}
    task = client.post('/api/projects/P001/agent/runs', headers=h, json=payload)
    assert task.status_code == 202 and task.json()['attachments'] == uploaded
    url = '/api/projects/P001/agent/runs/' + task.json()['id']
    assert client.post(url + '/cancel', headers=h, json={}).status_code == 200
    resumed = client.post(url + '/resume', headers=h, json=payload)
    assert resumed.status_code == 202 and resumed.json()['attachments'] == uploaded
    messages = client.get('/api/projects/P001/agent').json()['messages']
    assert messages[0]['attachments'] == uploaded and messages[1]['attachments'] == uploaded


def test_duplicate_image_ids_remain_rejected_on_submit_and_resume(client):
    h = headers(client)
    image = client.post('/api/projects/P001/images', headers=h, content=image_bytes()).json()
    invalid = {'message':'Review', 'attachment_ids':[image['id'], image['id']]}
    assert client.post('/api/projects/P001/agent/runs', headers=h, json=invalid).status_code == 409
    task = client.post('/api/projects/P001/agent/runs', headers=h, json={'message':'Review'}).json()
    url = '/api/projects/P001/agent/runs/' + task['id']
    client.post(url + '/cancel', headers=h, json={})
    assert client.post(url + '/resume', headers=h, json=invalid).status_code == 409


def test_missing_image_and_cross_project_reference_never_start_a_task(client):
    h = headers(client)
    image = client.post('/api/projects/P001/images', headers=h, content=image_bytes()).json()
    core.new_project('Other image project')
    for pid, identity in [('P001', 'a' * 32), ('P002', image['id'])]:
        response = client.post(f'/api/projects/{pid}/agent/runs', headers=h, json={'message':'Review', 'attachment_ids':[identity]})
        assert response.status_code == 409
        assert agent_bridge.conversation(pid)['messages'] == []


def test_file_upload_auth_and_forced_download_in_both_routes(client):
    url = '/api/projects/P001/files'
    params = {'filename':'参考说明.HTML'}
    content = b'<script>must remain inert</script>'
    assert client.post(url, params=params, content=content).status_code == 403
    h = headers(client)
    for bad in [{'Origin':'http://evil.example'}, {'X-PrintLab-CSRF':'wrong'}]:
        assert client.post(url, params=params, headers={**h, **bad}, content=content).status_code == 403
    response = client.post(url, params=params, headers=h, content=content)
    assert response.status_code == 201
    metadata = response.json()
    assert set(metadata) == set(project_files.PUBLIC_FIELDS)
    assert metadata['name'] == params['filename']
    _, stored = project_files.resolve_file('P001', metadata['id'])
    raw_url = '/api/files/P001/' + stored.relative_to(core.project_path('P001')).as_posix()
    for route in [metadata['url'], raw_url]:
        download = client.get(route)
        assert download.status_code == 200 and download.content == content
        assert download.headers['content-type'] == 'application/octet-stream'
        assert download.headers['content-disposition'].startswith("attachment; filename*=utf-8''")
        assert 'HTML' in download.headers['content-disposition']
        assert download.headers['x-content-type-options'] == 'nosniff'
        assert download.headers['cache-control'] == 'no-store'
        assert "sandbox" in download.headers['content-security-policy']
        assert TestClient(app, base_url='http://127.0.0.1:8018').get(route).status_code == 403
    manifest_url = '/api/files/P001/Sources/attachments/.metadata/' + metadata['id'] + '.json'
    assert client.get(manifest_url).status_code == 404
    core.new_project('Another files project')
    assert client.get(metadata['url'].replace('P001', 'P002')).status_code == 404


def test_generated_models_beside_extracted_attachment_keep_download_guards(client):
    h = headers(client)
    uploaded = client.post('/api/projects/P001/files?filename=source.zip', headers=h, content=b'archive bytes').json()
    root = core.project_path('P001') / project_files.DIRECTORY
    generated = root / uploaded['id'] / 'parts' / 'model.glb'
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b'generated model bytes')
    route = '/api/files/P001/' + generated.relative_to(core.project_path('P001')).as_posix()
    response = client.get(route)
    assert response.status_code == 200 and response.content == generated.read_bytes()
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'sandbox' in response.headers['content-security-policy']
    assert TestClient(app, base_url='http://127.0.0.1:8018').get(route).status_code == 403
    private = generated.parent / '.metadata' / 'secret.json'
    private.parent.mkdir()
    private.write_text('{"private":true}')
    assert client.get(route.replace('model.glb', '.metadata/secret.json')).status_code == 404
    _, original = project_files.resolve_file('P001', uploaded['id'])
    original.write_bytes(b'tampered archive')
    raw = '/api/files/P001/' + original.relative_to(core.project_path('P001')).as_posix()
    assert client.get(raw).status_code == 404
    generated.unlink()
    generated.symlink_to(original)
    assert client.get(route).status_code == 404


def test_file_stream_and_declared_limits_empty_bad_name_and_missing_project(client, monkeypatch):
    h = headers(client)
    monkeypatch.setattr(project_files, 'MAX_BYTES', 128)
    url = '/api/projects/P001/files?filename=part.stl'
    for length in ['-1', 'invalid']:
        assert client.post(url, headers={**h, 'Content-Length':length}, content=b'x').status_code == 400
    assert client.post(url, headers={**h, 'Content-Length':'129'}, content=b'x').status_code == 413
    assert client.post(url, headers={**h, 'Content-Length':'0'}, content=b'x' * 129).status_code == 413
    assert client.post(url, headers=h, content=b'').status_code == 400
    assert client.post(url, headers=h, content=b'x' * 128).status_code == 201
    assert client.post('/api/projects/P001/files?filename=../escape.stl', headers=h, content=b'x').status_code == 400
    assert client.post('/api/projects/P999/files?filename=part.stl', headers=h, content=b'x').status_code == 404
    directory = core.project_path('P001') / project_files.DIRECTORY
    assert len([path for path in directory.iterdir() if path.is_file()]) == 1
    assert list(directory.glob('.*.upload')) == []


def test_file_only_submit_resume_and_mixed_images_have_separate_metadata(client):
    h = headers(client)
    files = [client.post('/api/projects/P001/files', params={'filename':f'part{i}.stl'}, headers=h, content=b'solid part\nendsolid part').json() for i in range(9)]
    image = client.post('/api/projects/P001/images', headers=h, content=image_bytes()).json()
    payload = {'file_ids':[item['id'] for item in files], 'request_id':str(uuid.uuid4())}
    response = client.post('/api/projects/P001/agent/runs', headers=h, json=payload)
    assert response.status_code == 202
    task = response.json()
    assert task['files'] == files and task['attachments'] == []
    assert client.post('/api/projects/P001/agent/runs', headers=h, json=payload).json()['id'] == task['id']
    url = '/api/projects/P001/agent/runs/' + task['id']
    client.post(url + '/cancel', headers=h, json={})
    resumed = client.post(url + '/resume', headers=h, json={'file_ids':payload['file_ids'], 'attachment_ids':[image['id']]})
    assert resumed.status_code == 202 and resumed.json()['files'] == files and resumed.json()['attachments'] == [image]
    messages = client.get('/api/projects/P001/agent').json()['messages']
    assert all(row['files'] == files for row in messages)
    assert 'Sources/attachments' not in response.text


@pytest.mark.parametrize('file_ids,expected', [(['../private'],422), ([None],422), ('a' * 32,422), (['a' * 32],409), (['a' * 32] * 2,409)])
def test_bad_file_ids_cannot_start_task(client, file_ids, expected):
    response = client.post('/api/projects/P001/agent/runs', headers=headers(client), json={'file_ids':file_ids})
    assert response.status_code == expected
    assert agent_bridge.conversation('P001')['messages'] == []


def test_cross_project_file_cannot_be_submitted(client):
    h = headers(client)
    item = client.post('/api/projects/P001/files?filename=part.stl', headers=h, content=b'solid part').json()
    core.new_project('Other file project')
    assert client.post('/api/projects/P002/agent/runs', headers=h, json={'file_ids':[item['id']]}).status_code == 409


@pytest.mark.parametrize('failure', ['disconnect', 'cancel'])
def test_file_stream_disconnect_or_cancellation_removes_partial_bytes(client, monkeypatch, failure):
    import asyncio
    from starlette.requests import ClientDisconnect
    from printlab import agent_api
    monkeypatch.setattr(agent_api, '_session', lambda *args, **kwargs: None)
    class InterruptedRequest:
        headers = {}
        async def stream(self):
            yield b'partial upload'
            raise ClientDisconnect() if failure == 'disconnect' else asyncio.CancelledError()
    if failure == 'disconnect':
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as caught:
            asyncio.run(agent_api.upload_file('P001', InterruptedRequest(), 'part.stl'))
        assert caught.value.status_code == 400
    else:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(agent_api.upload_file('P001', InterruptedRequest(), 'part.stl'))
    directory = core.project_path('P001') / project_files.DIRECTORY
    assert list(directory.iterdir()) == []
