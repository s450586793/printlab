"""One-time administrator setup protected by a local, private initialization code."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

from . import core
from .container_runtime import WebLogin, read_private_json, web_auth_path


def _private(relative):
    try:
        path = core.safe_path(core.ROOT, 'Runtime/private/' + relative, require_file=False)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        return path
    except (OSError, core.WorkflowError):
        raise RuntimeError('Cannot prepare private PrintLab administrator setup') from None


def _setup_token():
    path = _private('setup-token')
    with core.lock('web-initialization'):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            try:
                value = path.read_text(encoding='ascii').strip()
                if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                    raise ValueError
                os.chmod(path, 0o600)
                return value
            except (OSError, ValueError, UnicodeError):
                raise RuntimeError('Cannot read the private PrintLab setup code') from None
        with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
            os.fchmod(stream.fileno(), 0o600)
            value = secrets.token_hex(32)
            stream.write(value + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        return value


class WebSetup:
    def __init__(self, app, *, credentials=None):
        self.app = app
        self.auth_file = web_auth_path()
        self.login = None
        if credentials is not None or self.auth_file.exists():
            self.login = WebLogin(app, *(credentials or read_private_json(self.auth_file)))
        else:
            self.setup_token = _setup_token()
            print('PrintLab first-run setup: open /setup; read the initialization code from '
                  'Runtime/private/setup-token in the data directory.', file=sys.stderr, flush=True)

    def _reload(self):
        if self.login is None and self.auth_file.exists():
            self.login = WebLogin(self.app, *read_private_json(self.auth_file))

    async def setup(self, request):
        from fastapi import HTTPException
        from starlette.responses import JSONResponse
        from .prepared_api import _origin
        try:
            if not _origin(request):
                raise HTTPException(403, '请从 PrintLab 初始化页面操作')
            if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                raise HTTPException(415, '请从初始化页面提交管理员信息')
            try:
                length = int(request.headers.get('content-length', '0'))
                if length < 0:
                    raise ValueError
            except ValueError:
                raise HTTPException(400, '初始化信息格式不正确') from None
            if length > WebLogin.max_login_bytes:
                raise HTTPException(413, '初始化信息过长')
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > WebLogin.max_login_bytes:
                    raise HTTPException(413, '初始化信息过长')
            try:
                data = json.loads(body.decode('utf-8'))
                if not isinstance(data, dict) or set(data) != {'setup_token', 'username', 'password'}:
                    raise ValueError
                code, username, password = data['setup_token'], data['username'], data['password']
                if (not all(isinstance(value, str) for value in [code, username, password])
                        or not 1 <= len(username) <= 200 or username != username.strip()
                        or any(ord(c) < 32 for c in username) or not 12 <= len(password) <= 1024):
                    raise ValueError
                username.encode('utf-8')
                password.encode('utf-8')
                code.encode('ascii')
            except (ValueError, UnicodeError, RecursionError):
                raise HTTPException(400, '请输入有效的初始化码、账号和至少 12 位密码') from None
            with core.lock('web-initialization'):
                self._reload()
                if self.login is not None:
                    raise HTTPException(409, '管理员已初始化，请登录')
                if not secrets.compare_digest(code, self.setup_token):
                    raise HTTPException(403, '初始化码不正确')
                # Publish credentials only after the persistent signing key is complete.
                login = WebLogin(self.app, username, password)
                path = _private('web-auth.json')
                temporary = None
                try:
                    descriptor, temporary = tempfile.mkstemp(prefix='.web-auth-', dir=path.parent)
                    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        json.dump({'username': username, 'password': password}, stream)
                        stream.write('\n')
                        stream.flush()
                        os.fsync(stream.fileno())
                    _private('web-auth.json')
                    os.replace(temporary, path)
                finally:
                    if temporary and os.path.exists(temporary):
                        os.unlink(temporary)
                self.login = login
                _private('setup-token').unlink(missing_ok=True)
                self.setup_token = None
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                headers={'Cache-Control': 'no-store'})
        except OSError:
            return JSONResponse({'detail': '无法保存管理员配置，请检查数据目录权限'}, status_code=409,
                                headers={'Cache-Control': 'no-store'})
        response = JSONResponse({'ok': True, 'next': '/#/overview'}, headers={'Cache-Control': 'no-store'})
        response.set_cookie(WebLogin.cookie_name, self.login.issue_token(), httponly=True, samesite='lax',
                            secure=request.url.scheme == 'https', max_age=WebLogin.session_seconds)
        return response

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from starlette.requests import Request
        from starlette.responses import FileResponse, JSONResponse, RedirectResponse
        request = Request(scope, receive)
        self._reload()
        path, method = request.url.path, request.method
        if path == '/api/auth/setup' and method == 'POST':
            response = (JSONResponse({'detail': '管理员已初始化，请登录'}, status_code=409,
                                    headers={'Cache-Control': 'no-store'})
                        if self.login else await self.setup(request))
        elif path == '/setup' and method in ('GET', 'HEAD') and self.login:
            response = RedirectResponse('/login', status_code=303, headers={'Cache-Control': 'no-store'})
        elif self.login:
            return await self.login(scope, receive, send)
        elif path == '/api/health' and method == 'GET':
            return await self.app(scope, receive, send)
        elif path == '/api/auth/session' and method == 'GET':
            response = JSONResponse({'enabled': True, 'authenticated': False, 'setup_required': True},
                                    headers={'Cache-Control': 'no-store'})
        elif path == '/setup' and method in ('GET', 'HEAD'):
            response = FileResponse(Path(__file__).with_name('setup.html'), media_type='text/html',
                                    headers={'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY'})
        elif method in ('GET', 'HEAD') and not path.startswith('/api/'):
            response = RedirectResponse('/setup', status_code=303, headers={'Cache-Control': 'no-store'})
        else:
            response = JSONResponse({'detail': '请先初始化 PrintLab 管理员'}, status_code=401,
                                    headers={'Cache-Control': 'no-store'})
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        return await response(scope, receive, send)
