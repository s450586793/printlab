"""只读选择已有模型预览，不将草稿提升为正式版本。"""
import os
import re
from pathlib import Path

from . import core

MAX_IMAGE_BYTES = 16 * 1024 * 1024
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
MODEL_SUFFIXES = {'.glb', '.stl', '.3mf'}
GENERATED_DIRS = ('Preview', 'Design', 'Exports')
EXCLUDED_DIRS = {'concepts', 'reference-images', 'verification', 'screenshots', 'screenshot', 'research'}


def _allowed_directory(name):
    return not name.startswith('.') and name.casefold() not in EXCLUDED_DIRS


def _artifacts(project, roots):
    images, models = [], []
    for relative in roots:
        try:
            directory = core.safe_path(project, relative, require_file=False)
        except (OSError, core.WorkflowError):
            continue
        for parent, directories, files in os.walk(directory, followlinks=False):
            directories[:] = sorted(name for name in directories if _allowed_directory(name)
                                     and not (Path(parent) / name).is_symlink())
            for name in sorted(files):
                path = Path(parent) / name
                suffix = path.suffix.lower()
                if name.startswith('.') or suffix not in IMAGE_SUFFIXES | MODEL_SUFFIXES:
                    continue
                try:
                    path = core.safe_path(project, path.relative_to(project).as_posix())
                    size = path.stat().st_size
                    if suffix in IMAGE_SUFFIXES and 0 < size <= MAX_IMAGE_BYTES:
                        images.append(path)
                    elif suffix in MODEL_SUFFIXES and size > 0:
                        models.append(path)
                except (OSError, core.WorkflowError):
                    continue
    return images, models


def _image_rank(image, models):
    stem = image.stem.casefold()
    if stem in {'cover', 'preview'}:
        return 2 if stem == 'cover' else 1
    for ending in ('_preview', '_cover'):
        if stem.endswith(ending):
            name = stem[:-len(ending)]
            if name and any(model.stem.casefold() == name or model.stem.casefold().endswith('_' + name)
                            for model in models):
                return 0
    return -1


def _model_directories(project, models):
    groups = {}
    for model in models:
        parent = model.relative_to(project).parent
        groups.setdefault(parent, []).append(model)
        # Preview/R01 可使用 Design/R01 或 Exports/R01 下的模型（含 parts 子目录）。
        if parent.parts[0] in {'Design', 'Exports'}:
            for ancestor in (parent, *parent.parents):
                if len(ancestor.parts) <= 1:
                    break
                preview_parent = Path('Preview', *ancestor.parts[1:])
                groups.setdefault(preview_parent, []).append(model)
    return groups


def _natural_key(value):
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r'(\d+)', value))


def cover_url(project, data):
    """正式版本只用本版本图片；未登记版本只用与模型配对的生成图。"""
    revision = data.get('current_revision')
    if revision:
        if not isinstance(revision, str) or not re.fullmatch(r'[\w-]+', revision):
            return None
        # 常见正式封面直接返回，避免每次刷新列表都扫描整个版本目录。
        formal = f'Preview/{revision}/cover.png'
        try:
            file = core.public_project_file(data['id'], formal)
            if 0 < file.stat().st_size <= MAX_IMAGE_BYTES:
                return core.file_url(data['id'], formal)
        except (OSError, core.WorkflowError):
            pass
        roots = [f'{directory}/{revision}' for directory in GENERATED_DIRS]
    else:
        roots = [*GENERATED_DIRS, 'Sources/attachments']
    images, models = _artifacts(project, roots)
    groups = _model_directories(project, models)
    candidates = []
    for image in images:
        paired = groups.get(image.relative_to(project).parent, [])
        rank = _image_rank(image, paired)
        if rank < 0 or (not revision and not paired):
            continue
        relative = image.relative_to(project)
        category = relative.parts[0]
        formal = relative.as_posix() == f'Preview/{revision}/cover.png'
        version = relative.parts[1] if category in GENERATED_DIRS and len(relative.parts) > 2 else ''
        try:
            modified = image.stat().st_mtime_ns
        except OSError:
            continue
        priority = (formal, category in GENERATED_DIRS, _natural_key(version), rank,
                    {'Preview': 3, 'Design': 2, 'Exports': 1}.get(category, 0), modified, relative.as_posix())
        candidates.append((priority, relative.as_posix()))
    for _, relative in sorted(candidates, reverse=True):
        try:
            core.public_project_file(data['id'], relative)
            return core.file_url(data['id'], relative)
        except (OSError, core.WorkflowError):
            continue
    return None
