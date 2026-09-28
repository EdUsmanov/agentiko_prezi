"""Shared semantic contracts for planning, composition and bounded repair.

No model output here is executable. References are opaque server-owned IDs.
"""

from .content_sources import (
    normalized as normalized,
    body_and_title_sources as body_and_title_sources,
)

import re
from .archetype_catalog import CATALOG


def compatible(pattern, slide, index=0):
    if not pattern.reusable or pattern.purpose in ("service", "reference"):
        return False
    purpose = slide.purpose
    if pattern.graphic_kind in (
        "sequence",
        "hierarchy",
        "radial",
        "pyramid",
        "comparison",
        "matrix",
    ):
        allowed = {
            "sequence": {"process", "timeline"},
            "hierarchy": {"structure"},
            "pyramid": {"structure"},
            "radial": {"structure", "composition"},
            "comparison": {"comparison"},
            "matrix": {"comparison", "structure"},
        }
        if purpose not in allowed[pattern.graphic_kind]:
            return False
    if slide.layout == "divider" or purpose == "divider":
        return pattern.role == "divider" and pattern.purpose in ("unknown", "divider")
    if purpose == "cover":
        return (
            pattern.purpose == "cover" or pattern.purpose == "unknown" and pattern.role == "cover"
        )
    if pattern.role == "divider" or pattern.purpose == "divider":
        return False
    if pattern.purpose == "cover" or pattern.role == "cover":
        return purpose == "auto" and index == 0
    # A native table carries its own quantitative semantics. Metric and trend
    # exemplars are interchangeable here when their real field geometry fits;
    # rejecting both in favour of a tiny generic content card causes overflow.
    # A claim-with-evidence template is also a valid home for quantitative
    # evidence; the object contract still checks its actual chart/table capacity.
    if slide.table_id and pattern.purpose in ("content", "metrics", "trend", "claim_evidence"):
        return True
    # A verified ordered path can represent dated events or undated steps.
    if pattern.graphic_kind == "sequence" and pattern.purpose in (
        "process",
        "timeline",
        "unknown",
        "content",
    ):
        return purpose in ("process", "timeline")
    if pattern.purpose in CATALOG and pattern.purpose != "content":
        return purpose == pattern.purpose or (purpose == "auto" and slide.layout == pattern.purpose)
    return True


def candidates(package, slide, index=0, *, source_slides_only=False, prefer_specialized=True):
    available = [
        p
        for p in package.template.patterns
        if p.title_zone
        and (p.body_zones or slide.purpose == "cover" or slide.layout == "divider")
        and compatible(p, slide, index)
        and (not source_slides_only or p.source_slide and p.fields)
    ]
    if any(p.graphic_kind != "none" for p in available):
        from .semantic_bindings import content_groups, structure_matches

        binding = content_groups(slide, package)
        groups = binding["groups"]
        available = [
            p
            for p in available
            if p.graphic_kind == "none"
            or (
                not slide.table_id
                and binding["status"] == "specialized"
                and len(p.body_zones) == len(groups)
                and structure_matches(p, groups)
            )
        ]
    # Summarized text can be re-edited to fit. Do not silently discard an
    # exactly matched authored graphic just because the first wording is long:
    # the editorial geometry gate sends its real field limits back to the model.
    # Non-summarized evidence retains the existing readable-layout fallback.
    if prefer_specialized and not slide.table_id and package.analysis.get("editorial"):
        graphics = [p for p in available if p.source_slide and p.graphic_kind != "none"]
        if graphics:
            return graphics
    if prefer_specialized and not slide.table_id:
        from .semantic_bindings import content_groups
        from .fonts import role_font, wrap_text

        semantic = content_groups(slide, package)
        if semantic["status"] != "specialized":
            available = [p for p in available if p.graphic_kind == "none"]
        if semantic["status"] == "specialized":
            groups = semantic["groups"]
            from .semantic_bindings import structure_matches

            available = [
                p
                for p in available
                if p.graphic_kind == "none"
                or len(p.body_zones) == len(groups)
                and structure_matches(p, groups)
            ]
            font = role_font(package.template, "body")[1]
            exact = []
            for pattern in available:
                if len(pattern.body_zones) < len(groups):
                    continue
                fits = True
                for i, (zone, group) in enumerate(zip(pattern.body_zones, groups)):
                    heading = pattern.heading_zones[i] if i < len(pattern.heading_zones) else None
                    reserve = 0 if heading else 24
                    lines = sum(
                        len(wrap_text(f.text, font, 12, max(1, zone.w - 16)))
                        for f in group["facts"]
                    )
                    if lines * 15 + reserve > zone.h:
                        fits = False
                        break
                if fits:
                    exact.append(pattern)
            if exact:
                return exact
    if (
        prefer_specialized
        and package.analysis.get("editorial")
        and getattr(getattr(package, "constraints", None), "summarize", False)
    ):
        authored = [p for p in available if p.source_slide and p.fields]
        if authored:
            return authored
    return available


def apply_meanings(profile, semantics):
    by_id = {m["pattern_id"]: m for m in semantics.get("patterns", [])}
    for pattern in profile.patterns:
        meaning = by_id.get(pattern.id)
        if not meaning:
            continue
        order = meaning.get("body_order", [])
        body_fields = {f["shape_id"]: f["index"] for f in pattern.fields if f["role"] == "body"}
        if (
            meaning.get("graphic_flow_confirmed")
            and len(order) >= 2
            and len(order) == len(body_fields)
            and set(order) == set(body_fields)
        ):
            indices = [body_fields[identity] for identity in order]
            for name in (
                "body_zones",
                "heading_zones",
                "number_zones",
                "zone_foregrounds",
                "zone_backgrounds",
            ):
                values = getattr(pattern, name)
                if len(values) == len(indices):
                    setattr(pattern, name, [values[i] for i in indices])
            mapping = {old: new for new, old in enumerate(indices)}
            for field in pattern.fields:
                if field["role"] in ("body", "heading", "number") and field["index"] in mapping:
                    field["index"] = mapping[field["index"]]
            pattern.text_zones = [pattern.title_zone] + pattern.body_zones
            pattern.graphic_order_verified = True
            pattern.graphic_kind = meaning.get("graphic_kind", "none")
            pattern.graphic_shape_ids = meaning.get("graphic_shape_ids", [])
            pattern.graphic_edges = meaning.get("graphic_edges", [])
        pattern.purpose = meaning.get("purpose", "unknown")
        pattern.reusable = meaning.get("reusable", True) and pattern.purpose not in (
            "service",
            "reference",
        )
        if (
            pattern.purpose == "divider"
            and pattern.body_zones
            and not pattern.source_slide
            and not re.search(r"section|divider|раздел", pattern.source_layout, re.I)
        ):
            observed = any(
                p.source_slide
                and p.source_layout == pattern.source_layout
                and by_id.get(p.id, {}).get("purpose") == "divider"
                for p in profile.patterns
            )
            if not observed:
                pattern.purpose = "unknown"
        if pattern.purpose in ("cover", "divider"):
            pattern.role = pattern.purpose
        elif pattern.role == "divider" or pattern.role == "cover" and pattern.purpose != "unknown":
            # Geometry alone cannot turn a resource page into a chapter break.
            pattern.role = "statement"
