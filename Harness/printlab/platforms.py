"""Host paths and private-directory permissions for desktop and Linux workspaces."""
import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys


def venv_python(directory):
    return Path(directory) / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def tool_defaults():
    if sys.platform == 'win32':
        programs = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))
        slicer = shutil.which('bambu-studio.exe') or str(programs / 'Bambu Studio/bambu-studio.exe')
        blenders = sorted((programs / 'Blender Foundation').glob('Blender */blender.exe'), reverse=True)
        blender = shutil.which('blender.exe') or str(next(iter(blenders), programs / 'Blender Foundation/Blender/blender.exe'))
        defaults = {'slicer_path': slicer, 'blender_path': blender,
                    'slicer_profiles_path': str(Path(slicer).parent / 'resources/profiles')}
    elif sys.platform.startswith('linux'):
        defaults = {'slicer_path': shutil.which('bambu-studio') or shutil.which('BambuStudio') or '/usr/bin/bambu-studio',
                    'blender_path': shutil.which('blender') or '/usr/bin/blender',
                    'slicer_profiles_path': '/opt/bambu-studio/resources/profiles'}
    else:
        defaults = {'slicer_path': '/Applications/BambuStudio.app/Contents/MacOS/BambuStudio',
                    'blender_path': '/Applications/Blender.app/Contents/MacOS/Blender',
                    'slicer_profiles_path': '/Applications/BambuStudio.app/Contents/Resources/profiles'}
    for field, variable in (('slicer_path', 'PRINTLAB_SLICER_PATH'),
                            ('blender_path', 'PRINTLAB_BLENDER_PATH'),
                            ('slicer_profiles_path', 'PRINTLAB_SLICER_PROFILES')):
        if os.environ.get(variable):
            defaults[field] = os.environ[variable]
    return defaults


def profile_directory(config):
    if config.get('slicer_profiles_path'):
        return Path(config['slicer_profiles_path'])
    if sys.platform == 'win32' and config.get('slicer_path'):
        return Path(config['slicer_path']).parent / 'resources/profiles'
    return Path(tool_defaults()['slicer_profiles_path'])


def protect_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != 'win32':
        path.chmod(0o700)
        return
    identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True, encoding='ascii', errors='replace')
    sid = next(csv.reader(identity.splitlines()))[1]
    # Replace the directory DACL, including explicit entries inherited from
    # an existing installation or supplied by Windows temporary-directory APIs.
    command = (
        '$ErrorActionPreference="Stop"; '
        '$acl = New-Object System.Security.AccessControl.DirectorySecurity; '
        '$acl.SetSecurityDescriptorSddlForm($env:PRINTLAB_PRIVATE_DACL); '
        '[System.IO.Directory]::SetAccessControl($env:PRINTLAB_PRIVATE_PATH, $acl)'
    )
    subprocess.run(['powershell', '-NoProfile', '-Command', command], check=True,
                   capture_output=True, env={**os.environ,
                       'PRINTLAB_PRIVATE_PATH': str(path.resolve()),
                       'PRINTLAB_PRIVATE_DACL': f'D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)'})
