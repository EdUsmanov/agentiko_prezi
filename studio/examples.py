"""Optional local demo files. Registration never runs analysis or a model."""
import json
import logging
from pathlib import Path
import re
import shutil

from .cache_version import atomic_json
from .security import digest, validate_pptx


def sources(settings):
    path = settings.data_dir / "references/index.json"
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text())
        if not isinstance(rows, list):
            raise ValueError("Expected a list of demo files")
    except (OSError, ValueError):
        logging.getLogger(__name__).warning("Cannot read optional demo index")
        return []
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid, name = row.get("id"), row.get("name")
        if (not isinstance(rid, str) or not re.fullmatch(r"[a-f0-9]{20}", rid)
                or not isinstance(name, str) or not name or rid in seen):
            continue
        if not (path.parent / rid / "input.pptx").is_file():
            continue
        result.append({"id": rid, "name": name})
        seen.add(rid)
    return result


def index_examples(paths, settings):
    """Register raw demos; analyze one only after explicit user selection."""
    root = settings.data_dir / "references"
    results, seen = [], set()
    for source in map(Path, paths):
        validate_pptx(source)
        rid = digest(source.read_bytes())[:20]
        if rid in seen:
            continue
        folder = root / rid
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "input.pptx"
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        results.append({"id": rid, "name": source.name})
        seen.add(rid)
    atomic_json(root / "index.json", results)
    return results
