"""Validated project reference images, addressed only by server-generated IDs."""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import uuid
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from . import core

MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_DIMENSION = 8192
DIRECTORY = 'Sources/reference-images'
FORMATS = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg'), 'WEBP': ('webp', 'image/webp')}
PUBLIC_FIELDS = ('id', 'name', 'url', 'width', 'height', 'content_type', 'size', 'created_at', 'sha256')


class ImageError(core.WorkflowError):
    def __init__(self, message, status_code=400, *, reason='invalid_image'):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


def _project(pid):
    if not isinstance(pid, str) or not re.fullmatch(r'P\d{3,}', pid):
        raise ImageError('项目编号无效。', 404)
    return core.project_path(pid)


def _identity(image_id):
    if not isinstance(image_id, str) or not re.fullmatch(r'[a-f0-9]{32}', image_id):
        raise ImageError('参考图编号无效。', 404)
    return image_id


def _name(filename):
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > 200
            or '/' in filename or '\\' in filename or any(ord(char) < 32 for char in filename)):
        raise ImageError('图片名称无效，请使用不含路径的文件名。')
    return filename.strip()


def _within_pixel_limits(size):
    return size[0] * size[1] <= MAX_PIXELS and max(size) <= MAX_DIMENSION


def _prepare_jpeg(image):
    """Ask libjpeg for a bounded primary-frame decode, including phone MPO JPEGs."""
    image.seek(0)
    width, height = image.size
    if _within_pixel_limits(image.size):
        return
    for factor in (2, 4, 8):
        if _within_pixel_limits(((width + factor - 1) // factor, (height + factor - 1) // factor)):
            image.draft('RGB', (max(1, width // factor), max(1, height // factor)))
            break
    # Draft is an optional decoder hint; never load if it was ignored or insufficient.
    if not _within_pixel_limits(image.size):
        raise ImageError('图片尺寸过大，请先缩小图片后再上传。', 413, reason='pixel_limit')


def _normalize(content):
    if not isinstance(content, bytes) or not content:
        raise ImageError('请选择一张图片。', reason='empty_image')
    if len(content) > MAX_BYTES:
        raise ImageError('每张参考图不能超过 12 MB。', 413, reason='byte_limit')
    image_format, source_size = None, None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as original:
                source_format = original.format
                image_format, source_size = source_format, original.size
                if source_format in {'JPEG', 'MPO'}:
                    # MPO is a JPEG container that can include a phone's HDR gain map.
                    # Only the primary photograph becomes the reference image.
                    image_format = 'JPEG'
                    _prepare_jpeg(original)
                if image_format not in FORMATS:
                    raise ImageError('仅支持 PNG、JPEG 和 WebP 图片。', reason='unsupported_format')
                if not _within_pixel_limits(original.size):
                    raise ImageError('图片尺寸过大，请缩小到每边 8192 像素以内、总计 2000 万像素以内。', 413, reason='pixel_limit')
                if source_format != 'MPO' and getattr(original, 'n_frames', 1) != 1:
                    raise ImageError('请上传静态参考图，暂不支持动图。', reason='animated_image')
                original.load()
                oriented = ImageOps.exif_transpose(original)
                alpha = 'A' in oriented.getbands() or 'transparency' in oriented.info
                mode = 'RGBA' if image_format != 'JPEG' and alpha else 'RGB'
                clean = Image.new(mode, oriented.size)
                clean.paste(oriented.convert(mode))
                output = io.BytesIO()
                options = {'quality': 95} if image_format in {'JPEG', 'WEBP'} else {}
                clean.save(output, format=image_format, **options)
                normalized = output.getvalue()
                if len(normalized) > MAX_BYTES:
                    raise ImageError('处理后的参考图超过 12 MB，请先缩小图片。', 413, reason='normalized_byte_limit')
                return normalized, image_format, clean.size
    except ImageError as exc:
        logging.getLogger('printlab').warning('Reference image rejected reason=%s bytes=%d format=%s size=%s',
                                           exc.reason, len(content), image_format, source_size)
        raise
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        logging.getLogger('printlab').warning('Reference image rejected reason=invalid_data bytes=%d format=%s size=%s',
                                           len(content), image_format, source_size)
        raise ImageError('无法读取这张图片，请重新选择完整的 PNG、JPEG 或 WebP 文件。', reason='invalid_data') from exc


def save_image(pid, content, filename='参考图'):
    """Decode and normalize an upload; callers cannot select its stored path."""
    project = _project(pid)
    name = _name(filename)
    content, image_format, (width, height) = _normalize(content)
    extension, content_type = FORMATS[image_format]
    identity = uuid.uuid4().hex
    relative = f'{DIRECTORY}/{identity}.{extension}'
    path = core.safe_path(project, relative, require_file=False)
    manifest = core.safe_path(project, f'{DIRECTORY}/{identity}.json', require_file=False)
    metadata = {'id': identity, 'project_id': pid, 'name': name,
                'url': f'/api/projects/{pid}/images/{identity}', 'width': width, 'height': height,
                'content_type': content_type, 'size': len(content), 'format': image_format,
                'created_at': core.now(), 'sha256': hashlib.sha256(content).hexdigest()}
    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        created = True
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
        core.write_json(manifest, metadata)
    except OSError as exc:
        if created:
            path.unlink(missing_ok=True)
        raise ImageError('参考图未能保存，请重试。', 503) from exc
    return {key: metadata[key] for key in PUBLIC_FIELDS}


def resolve_image(pid, image_id):
    """Resolve a project-owned image and verify it still matches the uploaded bytes."""
    project, identity = _project(pid), _identity(image_id)
    try:
        manifest = core.safe_path(project, f'{DIRECTORY}/{identity}.json')
        if not 0 < manifest.stat().st_size <= 4096:
            raise ValueError
        metadata = core.read_json(manifest)
        if (not isinstance(metadata, dict) or metadata.get('id') != identity
                or metadata.get('project_id') != pid or metadata.get('format') not in FORMATS):
            raise ValueError
        extension, content_type = FORMATS[metadata['format']]
        path = core.safe_path(project, f'{DIRECTORY}/{identity}.{extension}')
        if not 0 < path.stat().st_size <= MAX_BYTES:
            raise ValueError
        if (metadata.get('size') != path.stat().st_size or metadata.get('content_type') != content_type
                or metadata.get('url') != f'/api/projects/{pid}/images/{identity}'
                or metadata.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest()):
            raise ValueError
        _name(metadata['name'])
        if (any(type(metadata.get(key)) is not int or not 0 < metadata[key] <= MAX_DIMENSION for key in ('width', 'height'))
                or metadata['width'] * metadata['height'] > MAX_PIXELS):
            raise ValueError
        return {key: metadata[key] for key in PUBLIC_FIELDS}, path
    except (core.WorkflowError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ImageError('参考图不存在或已更改，请重新上传。', 404) from exc


def resolve_attachments(pid, image_ids=None):
    """Resolve unique image IDs belonging to this exact project."""
    image_ids = [] if image_ids is None else image_ids
    if (not isinstance(image_ids, list)
            or any(not isinstance(value, str) for value in image_ids) or len(set(image_ids)) != len(image_ids)):
        raise ImageError('请使用不重复的参考图编号列表。')
    return [resolve_image(pid, identity) for identity in image_ids]
