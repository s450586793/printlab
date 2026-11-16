import pytest
from fastapi.testclient import TestClient

from printlab import core, project_previews
from printlab.api import app


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    core.new_project('模型封面')
    return core.project_path('P001')


def asset(project, relative, content=b'model or image bytes'):
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def current(project, revision):
    path = project / 'project.json'
    core.write_json(path, {**core.read_json(path), 'current_revision': revision})


def cover(project):
    return core.summary(project)['cover_url']


def test_draft_design_preview_without_promoting_revision(project):
    asset(project, 'Design/R01/R01_assembly.glb')
    preview = asset(project, 'Design/R01/preview.png')
    before = (project / 'project.json').read_bytes()
    row = core.summary(project)
    assert row['cover_url'] == '/api/files/P001/Design/R01/preview.png'
    assert row['current_revision'] is None and row['model_url'] is None and row['status'] == 'designing'
    assert (project / 'project.json').read_bytes() == before and preview.read_bytes() == b'model or image bytes'


def test_current_version_wins_and_never_uses_history_or_draft(project):
    asset(project, 'Design/R99/assembly.glb')
    asset(project, 'Design/R99/preview.png')
    asset(project, 'Preview/v001/cover.png')
    current(project, 'v002')
    assert cover(project) is None
    asset(project, 'Preview/v002/cover.png')
    assert cover(project) == '/api/files/P001/Preview/v002/cover.png'


@pytest.mark.parametrize('relative', ['Design/v002/preview.webp', 'Exports/v002/cover.jpg', 'Preview/v002/preview.PNG'])
def test_current_version_alternate_generated_preview(project, relative):
    current(project, 'v002')
    asset(project, relative)
    assert cover(project) == '/api/files/P001/' + relative


def test_latest_design_revision_precedes_old_attachment_artwork(project):
    for directory in ['Design/R2', 'Design/R10', 'Sources/attachments/id']:
        asset(project, directory + '/assembly.glb')
        asset(project, directory + '/preview.png')
    assert cover(project) == '/api/files/P001/Design/R10/preview.png'


def test_preview_directory_pairs_with_exported_model(project):
    asset(project, 'Exports/R01/assembly.stl')
    asset(project, 'Preview/R01/cover.png')
    assert cover(project) == '/api/files/P001/Preview/R01/cover.png'


def test_reference_concepts_screenshots_and_unpaired_images_are_excluded(project):
    for directory in ['Sources/reference-images', 'Preview/Concepts', 'Design/verification', 'Design/.hidden']:
        asset(project, directory + '/assembly.glb')
        asset(project, directory + '/preview.png')
    asset(project, 'Design/R01/preview.png')
    asset(project, 'Design/R02/assembly.glb')
    asset(project, 'Design/R02/photo.png')
    assert cover(project) is None


def test_attachment_preview_is_paired_and_requires_existing_browser_session(project):
    asset(project, 'Sources/attachments/id/simplified_station.glb')
    image = asset(project, 'Sources/attachments/id/station_preview.png')
    url = cover(project)
    assert url == '/api/files/P001/Sources/attachments/id/station_preview.png'
    client = TestClient(app, base_url='http://127.0.0.1:8018')
    assert client.get(url).status_code == 403
    client.get('/api/prepared/session')
    response = client.get(url)
    assert response.status_code == 200 and response.content == image.read_bytes()


def test_symlinks_empty_and_oversized_images_do_not_become_covers(project, monkeypatch):
    asset(project, 'Design/R01/assembly.glb')
    image = asset(project, 'Design/R01/preview.png', b'')
    assert cover(project) is None
    image.unlink()
    secret = asset(project.parent.parent, 'Runtime/private/secret.png')
    image.symlink_to(secret)
    assert cover(project) is None
    image.unlink()
    image.write_bytes(b'large')
    monkeypatch.setattr(project_previews, 'MAX_IMAGE_BYTES', 4)
    assert cover(project) is None


def test_model_symlink_and_directory_symlink_are_excluded(project):
    external = project.parent.parent / 'outside'
    asset(external, 'assembly.glb')
    asset(external, 'preview.png')
    (project / 'Design/R01').symlink_to(external, target_is_directory=True)
    asset(project, 'Design/R02/preview.png')
    (project / 'Design/R02/assembly.glb').symlink_to(external / 'assembly.glb')
    assert cover(project) is None


def test_formal_cover_has_priority_within_same_version(project):
    current(project, 'v001')
    asset(project, 'Preview/v001/cover.png')
    asset(project, 'Design/v001/preview.png')
    assert cover(project) == '/api/files/P001/Preview/v001/cover.png'


def test_formal_cover_does_not_scan_model_directories(project, monkeypatch):
    current(project, 'v001')
    asset(project, 'Preview/v001/cover.png')
    monkeypatch.setattr(project_previews.os, 'walk', lambda *args, **kwargs: pytest.fail('Formal cover must not scan'))
    assert cover(project) == '/api/files/P001/Preview/v001/cover.png'


@pytest.mark.parametrize('revision', ['../v001', '/v001', 12, 'v001/../../R01'])
def test_invalid_revision_never_selects_another_directory(project, revision):
    asset(project, 'Preview/v001/cover.png')
    current(project, revision)
    assert cover(project) is None


def test_nested_export_parts_pair_with_preview_and_skip_unrelated_named_image(project):
    asset(project, 'Exports/R01/parts/body.stl')
    asset(project, 'Preview/R01/other_preview.png')
    assert cover(project) is None
    asset(project, 'Preview/R01/body_preview.png')
    assert cover(project) == '/api/files/P001/Preview/R01/body_preview.png'


def test_hidden_files_and_missing_or_empty_models_are_ignored(project):
    asset(project, 'Design/R01/.preview.png')
    asset(project, 'Design/R01/preview.png')
    asset(project, 'Design/R01/assembly.glb', b'')
    asset(project, 'Design/R01/brief.txt')
    assert cover(project) is None


def test_unreadable_or_unservable_artifacts_degrade_to_no_cover(project, monkeypatch):
    asset(project, 'Design/R01/assembly.glb')
    asset(project, 'Design/R01/preview.png')
    original = core.safe_path

    def disappearing(base, relative, require_file=True):
        if relative == 'Sources/attachments' or relative.endswith('assembly.glb'):
            raise OSError('File disappeared')
        return original(base, relative, require_file)

    monkeypatch.setattr(core, 'safe_path', disappearing)
    assert cover(project) is None
    monkeypatch.setattr(core, 'safe_path', original)
    monkeypatch.setattr(core, 'public_project_file', lambda *args: (_ for _ in ()).throw(core.WorkflowError('File unavailable')))
    assert cover(project) is None
