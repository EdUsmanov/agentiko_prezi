"""Optional local demo files. Registration never runs analysis or a model."""

import json
import logging
from pathlib import Path
import re
import shutil

from studio.cache_version import atomic_json
from studio.security import digest, validate_pptx


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
        if (
            not isinstance(rid, str)
            or not re.fullmatch(r"[a-f0-9]{20}", rid)
            or not isinstance(name, str)
            or not name
            or rid in seen
        ):
            continue
        if not (path.parent / rid / "input.pptx").is_file():
            continue
        item = {"id": rid, "name": name}
        if type(row.get("variant_count")) is int and row["variant_count"] in (1, 3):
            item["variant_count"] = row["variant_count"]
        result.append(item)
        seen.add(rid)
    return result


def index_examples(paths, settings, *, single_variant_paths=()):
    """Register raw demos; analyze one only after explicit user selection."""
    root = settings.data_dir / "references"
    single = {Path(p).resolve() for p in single_variant_paths}
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
        item = {"id": rid, "name": source.name}
        if source.resolve() in single:
            item["variant_count"] = 1
        results.append(item)
        seen.add(rid)
    atomic_json(root / "index.json", results)
    return results
