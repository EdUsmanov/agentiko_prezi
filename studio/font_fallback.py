"""Choose explicit, export-safe faces before measuring newly supplied text."""

from pathlib import Path
from fontTools.ttLib import TTLibError

from .config import ROOT
from .embedded_fonts import check_glyphs, inspect_font
from .font_identity import binary_identity, matches_face
from .fonts import coverage, font_runs, resolve_font
from .security import InputRejected, digest
from .text_layout import WORD_JOINERS


def missing_characters(path, text):
    missing = set()
    for fragment, face in font_runs(text, path):
        supported = coverage(face)
        missing.update(
            ord(c)
            for c in set(fragment)
            if not c.isspace() and c not in WORD_JOINERS and ord(c) not in supported
        )
    return sorted(missing)


def content_text(content, plans=None):
    # Include section/table labels as well as their values: all may become visible.
    values = [content.title]
    for fact in content.facts:
        values.extend([fact.text, fact.section])
    for table in content.tables:
        values.extend(str(cell) for row in [table.headers] + table.rows for cell in row)
        values.append(getattr(table, "title", ""))
    if plans:
        values.extend(slide.title for variant in plans.variants for slide in variant.slides)
    return "\n".join(str(value) for value in values if value)


def replacement_asset(asset, text):
    # Prefer a complete copy of the same exact face; otherwise use shipped fonts.
    weight = asset.get("weight", 400)
    style = "Bold" if weight >= 600 else "Medium" if weight >= 450 else "Regular"
    candidates = [
        (resolve_font(asset["requested"]), "local"),
        (str(ROOT / "fonts" / f"Montserrat-{style}.ttf"), "bundled"),
    ]
    for path, source in candidates:
        if not path or path == asset["path"]:
            continue
        try:
            raw = Path(path).read_bytes()
            inspect_font(raw)  # Do not ship restricted or variable font programs.
            if source == "local" and not matches_face(raw, asset["requested"]):
                continue
            check_glyphs(path, text)
            identity = binary_identity(raw)
            name = identity["family"]
            if identity["style"].lower() not in ("regular", "normal", "roman", "book"):
                name += " " + identity["style"]
            sha = digest(raw)
            return {
                "id": sha,
                "requested": name,
                "path": path,
                "sha256": sha,
                "bytes": len(raw),
                "status": "substituted",
                "weight": identity["weight"],
                "italic": identity["italic"],
                "redistributable": True,
                "fs_type": 0,
                "origin": {"kind": "glyph_fallback", "source": source, "sha256": sha},
            }
        except (ValueError, OSError, TTLibError):
            continue
    codes = ", ".join(f"U+{c:04X}" for c in missing_characters(asset["path"], text)[:8])
    raise InputRejected(
        f"В шрифте «{asset['requested']}» нет символов нового текста ({codes}). "
        "Доступная автоматическая замена тоже не покрывает весь текст. "
        "Нужен шрифт с поддержкой этих символов."
    )


def ensure_text_fonts(profile, text, directory=None):
    """Replace entire faces, including authored-field bindings, before composition.

    Work on a copy first: an unsupported character must never leave half a mapping.
    Source template font reports remain intact; generation decisions are separate.
    """
    assets = []
    mapping = {}
    records = list(profile.font_substitutions)
    primary_id = next(
        (a["id"] for a in profile.font_assets if a["path"] == profile.font_file), None
    )
    for original in profile.font_assets:
        asset = dict(original)
        missing = missing_characters(asset["path"], text)
        if missing:
            asset = replacement_asset(original, text)
            aliases = list(
                dict.fromkeys([original["requested"], *original.get("template_aliases", [])])
            )
            asset["template_aliases"] = aliases
            records.append(
                {
                    "scope": "font",
                    "template_font": original["requested"],
                    "fallback_font": asset["requested"],
                    "reason": "missing_glyphs",
                    "missing_codepoints": [f"U+{c:04X}" for c in missing[:8]],
                    "missing_count": len(missing),
                    "style_changed": (original.get("weight", 400), original.get("italic", False))
                    != (asset["weight"], asset["italic"]),
                }
            )
        mapping[original["id"]] = asset["id"]
        existing = next((a for a in assets if a["id"] == asset["id"]), None)
        if existing is not None:
            existing["template_aliases"] = list(
                dict.fromkeys(
                    [*existing.get("template_aliases", []), *asset.get("template_aliases", [])]
                )
            )
        else:
            assets.append(asset)
    profile.font_assets = assets
    profile.font_roles = {role: mapping.get(key, key) for role, key in profile.font_roles.items()}
    primary = next((a for a in assets if a["id"] == mapping.get(primary_id)), None)
    if primary:
        profile.font, profile.font_file, profile.font_origin = (
            primary["requested"],
            primary["path"],
            primary["origin"],
        )
    profile.fonts = list(dict.fromkeys([*profile.fonts, *[a["requested"] for a in assets]]))
    from .font_disclosure import unique

    profile.font_substitutions = unique(records)
    if directory and profile.font_substitutions:
        import json
        from .cache_version import atomic_json

        path = Path(directory) / "font-model.json"
        report = json.loads(path.read_text()) if path.is_file() else {}
        report["generation"] = {
            "roles": profile.font_roles,
            "substitutions": profile.font_substitutions,
            "assets": [{k: v for k, v in a.items() if k != "path"} for a in assets],
        }
        atomic_json(path, report)
    return profile.font_substitutions
