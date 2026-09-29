"""Select a complete face for new text after the reference font kit runs."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fontTools.ttLib import TTLibError

from studio.config import ROOT
from studio.templates.font_identity import binary_identity, matches_face
from studio.templates.fonts import coverage, font_runs, resolve_font
from studio.security import InputRejected
from studio.composition.text_layout import WORD_JOINERS


def content_text(content, plans=None):
    values = [content.title]
    for fact in content.facts:
        values.extend((fact.text, fact.section))
    for table in content.tables:
        values.append(getattr(table, "title", ""))
        values.extend(cell for row in [table.headers] + table.rows for cell in row)
    if plans:
        values.extend(slide.title for variant in plans.variants for slide in variant.slides)
    return "\n".join(str(value) for value in values if value)


def missing_codepoints(path, text):
    return sorted(
        {
            ord(char)
            for fragment, face in font_runs(text, path)
            for char in fragment
            if not char.isspace() and char not in WORD_JOINERS and ord(char) not in coverage(face)
        }
    )


def _complete_face(original, text):
    weight = int(original.get("weight") or 400)
    style = "Bold" if weight >= 600 else "Medium" if weight >= 450 else "Regular"
    candidates = (
        (resolve_font(original["requested"]), "local"),
        (str(ROOT / "fonts" / f"Montserrat-{style}.ttf"), "bundled"),
    )
    for candidate, source in candidates:
        if not candidate or candidate == original["path"]:
            continue
        try:
            path = Path(candidate)
            raw = path.read_bytes()
            if source == "local" and not matches_face(raw, original["requested"]):
                continue
            if missing_codepoints(path, text):
                continue
            identity = binary_identity(raw)
            name = identity["family"]
            if identity["style"].casefold() not in {"regular", "normal", "roman", "book"}:
                name += " " + identity["style"]
            digest = hashlib.sha256(raw).hexdigest()
            return {
                "id": digest[:20],
                "requested": name,
                "family": identity["family"],
                "path": str(path),
                "sha256": digest,
                "bytes": len(raw),
                "status": "substituted",
                "weight": identity["weight"],
                "style": "italic" if identity["italic"] else "normal",
                "italic": identity["italic"],
                "redistributable": source == "bundled",
                "origin": {"kind": "glyph_fallback", "source": source, "sha256": digest},
            }
        except (OSError, ValueError, TTLibError):
            continue
    codes = ", ".join(f"U+{code:04X}" for code in missing_codepoints(original["path"], text)[:8])
    raise InputRejected(
        f"В шрифте «{original['requested']}» нет символов нового текста ({codes}), "
        "а доступная замена не покрывает весь текст. Добавьте TTF/OTF с этими символами."
    )


def ensure_text_coverage(profile, text):
    """Replace incomplete output faces while keeping the kit's source model intact."""
    original_assets = profile.font_assets
    primary = next((a for a in original_assets if a["path"] == profile.font_file), None)
    selected = []
    mapping = {}
    records = list(profile.font_replacements)
    replaced_faces = {}
    for original in original_assets:
        missing = missing_codepoints(original["path"], text)
        asset = _complete_face(original, text) if missing else dict(original)
        if missing:
            replaced_faces[original["requested"]] = asset
            asset["template_aliases"] = list(
                dict.fromkeys([original["requested"], *original.get("template_aliases", [])])
            )
            records.append(
                {
                    "scope": "font",
                    "template_font": original["requested"],
                    "fallback_font": asset["requested"],
                    "reason": "missing_glyphs",
                    "missing_codepoints": [f"U+{code:04X}" for code in missing[:8]],
                    "missing_count": len(missing),
                    "style_changed": (original.get("weight", 400), original.get("italic", False))
                    != (asset["weight"], asset["italic"]),
                }
            )
        mapping[original["id"]] = asset["id"]
        existing = next((item for item in selected if item["id"] == asset["id"]), None)
        if existing is None:
            selected.append(asset)
        else:
            existing["template_aliases"] = list(
                dict.fromkeys(
                    [*existing.get("template_aliases", []), *asset.get("template_aliases", [])]
                )
            )
    for item in profile.missing_fonts:
        if final := replaced_faces.get(item.get("substituted_by")):
            item["substituted_by"] = final["requested"]
    missing_by_name = {item["requested"]: item for item in profile.missing_fonts}
    for record in records:
        if final := replaced_faces.get(record["fallback_font"]):
            record["fallback_font"] = final["requested"]
            if missing := missing_by_name.get(record["template_font"]):
                record["style_changed"] = (missing["weight"], missing["style"]) != (
                    final["weight"],
                    final["style"],
                )
    profile.font_assets = selected
    profile.font_roles = {role: mapping.get(key, key) for role, key in profile.font_roles.items()}
    chosen = next(
        (asset for asset in selected if primary and asset["id"] == mapping[primary["id"]]),
        None,
    )
    if chosen:
        profile.font = chosen["requested"]
        profile.font_file = chosen["path"]
        profile.font_origin = chosen["origin"]
    profile.fonts = list(dict.fromkeys([*profile.fonts, *(a["requested"] for a in selected)]))
    profile.font_replacements = list(
        {(item["template_font"], item["fallback_font"]): item for item in records}.values()
    )
    return profile.font_replacements
