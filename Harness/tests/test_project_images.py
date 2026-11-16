import io
from types import SimpleNamespace

from PIL import Image, ImageFile, JpegImagePlugin, PngImagePlugin
import pytest

from printlab import core, project_images as images


def picture(image_format='PNG', size=(20, 12), mode='RGB'):
    content = io.BytesIO()
    image = Image.new(mode, size, (20, 80, 160, 100) if mode == 'RGBA' else (20, 80, 160))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('private-note', 'metadata-must-not-be-forwarded')
    image.save(content, format=image_format, pnginfo=metadata)
    return content.getvalue()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    core.write_json(tmp_path / 'Runtime/config.json', {})
    core.new_project('Reference image project')
    core.new_project('Another project')
    return tmp_path


@pytest.mark.parametrize('image_format,mode', [('PNG', 'RGBA'), ('JPEG', 'RGB'), ('WEBP', 'RGB')])
def test_normalized_reference_round_trip_keeps_project_identity_and_strips_metadata(workspace, image_format, mode):
    metadata = images.save_image('P001', picture(image_format, mode=mode), '中文参考图.' + image_format.lower())
    assert set(metadata) == set(images.PUBLIC_FIELDS)
    assert metadata['width'] == 20 and metadata['height'] == 12
    assert metadata['url'] == f'/api/projects/P001/images/{metadata["id"]}'
    saved, path = images.resolve_image('P001', metadata['id'])
    assert saved == metadata and path.is_relative_to(core.project_path('P001'))
    assert path.parent.name == 'reference-images'
    with Image.open(path) as image:
        assert image.format == image_format and 'private-note' not in image.info and not image.getexif()
        if mode == 'RGBA':
            assert image.getpixel((0, 0))[3] == 100
    assert images.resolve_attachments('P001', [metadata['id']]) == [(metadata, path)]
    assert images.resolve_attachments('P001') == []
    with pytest.raises(images.ImageError):
        images.resolve_image('P002', metadata['id'])


def test_exif_orientation_is_applied_before_removing_metadata(workspace):
    image = Image.new('RGB', (10, 20), 'green')
    exif = image.getexif(); exif[274] = 6
    buffer = io.BytesIO(); image.save(buffer, format='JPEG', exif=exif)
    row = images.save_image('P001', buffer.getvalue(), 'phone.jpg')
    assert (row['width'], row['height']) == (20, 10)
    with Image.open(images.resolve_image('P001', row['id'])[1]) as saved:
        assert not saved.getexif()


def test_jpeg_with_mpo_auxiliary_frame_uses_oriented_primary_photo(workspace):
    primary = Image.new('RGB', (40, 24), 'red')
    exif = primary.getexif(); exif[274] = 6; exif[37500] = b'private-camera-metadata'
    content = io.BytesIO()
    primary.save(content, format='MPO', save_all=True,
                 append_images=[Image.new('RGB', (10, 6), 'blue')], exif=exif)
    with Image.open(io.BytesIO(content.getvalue())) as original:
        assert original.format == 'MPO' and original.n_frames == 2
    row = images.save_image('P001', content.getvalue(), 'phone-hdr.jpeg')
    assert row['content_type'] == 'image/jpeg'
    assert (row['width'], row['height']) == (24, 40)
    _, path = images.resolve_image('P001', row['id'])
    with Image.open(path) as saved:
        assert saved.format == 'JPEG' and getattr(saved, 'n_frames', 1) == 1
        red, green, blue = saved.getpixel((0, 0))
        assert red > 200 and green < 30 and blue < 30
        assert not saved.getexif() and 'mp' not in saved.info
    assert b'MPF\0' not in path.read_bytes()
    assert b'private-camera-metadata' not in path.read_bytes()


@pytest.mark.parametrize('image_format', ['JPEG', 'MPO'])
def test_48mp_phone_photo_is_reduced_before_pixel_decode(workspace, monkeypatch, image_format):
    original = Image.new('RGB', (8064, 6048), 'green')
    content = io.BytesIO()
    options = {'save_all': True, 'append_images': [Image.new('RGB', (10, 6), 'blue')]} if image_format == 'MPO' else {}
    original.save(content, format=image_format, **options)
    original.close()
    loads = []
    original_load = ImageFile.ImageFile.load

    def bounded_load(image, *args, **kwargs):
        loads.append(image.size)
        assert image.width * image.height <= images.MAX_PIXELS
        assert max(image.size) <= images.MAX_DIMENSION
        return original_load(image, *args, **kwargs)

    monkeypatch.setattr(ImageFile.ImageFile, 'load', bounded_load)
    row = images.save_image('P001', content.getvalue(), '48mp.jpeg')
    assert loads and (row['width'], row['height']) == (4032, 3024)
    assert row['size'] <= images.MAX_BYTES
    with Image.open(images.resolve_image('P001', row['id'])[1]) as saved:
        assert saved.format == 'JPEG' and saved.size == (4032, 3024)


def test_jpeg_still_too_large_after_maximum_draft_is_rejected_before_load(workspace, monkeypatch):
    content = picture('JPEG')
    monkeypatch.setattr(images, 'MAX_DIMENSION', 2)
    monkeypatch.setattr(ImageFile.ImageFile, 'load', lambda *args, **kwargs: pytest.fail('Unbounded JPEG decoded'))
    with pytest.raises(images.ImageError) as error:
        images.save_image('P001', content, 'very-wide.jpeg')
    assert error.value.status_code == 413


def test_ignored_jpeg_draft_is_rejected_before_load(workspace, monkeypatch):
    content = picture('JPEG')
    monkeypatch.setattr(images, 'MAX_PIXELS', 100)
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, 'draft', lambda *args: None)
    monkeypatch.setattr(ImageFile.ImageFile, 'load', lambda *args, **kwargs: pytest.fail('Unbounded JPEG decoded'))
    with pytest.raises(images.ImageError) as error:
        images.save_image('P001', content, 'large.jpeg')
    assert error.value.status_code == 413 and error.value.reason == 'pixel_limit'


def test_jpeg_decompression_bomb_warning_is_not_disabled(workspace, monkeypatch):
    content = picture('JPEG')
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 200)
    with pytest.raises(images.ImageError) as error:
        images.save_image('P001', content, 'excessive.jpeg')
    assert error.value.reason == 'invalid_data'


@pytest.mark.parametrize('image_format', ['WEBP', 'GIF'])
def test_animated_webp_and_gif_still_are_rejected(workspace, image_format):
    content = io.BytesIO()
    Image.new('RGB', (10, 6), 'red').save(content, format=image_format, save_all=True,
        append_images=[Image.new('RGB', (10, 6), 'blue')], duration=100, loop=0)
    with pytest.raises(images.ImageError) as error:
        images.save_image('P001', content.getvalue(), 'animation.' + image_format.lower())
    assert error.value.reason in {'animated_image', 'unsupported_format'}


def test_rejection_logs_reason_without_image_content_or_filename(workspace, caplog):
    with caplog.at_level('WARNING', logger='printlab'):
        with pytest.raises(images.ImageError):
            images.save_image('P001', b'PRIVATE_IMAGE_BYTES', 'private-camera-filename.jpeg')
    assert 'reason=invalid_data' in caplog.text
    assert 'PRIVATE_IMAGE_BYTES' not in caplog.text and 'private-camera-filename' not in caplog.text
    caplog.clear()
    with caplog.at_level('WARNING', logger='printlab'):
        with pytest.raises(images.ImageError):
            images.save_image('P001', picture('BMP'), 'private-camera-filename.jpeg')
    assert 'reason=unsupported_format' in caplog.text and 'format=BMP' in caplog.text


@pytest.mark.parametrize('content', [b'', b'not an image', b'<svg xmlns="http://www.w3.org/2000/svg"/>', b'GIF89a'])
def test_bad_image_bytes_never_create_project_files(workspace, content):
    with pytest.raises(images.ImageError):
        images.save_image('P001', content, 'reference.png')
    assert not (core.project_path('P001') / images.DIRECTORY).exists()


@pytest.mark.parametrize('name', ['', ' ', '../secret.png', 'C:\\photo.png', 'a\x00.png', 'a\n.png', 'x' * 201, None])
def test_filename_is_a_display_name_only_not_a_path(workspace, name):
    with pytest.raises(images.ImageError):
        images.save_image('P001', picture(), name)


def test_non_image_and_animated_files_are_rejected(workspace):
    buffer = io.BytesIO(); Image.new('RGB', (4, 4)).save(buffer, format='BMP')
    with pytest.raises(images.ImageError, match='仅支持'):
        images.save_image('P001', buffer.getvalue())
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buffer, format='PNG', save_all=True, append_images=[Image.new('RGB', (4, 4), 'blue')])
    with pytest.raises(images.ImageError, match='静态'):
        images.save_image('P001', buffer.getvalue())


def test_upload_byte_limit_is_bounded(workspace):
    with pytest.raises(images.ImageError) as error:
        images.save_image('P001', b'x' * (images.MAX_BYTES + 1))
    assert error.value.status_code == 413


def test_pixel_limit_rejects_header_before_load(workspace, monkeypatch):
    content = picture(size=(20, 12))
    monkeypatch.setattr(images, 'MAX_PIXELS', 100)
    monkeypatch.setattr(Image.Image, 'load', lambda *args, **kwargs: pytest.fail('Oversized image decoded'))
    with pytest.raises(images.ImageError) as error:
        images.save_image('P001', content)
    assert error.value.status_code == 413


def test_dimension_limit_and_decompression_bomb_are_rejected(workspace, monkeypatch):
    content = picture(size=(20, 12))
    monkeypatch.setattr(images, 'MAX_DIMENSION', 10)
    with pytest.raises(images.ImageError) as error:
        images.save_image('P001', content)
    assert error.value.status_code == 413
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 5)
    with pytest.raises(images.ImageError, match='无法读取'):
        images.save_image('P001', content)


def test_symlinked_reference_folder_and_blob_cannot_escape_project(workspace):
    outside = workspace / 'outside'; outside.mkdir()
    folder = core.project_path('P001') / images.DIRECTORY
    folder.parent.mkdir(exist_ok=True)
    folder.symlink_to(outside, target_is_directory=True)
    with pytest.raises(core.WorkflowError):
        images.save_image('P001', picture())
    assert list(outside.iterdir()) == []
    folder.unlink()
    row = images.save_image('P001', picture())
    _, blob = images.resolve_image('P001', row['id'])
    original = blob.read_bytes()
    external = outside / 'external.png'; external.write_bytes(blob.read_bytes())
    blob.unlink(); blob.symlink_to(external)
    with pytest.raises(images.ImageError):
        images.resolve_image('P001', row['id'])
    assert external.read_bytes() == original


def test_reference_folder_symlink_prevents_even_valid_id_read(workspace):
    row = images.save_image('P001', picture())
    folder = core.project_path('P001') / images.DIRECTORY
    outside = workspace / 'moved-images'
    folder.rename(outside)
    folder.symlink_to(outside, target_is_directory=True)
    with pytest.raises(images.ImageError):
        images.resolve_image('P001', row['id'])


@pytest.mark.parametrize('image_ids', ['x', [1], ['a' * 32] * 2, ['a' * 32] * 7, ['../secret'], ['/tmp/a']])
def test_only_unique_server_ids_are_accepted(workspace, image_ids):
    with pytest.raises(images.ImageError):
        images.resolve_attachments('P001', image_ids)
    with pytest.raises(images.ImageError):
        images.resolve_image('../Runtime', 'a' * 32)


@pytest.mark.parametrize('count', [7, 12])
def test_reference_images_have_no_fixed_per_message_count_limit(workspace, count):
    uploaded = [images.save_image('P001', picture(), f'reference-{index}.png') for index in range(count)]
    resolved = images.resolve_attachments('P001', [image['id'] for image in uploaded])
    assert [metadata for metadata, _ in resolved] == uploaded


@pytest.mark.parametrize('tamper', [
    {'project_id': 'P002'}, {'id': 'b' * 32}, {'format': '../../private'},
    {'url': 'https://evil.example/picture'}, {'width': True}, {'height': 999999},
    {'name': '../other.png'}, {'content_type': 'text/html'}, {'size': 1}, {'sha256': '0' * 64},
])
def test_modified_manifest_cannot_select_another_file_or_project(workspace, tamper):
    row = images.save_image('P001', picture())
    path = core.project_path('P001') / images.DIRECTORY / (row['id'] + '.json')
    metadata = core.read_json(path); metadata.update(tamper); core.write_json(path, metadata)
    with pytest.raises(images.ImageError):
        images.resolve_image('P001', row['id'])


def test_modified_or_missing_image_and_manifest_are_clear(workspace):
    row = images.save_image('P001', picture())
    _, path = images.resolve_image('P001', row['id'])
    path.write_bytes(b'changed')
    with pytest.raises(images.ImageError): images.resolve_image('P001', row['id'])
    path.unlink()
    with pytest.raises(images.ImageError): images.resolve_image('P001', row['id'])
    manifest = path.with_suffix('.json')
    manifest.write_text('x' * 4097)
    with pytest.raises(images.ImageError): images.resolve_image('P001', row['id'])
    manifest.write_text('{invalid')
    with pytest.raises(images.ImageError): images.resolve_image('P001', row['id'])


def test_save_failure_never_removes_a_preexisting_collision(workspace, monkeypatch):
    identity = 'a' * 32
    monkeypatch.setattr(images.uuid, 'uuid4', lambda: SimpleNamespace(hex=identity))
    row = images.save_image('P001', picture())
    _, path = images.resolve_image('P001', row['id'])
    original = path.read_bytes()
    with pytest.raises(images.ImageError): images.save_image('P001', picture())
    assert path.read_bytes() == original


def test_manifest_write_failure_cleans_only_new_blob(workspace, monkeypatch):
    def fail(*args): raise OSError('disk failure')
    monkeypatch.setattr(core, 'write_json', fail)
    with pytest.raises(images.ImageError) as error:
        images.save_image('P001', picture())
    assert error.value.status_code == 503
    assert list((core.project_path('P001') / images.DIRECTORY).iterdir()) == []


def test_reference_directory_write_failure_is_a_clear_error(workspace, monkeypatch):
    from pathlib import Path
    def fail(*args, **kwargs): raise OSError('directory permission denied')
    monkeypatch.setattr(Path, 'mkdir', fail)
    with pytest.raises(images.ImageError) as error:
        images.save_image('P001', picture())
    assert error.value.status_code == 503
