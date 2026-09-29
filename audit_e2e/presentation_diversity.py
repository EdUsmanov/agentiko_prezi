"""Conservative artifact evidence for three presentations, independent of decoration.

This proves equivalence on literal, visible source points, never semantic novelty.
Different arrangements still need an independently calibrated semantic review.
"""

from itertools import combinations
import json
import unicodedata

VERSION = "source-organization-and-composition-1"


def _norm(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _kind(obj):
    if obj.get("table_rows"):
        return "table"
    if obj.get("chart"):
        return "chart"
    if obj.get("image"):
        return "image"
    return "text"


def _nodes(slide, points):
    result = []
    uncertain = False
    for obj in slide.get("objects", []):
        if obj.get("visibility") == "uncertain_group_geometry":
            uncertain = True
        if obj.get("visibility") != "visible":
            continue
        text = _norm(obj.get("text", ""))
        ids = tuple(
            p["id"]
            for p in sorted(points, key=lambda p: (text.find(_norm(p["quote"])), p["id"]))
            if _norm(p["quote"]) in text
        )
        kind = _kind(obj)
        if not text and not ids and kind not in {"table", "chart", "image"}:
            continue
        geometry = obj.get("geometry_inches", {})
        if not all(
            isinstance(geometry.get(k), (int, float)) for k in ("x", "y", "width", "height")
        ):
            uncertain = True
            continue
        result.append(
            {
                "points": ids,
                "kind": kind,
                "region": obj.get("name", "unnamed exported object"),
                "geometry": geometry,
                "table": obj.get("table_rows"),
                "content": (
                    text,
                    json.dumps(obj.get("chart"), sort_keys=True),
                    obj.get("image", {}).get("pixels_sha256")
                    or obj.get("image", {}).get("sha256")
                    or "",
                ),
            }
        )
    return result, uncertain


def _signature(nodes):
    # Topology alone is too coarse to prove identical composition. Require the
    # same visible content kinds and exported bounds; decoration is excluded.
    return tuple(
        sorted(
            (
                n["kind"],
                *(n["geometry"][k] for k in ("x", "y", "width", "height")),
                n["content"][1:],
            )
            for n in nodes
        )
    )


def _organizations(nodes):
    # Exact point groups and unambiguous visible block order are necessary evidence.
    return tuple(
        (n["points"], n["content"])
        for n in sorted(nodes, key=lambda n: (n["geometry"]["y"], n["geometry"]["x"]))
    )


def assess_presentation_diversity(bundle):
    points = [
        p for p in bundle.get("reference", {}).get("points", []) if p.get("id") and p.get("quote")
    ]
    variants = bundle.get("variants", {})
    expected = bundle.get("expected_variant_ids", [])
    report = {
        "version": VERSION,
        "status": "inconclusive",
        "organization_status": "inconclusive",
        "composition_status": "inconclusive",
        "semantic_distinctness": "not_certified",
        "rule": "Both source organization and composition must differ; cosmetic differences do not qualify.",
        "pairs": [],
        "limitations": [
            "Literal source-point matching cannot certify paraphrased organization.",
            "Different content arrangements are necessary evidence, not proof of meaningful delivery.",
            "Shared covers and unchanged source tables are allowed.",
        ],
    }
    if (
        len(expected) != 3
        or set(expected) != set(variants)
        or sum(bool(p.get("required")) for p in points) < 2
    ):
        report["reason"] = "insufficient_variants_or_independent_source_points"
        return report
    for left, right in combinations(expected, 2):
        a, b = variants[left].get("slides", []), variants[right].get("slides", [])
        pair = {"variants": [left, right], "status": "inconclusive", "slides": []}
        uncertain = len(a) != len(b)
        organizations = [[], []]
        compositions = [[], []]
        matched = [set(), set()]
        exempt = set()
        for index in range(max(len(a), len(b))):
            left_slide = a[index] if index < len(a) else {}
            right_slide = b[index] if index < len(b) else {}
            na, ua = _nodes(left_slide, points)
            nb, ub = _nodes(right_slide, points)
            uncertain |= ua or ub
            shared_cover = (
                index == 0
                and bool(_norm(left_slide.get("text", "")))
                and _norm(left_slide.get("text", "")) == _norm(right_slide.get("text", ""))
                and _signature(na) == _signature(nb)
                and _organizations(na) == _organizations(nb)
                and not (ua or ub)
            )
            if shared_cover:
                exempt.update(point for n in na for point in n["points"])
                pair["slides"].append({"slide_number": index + 1, "shared_cover_allowed": True})
                continue
            shared_tables = [
                n["table"]
                for n in na
                if n["table"] and any(n["table"] == other["table"] for other in nb)
            ]
            exempt.update(
                point
                for n in na
                if n["table"] and n["table"] in shared_tables
                for point in n["points"]
            )
            row = {
                "slide_number": index + 1,
                "shared_tables_allowed": len(shared_tables),
                "regions": {},
            }
            for side, nodes, name in ((0, na, left), (1, nb, right)):
                nodes = [n for n in nodes if not n["table"] or n["table"] not in shared_tables]
                organizations[side].append(_organizations(nodes))
                compositions[side].append(_signature(nodes))
                matched[side].update(point for n in nodes for point in n["points"])
                row["regions"][name] = [
                    {
                        "region": n["region"],
                        "source_point_ids": list(n["points"]),
                        "kind": n["kind"],
                        "geometry_inches": n["geometry"],
                    }
                    for n in nodes
                ]
            pair["slides"].append(row)
        # Missing/paraphrased points must not be mistaken for proven duplication.
        eligible = matched[0] & matched[1]
        all_required = {p["id"] for p in points if p.get("required")} - exempt
        same_org = organizations[0] == organizations[1]
        same_composition = compositions[0] == compositions[1]
        coverage = all_required <= eligible and len(all_required) >= 2
        pair.update(
            organization="same_literal_point_groups_and_order"
            if same_org
            else "different_literal_grouping_or_order",
            composition="same_content_geometry"
            if same_composition
            else "different_content_geometry",
            organization_status="failed"
            if same_org and coverage and not uncertain
            else "inconclusive",
            composition_status="failed"
            if same_composition and coverage and not uncertain
            else "inconclusive",
            matched_source_point_ids=sorted(eligible),
            source_coverage_complete=coverage,
            uncertain_geometry=uncertain,
        )
        if "failed" in (pair["organization_status"], pair["composition_status"]):
            pair["status"] = "failed"
        report["pairs"].append(pair)
    for category in ("organization", "composition"):
        if any(p[category + "_status"] == "failed" for p in report["pairs"]):
            report[category + "_status"] = "failed"
    if any(p["status"] == "failed" for p in report["pairs"]):
        report["status"] = "failed"
    return report
