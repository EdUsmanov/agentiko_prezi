from pathlib import Path
import pytest
from studio.artifacts import publish_variants, PublicationRollbackError


def seed(root, keys=('executive', 'analytical')):
    stage=root/'repair';stage.mkdir()
    for key in keys:
        (root/key).mkdir();(root/key/'deck.pptx').write_bytes(b'original')
        (root/key/'old.png').write_bytes(b'old')
        (stage/key).mkdir();(stage/key/'deck.pptx').write_bytes(b'reviewed')
    return stage


def test_verified_revision_replaces_whole_directory(tmp_path):
    stage=seed(tmp_path)
    publish_variants(tmp_path,stage,['executive','analytical'])
    for key in ('executive','analytical'):
        assert (tmp_path/key/'deck.pptx').read_bytes()==b'reviewed'
        assert not (tmp_path/key/'old.png').exists()
        assert (stage/'.originals'/key/'deck.pptx').read_bytes()==b'original'


def test_failed_publication_restores_every_variant(tmp_path,monkeypatch):
    stage=seed(tmp_path);original=Path.rename
    def rename(path,target):
        if path==stage/'analytical':
            raise OSError('simulated publication failure')
        return original(path,target)
    monkeypatch.setattr(Path,'rename',rename)
    with pytest.raises(OSError):
        publish_variants(tmp_path,stage,['executive','analytical'])
    for key in ('executive','analytical'):
        assert (tmp_path/key/'deck.pptx').read_bytes()==b'original'
        assert (stage/key/'deck.pptx').read_bytes()==b'reviewed'


def test_rollback_failure_cannot_be_reported_as_unchanged_result(tmp_path,monkeypatch):
    stage=seed(tmp_path);original=Path.rename
    def rename(path,target):
        if path==stage/'analytical' or '.originals' in path.parts:
            raise OSError('simulated disk failure')
        return original(path,target)
    monkeypatch.setattr(Path,'rename',rename)
    with pytest.raises(PublicationRollbackError):
        publish_variants(tmp_path,stage,['executive','analytical'])


def test_only_server_owned_variant_directories_can_be_published(tmp_path):
    stage=seed(tmp_path)
    with pytest.raises(ValueError):
        publish_variants(tmp_path,stage,['../executive'])
    with pytest.raises(ValueError):
        publish_variants(tmp_path,tmp_path,['executive'])

