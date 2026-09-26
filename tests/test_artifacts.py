from zipfile import ZipFile

from studio.artifacts import package_results, public_path


def test_archive_only_publishes_known_deliverables(tmp_path):
    (tmp_path / 'executive').mkdir()
    (tmp_path / 'executive/deck.pptx').write_bytes(b'public deck')
    (tmp_path / 'manifest.json').write_text('{}')
    (tmp_path / 'request.json').write_text('private model request')
    (tmp_path / 'generation-content.json').write_text('private source content')
    (tmp_path / 'executive/private.json').write_text('private debug data')
    (tmp_path / 'refinement-1').mkdir()
    (tmp_path / 'refinement-1/deck.pptx').write_bytes(b'obsolete')
    package_results(tmp_path)
    with ZipFile(tmp_path / 'presentations.zip') as archive:
        assert set(archive.namelist()) == {'manifest.json', 'executive/deck.pptx'}


def test_public_files_reject_symlinks_and_traversal(tmp_path):
    public = tmp_path / 'job'
    public.mkdir()
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'deck.pptx').write_bytes(b'private')
    (public / 'manifest.json').symlink_to(private / 'deck.pptx')
    (public / 'executive').symlink_to(private, target_is_directory=True)
    for name in ('manifest.json', 'executive/deck.pptx', '../private/deck.pptx', str(private / 'deck.pptx')):
        assert public_path(public, name) is None
    package_results(public)
    with ZipFile(public / 'presentations.zip') as archive:
        assert not archive.namelist()


def test_archive_cannot_follow_staging_symlink(tmp_path):
    import pytest
    private = tmp_path / 'private.txt'
    private.write_text('unchanged')
    (tmp_path / 'presentations.zip.tmp').symlink_to(private)
    with pytest.raises(ValueError, match='Unsafe'):
        package_results(tmp_path)
    assert private.read_text() == 'unchanged'

