"""Versioned evaluation inputs with fail-closed source validation."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil

from PIL import Image, ImageDraw

from .reporting import AUDIT_ROOT, ROOT

CASE_FILE = AUDIT_ROOT / "fixtures/cases.json"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_POINT_KEYS = {"id", "statement", "quote", "required", "origin"}


def _digest(data):
    return sha256(data).hexdigest()


def _validate_registry(payload):
    if payload.get("schema_version") != 2 or not isinstance(payload.get("cases"), list):
        raise ValueError("Unsupported evaluation corpus schema")
    if not isinstance(payload.get("reference_version"), str) or not payload["reference_version"]:
        raise ValueError("Missing reference version")
    if payload.get("reference_provenance", {}).get("method") != "source_anchored_agent_authored":
        raise ValueError("Missing reference provenance method")
    ids = set()
    for case in payload["cases"]:
        required = {
            "id",
            "suite",
            "template",
            "content_source",
            "content",
            "images",
            "slides",
            "variants",
            "synthetic_template",
        }
        if not required <= case.keys():
            raise ValueError(f"Incomplete evaluation case: {case.get('id', '<unknown>')}")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError(f"Invalid or duplicate evaluation case id: {case_id!r}")
        ids.add(case_id)
        if case["suite"] not in {"core", "extended"}:
            raise ValueError(f"Invalid suite for {case_id}")
        if not isinstance(case["content"], str):
            raise ValueError(f"Case content must be text: {case_id}")
        source = case["content_source"]
        if (
            not _HASH.fullmatch(source.get("sha256", ""))
            or _digest(case["content"].encode()) != source["sha256"]
        ):
            raise ValueError(f"Registered content hash mismatch: {case_id}")
        template = case["template"]
        if not _HASH.fullmatch(template.get("sha256", "")) or template.get("format") not in {
            "PPTX",
            "POTX",
        }:
            raise ValueError(f"Invalid template metadata: {case_id}")
        if not Path(template.get("file", "")).name == template.get("file"):
            raise ValueError(f"Template file must be a basename: {case_id}")
        if type(case["slides"]) is not int or not 1 <= case["slides"] <= 30 or not case["variants"]:
            raise ValueError(f"Invalid slide or variant count: {case_id}")
        interface = case.get("interface", "http")
        if interface not in {"http", "browser"}:
            raise ValueError(f"Invalid interface for {case_id}")
        slide_contract = case.get("slide_contract")
        if interface == "browser" and not isinstance(slide_contract, dict):
            raise ValueError(f"Browser case needs an explicit slide contract: {case_id}")
        if slide_contract is not None:
            target, minimum, maximum = (
                slide_contract.get(key) for key in ("target", "minimum", "maximum")
            )
            if (
                any(type(value) is not int for value in (target, minimum, maximum))
                or target != case["slides"]
                or not 1 <= minimum <= target <= maximum <= 30
            ):
                raise ValueError(f"Invalid slide contract for {case_id}")
            request_kind = slide_contract.get("request_kind")
            if request_kind == "browser_preset":
                if (
                    interface != "browser"
                    or slide_contract.get("preset") != "mini"
                    or (minimum, maximum) != (3, 5)
                ):
                    raise ValueError(f"Invalid browser preset contract for {case_id}")
            elif request_kind == "explicit_count":
                if interface != "http" or (minimum, maximum) != (target, target):
                    raise ValueError(f"Invalid explicit-count contract for {case_id}")
            else:
                raise ValueError(f"Unknown slide contract request kind for {case_id}")
        image_names = set()
        for image in case["images"]:
            if (
                not Path(image.get("file", "")).name == image.get("file")
                or image["file"] in image_names
            ):
                raise ValueError(f"Invalid or duplicate image entry: {case_id}")
            image_names.add(image["file"])
            if not _HASH.fullmatch(image.get("sha256", "")):
                raise ValueError(f"Invalid image hash: {case_id}/{image['file']}")
        reference = case.get("reference")
        if reference:
            if reference.get("status") not in {"gold", "silver"} or not reference.get("version"):
                raise ValueError(f"Invalid reference status/version: {case_id}")
            provenance = reference.get("provenance", payload["reference_provenance"])
            if reference["status"] == "gold" and provenance.get("review_status") != "approved":
                raise ValueError(f"Gold reference requires explicit approval: {case_id}")
            point_ids = set()
            for point in reference.get("points", []):
                if not _POINT_KEYS <= point.keys() or point.get("origin") not in {
                    "user_text",
                    "model_proposal",
                }:
                    raise ValueError(f"Invalid reference point: {case_id}")
                if (
                    point["id"] in point_ids
                    or not point["quote"]
                    or point["quote"] not in case["content"]
                ):
                    raise ValueError(
                        f"Invalid or unanchored reference quote: {case_id}/{point.get('id')}"
                    )
                point_ids.add(point["id"])
    return payload


def load_cases(suite="core"):
    """Return four core or nine extended cases with references resolved."""
    if suite not in {"core", "extended"}:
        raise ValueError("suite must be 'core' or 'extended'")
    payload = _validate_registry(json.loads(CASE_FILE.read_text(encoding="utf-8")))
    references = {item["id"]: item["reference"] for item in payload["cases"] if "reference" in item}
    cases = []
    for raw in payload["cases"]:
        if suite == "core" and raw["suite"] != "core":
            continue
        case = deepcopy(raw)
        case.setdefault(
            "slide_contract",
            {
                "target": case["slides"],
                "minimum": case["slides"],
                "maximum": case["slides"],
                "request_kind": "explicit_count",
            },
        )
        inherited_id = case.pop("reference_case", None)
        local_requirements = case.pop("reference_requirements", [])
        if inherited_id:
            if inherited_id not in references:
                raise ValueError(f"Unknown reference case: {inherited_id}")
            case["reference"] = deepcopy(references[inherited_id])
        case.setdefault("reference", {"points": [], "requirements": []})
        case["reference"]["requirements"].extend(local_requirements)
        case["reference"].setdefault("version", payload["reference_version"])
        case["reference"].setdefault("status", "silver")
        case["reference"].setdefault("provenance", deepcopy(payload["reference_provenance"]))
        if any(point["quote"] not in case["content"] for point in case["reference"]["points"]):
            raise ValueError(f"Reference quote does not anchor in {case['id']}")
        cases.append(case)
    return cases


def _inputs_root(corpus_root):
    corpus_root = Path(corpus_root)
    if corpus_root.name == "external-template-corpus":
        return corpus_root.parent / "external-template-runs" / "inputs"
    if corpus_root.name == "test-results":
        return corpus_root / "external-template-runs" / "inputs"
    return corpus_root / "inputs"


def _synthetic_dashboard(path):
    image = Image.new("RGB", (640, 360), "#f3f6fa")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 640, 54), fill="#123f62")
    draw.text((22, 17), "SYNTHETIC SAMPLE DASHBOARD", fill="white")
    for index, label in enumerate(("NEW", "CLOSED", "HOURS")):
        x = 22 + index * 202
        draw.rounded_rectangle((x, 76, x + 180, 145), radius=8, fill="white", outline="#c8d5e2")
        draw.text((x + 14, 88), label, fill="#486176")
        draw.text((x + 14, 112), ("40", "34", "18")[index], fill="#123f62")
    draw.rectangle((22, 168, 618, 330), fill="white", outline="#c8d5e2")
    points = [(54, 284), (150, 250), (246, 263), (342, 218), (438, 236), (574, 190)]
    draw.line(points, fill="#e67e22", width=5)
    for x, y in points:
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#e67e22")
    image.save(path, format="PNG")
    return path


def materialize_case(case, directory, *, synthetic=False, corpus_root=None):
    """Validate registered bytes and freeze them under the case input directory."""
    result = deepcopy(case)
    if not result.get("id") or not _HASH.fullmatch(
        result.get("content_source", {}).get("sha256", "")
    ):
        raise ValueError("Case is missing its registered identity/content hash")
    content_hash = _digest(result["content"].encode("utf-8"))
    if content_hash != result["content_source"]["sha256"]:
        raise ValueError(f"Registered content hash mismatch: {result['id']}")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    template_info = deepcopy(result["template"])
    image_sources = deepcopy(result.get("images", []))
    if corpus_root is None:
        corpus_root = ROOT / "test-results" / "external-template-corpus"
    corpus_root = Path(corpus_root)

    source_content_path = directory / "source.md"
    source_content_path.write_text(result["content"], encoding="utf-8")
    if _digest(source_content_path.read_bytes()) != content_hash:
        raise ValueError(f"Could not freeze source text: {result['id']}")

    if synthetic:
        from test_support.inputs import make_template

        suffix = ".potx" if template_info["format"] == "POTX" else ".pptx"
        template_path = directory / f"synthetic-{result['id']}{suffix}"
        config = result["synthetic_template"]
        make_template(
            template_path,
            seed=config["seed"],
            aspect=config["aspect"],
            columns=config["columns"],
            dark=config["dark"],
        )
        image_paths = [_synthetic_dashboard(directory / item["file"]) for item in image_sources]
        template_origin = image_origin = "synthetic_analog"
    else:
        source_template = corpus_root / template_info["file"]
        if not source_template.is_file():
            raise FileNotFoundError(f"Missing external evaluation template: {source_template}")
        actual = _digest(source_template.read_bytes())
        if actual != template_info["sha256"]:
            raise ValueError(f"Registered template hash mismatch: {result['id']}")
        template_path = directory / template_info["file"]
        shutil.copy2(source_template, template_path)
        image_root = _inputs_root(corpus_root)
        image_paths = []
        for item in image_sources:
            source_image = image_root / item["file"]
            if not source_image.is_file():
                raise FileNotFoundError(f"Missing external evaluation image: {source_image}")
            if _digest(source_image.read_bytes()) != item["sha256"]:
                raise ValueError(f"Registered image hash mismatch: {result['id']}/{item['file']}")
            image_path = directory / item["file"]
            shutil.copy2(source_image, image_path)
            image_paths.append(image_path)
        template_origin = image_origin = "real_external_template"

    actual_template_hash = _digest(template_path.read_bytes())
    actual_image_hashes = {path.name: _digest(path.read_bytes()) for path in image_paths}
    if synthetic:
        for item in result.get("reference", {}).get("requirements", []):
            if item.get("id") == "template-fidelity" or item.get("kind") == "template-fidelity":
                item["applicability"] = "not_applicable_synthetic_template"
    result["template_source"] = template_info
    result["template"] = template_path
    result["images_source"] = image_sources
    result["images"] = image_paths
    result["content_path"] = source_content_path
    result["source_hashes"] = {
        "template": actual_template_hash,
        "content": content_hash,
        "images": actual_image_hashes,
    }
    result["registry_hashes"] = {
        "template": template_info["sha256"],
        "content": content_hash,
        "images": {item["file"]: item["sha256"] for item in image_sources},
    }
    result["synthetic"] = bool(synthetic)
    result["template_origin"] = template_origin
    result["image_origin"] = image_origin
    return result
