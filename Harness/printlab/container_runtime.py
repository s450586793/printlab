"""Container entry point. Application files and persistent user data stay separate."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time

from . import core


def prepare_data(data, secret_directory, uid=1000, gid=1000):
    """Prepare only this deployment's bind mounts; the long-running app stays unprivileged."""
    data, secret_directory = Path(data), Path(secret_directory)
    for base in (data, secret_directory):
        if base.is_symlink():
            raise RuntimeError('PrintLab bind mounts cannot be symbolic links')
        base.mkdir(parents=True, exist_ok=True)
        for directory, folders, files in os.walk(base, followlinks=False):
            for name in [directory, *(str(Path(directory) / item) for item in folders + files)]:
                path = Path(name)
                if path.is_symlink():
                    # Codex keeps helper symlinks in its persistent tmp directory.
                    # Never follow them or change their targets during preparation.
                    continue
                os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(base, 0o700)
    for base in (secret_directory, data / 'Runtime/private', data / 'Runtime/codex',
                 data / 'Runtime/codex-providers'):
        if base.is_symlink():
            raise RuntimeError('Private PrintLab directories cannot be symbolic links')
        if base.exists():
            for directory, _, files in os.walk(base, followlinks=False):
                os.chmod(directory, 0o700)
                for name in files:
                    path = Path(directory) / name
                    if not path.is_symlink():
                        os.chmod(path, 0o600)


def read_private_json(path):
    """Read the deployment credentials without putting them in logs or process arguments."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        username, password = value['username'], value['password']
        if not isinstance(username, str) or not isinstance(password, str) or not username or len(password) < 12:
            raise ValueError
        return username, password
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('Set a valid private PrintLab web login before starting the container') from exc


def load_session_key(path):
    """Keep sessions valid across restarts without following links out of private data."""
    path = Path(path)
    try:
        if any(item.is_symlink() for item in (path, path.parent, path.parent.parent)):
            raise ValueError('symbolic link')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        flags = getattr(os, 'O_NOFOLLOW', 0)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | flags, 0o600)
        except FileExistsError:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | flags)
            with os.fdopen(descriptor, 'rb') as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError('invalid file')
                key = source.read(33)
                if len(key) != 32:
                    raise ValueError('invalid key')
                os.fchmod(source.fileno(), 0o600)
                return key
        with os.fdopen(descriptor, 'wb') as target:
            # NAS ACL inheritance can override the mode supplied to os.open.
            os.fchmod(target.fileno(), 0o600)
            key = secrets.token_bytes(32)
            target.write(key)
            target.flush()
            os.fsync(target.fileno())
        return key
    except (OSError, ValueError) as exc:
        raise RuntimeError('Cannot prepare the private PrintLab login session key') from exc


class WebLogin:
    """Protect the entire container workbench, including files, cameras, and agent routes."""

    cookie_name = 'printlab_auth'
    session_seconds = 43200
    max_login_bytes = 8192

    def __init__(self, app, username, password, *, session_key=None, session_key_file=None):
        self.app = app
        self.username = username.encode('utf-8')
        self.password = password.encode('utf-8')
        self.key = session_key if session_key is not None else load_session_key(
            session_key_file or core.ROOT / 'Runtime/private/web-session.key')
        if not isinstance(self.key, bytes) or len(self.key) != 32:
            raise ValueError('PrintLab login session key must contain 32 bytes')
        self.credential_tag = hmac.new(self.key, self.username + b'\0' + self.password, hashlib.sha256).hexdigest()

    def credentials_match(self, username, password):
        return secrets.compare_digest(username, self.username) & secrets.compare_digest(password, self.password)

    def issue_token(self):
        payload = json.dumps({'expires': int(time.time()) + self.session_seconds,
                              'credentials': self.credential_tag, 'nonce': secrets.token_urlsafe(16)},
                             separators=(',', ':')).encode()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b'=').decode('ascii')
        signature = hmac.new(self.key, encoded.encode('ascii'), hashlib.sha256).hexdigest()
        return encoded + '.' + signature

    def valid_token(self, token):
        if not token or len(token) > 1024:
            return False
        try:
            encoded, signature = token.split('.')
            expected = hmac.new(self.key, encoded.encode('ascii'), hashlib.sha256).hexdigest()
            if not secrets.compare_digest(signature, expected):
                return False
            payload = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True))
            return (type(payload['expires']) is int and payload['expires'] > time.time()
                    and secrets.compare_digest(payload['credentials'], self.credential_tag))
        except (ValueError, KeyError, TypeError, binascii.Error, UnicodeError):
            return False

    def authenticated(self, request):
        if self.valid_token(request.cookies.get(self.cookie_name, '')):
            return True
        # Keep existing local API clients compatible without triggering a browser prompt.
        try:
            scheme, credentials = request.headers.get('authorization', '').split(' ', 1)
            if scheme.lower() == 'basic':
                username, password = base64.b64decode(credentials, validate=True).split(b':', 1)
                return self.credentials_match(username, password)
        except (ValueError, binascii.Error):
            pass
        return False

    async def login(self, request):
        from fastapi import HTTPException
        from starlette.responses import JSONResponse
        from .prepared_api import _origin
        try:
            if not _origin(request):
                raise HTTPException(403, '请从 PrintLab 登录页操作')
            if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                raise HTTPException(415, '请从 PrintLab 登录页提交账号和密码')
            try:
                length = int(request.headers.get('content-length', '0'))
                if length < 0:
                    raise ValueError
            except ValueError:
                raise HTTPException(400, '登录信息格式不正确') from None
            if length > self.max_login_bytes:
                raise HTTPException(413, '登录信息过长，请重新输入')
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > self.max_login_bytes:
                    raise HTTPException(413, '登录信息过长，请重新输入')
            try:
                data = json.loads(body.decode('utf-8'))
                if not isinstance(data, dict) or set(data) != {'username', 'password'}:
                    raise ValueError
                username, password = data['username'], data['password']
                if (not isinstance(username, str) or not isinstance(password, str)
                        or not 1 <= len(username) <= 200 or not 1 <= len(password) <= 1024):
                    raise ValueError
                username, password = username.encode('utf-8'), password.encode('utf-8')
            except (ValueError, UnicodeError, RecursionError):
                raise HTTPException(400, '请输入有效的账号和密码') from None
            if not self.credentials_match(username, password):
                raise HTTPException(401, '账号或密码不正确')
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                headers={'Cache-Control': 'no-store'})
        response = JSONResponse({'ok': True, 'next': '/#/overview'}, headers={'Cache-Control': 'no-store'})
        response.set_cookie(self.cookie_name, self.issue_token(), httponly=True, samesite='lax',
                            secure=request.url.scheme == 'https', max_age=self.session_seconds)
        return response

    async def logout(self, request):
        from fastapi import HTTPException
        from starlette.responses import JSONResponse
        from .prepared_api import _origin
        try:
            if not _origin(request):
                raise HTTPException(403, '请从 PrintLab 工作台操作')
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                headers={'Cache-Control': 'no-store'})
        response = JSONResponse({'ok': True}, headers={'Cache-Control': 'no-store'})
        response.delete_cookie(self.cookie_name, httponly=True, samesite='lax', secure=request.url.scheme == 'https')
        response.delete_cookie('printlab_session', httponly=True, samesite='strict', secure=request.url.scheme == 'https')
        return response

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from starlette.requests import Request
        from starlette.responses import FileResponse, JSONResponse, RedirectResponse
        request = Request(scope, receive)
        path, method = request.url.path, request.method
        # The Docker health probe has no session and exposes no private project data.
        if method == 'GET' and path == '/api/health':
            return await self.app(scope, receive, send)
        valid = self.authenticated(request)
        if path == '/api/auth/session' and method == 'GET':
            response = JSONResponse({'enabled': True, 'authenticated': valid}, headers={'Cache-Control': 'no-store'})
        elif path == '/api/auth/login' and method == 'POST':
            response = await self.login(request)
        elif path == '/api/auth/logout' and method == 'POST':
            response = await self.logout(request)
        elif path == '/login' and method in ('GET', 'HEAD'):
            if valid:
                response = RedirectResponse('/#/overview', status_code=303, headers={'Cache-Control': 'no-store'})
            else:
                response = FileResponse(Path(__file__).with_name('login.html'), media_type='text/html',
                                        headers={'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY'})
        elif valid:
            return await self.app(scope, receive, send)
        elif method in ('GET', 'HEAD') and not path.startswith('/api/'):
            response = RedirectResponse('/login', status_code=303, headers={'Cache-Control': 'no-store'})
        else:
            response = JSONResponse({'detail': '请先登录 PrintLab'}, status_code=401,
                                    headers={'Cache-Control': 'no-store'})
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        return await response(scope, receive, send)


def web_auth_path():
    """Prefer configured credentials and retain the previous mounted login on upgrades."""
    explicit = os.environ.get('PRINTLAB_WEB_AUTH_FILE')
    if explicit:
        return Path(explicit)
    try:
        stored = core.safe_path(core.ROOT, 'Runtime/private/web-auth.json', require_file=False)
    except core.WorkflowError:
        raise RuntimeError('Cannot prepare private PrintLab administrator setup') from None
    mounted = Path('/run/printlab-secrets/web-auth.json')
    return stored if stored.exists() or not mounted.exists() else mounted


def configure():
    """Initialize missing data and set deployment-specific paths without copying Mac binaries."""
    auth_file = web_auth_path()
    credentials = read_private_json(auth_file) if auth_file.exists() or os.environ.get('PRINTLAB_WEB_AUTH_FILE') else None
    first_run = not (core.ROOT / 'Runtime/config.json').exists()
    subprocess.run([sys.executable, str(core.SOURCE_ROOT / 'scripts/init_workspace.py')], check=True)
    config = core.config()
    if first_run:
        config.setdefault('device_provider', 'homeassistant')
    for key, env in [('cad_python', 'PRINTLAB_CAD_PYTHON'), ('blender_path', 'PRINTLAB_BLENDER_PATH'),
                     ('slicer_path', 'PRINTLAB_SLICER_PATH'), ('slicer_profiles_path', 'PRINTLAB_SLICER_PROFILES'),
                     ('studio_url', 'PRINTLAB_STUDIO_URL')]:
        if os.environ.get(env):
            config[key] = os.environ[env]
    core.write_json(core.ROOT / 'Runtime/config.json', config)
    codex_home = Path(os.environ.get('CODEX_HOME', str(core.ROOT / 'Runtime/codex')))
    codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(codex_home, 0o700)
    return credentials


def main():
    credentials = configure()
    from . import slicer_runtime
    slicer = slicer_runtime.capability()
    print(f"PrintLab slicer [{slicer['state']}]: {slicer['message']}", file=sys.stderr, flush=True)
    from .api import app
    from .web_setup import WebSetup
    import uvicorn
    secured = WebSetup(app, credentials=credentials)
    uvicorn.run(secured, host=os.environ.get('PRINTLAB_HOST', '0.0.0.0'),
                port=int(os.environ.get('PRINTLAB_PORT', '8018')),
                proxy_headers=os.environ.get('PRINTLAB_PROXY_HEADERS') == '1',
                forwarded_allow_ips=os.environ.get('PRINTLAB_FORWARDED_ALLOW_IPS', '127.0.0.1'))


if __name__ == '__main__':
    main()
