"""Studio server with same-origin, explicitly confirmed prepared-print controls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import asynccontextmanager, suppress
import asyncio
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from urllib.parse import quote

from . import __version__, bambuddy, core, shop, devices, project_files
from .prepared_api import router as prepared_router, _session
from .agent_api import router as agent_router
from .model_provider_api import router as model_provider_router
from .homeassistant_api import router as homeassistant_router

@asynccontextmanager
async def lifespan(app):
    from . import workbench, agent_bridge
    workbench.recover_operations()
    agent_bridge.recover()
    async def observe():
        while True:
            try:
                if devices.provider() == 'bambuddy':
                    await asyncio.to_thread(bambuddy.reconcile_active_jobs)
            except Exception:
                logging.getLogger("printlab").exception("Read-only reconciliation failed")
            await asyncio.sleep(30)
    task = asyncio.create_task(observe())
    async def sync_history():
        while True:
            try:
                await asyncio.to_thread(devices.history_view)
            except Exception:
                logging.getLogger('printlab').exception('Read-only history sync failed')
            await asyncio.sleep(60)
    history_task = asyncio.create_task(sync_history())
    try:
        yield
    finally:
        agent_bridge.shutdown()
        task.cancel()
        history_task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with suppress(asyncio.CancelledError):
            await history_task


app = FastAPI(title="PrintLab Studio", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)


@app.exception_handler(core.WorkflowError)
async def workflow_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    # Embedding in the local Bambuddy panel is intentional. Never allow scripts in user documents.
    private = request.url.path.startswith(("/api/prepared", "/api/ha/", "/api/agent", "/api/settings/")) or '/agent' in request.url.path or '/images' in request.url.path
    response.headers["Cache-Control"] = "no-store" if private else response.headers.get("Cache-Control", "no-cache")
    if request.url.path.startswith("/api/prepared"):
        response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "read_only": False,
            "device_provider": devices.provider(),
            "features": ["native-observation-v2", "material-slots-v2", "confirmation-card", "traceable-retry", "print-history-v1", "prepared-print-v1", "home-assistant-read-v1"]}


@app.get('/api/history')
def print_history(printer_id: str | None = None):
    return devices.history_view(printer_id)


@app.get('/api/auth/session')
def local_auth_session():
    # Authenticated deployments answer this in the outer login middleware.
    return {'enabled': False, 'authenticated': False}


@app.get("/api/projects")
def projects():
    return {"projects": core.list_projects()}


@app.get("/api/projects/{pid}")
def project(pid: str):
    return core.detail(pid)


class PrinterBinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    printer_id: str = Field(min_length=1, max_length=128, strict=True)


@app.post('/api/projects/{pid}/printer')
def bind_project_printer(pid: str, body: PrinterBinding, request: Request):
    _session(request, write=True)
    from . import printer_assignment
    try:
        return {'target_printer': printer_assignment.select(pid, body.printer_id)}
    except core.WorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get('/api/projects/{pid}/printer-options')
def project_printer_options(pid: str):
    from . import printer_assignment
    return printer_assignment.options(pid)


@app.get("/api/projects/{pid}/confirmation")
def confirmation_card(pid: str, job: str | None = None):
    from . import confirmation
    card = confirmation.card(pid, job)
    if card.get("job_id"):
        directory = core.job_path(pid, card["job_id"])
        card["validation_alerts"] = core.read_json(directory / "validation-alerts.json", [])
    return card


@app.get("/api/library")
def library():
    return core.library()


@app.get("/api/shop")
def shopping(project: str | None = None):
    return shop.view(project)


@app.get("/api/device")
def device(printer_id: str | None = None):
    return devices.device(printer_id)


@app.get('/api/devices')
def device_list():
    return devices.device_list()


def ha_asset_response(asset):
    filename = asset['filename']
    disposition = 'inline' if asset['content_type'].startswith('image/') else 'attachment'
    return Response(asset['content'], media_type=asset['content_type'], headers={
        'Content-Disposition': f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}",
        'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY',
    })


@app.get('/api/ha/files/{serial}/{relative:path}')
def ha_file(serial: str, relative: str):
    if devices.provider() != 'homeassistant':
        raise HTTPException(404, 'HA 文件连接尚未配置')
    from . import homeassistant
    return ha_asset_response(homeassistant.asset(serial, relative))


@app.get('/api/ha/camera/{printer_id}')
def ha_camera(printer_id: str):
    if devices.provider() != 'homeassistant':
        raise HTTPException(404, 'HA 摄像头连接尚未配置')
    from . import homeassistant
    return ha_asset_response(homeassistant.camera_image(printer_id))


@app.get("/api/overview")
def overview(printer_id: str | None = None):
    projects = core.list_projects()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(devices.device, printer_id), pool.submit(devices.inventory)
        device, inventory = a.result(), b.result()
    provider = devices.provider()
    listing = devices.device_list() if provider == 'homeassistant' else {'devices': []}
    selected = (device.get('printer') or {}).get('id')
    counts = {s: sum(p.get("status") == s for p in projects) for s in ("designing", "awaiting_review", "printing", "completed")}
    counts["total"] = len(projects)
    counts["active"] = sum(p.get("status") in {"preparing", "running", "printing", "paused", "dispatching", "submitted"} for p in projects)
    actions = [{"title": p["name"], "detail": "模型待确认" if p["status"] == "awaiting_review" else "打印结束，等待实物检查", "project_id": p["id"]} for p in projects if p.get("status") in {"awaiting_review", "awaiting_physical_review"}]
    if device["state"] != "online" or device.get("stale"):
        actions.append({"title": "设备连接待核验", "detail": device["message"]})
    return {"projects": projects, "counts": counts, "device": device, "inventory": inventory,
            "provider": provider, "devices": listing.get('devices', []),
            "selected_printer_id": selected, "management_url": devices.management_url(),
            "bambuddy_url": core.config().get("bambuddy_url", "http://127.0.0.1:8017"),
            "updated_at": core.now(), "library_count": len(core.library()["documents"]), "action_items": actions}


def artifact_response(path: Path):
    inline = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".glb": "model/gltf-binary", ".pdf": "application/pdf"}
    if path.suffix.lower() in inline:
        return FileResponse(path, media_type=inline[path.suffix.lower()])
    if path.suffix.lower() in {".md", ".txt", ".json", ".py", ".svg", ".html", ".js"}:
        return FileResponse(path, media_type="text/plain", headers={"Content-Security-Policy": "default-src 'none'; sandbox"})
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@app.get("/api/files/{pid}/{relative:path}")
def project_file(pid: str, relative: str, request: Request):
    path = core.public_project_file(pid, relative)
    directory = core.project_path(pid) / project_files.DIRECTORY
    if path.is_relative_to(directory):
        _session(request)
        # The artifact browser must not turn an uploaded HTML/PDF/SVG into an inline document.
        if any(part.startswith('.') for part in path.relative_to(directory).parts):
            raise HTTPException(404, '附件不存在。')
        # 原始上传仍校验清单与哈希，解包子目录中生成的文件沿用项目成果路径。
        name = path.name
        if path.parent == directory:
            metadata, verified = project_files.resolve_file(pid, path.stem)
            if verified != path:
                raise HTTPException(404, '附件不存在。')
            name = metadata['name']
        return FileResponse(path, media_type='application/octet-stream', filename=name,
                            headers=project_files.DOWNLOAD_HEADERS)
    return artifact_response(path)


@app.get("/api/documents/{relative:path}")
def document(relative: str):
    return artifact_response(core.public_document(relative))


app.include_router(prepared_router)
app.include_router(agent_router)
app.include_router(model_provider_router)
app.include_router(homeassistant_router)

# Mount last so a SPA route cannot shadow an API endpoint.
dist = getattr(core, 'SOURCE_ROOT', core.ROOT) / "Harness/studio/dist"
if dist.is_dir():
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        target = core.safe_path(dist, path or "index.html", require_file=False)
        if not target.is_file():
            target = dist / "index.html"
        return FileResponse(target)
