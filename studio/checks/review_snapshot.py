"""Immutable post-repair audit evidence for a single generation revision."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from studio.composition.artifacts import public_path
from studio.models import AuditFinding, FinalAuditSnapshot


def snapshot_hash(snapshot: FinalAuditSnapshot) -> str:
    raw = snapshot.model_dump_json(exclude_none=False)
    canonical = json.dumps(
        json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _action(row: dict, package, plans, scenes_by_variant) -> tuple[str | None, str]:
    variant, slide = row.get("variant"), row.get("slide")
    if row.get("source") == "repair" or not variant or not slide:
        return None, "Запись не относится к исправляемому слайду"
    if row.get("source") not in ("variant", "visual_audit"):
        return None, "Требуется правка содержания или ручная проверка"
    if row.get("code") not in {
        "overflow",
        "overlap",
        "readability",
        "hierarchy",
        "layout_purpose",
        "template_artwork",
        "chart_type",
        "text_overflow",
        "table_overflow",
        "chart_overflow",
        "out_of_bounds",
        "container_overflow",
        "unsafe_text_zone",
        "pptx_readability",
        "pptx_content_overlap",
        "pptx_out_of_bounds",
        "pptx_table_overflow",
        "pptx_text_overflow",
    }:
        return None, "Для этого кода нет безопасной операции"
    variant_plan = next((v for v in plans.variants if v.key == variant), None)
    if (
        variant_plan is None
        or slide > len(variant_plan.slides)
        or slide > len(scenes_by_variant.get(variant, []))
    ):
        return None, "План слайда недоступен"
    plan = variant_plan.slides[slide - 1]
    if (
        plan.layout == "chart"
        and plan.chart_style != "readable"
        and row["code"]
        in {
            "overlap",
            "readability",
            "hierarchy",
            "chart_type",
            "chart_overflow",
            "pptx_readability",
        }
    ):
        return "readable_chart", ""
    from studio.composition.contracts import candidates

    try:
        current = scenes_by_variant[variant][slide - 1].get("pattern_id")
        alternatives = [p for p in candidates(package, plan, slide - 1) if p.id != current]
    except (ValueError, KeyError):
        alternatives = []
    if alternatives:
        return "change_layout", ""
    return None, "Нет другого совместимого макета"


def build_snapshot(store, generation_id: str, manifest: dict, package, plans) -> FinalAuditSnapshot:
    root = store.directory(generation_id)
    scenes_by_variant = {}
    for result in manifest["variants"]:
        scene_file = public_path(root, f"{result['key']}/slides.json")
        if scene_file is None:
            raise ValueError("Сцены итогового аудита недоступны")
        scenes_by_variant[result["key"]] = json.loads(scene_file.read_text())
    counts: Counter[str] = Counter()
    findings = []
    for row in manifest["quality_report"]["findings"]:
        variant, slide = row.get("variant"), row.get("slide")
        action, reason = _action(row, package, plans, scenes_by_variant)
        element = row.get("element")
        box = None
        if row.get("source") == "variant" and variant and slide and isinstance(element, int):
            scenes = scenes_by_variant.get(variant, [])
            if 0 < slide <= len(scenes) and 0 <= element < len(scenes[slide - 1]["elements"]):
                box = scenes[slide - 1]["elements"][element]["box"]
            else:
                element = None
        else:
            element = None
        identity = {
            key: row.get(key)
            for key in ("source", "variant", "slide", "code", "severity", "message", "element")
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:20]
        counts[digest] += 1
        findings.append(
            AuditFinding(
                id=f"{digest}-{counts[digest]}",
                source=row["source"],
                variant=variant,
                slide=slide if isinstance(slide, int) and slide > 0 else None,
                code=row["code"],
                severity=row["severity"],
                message=row["message"],
                action=action,
                unsupported_reason=reason,
                element=element,
                scene_box=box,
                repaired=row.get("source") == "repair" or bool(row.get("repaired")),
            )
        )
    files = {}
    names = ["plans.json", "audit-input.json"] + [
        f"{variant}/{filename}"
        for variant in scenes_by_variant
        for filename in ("slides.json", "deck.pptx", "deck.pdf", "deck.html")
    ]
    names.extend(
        f"{variant}/FONT_LICENSES.txt"
        for variant in scenes_by_variant
        if public_path(root, f"{variant}/FONT_LICENSES.txt") is not None
    )
    for result in manifest["variants"]:
        names.extend(f"{result['key']}/slide-{n}.png" for n in range(1, result["slides"] + 1))
    for name in names:
        path = public_path(root, name)
        if path is None:
            raise ValueError(f"Отсутствует материал итогового аудита: {name}")
        files[name] = _file_hash(path)
    return FinalAuditSnapshot(
        generation_id=generation_id,
        package_id=package.id,
        findings=findings,
        quality_report=manifest["quality_report"],
        parent_generation_id=store.get(generation_id).get("parent_generation_id"),
        selected_finding_ids=store.get(generation_id).get("finding_ids", []),
        generated_at=time.time(),
        files=files,
    )


def save_snapshot(store, snapshot: FinalAuditSnapshot) -> str:
    path = store.directory(snapshot.generation_id) / "final-audit.json"
    # Exclusive creation prevents a late worker from replacing evidence being reviewed.
    with path.open("x") as stream:
        stream.write(snapshot.model_dump_json(indent=2))
    return snapshot_hash(snapshot)


def load_snapshot(store, gid: str) -> FinalAuditSnapshot:
    job = store.get(gid)
    if job["kind"] != "generation":
        raise ValueError("Это не генерация")
    path = public_path(store.directory(gid), "final-audit.json")
    if path is None:
        raise ValueError("Итоговый аудит недоступен")
    snapshot = FinalAuditSnapshot.model_validate_json(path.read_text())
    if snapshot_hash(snapshot) != job.get("audit_hash"):
        raise ValueError("Хэш итогового аудита не совпадает с заданием")
    if snapshot.generation_id != gid or snapshot.package_id != job.get("package_id"):
        raise ValueError("Аудит относится к другому запуску")
    for name, expected in snapshot.files.items():
        source = public_path(store.directory(gid), name)
        if source is None or _file_hash(source) != expected:
            raise ValueError(f"Артефакт аудита изменился: {name}")
    return snapshot
