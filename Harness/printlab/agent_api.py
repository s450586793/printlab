"""Same-origin project conversation API; executable configuration is server-only."""
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from . import agent_bridge, project_files, project_images, project_lifecycle
from .prepared_api import _session

router = APIRouter(prefix='/api')


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120, strict=True)
    brief: str = Field(default='', max_length=24000, strict=True)
    kind: Literal['functional', 'sculpture'] = 'functional'
    printer_id: str | None = Field(default=None, max_length=128, strict=True)
    request_id: str | None = Field(default=None, min_length=32, max_length=36, strict=True)


class Submit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(default='', max_length=24000, strict=True)
    attachment_ids: list[Annotated[str, Field(pattern=r'^[a-f0-9]{32}$', strict=True)]] = Field(default_factory=list)
    file_ids: list[Annotated[str, Field(pattern=r'^[a-f0-9]{32}$', strict=True)]] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=128, strict=True)
    provider_id: str | None = Field(default=None, min_length=1, max_length=64,
                                    pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$', strict=True)
    resume: bool = False
    request_id: str | None = Field(default=None, min_length=32, max_length=36, strict=True)

    @model_validator(mode='after')
    def require_message_or_attachments(self):
        if not self.message.strip() and not self.attachment_ids and not self.file_ids:
            raise ValueError('请填写需求或上传附件。')
        return self


class Resume(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str | None = Field(default=None, max_length=24000, strict=True)
    request_id: str | None = Field(default=None, min_length=32, max_length=36, strict=True)
    attachment_ids: list[Annotated[str, Field(pattern=r'^[a-f0-9]{32}$', strict=True)]] = Field(default_factory=list)
    file_ids: list[Annotated[str, Field(pattern=r'^[a-f0-9]{32}$', strict=True)]] = Field(default_factory=list)


class Reply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str | None = Field(default=None, max_length=4000, strict=True)
    answers: dict | None = None
    decision: Literal['accept', 'decline', 'cancel'] | None = None


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except agent_bridge.BridgeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/projects', status_code=201)
def create_project(body: ProjectCreate, request: Request):
    owner = _session(request, write=True)
    return _call(agent_bridge.create_project, **body.model_dump(), owner=owner)


class ProjectDelete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(pattern=r'^P\d{3,}$', strict=True)


@router.delete('/projects/{pid}')
def delete_project(pid: str, body: ProjectDelete, request: Request):
    _session(request, write=True)
    if body.project_id != pid:
        raise HTTPException(409, '删除确认与项目编号不一致，请刷新后重试。')
    try:
        return project_lifecycle.delete_project(pid)
    except project_lifecycle.DeleteError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/projects/{pid}/images', status_code=201)
async def upload_image(pid: str, request: Request, filename: str = Query(default='参考图', min_length=1, max_length=200)):
    _session(request, write=True)
    try:
        length = int(request.headers.get('content-length', '0'))
        if length < 0:
            raise ValueError
    except ValueError:
        raise HTTPException(400, '图片上传信息无效。') from None
    if length > project_images.MAX_BYTES:
        raise HTTPException(413, '每张参考图不能超过 12 MB。')
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > project_images.MAX_BYTES:
            raise HTTPException(413, '每张参考图不能超过 12 MB。')
        content.extend(chunk)
    try:
        return await run_in_threadpool(project_images.save_image, pid, bytes(content), filename)
    except project_images.ImageError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get('/projects/{pid}/images/{image_id}')
def project_image(pid: str, image_id: str, request: Request):
    _session(request)
    try:
        metadata, path = project_images.resolve_image(pid, image_id)
    except project_images.ImageError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return FileResponse(path, media_type=metadata['content_type'],
                        headers={'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY'})


@router.post('/projects/{pid}/files', status_code=201)
async def upload_file(pid: str, request: Request, filename: str = Query(min_length=1, max_length=200)):
    _session(request, write=True)
    try:
        length = int(request.headers.get('content-length', '0'))
        if length < 0:
            raise ValueError
    except ValueError:
        raise HTTPException(400, '附件上传信息无效。') from None
    if length > project_files.MAX_BYTES:
        raise HTTPException(413, '每个附件不能超过 100 MB。')
    upload = None
    try:
        upload = await run_in_threadpool(project_files.Upload, pid, filename)
        async for chunk in request.stream():
            await run_in_threadpool(upload.write, chunk)
        return await run_in_threadpool(upload.finish)
    except project_files.FileError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except ClientDisconnect as exc:
        raise HTTPException(400, '附件上传中断，请重试。') from exc
    finally:
        if upload is not None:
            upload.abort()


@router.get('/projects/{pid}/files/{file_id}')
def project_file(pid: str, file_id: str, request: Request):
    _session(request)
    try:
        metadata, path = project_files.resolve_file(pid, file_id)
    except project_files.FileError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return FileResponse(path, media_type='application/octet-stream', filename=metadata['name'],
                        headers=project_files.DOWNLOAD_HEADERS)


@router.get('/agent/capability')
def capability(request: Request):
    _session(request)
    return agent_bridge.capability()


@router.get('/agent/models')
def models(request: Request, provider_id: str | None = Query(default=None, min_length=1, max_length=64,
                                                          pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')):
    _session(request)
    return _call(agent_bridge.models, provider_id=provider_id)


@router.get('/projects/{pid}/agent')
def conversation(pid: str, request: Request):
    _session(request)
    return _call(agent_bridge.conversation, pid)


@router.post('/projects/{pid}/agent/runs', status_code=202)
def submit(pid: str, body: Submit, request: Request):
    owner = _session(request, write=True)
    return _call(agent_bridge.submit, pid, **body.model_dump(), owner=owner)


@router.get('/projects/{pid}/agent/runs/{rid}')
def run(pid: str, rid: str, request: Request, after: int = Query(default=0, ge=0)):
    _session(request)
    return _call(agent_bridge.run, pid, rid, after=after)


@router.post('/projects/{pid}/agent/runs/{rid}/cancel')
def cancel(pid: str, rid: str, request: Request):
    _session(request, write=True)
    return _call(agent_bridge.cancel, pid, rid)


@router.post('/projects/{pid}/agent/runs/{rid}/resume', status_code=202)
def resume(pid: str, rid: str, body: Resume, request: Request):
    owner = _session(request, write=True)
    return _call(agent_bridge.resume, pid, rid, **body.model_dump(), owner=owner)


@router.post('/projects/{pid}/agent/runs/{rid}/reply')
def reply(pid: str, rid: str, body: Reply, request: Request):
    _session(request, write=True)
    return _call(agent_bridge.reply, pid, rid, **body.model_dump())
