"""Same-origin provider administration; rejected input never leaves validation."""
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from . import model_providers
from .prepared_api import _session


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({'detail': '模型服务配置格式无效，请检查填写内容。'}, status_code=422)
            except model_providers.ProviderError as exc:
                response = JSONResponse({'detail': str(exc)}, status_code=exc.status_code)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response

        return handler


router = APIRouter(prefix='/api/agent', route_class=PrivateRoute)

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[a-z][a-z0-9_-]*$', strict=True)]
ModelId = Annotated[str, Field(min_length=1, max_length=128, strict=True)]


class Provider(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Identifier | None = None
    name: str = Field(min_length=1, max_length=120, strict=True)
    base_url: str = Field(min_length=1, max_length=2048, strict=True)
    api_key: str | None = Field(default=None, max_length=8192, strict=True)
    models: list[ModelId] = Field(default_factory=list, max_length=256)
    default_model: str | None = Field(default=None, max_length=128, strict=True)
    enabled: bool = Field(default=True, strict=True)


class SettingsWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=0, strict=True)
    provider: Provider | None = None
    make_default: bool = Field(default=False, strict=True)
    default_provider_id: Identifier | None = None


class TestCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Identifier | None = None
    name: str = Field(default='模型服务', min_length=1, max_length=120, strict=True)
    base_url: str = Field(min_length=1, max_length=2048, strict=True)
    api_key: str | None = Field(default=None, max_length=8192, strict=True)
    models: list[ModelId] = Field(default_factory=list, max_length=256)
    default_model: str | None = Field(default=None, max_length=128, strict=True)


@router.get('/settings')
def settings(request: Request):
    _session(request)
    return model_providers.settings()


@router.post('/settings')
def save_settings(body: SettingsWrite, request: Request):
    _session(request, write=True)
    return model_providers.save_settings(body.model_dump(exclude_unset=True))


@router.post('/settings/test')
def test_connection(body: TestCandidate, request: Request):
    _session(request, write=True)
    return model_providers.test_connection(body.model_dump(exclude_unset=True))
