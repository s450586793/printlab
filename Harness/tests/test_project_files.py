import hashlib
import os
import stat

import pytest

from printlab import core, project_files as files


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    core.write_json(tmp_path / 'Runtime/config.json', {})
    core.new_project('File uploads')
    core.new_project('Other project')
    return tmp_path


def upload(content=b'part dimensions 20 30 40', name='尺寸参考.step', pid='P001'):
    writer = files.Upload(pid, name)
    try:
        writer.write(content)
        return writer.finish()
    finally:
        writer.abort()


def test_streamed_bytes_and_public_metadata_round_trip(workspace):
    writer = files.Upload('P001', ' 零件尺寸.STEP ')
    writer.write(b'ISO-10303-21;\n')
    writer.write(b'END-ISO-10303-21;')
    metadata = writer.finish()
    writer.abort()
    found, path = files.resolve_file('P001', metadata['id'])
    assert found == metadata and set(metadata) == set(files.PUBLIC_FIELDS)
    assert metadata['name'] == '零件尺寸.STEP' and metadata['extension'] == 'step'
    assert metadata['url'] == f'/api/projects/P001/files/{metadata["id"]}'
    assert metadata['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert metadata['size'] == path.stat().st_size
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.parent == core.project_path('P001') / files.DIRECTORY
    assert not writer.temporary.exists()
    assert b'ISO-' in path.read_bytes()


@pytest.mark.parametrize('name,extension', [('notes.json', 'json'), ('README', 'bin'),
    ('模型.未知', 'bin'), ('model.abcdefghijklmn', 'bin'), ('<script>.HTML', 'html'), ('code.py', 'py')])
def test_original_names_never_select_stored_paths_or_execute(workspace, name, extension):
    metadata = upload(b'opaque upload bytes', name)
    _, path = files.resolve_file('P001', metadata['id'])
    assert path.name == f'{metadata["id"]}.{extension}'
    assert metadata['name'] == name and metadata['extension'] == extension


@pytest.mark.parametrize('name', ['', '  ', '.', '..', '../x', 'a/b', 'a\\b', 'x\0.py', 'x\n.txt', 'x\x7f.txt', 'x' * 201, None])
def test_bad_names_rejected_before_any_bytes_are_saved(workspace, name):
    with pytest.raises(files.FileError):
        files.Upload('P001', name)
    assert not (core.project_path('P001') / files.DIRECTORY).exists()


@pytest.mark.parametrize('pid', ['../P001', 'P999', None])
def test_project_must_exist_and_use_an_exact_id(workspace, pid):
    with pytest.raises(files.FileError, match='项目'):
        files.Upload(pid, 'part.stl')


def test_real_100_mib_boundary_accepts_exact_limit_and_rejects_one_more_byte(workspace):
    assert files.MAX_BYTES == 100 * 1024 * 1024
    writer = files.Upload('P001', 'large.stl')
    try:
        chunk = b'x' * (1024 * 1024)
        for _ in range(100):
            writer.write(chunk)
        with pytest.raises(files.FileError) as rejected:
            writer.write(b'x')
        assert rejected.value.status_code == 413
        metadata = writer.finish()
        assert metadata['size'] == files.MAX_BYTES
        assert files.resolve_file('P001', metadata['id'])[0] == metadata
    finally:
        writer.abort()


def test_abort_and_empty_upload_leave_no_files(workspace):
    writer = files.Upload('P001', 'empty.txt')
    with pytest.raises(files.FileError, match='空附件'):
        writer.finish()
    with pytest.raises(files.FileError, match='数据'):
        writer.write('not bytes')
    writer.abort()
    writer.abort()
    assert list(writer.path.parent.iterdir()) == []
    writer = files.Upload('P001', 'incomplete.zip')
    writer.write(b'PK partial')
    writer.abort()
    assert list(writer.path.parent.iterdir()) == []


@pytest.mark.parametrize('failure', ['link', 'manifest', 'write'])
def test_disk_failures_cleanup_partial_uploads(workspace, monkeypatch, failure):
    writer = files.Upload('P001', 'incomplete.txt')
    def fail(*args, **kwargs):
        raise OSError('private internal disk path')
    if failure == 'write':
        class FailedStream:
            def write(self, chunk): fail()
            def close(self): pass
        writer.stream.close()
        writer.stream = FailedStream()
        operation = lambda: writer.write(b'bytes')
    else:
        writer.write(b'bytes')
        monkeypatch.setattr(os, 'link', fail) if failure == 'link' else monkeypatch.setattr(core, 'write_json', fail)
        operation = writer.finish
    try:
        with pytest.raises(files.FileError) as rejected:
            operation()
        assert rejected.value.status_code == 503 and 'private' not in str(rejected.value)
    finally:
        writer.abort()
    assert not writer.path.exists() and not writer.temporary.exists()


def test_symlink_directory_or_blob_or_manifest_is_rejected(workspace, tmp_path):
    project = core.project_path('P001')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (project / files.DIRECTORY).symlink_to(outside, target_is_directory=True)
    with pytest.raises(files.FileError):
        files.Upload('P001', 'notes.txt')
    assert list(outside.iterdir()) == []
    (project / files.DIRECTORY).unlink()
    metadata = upload()
    _, path = files.resolve_file('P001', metadata['id'])
    original = path.read_bytes()
    path.unlink()
    (outside / 'part.step').write_bytes(original)
    path.symlink_to(outside / 'part.step')
    with pytest.raises(files.FileError): files.resolve_file('P001', metadata['id'])
    path.unlink()
    path.write_bytes(original)
    manifest = path.parent / '.metadata' / f'{metadata["id"]}.json'
    moved = outside / 'manifest.json'
    manifest.rename(moved)
    manifest.symlink_to(moved)
    with pytest.raises(files.FileError): files.resolve_file('P001', metadata['id'])


@pytest.mark.parametrize('change', ['blob', 'empty', 'size', 'id', 'project_id', 'extension', 'content_type', 'url', 'name', 'sha256', 'created_at', 'malformed', 'huge'])
def test_changed_or_invalid_uploads_are_not_resolved(workspace, change):
    metadata = upload()
    _, path = files.resolve_file('P001', metadata['id'])
    manifest = path.parent / '.metadata' / f'{metadata["id"]}.json'
    if change in {'blob', 'empty'}:
        path.write_bytes(b'changed' if change == 'blob' else b'')
    elif change == 'malformed':
        manifest.write_text('[]')
    elif change == 'huge':
        manifest.write_text('x' * 4097)
    else:
        stored = core.read_json(manifest)
        stored[change] = None
        core.write_json(manifest, stored)
    with pytest.raises(files.FileError) as rejected:
        files.resolve_file('P001', metadata['id'])
    assert rejected.value.status_code == 404


def test_unique_ids_project_isolation_and_unlimited_count(workspace):
    metadata = [upload(name=f'part{i}.stl') for i in range(9)]
    ids = [item['id'] for item in metadata]
    assert [item for item, _ in files.resolve_files('P001', ids)] == metadata
    assert files.resolve_files('P001') == []
    for values in [ids[:1] * 2, 'a' * 32, [None], ['../path'], ['a' * 32]]:
        with pytest.raises(files.FileError): files.resolve_files('P001', values)
    with pytest.raises(files.FileError): files.resolve_files('P002', ids[:1])
