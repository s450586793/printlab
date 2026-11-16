"""Bounded checks for the installed slicer and its official offline resources."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

from . import platforms

BUNDLED_WRAPPER = Path('/usr/local/bin/printlab-slicer')
BUNDLED_RECEIPT = Path('/opt/bambu-studio/PRINTLAB-SOURCE.json')
BUNDLED_ENGINE = Path('/opt/bambu-studio/bin/bambu-studio')


def fingerprint(path):
    """Bind built-in jobs to the actual engine and pinned source receipt."""
    candidate = Path(path)
    same_wrapper = candidate.resolve() == BUNDLED_WRAPPER.resolve()
    if not same_wrapper and candidate.is_file() and BUNDLED_WRAPPER.is_file():
        same_wrapper = candidate.samefile(BUNDLED_WRAPPER)
    if not same_wrapper:
        return None
    try:
        identity = {}
        for field, source in (('receipt_sha256', BUNDLED_RECEIPT), ('engine_sha256', BUNDLED_ENGINE)):
            with source.open('rb') as stream:
                identity[field] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return identity
    except OSError:
        from .core import WorkflowError
        raise WorkflowError('Built-in slicer identity is unavailable; check the container installation') from None


def capability(config=None):
    """Report actual CLI startup without exposing tool output or stopping the web app."""
    if config is None:
        from . import core
        config = core.config()
    defaults = platforms.tool_defaults()
    configured = config.get('slicer_path') or defaults['slicer_path']
    executable = Path(shutil.which(str(configured)) or str(configured)).expanduser()
    profiles = platforms.profile_directory(config)
    result = {'path': str(executable), 'profiles_path': str(profiles), 'available': False,
              'state': 'missing', 'message': '切片程序不存在或没有执行权限。', 'missing_libraries': []}
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return result
    required = []
    for model in ('A1', 'P1P'):
        required.extend([profiles / 'BBL/machine' / f'Bambu Lab {model} 0.4 nozzle.json',
                         profiles / 'BBL/process' / f'0.20mm Standard @BBL {model}.json'])
        filament = profiles / 'BBL/filament' / ('P1P' if model == 'P1P' else '')
        required.extend(filament / f'Generic {material} @BBL {model}.json' for material in ('PLA', 'PETG'))
    if not all(path.is_file() for path in required):
        result.update(state='missing_profiles', message='缺少官方 A1／P1P 0.4 mm 切片资源，请检查资源目录。')
        return result
    result.update(state='unavailable', message='切片程序无法启动，请检查容器日志和运行库。')
    try:
        probe = subprocess.run([str(executable), '--help'], capture_output=True, text=True,
                               errors='replace', timeout=20)
    except subprocess.TimeoutExpired:
        result['message'] = '切片程序启动检查超时；工作台仍可使用。'
        return result
    except OSError:
        return result
    if probe.returncode:
        result['missing_libraries'] = sorted(set(re.findall(
            r'\b(lib[A-Za-z0-9_+.-]+\.so(?:\.[0-9]+)*)\b', probe.stderr or '')))
        if result['missing_libraries']:
            result['message'] = '切片程序缺少运行库：' + '、'.join(result['missing_libraries']) + '。'
        return result
    if not all(flag in (probe.stdout or '') for flag in ('--load-settings', '--load-filaments', '--slice', '--export-3mf')):
        result['message'] = '切片程序未报告所需的 Bambu Studio CLI 命令。'
        return result
    result.update(available=True, state='ready', message='切片程序及 A1／P1P 离线资源可用；尚未验证实际硬件。')
    return result
