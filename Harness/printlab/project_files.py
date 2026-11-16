"""Project-owned file uploads; bytes remain inert until explicitly used by a task."""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
from pathlib import Path
import re
import uuid

from . import core

MAX_BYTES = 100 * 1024 * 1024
DIRECTORY = 'Sources/attachments'
PUBLIC_FIELDS = ('id', 'name', 'url', 'size', 'content_type', 'extension', 'created_at', 'sha256')
DOWNLOAD_HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                    'X-Frame-Options': 'DENY', 'Content-Security-Policy': "default-src 'none'; sandbox"}


class FileError(core.WorkflowError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _project(pid):
    try:
        if not isinstance(pid, str) or not re.fullmatch(r'P\d{3,}', pid):
            raise ValueError
        return core.project_path(pid)
    except (core.WorkflowError, ValueError, OSError) as exc:
        raise FileError('项目不存在。', 404) from exc


def _identity(identity):
    if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise FileError('附件编号无效。', 404)
    return identity


def _name(filename):
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > 200
            or filename.strip() in {'.', '..'} or '/' in filename or '\\' in filename
            or any(ord(char) < 32 or ord(char) == 127 for char in filename)):
        raise FileError('附件名称无效，请使用不含路径的文件名。')
    return filename.strip()


def _extension(name):
    extension = Path(name).suffix.lower().lstrip('.')
    return extension if re.fullmatch(r'[a-z0-9]{1,12}', extension) else 'bin'


def _content_type(name):
    return mimetypes.guess_type(name)[0] or 'application/octet-stream'


class Upload:
    """Write bounded chunks to a private temporary file, then publish atomically."""

    def __init__(self, pid, filename):
        self.project, self.name = _project(pid), _name(filename)
        self.pid, self.identity = pid, uuid.uuid4().hex
        self.extension = _extension(self.name)
        self.size, self.digest = 0, hashlib.sha256()
        self.stream = None
        try:
            self.path = core.safe_path(self.project, f'{DIRECTORY}/{self.identity}.{self.extension}', require_file=False)
            self.manifest = core.safe_path(self.project, f'{DIRECTORY}/.metadata/{self.identity}.json', require_file=False)
            self.temporary = core.safe_path(self.project, f'{DIRECTORY}/.{self.identity}.upload', require_file=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Recheck after creating the directory; no caller can select a stored path.
            core.safe_path(self.project, self.temporary.relative_to(self.project).as_posix(), require_file=False)
            descriptor = os.open(self.temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            self.stream = os.fdopen(descriptor, 'wb')
        except (core.WorkflowError, OSError) as exc:
            raise FileError('附件未能保存，请重试。', 503) from exc

    def write(self, chunk):
        if not isinstance(chunk, bytes):
            raise FileError('附件上传数据无效。')
        if self.size + len(chunk) > MAX_BYTES:
            raise FileError('每个附件不能超过 100 MB。', 413)
        try:
            self.stream.write(chunk)
        except OSError as exc:
            raise FileError('附件未能保存，请重试。', 503) from exc
        self.digest.update(chunk)
        self.size += len(chunk)

    def finish(self):
        if not self.size:
            raise FileError('不能上传空附件。')
        metadata = {'id': self.identity, 'project_id': self.pid, 'name': self.name,
                    'url': f'/api/projects/{self.pid}/files/{self.identity}', 'size': self.size,
                    'content_type': _content_type(self.name), 'extension': self.extension,
                    'created_at': core.now(), 'sha256': self.digest.hexdigest()}
        published = False
        try:
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()
            self.stream = None
            # Linking is exclusive, so even an unexpected ID collision cannot replace a file.
            os.link(self.temporary, self.path, follow_symlinks=False)
            published = True
            self.temporary.unlink()
            core.safe_path(self.project, self.manifest.relative_to(self.project).as_posix(), require_file=False)
            core.write_json(self.manifest, metadata)
        except (core.WorkflowError, OSError) as exc:
            if published:
                self.path.unlink(missing_ok=True)
            raise FileError('附件未能保存，请重试。', 503) from exc
        return {key: metadata[key] for key in PUBLIC_FIELDS}

    def abort(self):
        """Remove incomplete uploads after errors, disconnects or cancellation."""
        try:
            if self.stream is not None:
                self.stream.close()
        except OSError:
            logging.getLogger('printlab').warning('Incomplete attachment could not be closed.')
        finally:
            self.stream = None
        try:
            self.temporary.unlink(missing_ok=True)
        except OSError:
            logging.getLogger('printlab').warning('Incomplete attachment could not be removed.')


def resolve_file(pid, file_id):
    """Resolve only server-generated project IDs and verify the original bytes."""
    project, identity = _project(pid), _identity(file_id)
    try:
        manifest = core.safe_path(project, f'{DIRECTORY}/.metadata/{identity}.json')
        if not 0 < manifest.stat().st_size <= 4096:
            raise ValueError
        metadata = core.read_json(manifest)
        if not isinstance(metadata, dict):
            raise ValueError
        name = _name(metadata['name'])
        extension = _extension(name)
        if (metadata.get('id') != identity or metadata.get('project_id') != pid
                or metadata.get('extension') != extension or metadata.get('content_type') != _content_type(name)
                or metadata.get('url') != f'/api/projects/{pid}/files/{identity}'
                or not isinstance(metadata.get('created_at'), str)):
            raise ValueError
        path = core.safe_path(project, f'{DIRECTORY}/{identity}.{extension}')
        size = path.stat().st_size
        if (not 0 < size <= MAX_BYTES or type(metadata.get('size')) is not int or metadata['size'] != size
                or metadata.get('sha256') != core.digest(path)):
            raise ValueError
        return {key: metadata[key] for key in PUBLIC_FIELDS}, path
    except (core.WorkflowError, OSError, ValueError, KeyError, TypeError) as exc:
        raise FileError('附件不存在或已更改，请重新上传。', 404) from exc


def resolve_files(pid, file_ids=None):
    """Resolve unique file IDs belonging to this exact project without a count cap."""
    file_ids = [] if file_ids is None else file_ids
    if (not isinstance(file_ids, list) or any(not isinstance(value, str) for value in file_ids)
            or len(set(file_ids)) != len(file_ids)):
        raise FileError('请使用不重复的附件编号列表。')
    return [resolve_file(pid, identity) for identity in file_ids]
