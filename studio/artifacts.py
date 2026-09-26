"""Explicit publication boundary: runtime files are private by default."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

VARIANTS = ('executive', 'analytical', 'story')
REPORTS = ('manifest.json', 'plans.json', 'visual-audit-initial.json',
           'visual-audit.json', 'refinement.json')
DECK_FILES = ('deck.pptx', 'deck.pdf', 'deck.html', 'slides.json', 'FONT_LICENSES.txt')


class PublicationRollbackError(RuntimeError):
    """An incomplete filesystem rollback must fail the job, not return old metadata."""


def publish_variants(directory, staging, keys):
    """Swap reviewed directories; roll back every swap on a publication failure.

    Jobs remain non-public while running. Renames on the same filesystem avoid
    partly copied files and stale preview pages. Originals stay private for recovery.
    """
    directory=Path(directory).resolve();staging=Path(staging)
    keys=list(dict.fromkeys(keys))
    if (not keys or not set(keys)<=set(VARIANTS) or staging.is_symlink()
            or staging.resolve()==directory or not staging.resolve().is_relative_to(directory)):
        raise ValueError('Invalid publication scope')
    for key in keys:
        if (not (staging/key).is_dir() or (staging/key).is_symlink()
                or (directory/key).is_symlink() or (directory/key).exists() and not (directory/key).is_dir()):
            raise ValueError('Invalid variant directory')
    backup=staging/'.originals';backup.mkdir()
    moved=[];published=[]
    try:
        for key in keys:
            if (directory/key).exists():
                (directory/key).rename(backup/key);moved.append(key)
            (staging/key).rename(directory/key);published.append(key)
    except Exception:
        failures=[]
        for key in reversed(keys):
            try:
                if key in published:
                    (directory/key).rename(staging/key)
                if key in moved:
                    (backup/key).rename(directory/key)
            except OSError as exc:
                failures.append(exc)
        if failures:
            raise PublicationRollbackError('Could not restore original artifacts; job must not be published') from failures[0]
        raise


def public_path(root, filename):
    """Resolve an already allowlisted artifact without following any symlink."""
    relative = Path(filename)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        return None
    root = Path(root).resolve()
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            return None
    if not path.is_file() or not path.resolve().is_relative_to(root):
        return None
    return path


def package_results(directory):
    directory = Path(directory)
    names = list(REPORTS) + [f'{variant}/{name}' for variant in VARIANTS for name in DECK_FILES]
    # Temporary file prevents a partly written archive being published.
    temporary = directory / 'presentations.zip.tmp'
    if temporary.is_symlink():
        raise ValueError('Unsafe archive staging path')
    with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
        for name in names:
            path = public_path(directory, name)
            if path is not None:
                archive.write(path, name)
    temporary.replace(directory / 'presentations.zip')
