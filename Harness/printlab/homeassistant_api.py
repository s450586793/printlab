"""Same-origin HA administration; credentials stay on the server."""
from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from . import homeassistant_settings
from .prepared_api import _session


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({'detail': 'Home Assistant 配置格式无效，请检查填写内容。'}, status_code=422)
            except homeassistant_settings.SettingsError as exc:
                response = JSONResponse({'detail': str(exc)}, status_code=exc.status_code)
            except HTTPException as exc:
                response = JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response

        return handler


router = APIRouter(prefix='/api/settings/home-assistant', route_class=PrivateRoute)


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = Field(min_length=1, max_length=2048, strict=True)
    token: str | None = Field(default=None, max_length=8192, strict=True)


class Disconnect(BaseModel):
    model_config = ConfigDict(extra='forbid')


@router.get('')
def settings(request: Request):
    _session(request)
    return homeassistant_settings.status()


@router.post('')
def save_settings(body: Connection, request: Request):
    _session(request, write=True)
    return homeassistant_settings.save_settings(body.model_dump())


@router.post('/test')
def test_connection(body: Connection, request: Request):
    _session(request, write=True)
    return homeassistant_settings.test_connection(body.model_dump())


@router.post('/disconnect')
def disconnect(body: Disconnect, request: Request):
    _session(request, write=True)
    return homeassistant_settings.disconnect()
