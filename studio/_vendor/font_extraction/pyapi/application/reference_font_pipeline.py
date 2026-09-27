"""Resolve reference font usage into a role-specific model and usable font files."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from collections import defaultdict

from ..domain.font_model import (
    FontAsset,
    FontRoleSummary,
    FontUse,
    MissingFont,
    PresentationFontData,
    SlideFontData,
)
from ..domain.font_names import base_font_family
from ..infrastructure.font_downloads import _matches
from ..pipeline.reference_font_usage import extract_reference_font_usage
from ..pipeline.reference_fonts import (
    SFNT_FORMATS,
    _font_permissions,
    _installed_fonts,
    _may_repackage,
    _style_weight,
    reference_font_assets,
)
from .ports import FontDecoder, FontVariantResolver

logger = logging.getLogger(__name__)
FontKey = tuple[str, int, str]


def _variant(item: dict) -> FontKey:
    family = item["family"]
    named_weight, named_style = _style_weight(family)
    weight = item.get("weight") or named_weight
    if named_weight != 400 and weight == 400:
        weight = named_weight
    style = "italic" if item.get("italic") or named_style == "italic" else "normal"
    return family, weight, style


def _asset_matches(asset: dict, key: FontKey) -> bool:
    family, weight, style = key
    return (
        str(asset.get("family", "")).casefold()
        in {family.casefold(), base_font_family(family).casefold()}
        and int(asset.get("weight") or 400) == weight
        and str(asset.get("style") or "normal") == style
        and _matches(asset["binary"], family, weight, style)
    )


def _validated_asset(asset: dict) -> dict | None:
    try:
        font = (
            asset["data"]
            if isinstance(asset["data"], bytes)
            else base64.b64decode(asset["data"], validate=True)
        )
        if font[:4] not in SFNT_FORMATS or not _may_repackage(_font_permissions(font)):
            return None
        return {**asset, "binary": font}
    except (KeyError, TypeError, ValueError):
        logger.warning(
            "Font asset failed validation",
            extra={"event": "pipeline.font_model.invalid_asset", "stage": "font-model"},
            exc_info=True,
        )
        return None


def _observed_roles(slide: dict) -> dict[int, tuple[str, str]]:
    """Infer only roles supported by relative font size; keep ambiguous text neutral."""
    used = slide["fontUses"]
    others = [item for item in used if item["role"] == "other" and item["sizesPt"]]
    if any(item["role"] == "title" for item in [*used, *slide["fontHints"]]):
        return {}
    sizes = sorted({max(item["sizesPt"]) for item in others})
    if not others or (slide["number"] != 1 and len(sizes) < 2):
        return {}
    biggest = sizes[-1]
    assignments = {}
    for item in others:
        if max(item["sizesPt"]) == biggest:
            assignments[id(item)] = ("title", "size-heuristic")
        elif len(sizes) > 1 and not any(row["role"] == "body" for row in used):
            assignments[id(item)] = ("body", "text-heuristic")
    return assignments


def _asset_file(key: FontKey, asset: dict) -> tuple[FontAsset, bytes]:
    family, weight, style = key
    binary = asset["binary"]
    digest = hashlib.sha256(binary).hexdigest()
    identifier = hashlib.sha256(f"{family}|{weight}|{style}|{digest}".encode()).hexdigest()[:20]
    extension = SFNT_FORMATS[binary[:4]][0]
    slug = re.sub(r"[^a-z0-9]+", "-", family.casefold()).strip("-")[:40] or "font"
    source = {"pptx": "embedded", "installed": "installed"}.get(asset.get("source"), "downloaded")
    model = FontAsset(
        id=identifier,
        family=family,
        resolvedFamily=str(asset.get("resolvedFamily") or asset["family"]),
        weight=weight,
        style=style,
        source=source,
        origin=str(asset.get("source")) if source == "downloaded" else None,
        path=f"fonts/{slug}-{weight}-{style}-{identifier}.{extension}",
        sha256=digest,
        bytes=len(binary),
    )
    return model, binary


def _summarize(slides: list[SlideFontData]) -> dict[str, dict[str, list[FontRoleSummary]]]:
    grouped: dict[tuple, dict] = {}
    for slide in slides:
        for role, choices in (slide.elements or {}).items():
            for item in choices:
                key = (
                    slide.kind,
                    role,
                    item.family,
                    item.script,
                    item.weight,
                    item.style,
                    item.assetId,
                    item.status,
                )
                record = grouped.setdefault(
                    key,
                    {"sizes": set(), "slides": set(), "sources": set(), "evidence": set()},
                )
                record["sizes"].update(item.sizesPt or [])
                record["slides"].add(slide.number)
                record["sources"].add(item.source)
                record["evidence"].add(item.evidence)
    result: dict[str, dict[str, list[FontRoleSummary]]] = {}
    for key, record in sorted(grouped.items(), key=lambda entry: str(entry[0])):
        kind, role, family, script, weight, style, asset_id, status = key
        result.setdefault(kind, {}).setdefault(role, []).append(
            FontRoleSummary(
                family=family,
                script=script,
                weight=weight,
                style=style,
                sizesPt=sorted(record["sizes"]) or None,
                slideNumbers=sorted(record["slides"]),
                sources=sorted(record["sources"]),
                evidence=sorted(record["evidence"]),
                assetId=asset_id,
                status=status,
            )
        )
    return result


async def build_reference_font_data(
    data: bytes,
    presentation_name: str,
    decoder: FontDecoder,
    resolver: FontVariantResolver,
) -> tuple[PresentationFontData, dict[str, bytes]]:
    """Inventory real elements, resolve required variants, and return files by relative path."""
    logger.info(
        "Reference font model started",
        extra={"event": "pipeline.font_model.started", "stage": "font-model"},
    )
    inventory = extract_reference_font_usage(data, presentation_name)
    required: set[FontKey] = {
        _variant(item)
        for slide in inventory["slides"]
        for field in ("fontUses", "fontHints")
        for item in slide[field]
    }
    embedded, warnings = await reference_font_assets(data, set(), decoder)
    installed, _ = await _installed_fonts({family for family, _, _ in required})
    available = [valid for item in [*embedded, *installed] if (valid := _validated_asset(item))]
    selected: dict[FontKey, dict] = {}
    for key in sorted(required):
        match = next((asset for asset in available if _asset_matches(asset, key)), None)
        if match is not None:
            selected[key] = match
    missing = required - selected.keys()
    if missing:
        requests: dict[str, set[tuple[int, str]]] = defaultdict(set)
        for family, weight, style in missing:
            requests[family].add((weight, style))
        downloaded = await resolver.resolve_variants(dict(requests))
        valid_downloads = [valid for item in downloaded if (valid := _validated_asset(item))]
        for key in sorted(missing):
            match = next(
                (
                    asset
                    for asset in valid_downloads
                    if str(asset.get("family", "")).casefold() == key[0].casefold()
                    and _asset_matches(asset, key)
                ),
                None,
            )
            if match is not None:
                selected[key] = match
    unresolved = sorted(required - selected.keys())
    asset_models: dict[FontKey, FontAsset] = {}
    binaries: dict[str, bytes] = {}
    for key, asset in sorted(selected.items()):
        model, binary = _asset_file(key, asset)
        asset_models[key] = model
        binaries[model.path] = binary

    slide_models = []
    for slide in inventory["slides"]:
        role_overrides = _observed_roles(slide)
        elements: dict[str, list[FontUse]] = defaultdict(list)
        for field, default_evidence in (("fontUses", "observed"), ("fontHints", "placeholder")):
            for item in slide[field]:
                role, evidence = role_overrides.get(
                    id(item),
                    ("text" if item["role"] == "other" else item["role"], default_evidence),
                )
                key = _variant(item)
                asset = asset_models.get(key)
                elements[role].append(
                    FontUse(
                        family=key[0],
                        script=item["script"],
                        weight=key[1],
                        style=key[2],
                        sizesPt=item["sizesPt"] or None,
                        source=item["source"],
                        evidence=evidence,
                        shapeIds=item["shapeIds"],
                        characters=item["characters"] or None,
                        assetId=asset.id if asset else None,
                        status=asset.source if asset else "missing",
                    )
                )
        slide_models.append(
            SlideFontData(
                number=slide["number"],
                kind="title" if slide["number"] == 1 else "content",
                elements=dict(elements) or None,
            )
        )
    for family, weight, style in unresolved:
        warning = f"Шрифт {family} ({weight}, {style}) недоступен: приложите TTF/OTF"
        warnings.append(warning)
        logger.warning(
            warning,
            extra={
                "event": "pipeline.font_model.missing",
                "stage": "font-model",
                "item_key": family,
            },
        )
    result = PresentationFontData(
        presentation=presentation_name,
        slides=slide_models,
        usageBySlideKind=_summarize(slide_models),
        fontAssets=sorted(
            asset_models.values(), key=lambda item: (item.family, item.weight, item.style)
        ),
        unresolved=[
            MissingFont(family=family, weight=weight, style=style)
            for family, weight, style in unresolved
        ],
        warnings=warnings,
    )
    logger.info(
        "Reference font model completed",
        extra={
            "event": "pipeline.font_model.completed",
            "stage": "font-model",
            "slide_count": len(slide_models),
            "font_count": len(asset_models),
            "missing_count": len(unresolved),
        },
    )
    return result, binaries
