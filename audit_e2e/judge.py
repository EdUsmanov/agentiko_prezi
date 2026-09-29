"""Independent, blind presentation judging through an ephemeral Codex session."""

from __future__ import annotations

import hashlib
import json
import math
import os
import copy
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .reporting import write_json

MODEL = "gpt-6-luna"
MODEL_REASONING_EFFORT = "max"
SCHEMA_VERSION = 1
PROMPT_VERSION = "presentation-judge-2026-09-29.4"
WORKER_SCHEMA_VERSION = "case-bound-opaque-ids-v3"
MAX_PARALLEL_WORKERS = 3
FINDING_CATEGORIES = (
    "omission",
    "number",
    "negation",
    "condition",
    "table_binding",
    "units",
    "provenance",
    "hidden_text",
    "duplicates",
    "template_fidelity",
    "readability",
    "unsupported",
    "overflow",
    "overlap",
    "missing_element",
)
POSITIVE_CONTROL_CATEGORIES = ("clean", "paraphrase")
DEFECT_CATEGORIES = (
    "omission",
    "number",
    "negation",
    "condition",
    "table_binding",
    "units",
    "provenance",
    "hidden_text",
    "duplicates",
    "template_fidelity",
    "readability",
)
CONTROL_CATEGORIES = POSITIVE_CONTROL_CATEGORIES + DEFECT_CATEGORIES
SEMANTIC_FINDINGS = {
    "omission",
    "number",
    "negation",
    "condition",
    "table_binding",
    "units",
    "provenance",
    "unsupported",
}
VISIBLE_FINDINGS = {
    "hidden_text",
    "duplicates",
    "readability",
    "overflow",
    "overlap",
    "missing_element",
}
DESIGN_FINDINGS = {"template_fidelity", "readability", "overlap", "missing_element"}
SEVERITIES = {"critical", "major", "minor", "info"}
POINT_STATUSES = {"preserved", "partially", "absent", "distorted", "unverifiable"}
CATEGORY_STATUSES = {"passed", "failed", "inconclusive"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

_HERE = Path(__file__).resolve().parent
_PROMPTS = _HERE / "prompts"


def _judge_prompt() -> str:
    return (_PROMPTS / "judge.md").read_text(encoding="utf-8")


def _compare_prompt() -> str:
    return (_PROMPTS / "compare.md").read_text(encoding="utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _prompt_hashes() -> dict[str, str]:
    return {"judge": _sha(_judge_prompt().encode()), "compare": _sha(_compare_prompt().encode())}


def _judge_schema() -> dict[str, Any]:
    score = {"type": "integer"}
    status = {"type": "string", "enum": ["passed", "failed", "inconclusive"]}
    point = {
        "type": "object",
        "additionalProperties": False,
        "required": ["point_id", "status", "evidence", "slide_number"],
        "properties": {
            "point_id": {"type": "string"},
            "status": {"type": "string", "enum": sorted(POINT_STATUSES)},
            "evidence": {"type": "string"},
            "slide_number": {"type": ["integer", "null"]},
        },
    }
    finding = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "category",
            "severity",
            "slide_number",
            "region",
            "description",
            "evidence",
            "point_ids",
        ],
        "properties": {
            "category": {"type": "string", "enum": list(FINDING_CATEGORIES)},
            "severity": {"type": "string", "enum": sorted(SEVERITIES)},
            "slide_number": {"type": "integer"},
            "region": {"type": "string"},
            "description": {"type": "string"},
            "evidence": {"type": "string"},
            "point_ids": {"type": "array", "items": {"type": "string"}},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "cases"],
        "properties": {
            "schema_version": {"type": "integer", "enum": [SCHEMA_VERSION]},
            "cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["case_id", "variants"],
                    "properties": {
                        "case_id": {"type": "string"},
                        "variants": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "variant_id",
                                    "categories",
                                    "coverage",
                                    "points",
                                    "findings",
                                    "summary",
                                ],
                                "properties": {
                                    "variant_id": {"type": "string"},
                                    "categories": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["semantic", "visible", "design"],
                                        "properties": {
                                            name: {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["status", "score"],
                                                "properties": {"status": status, "score": score},
                                            }
                                            for name in ("semantic", "visible", "design")
                                        },
                                    },
                                    "coverage": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["slide_numbers", "point_ids"],
                                        "properties": {
                                            "slide_numbers": {
                                                "type": "array",
                                                "items": {"type": "integer"},
                                            },
                                            "point_ids": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                        },
                                    },
                                    "points": {"type": "array", "items": point},
                                    "findings": {"type": "array", "items": finding},
                                    "summary": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def _compare_schema() -> dict[str, Any]:
    # A comparison returns the same fully evidenced per-side evaluation as a judge,
    # plus independent category winners. This keeps one-case worker results explicit.
    variant_schema = _judge_schema()["properties"]["cases"]["items"]["properties"]["variants"][
        "items"
    ]
    # A/B are different experiments of one variant. Cross-experiment equality is
    # a valid tie; within-run diversity belongs to the separate whole-case judge.
    variant_schema["properties"]["findings"]["items"]["properties"]["category"]["enum"].remove(
        "duplicates"
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "cases"],
        "properties": {
            "schema_version": {"type": "integer", "enum": [SCHEMA_VERSION]},
            "cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["case_id", "comparisons"],
                    "properties": {
                        "case_id": {"type": "string"},
                        "comparisons": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["pair_id", "sides", "winners", "rationale"],
                                "properties": {
                                    "pair_id": {"type": "string"},
                                    "sides": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["A", "B"],
                                        "properties": {
                                            side: variant_schema["properties"]
                                            and {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": [
                                                    "categories",
                                                    "coverage",
                                                    "points",
                                                    "findings",
                                                    "summary",
                                                ],
                                                "properties": {
                                                    key: value
                                                    for key, value in variant_schema[
                                                        "properties"
                                                    ].items()
                                                    if key != "variant_id"
                                                },
                                            }
                                            for side in ("A", "B")
                                        },
                                    },
                                    "winners": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["semantic", "visible", "design"],
                                        "properties": {
                                            key: {
                                                "type": "string",
                                                "enum": ["A", "B", "tie", "inconclusive"],
                                            }
                                            for key in ("semantic", "visible", "design")
                                        },
                                    },
                                    "rationale": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


JUDGE_SCHEMA = _judge_schema()
COMPARE_SCHEMA = _compare_schema()


def _single_case_schema(root_schema: dict[str, Any], case_key: str) -> dict[str, Any]:
    case_schema = root_schema["properties"]["cases"]["items"]
    return {
        "$schema": root_schema["$schema"],
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "case"],
        "properties": {
            "schema_version": root_schema["properties"]["schema_version"],
            "case": case_schema,
        },
        "description": f"One complete case result: {case_key}",
    }


JUDGE_CASE_SCHEMA = _single_case_schema(JUDGE_SCHEMA, "variants")
COMPARE_CASE_SCHEMA = _single_case_schema(COMPARE_SCHEMA, "comparisons")
SCHEMA_HASHES = {
    "judge": _canonical_hash(JUDGE_SCHEMA),
    "judge_case": _canonical_hash(JUDGE_CASE_SCHEMA),
    "compare": _canonical_hash(COMPARE_SCHEMA),
    "compare_case": _canonical_hash(COMPARE_CASE_SCHEMA),
    "worker_id_bindings": _canonical_hash(WORKER_SCHEMA_VERSION),
}
CALIBRATION_SCHEMA_HASH = _canonical_hash(SCHEMA_HASHES)
CALIBRATION_PROMPT_HASH = _canonical_hash(_prompt_hashes())


def _bind_point_ids(schema: dict[str, Any], point_ids: list[str]) -> None:
    ids = sorted(point_ids)
    schema["properties"]["coverage"]["properties"]["point_ids"]["items"]["enum"] = ids
    schema["properties"]["points"]["items"]["properties"]["point_id"]["enum"] = ids
    schema["properties"]["findings"]["items"]["properties"]["point_ids"]["items"]["enum"] = ids


def _bound_judge_case_schema(case_meta: dict[str, Any]) -> dict[str, Any]:
    schema = copy.deepcopy(JUDGE_CASE_SCHEMA)
    case = schema["properties"]["case"]
    case["properties"]["case_id"]["enum"] = [case_meta["case_alias"]]
    point_ids = [point["id"] for point in case_meta["reference"]["points"]]
    variants = case["properties"]["variants"]["items"]
    variants["properties"]["variant_id"]["enum"] = sorted(
        row["alias"] for row in case_meta["variants"]
    )
    _bind_point_ids(variants, point_ids)
    return schema


def _bound_compare_case_schema(meta_cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not meta_cases:
        raise ValueError("Comparison worker schema requires at least one pair")
    schema = copy.deepcopy(COMPARE_CASE_SCHEMA)
    case = schema["properties"]["case"]
    aliases = sorted({row["case_alias"] for row in meta_cases})
    case["properties"]["case_id"]["enum"] = aliases
    comparison = case["properties"]["comparisons"]["items"]
    comparison["properties"]["pair_id"]["enum"] = sorted({row["pair_id"] for row in meta_cases})
    point_ids = [point["id"] for point in meta_cases[0]["reference"]["points"]]
    for side in ("A", "B"):
        side_schema = comparison["properties"]["sides"]["properties"][side]
        # The side object key itself is the fixed opaque variant label in compare mode.
        _bind_point_ids(side_schema, point_ids)
    return schema


def _resolve_asset(root: Path, value: Any, *, suffix: str | None = None) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Evidence asset path must be a non-empty relative path")
    relative = PurePosixPath(value.replace("\\", "/"))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Evidence asset path must stay inside its bundle")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("Evidence bundle must not use symlinked assets")
    candidate = candidate.resolve(strict=True)
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("Evidence asset escaped its bundle")
    if not candidate.is_file():
        raise ValueError("Evidence asset is not a file")
    if suffix and candidate.suffix.lower() != suffix:
        raise ValueError(f"Evidence asset must be {suffix}")
    return candidate


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _supported_image(path: Path) -> bool:
    data = path.read_bytes()[:16]
    return (
        (path.suffix.lower() == ".png" and data.startswith(PNG_SIGNATURE))
        or (path.suffix.lower() in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"))
        or (path.suffix.lower() == ".webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def _read_bundle(value: str | Path) -> dict[str, Any]:
    path = Path(value).expanduser().resolve(strict=True)
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError("A bundle path must name its JSON file")
    document = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent.resolve()
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported evidence schema in {path.name}")
    case_id = _nonempty_text(document.get("case_id"), "case_id")
    if not ID_RE.fullmatch(case_id):
        raise ValueError("case_id contains unsafe characters")
    source_doc = document.get("source")
    if not isinstance(source_doc, dict):
        raise ValueError(f"{case_id}: source must be an object")
    source_text = source_doc.get("text")
    if not isinstance(source_text, str) or not source_text.strip():
        raise ValueError("source.text must be non-empty text")
    source_digest = source_doc.get("sha256")
    actual_source_digest = _sha(source_text.encode("utf-8"))
    if not isinstance(source_digest, str) or source_digest != actual_source_digest:
        raise ValueError(f"{case_id}: source sha256 does not match source text")
    reference_doc = document.get("reference")
    if not isinstance(reference_doc, dict) or reference_doc.get("status") not in {"gold", "silver"}:
        raise ValueError(f"{case_id}: reference.status must be gold or silver")
    provenance = reference_doc.get("provenance", {})
    if reference_doc["status"] == "gold" and (
        not isinstance(provenance, dict) or provenance.get("review_status") != "approved"
    ):
        raise ValueError(f"{case_id}: gold reference must have approved provenance")
    ref_version = _nonempty_text(reference_doc.get("version"), "reference.version")
    raw_points = reference_doc.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError(f"{case_id}: reference must contain at least one key point")
    points = []
    point_ids = set()
    for raw in raw_points:
        if not isinstance(raw, dict):
            raise ValueError(f"{case_id}: reference point must be an object")
        point_id = _nonempty_text(raw.get("id"), "reference point id")
        if not ID_RE.fullmatch(point_id) or point_id in point_ids:
            raise ValueError(f"{case_id}: reference point ids must be unique safe identifiers")
        point_ids.add(point_id)
        statement = _nonempty_text(raw.get("statement"), f"point {point_id} statement")
        quote = _nonempty_text(raw.get("quote"), f"point {point_id} source quote")
        if quote not in source_text:
            raise ValueError(
                f"{case_id}: reference quote for {point_id} is not an exact source anchor"
            )
        if not isinstance(raw.get("required"), bool):
            raise ValueError(f"{case_id}: point {point_id} needs a boolean required flag")
        point = {
            "id": point_id,
            "statement": statement,
            "quote": quote,
            "required": raw["required"],
        }
        for extra in (
            "origin",
            "numbers",
            "units",
            "condition",
            "negation",
            "table_binding",
            "provenance",
        ):
            if extra in raw:
                point[extra] = raw[extra]
        points.append(point)
    raw_requirements = reference_doc.get("requirements", [])
    if not isinstance(raw_requirements, list) or any(
        not isinstance(item, (str, dict)) for item in raw_requirements
    ):
        raise ValueError(f"{case_id}: reference requirements must be strings or objects")
    requirements = raw_requirements
    variants_doc = document.get("variants")
    if not isinstance(variants_doc, dict) or not variants_doc:
        raise ValueError(f"{case_id}: variants must be a non-empty object")
    variants: dict[str, Any] = {}
    all_images: list[dict[str, Any]] = []
    source_images = []
    raw_source_images = source_doc.get("images", [])
    if not isinstance(raw_source_images, list):
        raise ValueError(f"{case_id}: source.images must be a list")
    for index, raw_image in enumerate(raw_source_images, 1):
        if not isinstance(raw_image, dict):
            raise ValueError(f"{case_id}: source image metadata must be an object")
        image_path = _resolve_asset(root, raw_image.get("path"))
        image_data = image_path.read_bytes()
        if not _supported_image(image_path):
            raise ValueError(f"{case_id}: source image must be PNG, JPEG, or WebP")
        digest = _sha(image_data)
        if raw_image.get("sha256") and raw_image["sha256"] != digest:
            raise ValueError(f"{case_id}: source image digest does not match its bytes")
        source_images.append(
            {
                "image_id": f"S{index:02d}",
                "path": str(PurePosixPath(raw_image["path"].replace("\\", "/"))),
                "sha256": digest,
                "pixels_sha256": raw_image.get("pixels_sha256"),
                "registered_sha256": raw_image.get("registered_sha256"),
                "origin": raw_image.get("origin"),
                "size": raw_image.get("size"),
            }
        )
        all_images.append({"kind": "source", "number": index, "path": image_path, "sha256": digest})
    for name in sorted(variants_doc):
        raw_variant = variants_doc[name]
        if not ID_RE.fullmatch(str(name)) or not isinstance(raw_variant, dict):
            raise ValueError(f"{case_id}: invalid variant entry")
        pptx = _resolve_asset(root, raw_variant.get("pptx"), suffix=".pptx")
        pdf = (
            _resolve_asset(root, raw_variant.get("pdf"), suffix=".pdf")
            if raw_variant.get("pdf")
            else None
        )
        if not isinstance(raw_variant.get("slides"), list) or not raw_variant["slides"]:
            raise ValueError(f"{case_id}/{name}: every variant needs exported slides")
        slide_nums = set()
        slides = []
        for raw_slide in raw_variant["slides"]:
            if not isinstance(raw_slide, dict):
                raise ValueError(f"{case_id}/{name}: slide entry must be an object")
            number = raw_slide.get("number")
            if (
                isinstance(number, bool)
                or not isinstance(number, int)
                or number < 1
                or number in slide_nums
            ):
                raise ValueError(
                    f"{case_id}/{name}: slide numbers must be unique positive integers"
                )
            slide_nums.add(number)
            image = _resolve_asset(root, raw_slide.get("image"), suffix=".png")
            data = image.read_bytes()
            if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
                raise ValueError(f"{case_id}/{name}/{number}: image is not a valid PNG")
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            if not width or not height or width > 30000 or height > 30000:
                raise ValueError(f"{case_id}/{name}/{number}: PNG dimensions are invalid")
            text = raw_slide.get("text")
            if not isinstance(text, str):
                raise ValueError(f"{case_id}/{name}/{number}: slide text must be a string")
            objects = raw_slide.get("objects", [])
            if not isinstance(objects, list):
                raise ValueError(f"{case_id}/{name}/{number}: slide objects must be a list")
            slide = {"number": number, "text": text, "objects": objects}
            if isinstance(raw_slide.get("pdf_visible_text"), str):
                slide["pdf_visible_text"] = raw_slide["pdf_visible_text"]
            slides.append(slide)
            all_images.append(
                {"variant": name, "number": number, "path": image, "sha256": _sha(data)}
            )
        slide_nums_ordered = sorted(slide_nums)
        if slide_nums_ordered != list(range(1, max(slide_nums_ordered) + 1)):
            raise ValueError(f"{case_id}/{name}: slide numbers must cover 1..N")
        variants[name] = {
            "pptx": str(PurePosixPath(raw_variant["pptx"].replace("\\", "/"))),
            "pptx_sha256": _sha(pptx.read_bytes()),
            "pdf": str(PurePosixPath(raw_variant["pdf"].replace("\\", "/"))) if pdf else None,
            "pdf_sha256": _sha(pdf.read_bytes()) if pdf else None,
            "slides": sorted(slides, key=lambda row: row["number"]),
        }
    template = None
    raw_template = document.get("template")
    if raw_template is not None:
        if not isinstance(raw_template, dict):
            raise ValueError(f"{case_id}: template must be an object")
        template = {
            key: raw_template[key]
            for key in (
                "name",
                "description",
                "format",
                "aspect_ratio",
                "source_slides",
                "layouts",
                "fidelity_applicability",
                "preview_status",
                "origin",
                "source_origin",
                "sha256",
                "registered_sha256",
            )
            if key in raw_template
        }
        raw_previews = raw_template.get("previews", [])
        if not isinstance(raw_previews, list):
            raise ValueError(f"{case_id}: template.previews must be a list")
        previews = []
        for index, preview in enumerate(raw_previews, 1):
            if not isinstance(preview, dict):
                raise ValueError(f"{case_id}: template preview must be an object")
            preview_path = _resolve_asset(root, preview.get("image"), suffix=".png")
            previews.append(
                {
                    "preview_id": f"T{index:02d}",
                    "source_slide": preview.get("source_slide"),
                    "path": str(PurePosixPath(preview["image"].replace("\\", "/"))),
                    "sha256": _sha(preview_path.read_bytes()),
                }
            )
            all_images.append(
                {
                    "kind": "template",
                    "number": index,
                    "path": preview_path,
                    "sha256": _sha(preview_path.read_bytes()),
                }
            )
        template["previews"] = previews
    source = {"text": source_text, "sha256": source_digest}
    proposals = source_doc.get("approved_proposals", [])
    if proposals:
        if not isinstance(proposals, list) or any(not isinstance(row, dict) for row in proposals):
            raise ValueError(f"{case_id}: approved proposals must be a list of objects")
        source["approved_proposals"] = [
            {key: row[key] for key in ("slide_number", "text", "origin", "fact_ids") if key in row}
            for row in proposals
        ]
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "source": source,
        "source_images": source_images,
        "reference": {
            "version": ref_version,
            "status": reference_doc["status"],
            "provenance": provenance,
            "points": points,
            "requirements": requirements,
        },
        "variants": variants,
        "template": template,
    }
    bundle_hash = _canonical_hash(
        {
            "bundle": normalized,
            "pptx": {name: row["pptx_sha256"] for name, row in variants.items()},
            "pdf": {name: row["pdf_sha256"] for name, row in variants.items()},
            "images": [
                {
                    "kind": row.get("kind", "slide"),
                    "variant": row.get("variant"),
                    "number": row["number"],
                    "sha256": row["sha256"],
                }
                for row in all_images
            ],
        }
    )
    return {
        "path": path,
        "root": root,
        "data": normalized,
        "images": all_images,
        "hash": bundle_hash,
    }


def _copy_case_packet(
    case: dict[str, Any],
    destination: Path,
    *,
    case_alias: str,
    variant_aliases: dict[str, str],
) -> tuple[dict[str, Any], list[Path]]:
    data = case["data"]
    variants = []
    image_paths = []
    for original_name, variant in sorted(data["variants"].items()):
        alias = variant_aliases[original_name]
        slides = []
        for slide in variant["slides"]:
            source_image = next(
                row["path"]
                for row in case["images"]
                if row.get("variant") == original_name and row["number"] == slide["number"]
            )
            relative_image = (
                Path("assets") / case_alias / alias / f"slide-{slide['number']:03d}.png"
            )
            image_dest = destination / relative_image
            image_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_image, image_dest)
            image_paths.append(image_dest)
            slides.append(
                {
                    "number": slide["number"],
                    "image": relative_image.as_posix(),
                    "text": slide["text"],
                    "objects": slide["objects"],
                    **(
                        {"pdf_visible_text": slide["pdf_visible_text"]}
                        if "pdf_visible_text" in slide
                        else {}
                    ),
                }
            )
        variants.append({"variant_id": alias, "slides": slides})
    packet = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_alias,
        "source": data["source"],
        "reference": _packet_reference(data["reference"]),
        "variants": variants,
    }
    shared, shared_images = _copy_reference_assets(case, destination, case_alias)
    packet.update(shared)
    image_paths.extend(shared_images)
    packet_path = destination / "cases" / f"{case_alias}.json"
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return packet, image_paths


def _packet_reference(reference: dict[str, Any]) -> dict[str, Any]:
    # Gold/silver state and approval provenance are for the controller's gate,
    # not labels for a blind evaluator.
    return {key: reference[key] for key in ("version", "points", "requirements")}


def _copy_reference_assets(
    case: dict[str, Any], destination: Path, case_alias: str
) -> tuple[dict[str, Any], list[Path]]:
    data = case["data"]
    packet: dict[str, Any] = {"source_images": []}
    image_paths = []
    for index, image in enumerate(data["source_images"], 1):
        source_image = next(
            row["path"]
            for row in case["images"]
            if row.get("kind") == "source" and row["number"] == index
        )
        relative = Path("assets") / case_alias / f"source-{index:02d}{source_image.suffix.lower()}"
        destination_image = destination / relative
        destination_image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_image, destination_image)
        image_paths.append(destination_image)
        packet["source_images"].append(
            {
                "image_id": image["image_id"],
                "path": relative.as_posix(),
                "sha256": image["sha256"],
                "pixels_sha256": image.get("pixels_sha256"),
                "registered_sha256": image.get("registered_sha256"),
                "origin": image.get("origin"),
                "size": image.get("size"),
            }
        )
    if data["template"]:
        template = dict(data["template"])
        previews = []
        for index, preview in enumerate(template.get("previews", []), 1):
            source_preview = next(
                row["path"]
                for row in case["images"]
                if row.get("kind") == "template" and row["number"] == index
            )
            relative = Path("assets") / case_alias / f"template-{index:02d}.png"
            destination_image = destination / relative
            destination_image.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_preview, destination_image)
            image_paths.append(destination_image)
            previews.append(
                {
                    "preview_id": preview["preview_id"],
                    "source_slide": preview.get("source_slide"),
                    "image": relative.as_posix(),
                    "sha256": preview["sha256"],
                }
            )
        template["previews"] = previews
        packet["template"] = template
    return packet, image_paths


def _write_index(destination: Path, packet_paths: list[Path]) -> None:
    index = {
        "schema_version": SCHEMA_VERSION,
        "cases": [
            {"case_id": path.stem, "packet": path.relative_to(destination).as_posix()}
            for path in packet_paths
        ],
    }
    (destination / "packets.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_image_manifest(destination: Path, image_paths: list[Path]) -> None:
    rows = []
    for index, path in enumerate(image_paths, 1):
        relative = path.relative_to(destination).as_posix()
        if path.name.startswith("template-"):
            kind = "template_preview"
        elif path.name.startswith("source-"):
            kind = "source_image"
        else:
            kind = "rendered_slide"
        rows.append({"order": index, "path": relative, "kind": kind})
    (destination / "image-manifest.json").write_text(
        json.dumps({"images": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _usage_events(stdout_path: Path) -> dict[str, Any]:
    """Read JSONL token accounting without retaining raw event logs."""
    by_thread: dict[str, dict[str, int]] = {}
    event_count = 0
    total_names = {
        "input_tokens": "input_tokens",
        "prompt_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "completion_tokens": "output_tokens",
        "cached_input_tokens": "cached_input_tokens",
        "cached_tokens": "cached_input_tokens",
        "reasoning_tokens": "reasoning_tokens",
    }
    try:
        with stdout_path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict):
                    continue
                usage = event.get("usage")
                if not isinstance(usage, dict):
                    usage = event.get("token_usage")
                if not isinstance(usage, dict):
                    continue
                counts = {}
                for source, target in total_names.items():
                    value = usage.get(source)
                    if isinstance(value, int) and not isinstance(value, bool):
                        counts[target] = max(counts.get(target, 0), value)
                if not counts:
                    continue
                event_count += 1
                thread_id = str(event.get("thread_id") or event.get("session_id") or "root")
                turn_id = str(event.get("turn_id") or event.get("turnId") or "latest")
                key = f"{thread_id}:{turn_id}"
                # Codex may emit cumulative updates for a thread. Keep the largest
                # observation for that turn instead of summing snapshots.
                previous = by_thread.setdefault(key, {})
                for name, value in counts.items():
                    previous[name] = max(previous.get(name, 0), value)
    except OSError:
        return {"status": "unavailable", "reason": "could_not_read_jsonl", "events": 0}
    if not by_thread:
        return {"status": "unavailable", "reason": "no_usage_events", "events": 0}
    totals: dict[str, int] = {}
    for usage in by_thread.values():
        for key, value in usage.items():
            totals[key] = totals.get(key, 0) + value
    return {
        "status": "reported",
        "scope": "jsonl thread/turn usage events; child threads included when emitted",
        "events": event_count,
        "threads": len(by_thread),
        "totals": totals,
    }


def _environment_for_codex() -> dict[str, str]:
    # Never pass application API keys from the generator process to the judge.
    allowed = {
        "PATH",
        "HOME",
        "CODEX_HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "NO_COLOR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


_SECRET_TEXT = re.compile(
    r"(?i)(?:\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{12,}|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+)"
)


def _redact_text(value: str) -> str:
    for key in ("OPENAI_API_KEY", "STUDIO_API_KEY", "API_KEY"):
        secret = os.environ.get(key)
        if secret:
            value = value.replace(secret, "[redacted]")
    return _SECRET_TEXT.sub("[redacted]", value)


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if re.search(
                r"(?i)(api.?key|token|secret|password|authorization|credential)", str(key)
            ):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _redact_json(item)
        return result
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _save_pass_artifacts(
    artifact_dir: Path | None,
    *,
    prompt: str,
    schema: dict[str, Any],
    stdout_path: Path,
    result: Any,
    usage: dict[str, Any],
    error: str | None = None,
) -> None:
    if artifact_dir is None:
        return
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "prompt.md").write_text(_redact_text(prompt), encoding="utf-8")
    (artifact_dir / "output-schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    redacted = artifact_dir / "stdout.redacted.jsonl"
    with (
        stdout_path.open("r", encoding="utf-8", errors="replace") as source,
        redacted.open("w", encoding="utf-8") as destination,
    ):
        for line in source:
            try:
                event = _redact_json(json.loads(line))
            except (ValueError, TypeError):
                continue
            destination.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    (artifact_dir / "usage.json").write_text(
        json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if result is not None:
        (artifact_dir / "structured-result.json").write_text(
            json.dumps(_redact_json(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if error:
        (artifact_dir / "error.json").write_text(
            json.dumps({"error": _redact_text(error)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _saved_usage(artifact_dir: Path) -> dict[str, Any] | None:
    path = artifact_dir / "usage.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _run_codex(
    workdir: Path,
    prompt: str,
    schema: dict[str, Any],
    image_paths: list[Path],
    *,
    timeout: float,
    artifact_dir: Path | None = None,
    worker_alias: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI is unavailable")
    schema_path = workdir / "output-schema.json"
    result_path = workdir / "last-message.json"
    stdout_path = workdir / "stdout.jsonl"
    stderr_path = workdir / "stderr.tmp"
    schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    command = [
        executable,
        "exec",
        "--cd",
        str(workdir),
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(result_path),
        "--model",
        MODEL,
        "-c",
        f'model_reasoning_effort="{MODEL_REASONING_EFFORT}"',
        "-c",
        "agents.enabled=false",
        "-c",
        "mcp_servers={}",
        "-c",
        "project_doc_max_bytes=0",
        "-c",
        'approval_policy="never"',
    ]
    # Images stay in the packet directory and are opened by whole-case workers.
    # Attaching every PNG to the root call would duplicate visual tokens.
    for image_path in image_paths:
        command.extend(("--image", str(image_path)))
    command.extend(("--", prompt))
    started = time.monotonic()
    timed_out = False
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env=_environment_for_codex(),
            start_new_session=True,
        )
        try:
            process.wait(timeout=max(0.01, timeout))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            process.wait()
            timed_out = True
    elapsed = round(time.monotonic() - started, 3)
    usage = _usage_events(stdout_path)
    usage["elapsed_seconds"] = elapsed
    usage["worker_execution"] = {
        "mode": "isolated_codex_process",
        "case_alias": worker_alias,
        "model": MODEL,
        "reasoning_effort": MODEL_REASONING_EFFORT,
        "status": "started",
    }
    result = None
    parse_error = None
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            parse_error = type(exc).__name__
    error = (
        "Codex judge exceeded the total batch timeout"
        if timed_out
        else f"Codex judge exited with status {process.returncode}"
        if process.returncode != 0
        else "Codex judge did not write valid structured JSON"
        if result is None
        else None
    )
    if error:
        usage["worker_execution"]["status"] = "failed"
    else:
        usage["worker_execution"]["status"] = "cli_completed"
    _save_pass_artifacts(
        artifact_dir,
        prompt=prompt,
        schema=schema,
        stdout_path=stdout_path,
        result=result,
        usage=usage,
        error=error or (f"Invalid result JSON: {parse_error}" if parse_error else None),
    )
    if timed_out:
        raise TimeoutError("Codex judge exceeded the total batch timeout")
    if process.returncode != 0:
        raise RuntimeError(f"Codex judge exited with status {process.returncode}")
    if result is None:
        raise RuntimeError("Codex judge did not write a structured result")
    if not isinstance(result, dict):
        raise RuntimeError("Codex judge result must be a JSON object")
    return result, usage


def _aggregate_status(statuses: list[str]) -> str:
    if any(status == "failed" for status in statuses):
        return "failed"
    if statuses and all(status == "passed" for status in statuses):
        return "passed"
    return "inconclusive"


def _status_from_score(score: int) -> str:
    if score <= 2:
        return "failed"
    if score == 3:
        return "inconclusive"
    return "passed"


def _exact_slide_quote(evidence: str, slide_text: str, pdf_visible_text: str = "") -> str | None:
    def expand_formatting(value: str) -> str:
        # PDF extractors and structured responses can encode a visual line wrap
        # as a literal escape. Treat only those formatting markers as whitespace.
        for marker in (r"\\r\\n", r"\r\n", r"\\n", r"\n", r"\\t", r"\t"):
            replacement = "\t" if marker.endswith("t") else "\n"
            value = value.replace(marker, replacement)
        return value

    def formatting_only(value: str) -> str:
        return re.sub(r"\s+", " ", expand_formatting(value)).strip()

    sources = [text for text in (slide_text, pdf_visible_text) if text]

    def matches(quote: str) -> bool:
        normalized_quote = formatting_only(quote)
        return any(normalized_quote in formatting_only(source) for source in sources)

    if matches(evidence):
        return expand_formatting(evidence)
    quote_pairs = (('"', '"'), ("“", "”"), ("‘", "’"), ("«", "»"))
    for opening, closing in quote_pairs:
        prefix = "Slide text: " + opening
        if evidence.startswith(prefix) and evidence.endswith(closing):
            quoted = evidence[len(prefix) : -len(closing)]
            if matches(quoted):
                return expand_formatting(quoted)
        if evidence.startswith(opening) and evidence.endswith(closing):
            quoted = evidence[len(opening) : -len(closing)]
            if matches(quoted):
                return expand_formatting(quoted)
    quoted_parts = re.findall(
        r'"([^"\n]+(?:\n[^"\n]+)*)"|“([^”\n]+(?:\n[^”\n]+)*)”|‘([^’\n]+(?:\n[^’\n]+)*)’|«([^»\n]+(?:\n[^»\n]+)*)»',
        evidence,
    )
    quoted_parts = [next(part for part in group if part) for group in quoted_parts]
    if quoted_parts and all(matches(part) for part in quoted_parts):
        return (
            expand_formatting(quoted_parts[0])
            if len(quoted_parts) == 1
            else expand_formatting(evidence)
        )
    return None


def _grounded_visual_evidence(
    evidence: str,
    slide_number: int,
    *,
    region: str | None = None,
    require_visible_quote: bool = False,
) -> bool:
    match = re.match(rf"(?is)^visual:\s*slide\s+{slide_number}\s*,\s*([^:]+):\s*(.+)$", evidence)
    if not match:
        return False
    evidence_region, observation = match.groups()
    if region is not None:
        ignored = {
            "a",
            "an",
            "and",
            "area",
            "complete",
            "composition",
            "concrete",
            "deck",
            "entire",
            "for",
            "from",
            "in",
            "layout",
            "of",
            "on",
            "overall",
            "region",
            "slide",
            "the",
            "to",
            "visual",
            "whole",
            "with",
        }

        def tokens(value: str) -> set[str]:
            return set(re.findall(r"[a-z0-9]+", value.casefold())) - ignored

        def normalize(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

        same_label = normalize(region) == normalize(evidence_region)
        if not same_label and not tokens(region).intersection(
            tokens(evidence_region + " " + observation)
        ):
            return False
    if not observation.strip():
        return False
    if require_visible_quote:
        quotes = re.findall(r'"([^"\n]+)"|“([^”\n]+)”|‘([^’\n]+)’', observation)
        return any(any(part.strip() for part in quote) for quote in quotes)
    return True


def _derived_categories(
    categories: dict[str, Any],
    point_results: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    reference_points: list[dict[str, Any]],
) -> dict[str, str]:
    severe_by_category = {
        name: any(
            finding["category"] in labels and finding["severity"] in {"critical", "major"}
            for finding in findings
        )
        for name, labels in (
            ("semantic", SEMANTIC_FINDINGS),
            ("visible", VISIBLE_FINDINGS),
            ("design", DESIGN_FINDINGS),
        )
    }
    required = {row["id"] for row in reference_points if row["required"]}
    by_point = {row["point_id"]: row["status"] for row in point_results}
    missing = any(
        by_point.get(point_id) in {"partially", "absent", "distorted"} for point_id in required
    )
    uncertain = any(by_point.get(point_id) == "unverifiable" for point_id in required)
    derived = {}
    for name in ("semantic", "visible", "design"):
        score_status = _status_from_score(categories[name]["score"])
        if severe_by_category[name] or (name == "semantic" and missing):
            derived[name] = "failed"
        elif score_status == "failed":
            derived[name] = "failed"
        elif (name == "semantic" and uncertain) or score_status == "inconclusive":
            derived[name] = "inconclusive"
        else:
            derived[name] = "passed"
    return derived


def _validate_variant(
    candidate: Any,
    *,
    alias: str,
    slide_texts: dict[int, str],
    pdf_visible_texts: dict[int, str] | None = None,
    hidden_texts: dict[int, str],
    reference_points: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(candidate, dict) or candidate.get("variant_id") != alias:
        raise ValueError("Variant result id is missing or unexpected")
    categories = candidate.get("categories")
    if not isinstance(categories, dict) or set(categories) != {"semantic", "visible", "design"}:
        raise ValueError("Variant category scores are incomplete")
    normalized_categories = {}
    for name in ("semantic", "visible", "design"):
        value = categories[name]
        if not isinstance(value, dict) or value.get("status") not in CATEGORY_STATUSES:
            raise ValueError(f"Invalid {name} category status")
        score = value.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or score not in range(1, 6):
            raise ValueError(f"Invalid {name} category score")
        normalized_categories[name] = {"status": value["status"], "score": score}
    coverage = candidate.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("Variant coverage is missing")
    expected_slides = sorted(slide_texts)
    expected_point_ids = [row["id"] for row in reference_points]
    slide_coverage = coverage.get("slide_numbers")
    point_coverage = coverage.get("point_ids")
    if (
        not isinstance(slide_coverage, list)
        or len(slide_coverage) != len(set(slide_coverage))
        or sorted(slide_coverage) != expected_slides
        or not isinstance(point_coverage, list)
        or len(point_coverage) != len(set(point_coverage))
        or set(point_coverage) != set(expected_point_ids)
    ):
        raise ValueError("Variant omitted or duplicated slide/key-point coverage")
    raw_points = candidate.get("points")
    if not isinstance(raw_points, list) or len(raw_points) != len(expected_point_ids):
        raise ValueError("Variant point evaluations are incomplete")
    normalized_points = []
    seen_points = set()
    for row in raw_points:
        if not isinstance(row, dict):
            raise ValueError("Point evaluation must be an object")
        point_id, status = row.get("point_id"), row.get("status")
        if (
            point_id not in set(expected_point_ids)
            or point_id in seen_points
            or status not in POINT_STATUSES
        ):
            raise ValueError("Point evaluation id/status is invalid or duplicated")
        seen_points.add(point_id)
        evidence = row.get("evidence")
        if not isinstance(evidence, str):
            raise ValueError("Point evidence must be text")
        slide_number = row.get("slide_number")
        if slide_number is not None and (
            isinstance(slide_number, bool) or slide_number not in slide_texts
        ):
            raise ValueError("Point evidence references an unknown slide")
        if status in {"preserved", "partially", "distorted"}:
            exact_quote = (
                _exact_slide_quote(
                    evidence,
                    slide_texts[slide_number],
                    (pdf_visible_texts or {}).get(slide_number, ""),
                )
                if slide_number is not None
                else None
            )
            visual_quote = (
                _grounded_visual_evidence(evidence, slide_number, require_visible_quote=True)
                if slide_number is not None
                else False
            )
            if not evidence or slide_number is None or (exact_quote is None and not visual_quote):
                raise ValueError(
                    "Preserved, partial, or distorted points need exact slide-text or located visual evidence"
                )
            if exact_quote is not None:
                evidence = exact_quote
        normalized_points.append(
            {
                "point_id": point_id,
                "status": status,
                "evidence": evidence,
                "slide_number": slide_number,
            }
        )
    normalized_points.sort(key=lambda item: expected_point_ids.index(item["point_id"]))
    raw_findings = candidate.get("findings")
    if not isinstance(raw_findings, list):
        raise ValueError("Variant findings must be a list")
    normalized_findings = []
    for finding in raw_findings:
        if not isinstance(finding, dict):
            raise ValueError("Finding must be an object")
        category = finding.get("category")
        severity = finding.get("severity")
        slide_number = finding.get("slide_number")
        if category not in FINDING_CATEGORIES or severity not in SEVERITIES:
            raise ValueError("Finding category or severity is outside the calibrated taxonomy")
        if isinstance(slide_number, bool) or slide_number not in slide_texts:
            raise ValueError("Finding must reference an existing slide")
        region = _nonempty_text(finding.get("region"), "finding.region")
        description = _nonempty_text(finding.get("description"), "finding.description")
        evidence = _nonempty_text(finding.get("evidence"), "finding.evidence")
        related = finding.get("point_ids", [])
        if not isinstance(related, list) or any(
            pid not in set(expected_point_ids) for pid in related
        ):
            raise ValueError("Finding point references are invalid")
        if category == "omission":
            related_points = {row["point_id"]: row["status"] for row in normalized_points}
            if not related or not any(
                related_points[pid] in {"partially", "absent", "distorted"} for pid in related
            ):
                raise ValueError(
                    "Omission findings must identify an absent, partial, or distorted reference point"
                )
        exact_quote = _exact_slide_quote(
            evidence,
            slide_texts[slide_number],
            (pdf_visible_texts or {}).get(slide_number, ""),
        )
        if exact_quote is not None:
            evidence = exact_quote
        elif (
            category == "hidden_text"
            and _exact_slide_quote(evidence, hidden_texts.get(slide_number, "")) is not None
        ):
            evidence = _exact_slide_quote(evidence, hidden_texts.get(slide_number, ""))
        elif not _grounded_visual_evidence(
            evidence,
            slide_number,
            region=region,
            require_visible_quote=category in SEMANTIC_FINDINGS and category != "omission",
        ):
            raise ValueError("Finding evidence must be exact slide text or located visual evidence")
        normalized_findings.append(
            {
                "category": category,
                "severity": severity,
                "slide_number": slide_number,
                "region": region,
                "description": description,
                "evidence": evidence,
                "point_ids": related,
            }
        )
    computed = _derived_categories(
        normalized_categories,
        normalized_points,
        normalized_findings,
        reference_points,
    )
    if {name: normalized_categories[name]["status"] for name in computed} != computed:
        raise ValueError(
            "Reported category status conflicts with scores, findings, or point coverage"
        )
    summary = candidate.get("summary")
    if not isinstance(summary, str):
        raise ValueError("Variant summary must be text")
    return {
        "categories": normalized_categories,
        "coverage": {"slide_numbers": expected_slides, "point_ids": expected_point_ids},
        "points": normalized_points,
        "findings": normalized_findings,
        "summary": summary,
        # Design is still scored and calibrated, but it is advisory to a green
        # content/render-quality result. Semantic and visible defects gate it.
        "status": _aggregate_status([computed["semantic"], computed["visible"]]),
    }


def _validate_judge_response(raw: Any, meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != SCHEMA_VERSION
        or not isinstance(raw.get("cases"), list)
    ):
        raise ValueError("Structured judge result has the wrong schema version")
    by_alias = {item["case_alias"]: item for item in meta["cases"]}
    if len(raw["cases"]) != len(by_alias):
        raise ValueError("Structured judge result omitted or added cases")
    result = {}
    for case_result in raw["cases"]:
        if not isinstance(case_result, dict) or case_result.get("case_id") not in by_alias:
            raise ValueError("Structured judge result contains an unknown case")
        case_meta = by_alias[case_result["case_id"]]
        raw_variants = case_result.get("variants")
        if not isinstance(raw_variants, list) or len(raw_variants) != len(case_meta["variants"]):
            raise ValueError("Structured judge result omitted or added variants")
        aliases = {item["alias"]: item for item in case_meta["variants"]}
        validated = {}
        for row in raw_variants:
            if not isinstance(row, dict) or row.get("variant_id") not in aliases:
                raise ValueError("Structured judge result contains an unknown variant")
            alias = row["variant_id"]
            details = aliases[alias]
            validated[details["name"]] = _validate_variant(
                row,
                alias=alias,
                slide_texts=details["slide_texts"],
                pdf_visible_texts=details["pdf_visible_texts"],
                hidden_texts=details["hidden_texts"],
                reference_points=case_meta["reference"]["points"],
            )
        actual = case_meta["original_id"]
        result[actual] = {
            "case_id": actual,
            "reference_status": case_meta["reference"]["status"],
            "status": _aggregate_status([row["status"] for row in validated.values()]),
            "variants": validated,
        }
    if set(result) != {item["original_id"] for item in meta["cases"]}:
        raise ValueError("Structured judge response is missing a case")
    return result


def _signature(case_result: dict[str, Any]) -> str:
    def location(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
        for first, second in (
            ("upper left", "top left"),
            ("upper right", "top right"),
            ("lower left", "bottom left"),
            ("lower right", "bottom right"),
        ):
            normalized = normalized.replace(first, second)
        return normalized

    variants = {}
    for name, variant in sorted(case_result["variants"].items()):
        variants[name] = {
            "status": variant["status"],
            # Scores 4 vs 5 are both acceptable; only a decision-boundary
            # difference matters for independent agreement.
            "categories": {
                key: variant["categories"][key]["status"] for key in ("semantic", "visible")
            },
            "points": [
                {key: row[key] for key in ("point_id", "status")} for row in variant["points"]
            ],
            "findings": [
                {
                    "category": row["category"],
                    "severity": row["severity"],
                    "slide_number": row["slide_number"],
                    "region": location(row["region"]),
                    "point_ids": sorted(row["point_ids"]),
                }
                for row in variant["findings"]
                if row["severity"] in {"critical", "major"}
                and row["category"] in (SEMANTIC_FINDINGS | VISIBLE_FINDINGS)
            ],
        }
    return _canonical_hash(variants)


def _prepare_judge_pass(
    cases: list[dict[str, Any]], pass_number: int, *, case_offset: int = 1
) -> tuple[Path, dict[str, Any], list[Path]]:
    scratch = Path(tempfile.mkdtemp(prefix="presentation-judge-packets-"))
    packet_paths = []
    images = []
    meta_cases = []
    for index, case in enumerate(cases, case_offset):
        case_alias = f"case-{index:03d}"
        # Pass number rotates opaque aliases without changing the true labels visible to agents.
        names = sorted(case["data"]["variants"])
        shift = (pass_number + index) % len(names)
        rotated = names[shift:] + names[:shift]
        aliases = {name: f"V{ix:02d}" for ix, name in enumerate(rotated, 1)}
        packet, case_images = _copy_case_packet(
            case, scratch, case_alias=case_alias, variant_aliases=aliases
        )
        packet_paths.append(scratch / "cases" / f"{case_alias}.json")
        images.extend(case_images)
        meta_cases.append(
            {
                "case_alias": case_alias,
                "original_id": case["data"]["case_id"],
                "reference": case["data"]["reference"],
                "variants": [
                    {
                        "name": name,
                        "alias": aliases[name],
                        "slide_texts": {
                            slide["number"]: slide["text"]
                            for slide in case["data"]["variants"][name]["slides"]
                        },
                        "pdf_visible_texts": {
                            slide["number"]: slide.get("pdf_visible_text", "")
                            for slide in case["data"]["variants"][name]["slides"]
                        },
                        "hidden_texts": {
                            slide["number"]: "\n".join(
                                str(obj.get("text", ""))
                                for obj in slide["objects"]
                                if isinstance(obj, dict)
                                and obj.get("text")
                                and (obj.get("hidden") or obj.get("out_of_bounds"))
                            )
                            for slide in case["data"]["variants"][name]["slides"]
                        },
                    }
                    for name in names
                ],
            }
        )
    _write_index(scratch, packet_paths)
    _write_image_manifest(scratch, images)
    return scratch, {"cases": meta_cases}, images


def _run_judge_pass(
    cases: list[dict[str, Any]],
    *,
    timeout: float,
    pass_number: int,
    prompt_text: str | None = None,
    artifact_dir: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if not cases:
        raise ValueError("At least one case is required")
    prompt_text = prompt_text if prompt_text is not None else _judge_prompt()
    deadline = time.monotonic() + timeout
    worker_results: dict[str, dict[str, Any]] = {}
    worker_rows = []
    errors = []
    errors_by_case = {}

    def run_one(index: int, case: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        case_alias = f"case-{index + 1:03d}"
        worker_dir = Path(artifact_dir) / f"worker-{index + 1:03d}" if artifact_dir else None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Judge batch timed out before this case worker started")
        scratch, meta, images = _prepare_judge_pass(
            [case], pass_number + index, case_offset=index + 1
        )
        worker_schema = _bound_judge_case_schema(meta["cases"][0])
        usage = None
        try:
            raw, usage = _run_codex(
                scratch,
                prompt_text,
                worker_schema,
                images,
                timeout=remaining,
                artifact_dir=worker_dir,
                worker_alias=case_alias,
            )
            if worker_dir is not None:
                _save_alias_map(worker_dir, meta)
            wrapped = {"schema_version": raw.get("schema_version"), "cases": [raw.get("case")]}
            validated = _validate_judge_response(wrapped, meta)
            usage["worker_execution"]["status"] = "validated"
            _write_worker_usage(worker_dir, usage)
            case_id = case["data"]["case_id"]
            return case_id, validated[case_id], usage
        except Exception as exc:
            if usage is None and worker_dir is not None:
                usage = _saved_usage(worker_dir)
            if usage is not None:
                execution = usage.setdefault("worker_execution", {})
                execution["status"] = "failed"
                _write_worker_usage(worker_dir, usage)
            _write_worker_error(worker_dir, exc)
            raise WorkerExecutionError(case_alias, exc, usage, worker_dir) from exc
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_WORKERS, len(cases))) as pool:
        futures = {
            pool.submit(run_one, index, case): (index, case) for index, case in enumerate(cases)
        }
        for future in as_completed(futures):
            index, case = futures[future]
            case_alias = f"case-{index + 1:03d}"
            worker_dir = Path(artifact_dir) / f"worker-{index + 1:03d}" if artifact_dir else None
            try:
                case_id, result, usage = future.result()
                worker_results[case_id] = result
                worker_rows.append(
                    {
                        "case_alias": case_alias,
                        "status": "validated",
                        "started": True,
                        "usage": usage,
                        "artifacts": f"worker-{index + 1:03d}" if worker_dir else None,
                    }
                )
            except Exception as exc:
                usage = getattr(exc, "usage", None) or (
                    _saved_usage(worker_dir) if worker_dir is not None else None
                )
                error = _safe_error(exc)
                case_id = case["data"]["case_id"]
                errors.append(error)
                errors_by_case[case_id] = error
                worker_rows.append(
                    {
                        "case_alias": case_alias,
                        "status": "failed",
                        "started": usage is not None,
                        "usage": usage,
                        "artifacts": f"worker-{index + 1:03d}" if worker_dir else None,
                        "error": error,
                    }
                )
    worker_rows.sort(key=lambda row: row["case_alias"])
    usage = _aggregate_worker_usage(worker_rows)
    meta = {
        "cases": [
            {
                "case_alias": f"case-{index + 1:03d}",
                "original_id": case["data"]["case_id"],
            }
            for index, case in enumerate(cases)
        ]
    }
    if errors:
        raise WorkerBatchError(
            f"{len(errors)} of {len(cases)} independent case workers failed",
            usage=usage,
            workers=worker_rows,
            results=worker_results,
            case_errors=errors_by_case,
            meta=meta,
        )
    return worker_results, usage, meta


class WorkerExecutionError(RuntimeError):
    def __init__(self, alias: str, cause: Exception, usage: dict[str, Any] | None, artifact_dir):
        super().__init__(_safe_error(cause)["message"])
        self.alias = alias
        self.usage = usage
        self.artifact_dir = artifact_dir


class WorkerBatchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        usage: dict[str, Any],
        workers: list[dict[str, Any]],
        results: dict[str, Any] | None = None,
        case_errors: dict[str, dict[str, str]] | None = None,
        meta: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.usage = usage
        self.workers = workers
        self.results = results or {}
        self.case_errors = case_errors or {}
        self.meta = meta or {}


def _write_worker_usage(artifact_dir: Path | None, usage: dict[str, Any]) -> None:
    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "usage.json").write_text(
            json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def _write_worker_error(artifact_dir: Path | None, error: Exception) -> None:
    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "error.json").write_text(
            json.dumps(_safe_error(error), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _aggregate_worker_usage(workers: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, int] = {}
    missing_usage = []
    for worker in workers:
        usage = worker.get("usage") or {}
        worker_totals = usage.get("totals", {})
        for name, value in worker_totals.items():
            totals[name] = totals.get(name, 0) + value
        if worker.get("started") and (usage.get("status") != "reported" or not worker_totals):
            missing_usage.append(worker["case_alias"])
    if missing_usage:
        status = "partial"
    elif totals:
        status = "reported"
    else:
        status = "unavailable"
    return {
        "status": status,
        "scope": "independent per-case Codex process usage; child processes are not used",
        "totals": totals,
        "worker_count": len(workers),
        "started_worker_count": sum(bool(worker.get("started")) for worker in workers),
        "not_started_worker_count": sum(not worker.get("started") for worker in workers),
        "workers_missing_usage": missing_usage,
        "execution_mode": "isolated_codex_process",
        "max_concurrency": MAX_PARALLEL_WORKERS,
        "workers": workers,
    }


def _save_alias_map(artifact_dir: Path, meta: dict[str, Any]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    alias_map = {
        row["original_id"]: {item["name"]: item["alias"] for item in row["variants"]}
        for row in meta["cases"]
    }
    (artifact_dir / "alias-map.json").write_text(
        json.dumps(alias_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _calibration_validation(certificate: Any) -> dict[str, Any]:
    if not isinstance(certificate, dict):
        return {"status": "inconclusive", "reason": "missing_calibration_certificate"}
    if certificate.get("status") != "passed":
        return {"status": "inconclusive", "reason": "calibration_not_passed"}
    if certificate.get("model") != MODEL:
        return {"status": "inconclusive", "reason": "model_mismatch"}
    if certificate.get("model_reasoning_effort") != MODEL_REASONING_EFFORT:
        return {"status": "inconclusive", "reason": "reasoning_effort_mismatch"}
    current = _definition_snapshot()
    if certificate.get("prompt_hashes") != current["prompt_hashes"]:
        return {"status": "inconclusive", "reason": "prompt_hash_mismatch"}
    if certificate.get("schema_hashes") != current["schema_hashes"]:
        return {"status": "inconclusive", "reason": "schema_hash_mismatch"}
    if certificate.get("prompt_bundle_hash") != current["prompt_bundle_hash"]:
        return {"status": "inconclusive", "reason": "prompt_bundle_hash_mismatch"}
    if certificate.get("schema_bundle_hash") != current["schema_bundle_hash"]:
        return {"status": "inconclusive", "reason": "schema_bundle_hash_mismatch"}
    if certificate.get("controls_builder_sha256") != current["controls_builder_sha256"]:
        return {"status": "inconclusive", "reason": "controls_builder_changed"}
    if certificate.get("runner_sha256") != current["runner_sha256"]:
        return {"status": "inconclusive", "reason": "runner_changed"}
    if certificate.get("repeats") != 3:
        return {"status": "inconclusive", "reason": "calibration_requires_three_repeats"}
    categories = certificate.get("categories")
    if not isinstance(categories, dict) or any(
        not isinstance(categories.get(category), dict)
        or categories[category].get("status") != "passed"
        for category in _required_calibration_categories()
    ):
        return {"status": "inconclusive", "reason": "category_calibration_incomplete"}
    gates = certificate.get("gates")
    if not isinstance(gates, dict) or any(
        gates.get(name) != "passed" for name in ("semantic", "visible")
    ):
        return {"status": "inconclusive", "reason": "quality_gate_calibration_incomplete"}
    if not isinstance(certificate.get("controls_hash"), str) or not SHA256_RE.fullmatch(
        certificate["controls_hash"]
    ):
        return {"status": "inconclusive", "reason": "controls_hash_missing"}
    return {
        "status": "passed",
        "reason": None,
        "controls_hash": certificate["controls_hash"],
    }


def _write_report(output_dir: Path, document: dict[str, Any]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "report.json", document)
    return document


def judge_cases(
    bundle_paths: list[str | Path],
    output_dir: str | Path,
    *,
    timeout: float = 1200,
    calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Judge sealed presentation bundles; no generator or application calls are made."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    cases = [_read_bundle(path) for path in bundle_paths]
    if not cases:
        raise ValueError("At least one evidence bundle is required")
    ids = [case["data"]["case_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Case ids must be unique")
    deadline = time.monotonic() + timeout
    judge_prompt = _judge_prompt()
    prompt_hash = _sha(judge_prompt.encode())
    output_root = Path(output_dir)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "judge",
        "status": "inconclusive",
        "model": MODEL,
        "model_reasoning_effort": MODEL_REASONING_EFFORT,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash,
        "schema_hash": SCHEMA_HASHES["judge_case"],
        "controls_hash": None,
        "calibration": _calibration_validation(calibration),
        "usage": {"status": "unavailable", "passes": []},
        "passes": [],
        "artifacts_directory": "passes",
        "isolation": {
            "cwd": "ephemeral packet directory outside the project",
            "sandbox": "read-only",
            "user_config": "ignored",
            "mcp": "disabled",
            "execution_mode": "one isolated Codex process per complete case",
            "max_parallel_workers": MAX_PARALLEL_WORKERS,
            "limitation": "Read-only limits writes; filesystem reads beyond the packet are restricted by instructions and zero project-doc loading, not OS secrecy.",
        },
        "cases": [],
        "bundle_hashes": {case["data"]["case_id"]: case["hash"] for case in cases},
    }
    record["controls_hash"] = record["calibration"].get("controls_hash")
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Judge batch timed out before the first pass")
        primary_errors = {}
        try:
            primary, usage, _ = _run_judge_pass(
                cases,
                timeout=remaining,
                pass_number=0,
                prompt_text=judge_prompt,
                artifact_dir=output_root / "passes" / "primary",
            )
        except WorkerBatchError as exc:
            primary, usage, primary_errors = exc.results, exc.usage, exc.case_errors
        record["passes"].append(
            {
                "pass": "primary",
                "case_ids": ids,
                "status": "partial" if primary_errors else "complete",
                "usage": usage,
                "worker_errors": primary_errors,
                "artifacts": "passes/primary",
            }
        )
        record["usage"] = _sum_usage(record["passes"])
        success = [
            case_id
            for case_id in ids
            if case_id in primary and primary[case_id]["status"] == "passed"
        ]
        selected = {case_id for case_id, result in primary.items() if result["status"] != "passed"}
        selected.update(primary_errors)
        selected.update(set(ids) - set(primary))
        if success:
            count = math.ceil(0.2 * len(success))
            selected.update(sorted(success, key=lambda value: _sha(value.encode()))[:count])
        secondary: dict[str, dict[str, Any]] = {}
        secondary_usage = {"status": "unavailable"}
        secondary_error = None
        secondary_errors = {}
        if selected:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                secondary_error = "timeout_before_recheck"
            else:
                try:
                    secondary, secondary_usage, _ = _run_judge_pass(
                        [case for case in cases if case["data"]["case_id"] in selected],
                        timeout=remaining,
                        pass_number=1,
                        prompt_text=judge_prompt,
                        artifact_dir=output_root / "passes" / "secondary",
                    )
                except WorkerBatchError as exc:
                    secondary, secondary_usage, secondary_errors = (
                        exc.results,
                        exc.usage,
                        exc.case_errors,
                    )
                except Exception as exc:  # preserve the primary evidence; never turn errors green
                    secondary_error = _safe_error(exc)
                    secondary_usage = getattr(exc, "usage", secondary_usage)
                record["passes"].append(
                    {
                        "pass": "secondary",
                        "case_ids": sorted(selected),
                        "status": "partial" if secondary_errors or secondary_error else "complete",
                        "usage": secondary_usage,
                        "worker_errors": secondary_errors,
                        **({"error": secondary_error} if secondary_error else {}),
                        "artifacts": "passes/secondary",
                    }
                )
                record["usage"] = _sum_usage(record["passes"])
        for case_id in ids:
            first = primary.get(case_id)
            if first is None:
                second = secondary.get(case_id)
                primary_error = primary_errors.get(case_id, {"message": "primary_worker_failed"})
                secondary_error_for_case = secondary_errors.get(case_id) or secondary_error
                record["cases"].append(
                    {
                        "case_id": case_id,
                        "status": "inconclusive",
                        "observed_status": "invalid_primary",
                        "reference_status": next(
                            case["data"]["reference"]["status"]
                            for case in cases
                            if case["data"]["case_id"] == case_id
                        ),
                        "primary": None,
                        "secondary": second,
                        "variants": second["variants"] if second else {},
                        "recheck": {
                            "selected": case_id in selected,
                            "agreement": "inconclusive",
                            **(
                                {"error": secondary_error_for_case}
                                if case_id in selected and secondary_error_for_case
                                else {}
                            ),
                        },
                        "error": primary_error,
                    }
                )
                continue
            second = secondary.get(case_id)
            agreement = "not_selected"
            agreement_error = secondary_errors.get(case_id) or secondary_error
            if case_id in selected:
                if agreement_error or second is None:
                    agreement = "inconclusive"
                elif _signature(first) == _signature(second):
                    agreement = "agreed"
                else:
                    agreement = "disagreed"
            if agreement == "disagreed" or agreement == "inconclusive":
                status = "inconclusive"
            else:
                status = first["status"]
            if first["reference_status"] == "silver" and status == "passed":
                status = "inconclusive"
            observed_status = status
            if status == "failed" and record["calibration"]["status"] != "passed":
                status = "inconclusive"
            selected_result = first if agreement != "agreed" else second
            row = {
                "case_id": case_id,
                "status": status,
                "observed_status": observed_status,
                "reference_status": first["reference_status"],
                "primary": first,
                "secondary": second,
                "variants": selected_result["variants"],
                "recheck": {
                    "selected": case_id in selected,
                    "agreement": agreement,
                    **(
                        {"error": agreement_error}
                        if case_id in selected and agreement_error
                        else {}
                    ),
                },
                **(
                    {"quality_limit": "silver_reference_requires_gold_for_pass"}
                    if first["reference_status"] == "silver"
                    and status == "inconclusive"
                    and first["status"] == "passed"
                    else {}
                ),
            }
            record["cases"].append(row)
        case_status = _aggregate_status([row["status"] for row in record["cases"]])
        if _sha(_judge_prompt().encode()) != prompt_hash:
            record["calibration"] = {
                "status": "inconclusive",
                "reason": "judge_prompt_changed_during_run",
            }
        if case_status == "failed":
            record["status"] = "failed"
        elif case_status != "passed" or record["calibration"]["status"] != "passed":
            record["status"] = "inconclusive"
        else:
            record["status"] = "passed"
    except Exception as exc:
        record["status"] = "inconclusive"
        record["error"] = _safe_error(exc)
        if isinstance(exc, WorkerBatchError):
            record["passes"].append(
                {
                    "pass": "primary_failed",
                    "case_ids": ids,
                    "status": "failed",
                    "usage": exc.usage,
                    "artifacts": "passes/primary",
                }
            )
            record["usage"] = _sum_usage(record["passes"])
        for case in cases:
            case_id = case["data"]["case_id"]
            if not any(row["case_id"] == case_id for row in record["cases"]):
                record["cases"].append(
                    {
                        "case_id": case_id,
                        "status": "inconclusive",
                        "reference_status": case["data"]["reference"]["status"],
                        "primary": None,
                        "secondary": None,
                        "variants": {},
                        "recheck": {"selected": False, "agreement": "inconclusive"},
                    }
                )
    return _write_report(Path(output_dir), record)


def _safe_error(exc: Exception) -> dict[str, str]:
    # Do not retain command lines, environment values, stderr, or user credentials.
    message = str(exc)
    for key in ("OPENAI_API_KEY", "STUDIO_API_KEY", "API_KEY"):
        secret = os.environ.get(key)
        if secret:
            message = message.replace(secret, "[redacted]")
    return {"type": type(exc).__name__, "message": message[:1000]}


def _sum_usage(passes: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row.get("usage", {}) for row in passes]
    totals: dict[str, int] = {}
    worker_runs = []
    for row in rows:
        for name, value in row.get("totals", {}).items():
            totals[name] = totals.get(name, 0) + value
        for worker in row.get("workers", []):
            worker_runs.append({"pass": row.get("pass"), **worker})
    incomplete = any(
        row.get("status") == "partial"
        or (row.get("status") == "unavailable" and row.get("started_worker_count", 0) > 0)
        for row in rows
    )
    detail = {
        "passes": len(passes),
        **(
            {
                "execution_mode": "isolated_codex_process",
                "max_concurrency": MAX_PARALLEL_WORKERS,
                "worker_executions": worker_runs,
            }
            if worker_runs
            else {}
        ),
    }
    if not totals:
        return {"status": "partial" if incomplete else "unavailable", **detail}
    return {
        "status": "partial" if incomplete else "reported",
        "scope": "sum of independent per-case Codex process usage",
        "totals": totals,
        **detail,
    }


def _control_rows(controls: Any) -> list[dict[str, Any]]:
    if isinstance(controls, dict):
        controls = controls.get("controls")
    if not isinstance(controls, list) or not controls:
        raise ValueError("Controls must be a non-empty list")
    rows = []
    for item in controls:
        if not isinstance(item, dict):
            raise ValueError("Each calibration control must be an object")
        control_id = _nonempty_text(item.get("id"), "control.id")
        category = item.get("category")
        expected = item.get("expected")
        path = item.get("bundle") or item.get("bundle_path") or item.get("path")
        if not ID_RE.fullmatch(control_id) or category not in CONTROL_CATEGORIES:
            raise ValueError("Calibration control id/category is invalid")
        should_pass = category in POSITIVE_CONTROL_CATEGORIES
        if expected != ("passed" if should_pass else "failed"):
            raise ValueError(
                f"Control {control_id} has an expected verdict inconsistent with {category}"
            )
        if not path:
            raise ValueError(f"Control {control_id} is missing its bundle path")
        bundle = _read_bundle(path)
        rows.append(
            {"id": control_id, "category": category, "expected": expected, "bundle": bundle}
        )
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Calibration control ids must be unique")
    supplied = {row["category"] for row in rows}
    missing = set(CONTROL_CATEGORIES) - supplied
    if missing:
        raise ValueError(
            "Calibration needs clean/paraphrase and every exact defect control: "
            + ", ".join(sorted(missing))
        )
    return rows


def _controls_hash(controls: list[dict[str, Any]]) -> str:
    return _canonical_hash(
        {
            "builder_sha256": _controls_builder_hash(),
            "runner_sha256": _runner_hash(),
            "categories": list(CONTROL_CATEGORIES),
            "controls": [
                {
                    "id": row["id"],
                    "category": row["category"],
                    "expected": row["expected"],
                    "bundle_hash": row["bundle"]["hash"],
                }
                for row in sorted(controls, key=lambda item: item["id"])
            ],
        }
    )


def _controls_builder_hash() -> str | None:
    path = _HERE / "controls.py"
    return _sha(path.read_bytes()) if path.is_file() else None


def _runner_hash() -> str:
    return _sha(Path(__file__).read_bytes())


def _definition_snapshot() -> dict[str, Any]:
    prompt_hashes = _prompt_hashes()
    return {
        "prompt_hashes": prompt_hashes,
        "schema_hashes": dict(SCHEMA_HASHES),
        "prompt_bundle_hash": _canonical_hash(prompt_hashes),
        "schema_bundle_hash": _canonical_hash(SCHEMA_HASHES),
        "controls_builder_sha256": _controls_builder_hash(),
        "runner_sha256": _runner_hash(),
    }


def _required_calibration_categories() -> set[str]:
    return (
        set(POSITIVE_CONTROL_CATEGORIES)
        | SEMANTIC_FINDINGS.intersection(CONTROL_CATEGORIES)
        | VISIBLE_FINDINGS.intersection(CONTROL_CATEGORIES)
    )


def calibrate(
    controls: list[dict[str, Any]],
    output_dir: str | Path,
    *,
    repeats: int = 3,
    timeout: float = 1200,
) -> dict[str, Any]:
    """Run three fresh, blind passes over the complete clean/defect control suite."""
    if repeats != 3:
        raise ValueError("Calibration must run exactly three independent repeats")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    rows = _control_rows(controls)
    deadline = time.monotonic() + timeout
    definitions = _definition_snapshot()
    judge_prompt = _judge_prompt()
    control_hash = _controls_hash(rows)
    categories = {
        category: {
            "status": "inconclusive",
            "repeats": [],
            "expected": "passed" if category in POSITIVE_CONTROL_CATEGORIES else "failed",
        }
        for category in CONTROL_CATEGORIES
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "calibration_version": "luna-max-controls-v6",
        "status": "inconclusive",
        "model": MODEL,
        "model_reasoning_effort": MODEL_REASONING_EFFORT,
        "prompt_version": PROMPT_VERSION,
        **definitions,
        "controls_hash": control_hash,
        "repeats": 3,
        "execution_mode": "isolated_codex_process",
        "max_parallel_workers": MAX_PARALLEL_WORKERS,
        "categories": categories,
        "gates": {name: "inconclusive" for name in ("semantic", "visible", "design")},
        "control_results": [],
        "usage": {"status": "unavailable", "passes": []},
    }
    results: dict[str, list[dict[str, Any] | None]] = {row["id"]: [] for row in rows}
    pass_rows = []
    errors = []
    for repeat in range(1, 4):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append("timeout_before_repeat_" + str(repeat))
            break
        repeat_name = f"repeat-{repeat}"
        artifact_path = Path(output_dir) / "passes" / repeat_name
        judged = {}
        usage = {"status": "unavailable"}
        worker_errors = {}
        try:
            judged, usage, _ = _run_judge_pass(
                [row["bundle"] for row in rows],
                timeout=remaining,
                pass_number=repeat,
                prompt_text=judge_prompt,
                artifact_dir=artifact_path,
            )
            repeat_status = "complete"
        except WorkerBatchError as exc:
            judged, usage = exc.results, exc.usage
            worker_errors = exc.case_errors
            repeat_status = "partial"
            errors.append(
                {
                    "repeat": repeat_name,
                    "error": _safe_error(exc),
                    "case_errors": worker_errors,
                }
            )
        except Exception as exc:
            usage = getattr(exc, "usage", None) or usage
            worker_errors = getattr(exc, "case_errors", {})
            repeat_status = "failed"
            errors.append({"repeat": repeat_name, "error": _safe_error(exc)})
        pass_rows.append(
            {
                "pass": repeat_name,
                "case_ids": [row["bundle"]["data"]["case_id"] for row in rows],
                "status": repeat_status,
                "usage": usage,
                "worker_errors": worker_errors,
                "artifacts": f"passes/{repeat_name}",
            }
        )
        for control in rows:
            case_id = control["bundle"]["data"]["case_id"]
            results[control["id"]].append(judged.get(case_id))
    report["usage"] = _sum_usage(pass_rows)
    report["usage"]["passes_detail"] = pass_rows
    gate_controls = {
        "semantic": {
            "omission",
            "number",
            "negation",
            "condition",
            "table_binding",
            "units",
            "provenance",
        },
        "visible": {"hidden_text", "duplicates", "readability"},
        "design": {"template_fidelity", "readability", "duplicates"},
    }
    all_good = True
    control_statuses: dict[str, list[str]] = {category: [] for category in CONTROL_CATEGORIES}
    positive_design_statuses = []
    for control in rows:
        observed = results[control["id"]]
        repeat_verdicts = []
        for result in observed:
            if result is None:
                repeat_verdicts.append(
                    {
                        "status": "inconclusive",
                        "observed_status": "inconclusive",
                        "expected_category_detected": False,
                        "design_status": "inconclusive"
                        if control["category"] in POSITIVE_CONTROL_CATEGORIES
                        else None,
                    }
                )
                continue
            all_variants = list(result["variants"].values())
            all_findings = [finding for variant in all_variants for finding in variant["findings"]]
            targeted = [
                finding for finding in all_findings if finding["category"] == control["category"]
            ]
            if control["expected"] == "failed":
                targeted_variants = sorted(
                    name
                    for name, variant in result["variants"].items()
                    if any(
                        finding["category"] == control["category"]
                        and finding["severity"] in {"critical", "major"}
                        for finding in variant["findings"]
                    )
                )
                relevant_gates = {
                    "template_fidelity": ("design",),
                    "hidden_text": ("visible",),
                    "readability": ("visible",),
                    "duplicates": ("visible",),
                }.get(control["category"], ("semantic",))
                gate_failed_variants = sorted(
                    name
                    for name, variant in result["variants"].items()
                    if any(
                        variant["categories"][gate]["status"] == "failed" for gate in relevant_gates
                    )
                )
                required_variants = sorted(result["variants"])
                repeat_ok = (
                    bool(required_variants)
                    and set(targeted_variants) == set(required_variants)
                    and set(gate_failed_variants) == set(required_variants)
                )
                design_status = None
            else:
                blocking_false_positive = any(
                    finding["severity"] in {"critical", "major"}
                    and finding["category"] in (SEMANTIC_FINDINGS | VISIBLE_FINDINGS)
                    for finding in all_findings
                )
                # Design remains advisory to the content/render-quality gate.
                repeat_ok = result["status"] == "passed" and not blocking_false_positive
                design_states = [
                    variant["categories"]["design"]["status"] for variant in all_variants
                ]
                design_false_positive = any(
                    finding["category"] in DESIGN_FINDINGS
                    and finding["severity"] in {"critical", "major"}
                    for finding in all_findings
                )
                design_status = (
                    "failed"
                    if design_false_positive or "failed" in design_states
                    else "inconclusive"
                    if "inconclusive" in design_states
                    else "passed"
                )
                positive_design_statuses.append(design_status)
            repeat_verdicts.append(
                {
                    "status": "passed" if repeat_ok else "failed",
                    "observed_status": result["status"],
                    "expected_category_detected": bool(targeted),
                    **(
                        {
                            "required_variants": required_variants,
                            "detected_variants": targeted_variants,
                            "gate_failed_variants": gate_failed_variants,
                            "detection_scope": "casewide_all_variants"
                            if control["category"] == "duplicates"
                            else "all_variants",
                        }
                        if control["expected"] == "failed"
                        else {}
                    ),
                    **({"design_status": design_status} if design_status is not None else {}),
                }
            )
        control_status = (
            "failed"
            if any(item["status"] == "failed" for item in repeat_verdicts)
            else "passed"
            if len(repeat_verdicts) == 3
            and all(item["status"] == "passed" for item in repeat_verdicts)
            else "inconclusive"
        )
        control_statuses[control["category"]].append(control_status)
        categories[control["category"]]["repeats"].append(
            {"control_id": control["id"], "verdicts": repeat_verdicts}
        )
        if control["category"] in POSITIVE_CONTROL_CATEGORIES:
            categories[control["category"]]["design_status"] = (
                "failed"
                if any(verdict.get("design_status") == "failed" for verdict in repeat_verdicts)
                else "passed"
                if len(repeat_verdicts) == 3
                and all(item.get("design_status") == "passed" for item in repeat_verdicts)
                else "inconclusive"
            )
        report["control_results"].append(
            {
                "id": control["id"],
                "category": control["category"],
                "expected": control["expected"],
                "status": control_status,
                **(
                    {"design_status": categories[control["category"]]["design_status"]}
                    if control["category"] in POSITIVE_CONTROL_CATEGORIES
                    else {}
                ),
            }
        )
    for category, statuses in control_statuses.items():
        categories[category]["status"] = (
            "failed"
            if "failed" in statuses
            else "inconclusive"
            if "inconclusive" in statuses or not statuses
            else "passed"
        )
    report["positive_design_status"] = (
        "failed"
        if "failed" in positive_design_statuses
        else "passed"
        if len(positive_design_statuses) == 3 * len(POSITIVE_CONTROL_CATEGORIES)
        and all(status == "passed" for status in positive_design_statuses)
        else "inconclusive"
    )
    for gate, labels in gate_controls.items():
        gate_statuses = [categories[label]["status"] for label in labels]
        if gate in {"semantic", "visible"}:
            gate_statuses.extend(
                categories[label]["status"] for label in POSITIVE_CONTROL_CATEGORIES
            )
        else:
            gate_statuses.append(report["positive_design_status"])
        report["gates"][gate] = (
            "failed"
            if "failed" in gate_statuses
            else "passed"
            if all(status == "passed" for status in gate_statuses)
            else "inconclusive"
        )
        # Design sensitivity is reported separately and remains advisory. A
        # missing semantic or visible-defect detector blocks certification.
        if gate in {"semantic", "visible"}:
            all_good = all_good and report["gates"][gate] == "passed"
    if _definition_snapshot() != definitions:
        errors.append("prompt_schema_runner_or_control_builder_changed_during_calibration")
        all_good = False
    if errors:
        report["errors"] = errors
    required_incomplete = any(
        categories[label]["status"] == "inconclusive"
        for label in _required_calibration_categories()
    ) or any(report["gates"][gate] == "inconclusive" for gate in ("semantic", "visible"))
    report["status"] = "passed" if all_good else "inconclusive" if required_incomplete else "failed"
    return _write_report(Path(output_dir), report)


def _compare_signature(case_result: dict[str, Any]) -> str:
    normalized = []
    for item in case_result["comparisons"]:
        sides = {}
        for side, side_result in item["sides"].items():
            sides[side] = {
                "status": side_result["status"],
                "categories": {
                    name: side_result["categories"][name]["status"]
                    for name in ("semantic", "visible")
                },
                "points": [
                    {key: point[key] for key in ("point_id", "status")}
                    for point in side_result["points"]
                ],
                "findings": [
                    {
                        "category": finding["category"],
                        "severity": finding["severity"],
                        "slide_number": finding["slide_number"],
                        "region": re.sub(r"[^a-z0-9]+", " ", finding["region"].casefold()).strip(),
                        "point_ids": sorted(finding["point_ids"]),
                    }
                    for finding in side_result["findings"]
                    if finding["severity"] in {"critical", "major"}
                    and finding["category"] in (SEMANTIC_FINDINGS | VISIBLE_FINDINGS)
                ],
            }
        normalized.append(
            {
                "pair_id": item["variant"],
                "sides": sides,
                "winners": {name: item["winners"][name] for name in ("semantic", "visible")},
            }
        )
    return _canonical_hash(normalized)


def _prepare_compare_case(
    pair: dict[str, Any],
    *,
    blind_seed: str,
    pass_number: int,
    case_number: int,
) -> tuple[Path, list[dict[str, Any]], list[Path], dict[str, str]]:
    scratch = Path(tempfile.mkdtemp(prefix="presentation-compare-worker-"))
    packet_paths = []
    image_paths = []
    meta_cases = []
    case_alias = f"case-{case_number:03d}"
    left, right = pair["baseline"], pair["candidate"]
    mapping = _comparison_mapping(pair, blind_seed)
    pairs = []
    try:
        for pair_index, variant in enumerate(sorted(left["data"]["variants"]), 1):
            side_a_is_left = mapping[variant] == "baseline_A"
            decks = {
                "A": left if side_a_is_left else right,
                "B": right if side_a_is_left else left,
            }
            side_docs = {}
            side_expected = {}
            side_pdf_visible = {}
            for side, bundle in decks.items():
                variant_data = bundle["data"]["variants"][variant]
                slides = []
                slide_texts = {}
                pdf_visible_texts = {}
                for slide in variant_data["slides"]:
                    source_image = next(
                        row["path"]
                        for row in bundle["images"]
                        if row["variant"] == variant and row["number"] == slide["number"]
                    )
                    relative = (
                        Path("assets")
                        / case_alias
                        / f"pair-{pair_index:02d}"
                        / side
                        / f"slide-{slide['number']:03d}.png"
                    )
                    destination = scratch / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_image, destination)
                    image_paths.append(destination)
                    slides.append(
                        {
                            "number": slide["number"],
                            "image": relative.as_posix(),
                            "text": slide["text"],
                            "objects": slide["objects"],
                            **(
                                {"pdf_visible_text": slide["pdf_visible_text"]}
                                if "pdf_visible_text" in slide
                                else {}
                            ),
                        }
                    )
                    slide_texts[slide["number"]] = slide["text"]
                    pdf_visible_texts[slide["number"]] = slide.get("pdf_visible_text", "")
                side_docs[side] = {"slides": slides}
                side_expected[side] = slide_texts
                side_pdf_visible[side] = pdf_visible_texts
            source_context = {
                side: [
                    {
                        "slide_number": proposal.get("slide_number"),
                        "text": proposal.get("text", ""),
                        "origin": proposal.get("origin", "approved_proposal"),
                        "fact_ids": proposal.get("fact_ids", []),
                    }
                    for proposal in bundle["data"]["source"].get("approved_proposals", [])
                ]
                for side, bundle in decks.items()
            }
            pair_id = f"P{pair_index:02d}"
            pairs.append({"pair_id": pair_id, "sides": side_docs, "source_context": source_context})
            meta_cases.append(
                {
                    "case_alias": case_alias,
                    "pair_id": pair_id,
                    "original_id": pair["case_id"],
                    "variant": variant,
                    "reference": left["data"]["reference"],
                    "expected": side_expected,
                    "pdf_visible_texts": side_pdf_visible,
                    "objects": {
                        side: {
                            slide["number"]: slide["objects"]
                            for slide in bundle["data"]["variants"][variant]["slides"]
                        }
                        for side, bundle in decks.items()
                    },
                }
            )
        shared, shared_images = _copy_reference_assets(left, scratch, case_alias)
        image_paths.extend(shared_images)
        packet = {
            "schema_version": SCHEMA_VERSION,
            "case_id": case_alias,
            "source": {key: left["data"]["source"][key] for key in ("text", "sha256")},
            "reference": _packet_reference(left["data"]["reference"]),
            "comparisons": pairs,
            **shared,
        }
        packet_path = scratch / "cases" / f"{case_alias}.json"
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        packet_paths.append(packet_path)
        _write_index(scratch, packet_paths)
        _write_image_manifest(scratch, image_paths)
        return scratch, meta_cases, image_paths, mapping
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


def _comparison_mapping(pair: dict[str, Any], blind_seed: str) -> dict[str, str]:
    return {
        variant: (
            "baseline_A"
            if int(_sha(f"{blind_seed}:{pair['case_id']}:{variant}".encode())[:16], 16) & 1
            else "baseline_B"
        )
        for variant in sorted(pair["baseline"]["data"]["variants"])
    }


def _run_compare_pass(
    cases: list[dict[str, Any]],
    *,
    timeout: float,
    blind_seed: str,
    pass_number: int,
    artifact_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, str]]]:
    if not cases:
        raise ValueError("At least one comparison case is required")
    deadline = time.monotonic() + timeout
    results: dict[str, Any] = {}
    mappings: dict[str, dict[str, str]] = {}
    worker_rows = []
    errors = []
    errors_by_case = {}

    def run_one(index: int, pair: dict[str, Any]):
        case_alias = f"case-{index + 1:03d}"
        worker_dir = Path(artifact_dir) / f"worker-{index + 1:03d}" if artifact_dir else None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Comparison batch timed out before this case worker started")
        scratch, meta, images, mapping = _prepare_compare_case(
            pair,
            blind_seed=blind_seed,
            pass_number=pass_number + index,
            case_number=index + 1,
        )
        worker_schema = _bound_compare_case_schema(meta)
        usage = None
        try:
            raw, usage = _run_codex(
                scratch,
                _compare_prompt(),
                worker_schema,
                images,
                timeout=remaining,
                artifact_dir=worker_dir,
                worker_alias=case_alias,
            )
            wrapped = {"schema_version": raw.get("schema_version"), "cases": [raw.get("case")]}
            validated = _validate_compare_response(wrapped, meta)
            usage["worker_execution"]["status"] = "validated"
            _write_worker_usage(worker_dir, usage)
            if worker_dir is not None:
                worker_dir.mkdir(parents=True, exist_ok=True)
                (worker_dir / "blind-map.json").write_text(
                    json.dumps({pair["case_id"]: mapping}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            case_id = pair["case_id"]
            return case_id, validated[case_id], usage, mapping
        except Exception as exc:
            if usage is None and worker_dir is not None:
                usage = _saved_usage(worker_dir)
            if usage is not None:
                usage.setdefault("worker_execution", {})["status"] = "failed"
                _write_worker_usage(worker_dir, usage)
            _write_worker_error(worker_dir, exc)
            raise WorkerExecutionError(case_alias, exc, usage, worker_dir) from exc
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_WORKERS, len(cases))) as pool:
        futures = {
            pool.submit(run_one, index, pair): (index, pair) for index, pair in enumerate(cases)
        }
        for future in as_completed(futures):
            index, pair = futures[future]
            case_alias = f"case-{index + 1:03d}"
            worker_dir = Path(artifact_dir) / f"worker-{index + 1:03d}" if artifact_dir else None
            try:
                case_id, result, usage, mapping = future.result()
                results[case_id] = result
                mappings[case_id] = mapping
                worker_rows.append(
                    {
                        "case_alias": case_alias,
                        "status": "validated",
                        "started": True,
                        "usage": usage,
                        "artifacts": f"worker-{index + 1:03d}" if worker_dir else None,
                    }
                )
            except Exception as exc:
                usage = getattr(exc, "usage", None) or (
                    _saved_usage(worker_dir) if worker_dir is not None else None
                )
                error = _safe_error(exc)
                errors.append(error)
                errors_by_case[pair["case_id"]] = error
                worker_rows.append(
                    {
                        "case_alias": case_alias,
                        "status": "failed",
                        "started": usage is not None,
                        "usage": usage,
                        "artifacts": f"worker-{index + 1:03d}" if worker_dir else None,
                        "error": _safe_error(exc),
                    }
                )
    worker_rows.sort(key=lambda row: row["case_alias"])
    usage = _aggregate_worker_usage(worker_rows)
    if errors:
        raise WorkerBatchError(
            f"{len(errors)} of {len(cases)} independent comparison workers failed",
            usage=usage,
            workers=worker_rows,
            results=results,
            case_errors=errors_by_case,
            meta={"mappings": mappings},
        )
    return results, usage, mappings


def _validate_compare_response(
    raw: Any, meta_cases: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != SCHEMA_VERSION
        or not isinstance(raw.get("cases"), list)
    ):
        raise ValueError("Structured comparison result has the wrong schema version")
    expected = {(row["case_alias"], row["pair_id"]): row for row in meta_cases}
    if len(raw["cases"]) != len({key[0] for key in expected}):
        raise ValueError("Structured comparison result omitted or added cases")
    result = {}
    for case in raw["cases"]:
        alias = case.get("case_id") if isinstance(case, dict) else None
        case_meta = [row for row in meta_cases if row["case_alias"] == alias]
        if (
            not case_meta
            or not isinstance(case.get("comparisons"), list)
            or len(case["comparisons"]) != len(case_meta)
        ):
            raise ValueError("Comparison case is incomplete")
        comparisons = []
        for row in case["comparisons"]:
            meta = expected.get((alias, row.get("pair_id"))) if isinstance(row, dict) else None
            if meta is None:
                raise ValueError("Comparison pair id is unknown")
            if not isinstance(row.get("sides"), dict) or set(row["sides"]) != {"A", "B"}:
                raise ValueError("Both blind comparison sides are required")
            sides = {}
            for side in ("A", "B"):
                side_result = {**row["sides"][side], "variant_id": side}
                if any(
                    finding.get("category") == "duplicates"
                    for finding in side_result.get("findings", [])
                    if isinstance(finding, dict)
                ):
                    raise ValueError("Paired A/B equality is not a within-run duplicate defect")
                hidden = {
                    number: "\n".join(
                        str(obj.get("text", ""))
                        for obj in meta["objects"][side][number]
                        if isinstance(obj, dict)
                        and obj.get("text")
                        and (obj.get("hidden") or obj.get("out_of_bounds"))
                    )
                    for number in meta["expected"][side]
                }
                sides[side] = _validate_variant(
                    side_result,
                    alias=side,
                    slide_texts=meta["expected"][side],
                    pdf_visible_texts=meta["pdf_visible_texts"][side],
                    hidden_texts=hidden,
                    reference_points=meta["reference"]["points"],
                )
            winners = row.get("winners")
            if (
                not isinstance(winners, dict)
                or set(winners) != {"semantic", "visible", "design"}
                or any(value not in {"A", "B", "tie", "inconclusive"} for value in winners.values())
            ):
                raise ValueError("Comparison category winners are incomplete")
            rationale = row.get("rationale")
            if not isinstance(rationale, str):
                raise ValueError("Comparison rationale must be text")
            comparisons.append(
                {
                    "variant": meta["variant"],
                    "sides": sides,
                    "winners": winners,
                    "rationale": rationale,
                }
            )
        original_id = case_meta[0]["original_id"]
        result[original_id] = {
            "case_id": original_id,
            "reference_status": case_meta[0]["reference"]["status"],
            "status": _aggregate_status(
                [
                    side["status"]
                    for comparison in comparisons
                    for side in comparison["sides"].values()
                ]
                + [
                    "inconclusive" if comparison["winners"][name] == "inconclusive" else "passed"
                    for comparison in comparisons
                    for name in ("semantic", "visible")
                ]
            ),
            "comparisons": comparisons,
        }
        if (
            result[original_id]["reference_status"] == "silver"
            and result[original_id]["status"] == "passed"
        ):
            result[original_id]["status"] = "inconclusive"
    if len(result) != len({row["original_id"] for row in meta_cases}):
        raise ValueError("Comparison response omitted a case")
    return result


def compare_cases(
    baseline_bundles: list[str | Path],
    candidate_bundles: list[str | Path],
    output_dir: str | Path,
    *,
    timeout: float = 1200,
    calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Blindly compare paired variants; winners remain advisory."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    baseline_rows = [_read_bundle(path) for path in baseline_bundles]
    candidate_rows = [_read_bundle(path) for path in candidate_bundles]
    baseline = {row["data"]["case_id"]: row for row in baseline_rows}
    candidate = {row["data"]["case_id"]: row for row in candidate_rows}
    if len(baseline) != len(baseline_rows) or len(candidate) != len(candidate_rows):
        raise ValueError("Baseline and candidate case ids must be unique")
    if not baseline or set(baseline) != set(candidate):
        raise ValueError("Baseline and candidate must contain the same non-empty case set")
    pairs = []
    for case_id in sorted(baseline):
        left, right = baseline[case_id], candidate[case_id]
        source_left, source_right = left["data"]["source"], right["data"]["source"]
        if (source_left["text"], source_left["sha256"]) != (
            source_right["text"],
            source_right["sha256"],
        ):
            raise ValueError(f"{case_id}: baseline and candidate source text must match exactly")

        def image_identity(bundle):
            return [
                (
                    image["sha256"],
                    image.get("pixels_sha256"),
                    image.get("registered_sha256"),
                )
                for image in bundle["data"]["source_images"]
            ]

        if image_identity(left) != image_identity(right):
            raise ValueError(f"{case_id}: baseline and candidate source images must match exactly")

        def reference_identity(bundle):
            return {
                key: bundle["data"]["reference"][key]
                for key in ("version", "status", "provenance", "points", "requirements")
            }

        if reference_identity(left) != reference_identity(right):
            raise ValueError(f"{case_id}: baseline and candidate reference must match exactly")

        def template_identity(bundle):
            template = bundle["data"]["template"] or {}
            return (
                {key: value for key, value in template.items() if key != "previews"},
                [
                    (preview.get("source_slide"), preview["sha256"])
                    for preview in template.get("previews", [])
                ],
            )

        if template_identity(left) != template_identity(right):
            raise ValueError(
                f"{case_id}: baseline and candidate template identity must match exactly"
            )
        if set(left["data"]["variants"]) != set(right["data"]["variants"]):
            raise ValueError(f"{case_id}: baseline and candidate variant sets must match")
        pairs.append({"case_id": case_id, "baseline": left, "candidate": right})
    deadline = time.monotonic() + timeout
    blind_seed = _canonical_hash(
        {"case_ids": sorted(baseline), "purpose": "presentation-paired-eval-v1"}
    )
    calibration_status = _calibration_validation(calibration)
    document = {
        "schema_version": SCHEMA_VERSION,
        "mode": "compare",
        "status": "inconclusive",
        "advisory": True,
        "model": MODEL,
        "model_reasoning_effort": MODEL_REASONING_EFFORT,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": _prompt_hashes()["compare"],
        "schema_hash": SCHEMA_HASHES["compare_case"],
        "execution_mode": "isolated_codex_process",
        "max_parallel_workers": MAX_PARALLEL_WORKERS,
        "controls_hash": calibration_status.get("controls_hash"),
        "calibration": calibration_status,
        "usage": {"status": "unavailable", "passes": []},
        "passes": [],
        "blind_mapping": {},
        "cases": [],
        "bundle_hashes": {
            case_id: {
                "baseline": baseline[case_id]["hash"],
                "candidate": candidate[case_id]["hash"],
            }
            for case_id in sorted(baseline)
        },
    }
    expected_mapping = {pair["case_id"]: _comparison_mapping(pair, blind_seed) for pair in pairs}
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Comparison timed out before the first pass")
        primary_errors = {}
        try:
            primary, usage, primary_mapping = _run_compare_pass(
                pairs,
                timeout=remaining,
                blind_seed=blind_seed,
                pass_number=0,
                artifact_dir=Path(output_dir) / "passes" / "primary",
            )
        except WorkerBatchError as exc:
            primary, usage, primary_mapping = exc.results, exc.usage, exc.meta.get("mappings", {})
            primary_errors = exc.case_errors
        document["passes"].append(
            {
                "pass": "primary",
                "case_ids": sorted(baseline),
                "status": "partial" if primary_errors else "complete",
                "usage": usage,
                "worker_errors": primary_errors,
                "artifacts": "passes/primary",
            }
        )
        document["usage"] = _sum_usage(document["passes"])
        if any(
            primary_mapping.get(case_id) != expected_mapping[case_id] for case_id in primary_mapping
        ):
            raise ValueError("Primary blind A/B side mapping did not match its deterministic seed")
        document["blind_mapping"] = expected_mapping
        successful = [case_id for case_id, row in primary.items() if row["status"] == "passed"]
        selected = {case_id for case_id, row in primary.items() if row["status"] != "passed"}
        selected.update(primary_errors)
        selected.update(set(baseline) - set(primary))
        if successful:
            selected.update(
                sorted(successful, key=lambda value: _sha(value.encode()))[
                    : math.ceil(0.2 * len(successful))
                ]
            )
        secondary = {}
        secondary_errors = {}
        recheck_error = None
        if selected:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                recheck_error = "timeout_before_recheck"
            else:
                try:
                    selected_pairs = [pair for pair in pairs if pair["case_id"] in selected]
                    secondary, second_usage, second_mapping = _run_compare_pass(
                        selected_pairs,
                        timeout=remaining,
                        blind_seed=blind_seed,
                        pass_number=1,
                        artifact_dir=Path(output_dir) / "passes" / "secondary",
                    )
                    if any(
                        second_mapping.get(case_id) != expected_mapping[case_id]
                        for case_id in second_mapping
                    ):
                        raise ValueError(
                            "Blind A/B side mapping changed between independent passes"
                        )
                    document["passes"].append(
                        {
                            "pass": "secondary",
                            "case_ids": sorted(selected),
                            "usage": second_usage,
                            "artifacts": "passes/secondary",
                        }
                    )
                    document["usage"] = _sum_usage(document["passes"])
                except WorkerBatchError as exc:
                    secondary, second_usage = exc.results, exc.usage
                    secondary_errors = exc.case_errors
                    document["passes"].append(
                        {
                            "pass": "secondary",
                            "case_ids": sorted(selected),
                            "status": "partial",
                            "usage": second_usage,
                            "worker_errors": secondary_errors,
                            "artifacts": "passes/secondary",
                        }
                    )
                    document["usage"] = _sum_usage(document["passes"])
                except Exception as exc:
                    recheck_error = _safe_error(exc)
                    if isinstance(exc, WorkerBatchError):
                        document["passes"].append(
                            {
                                "pass": "secondary_failed",
                                "case_ids": sorted(selected),
                                "status": "failed",
                                "usage": exc.usage,
                                "artifacts": "passes/secondary",
                            }
                        )
                        document["usage"] = _sum_usage(document["passes"])
        for case_id in sorted(baseline):
            first, second = primary.get(case_id), secondary.get(case_id)
            if first is None:
                second_error = secondary_errors.get(case_id) or recheck_error
                document["cases"].append(
                    {
                        "case_id": case_id,
                        "status": "inconclusive",
                        "observed_status": "invalid_primary",
                        "reference_status": baseline[case_id]["data"]["reference"]["status"],
                        "approved_proposals": {
                            "baseline": baseline[case_id]["data"]["source"].get(
                                "approved_proposals", []
                            ),
                            "candidate": candidate[case_id]["data"]["source"].get(
                                "approved_proposals", []
                            ),
                        },
                        "primary": None,
                        "secondary": second,
                        "comparisons": second["comparisons"] if second else [],
                        "recheck": {
                            "selected": case_id in selected,
                            "agreement": "inconclusive",
                            **({"error": second_error} if second_error else {}),
                        },
                        "error": primary_errors.get(case_id, {"message": "primary_worker_failed"}),
                    }
                )
                continue
            agreement = "not_selected"
            if case_id in selected:
                if recheck_error or second is None or case_id in secondary_errors:
                    agreement = "inconclusive"
                elif _compare_signature(first) == _compare_signature(second):
                    agreement = "agreed"
                else:
                    agreement = "disagreed"
            status = (
                "inconclusive" if agreement in {"inconclusive", "disagreed"} else first["status"]
            )
            if first.get("reference_status") == "silver" and status == "passed":
                status = "inconclusive"
            observed_status = status
            if status == "failed" and calibration_status["status"] != "passed":
                status = "inconclusive"
            document["cases"].append(
                {
                    "case_id": case_id,
                    "status": status,
                    "observed_status": observed_status,
                    "reference_status": first.get("reference_status"),
                    "approved_proposals": {
                        "baseline": baseline[case_id]["data"]["source"].get(
                            "approved_proposals", []
                        ),
                        "candidate": candidate[case_id]["data"]["source"].get(
                            "approved_proposals", []
                        ),
                    },
                    "primary": first,
                    "secondary": second,
                    "comparisons": (second if agreement == "agreed" else first)["comparisons"],
                    "recheck": {
                        "selected": case_id in selected,
                        "agreement": agreement,
                        **(
                            {"error": recheck_error}
                            if case_id in selected and recheck_error
                            else {}
                        ),
                    },
                }
            )
        overall = _aggregate_status([row["status"] for row in document["cases"]])
        if overall == "passed" and calibration_status["status"] != "passed":
            overall = "inconclusive"
        document["status"] = overall
    except Exception as exc:
        document["status"] = "inconclusive"
        document["error"] = _safe_error(exc)
        if isinstance(exc, WorkerBatchError):
            document["passes"].append(
                {
                    "pass": "primary_failed",
                    "case_ids": sorted(baseline),
                    "status": "failed",
                    "usage": exc.usage,
                    "artifacts": "passes/primary",
                }
            )
            document["usage"] = _sum_usage(document["passes"])
        for case_id in sorted(baseline):
            if not any(row.get("case_id") == case_id for row in document["cases"]):
                document["cases"].append(
                    {
                        "case_id": case_id,
                        "status": "inconclusive",
                        "primary": None,
                        "secondary": None,
                        "comparisons": [],
                        "recheck": {"selected": False, "agreement": "inconclusive"},
                    }
                )
    return _write_report(Path(output_dir), document)
