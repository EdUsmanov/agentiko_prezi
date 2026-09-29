"""Deck-wide selection of distinct source artwork, subject to existing quality contracts."""

from collections import Counter
from hashlib import sha256
from pathlib import Path


def artwork_family(pattern):
    if pattern is None:
        return "generic"
    path = Path(pattern.background_image) if pattern.background_image else None
    if path and path.is_file():
        from studio.composition.background_selection import flat_region
        from studio.models import Box
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(path) as image:
                width, height = image.size
            color = flat_region(path, Box(x=0, y=0, w=width, h=height), width, height)
            if color:
                return "solid:" + color
        except (OSError, UnidentifiedImageError):
            pass
        return sha256(path.read_bytes()).hexdigest()
    return (
        f"source:{pattern.source_slide}"
        if pattern.source_slide
        else f"master:{pattern.master_index}"
    )


def sequence_cost(families):
    counts = Counter(families)
    # Prefer several source examples and break long runs. IDs do not create variety.
    dominance = sum(max(0, n - max(2, (len(families) + 2) // 3)) ** 2 for n in counts.values())
    repeats = sum(a == b for a, b in zip(families, families[1:]))
    triples = sum(a == b == c for a, b, c in zip(families, families[1:], families[2:]))
    return dominance * 20 + triples * 12 + repeats * 3 + sum(n * n for n in counts.values())


def background_report(scenes, profile):
    patterns = {p.id: p for p in profile.patterns}
    families = [
        artwork_family(patterns.get(s.background_pattern_id or s.pattern_id))
        if s.background_pattern_id or s.pattern_id
        else "solid:" + s.background
        for s in scenes
    ]
    counts = Counter(families)
    return {
        "source_slides": [
            getattr(patterns.get(s.background_pattern_id or s.pattern_id), "source_slide", None)
            for s in scenes
        ],
        "families": families,
        "background_colors": dict(Counter(s.background for s in scenes)),
        "unique_backgrounds": len(counts),
        "largest_use": max(counts.values(), default=0),
        "adjacent_repeats": sum(a == b for a, b in zip(families, families[1:])),
    }


def diversify_backgrounds(variant, package, composition_cache=None):
    """Keep text, evidence and order. Search only compatible, equally readable native layouts."""
    from studio.composition.composer import CompositionSession
    from studio.composition.contracts import candidates
    from studio.checks.audit import repair_scenes
    from studio.checks.quality import candidate_regressions
    from studio.composition.background_selection import (
        background_candidates,
        apply_background,
        preserves_data_space,
    )
    from studio.composition.design_balance import composition_family, design_cost, rhythm_cost

    cache = composition_cache or CompositionSession(package)
    original = cache.variant(variant)
    repair_scenes(original, package)
    patterns = {p.id: p for p in package.template.patterns}
    families = {p.id: artwork_family(p) for p in package.template.patterns}
    donors = background_candidates(package)
    choices = []
    blocked = []
    for index, (slide, scene) in enumerate(zip(variant.slides, original)):
        base_family = families.get(
            scene.background_pattern_id or scene.pattern_id, "solid:" + scene.background
        )
        options = {
            (base_family, composition_family(scene, package.template)): (
                scene.pattern_id,
                scene.background_pattern_id,
                scene,
                False,
            )
        }
        if scene.purpose not in ("cover", "divider") and not any(
            e.image_id for e in scene.elements
        ):
            # Evaluate different geometries even when their artwork is identical.
            # Bound the search per artwork family before expensive composition.
            available = candidates(package, slide, index, prefer_specialized=False)
            grouped = {}
            for pattern in available:
                grouped.setdefault(families[pattern.id], []).append(pattern)
            available = [
                p
                for group in grouped.values()
                for p in sorted(
                    group,
                    key=lambda p: (
                        abs(
                            len(p.body_zones)
                            - max(
                                1,
                                len(
                                    {
                                        (round(e.box.x), round(e.box.w))
                                        for e in scene.elements
                                        if e.kind == "text" and e.role == "body" and e.source_ids
                                    }
                                ),
                            )
                        ),
                        -sum(z.w * z.h for z in p.body_zones),
                        p.id,
                    ),
                )[:3]
            ]
            for pattern in available:
                family = families[pattern.id]
                trial = variant.model_copy(deep=True)
                trial.slides[index].pattern_id = pattern.id
                trial.slides[index].background_pattern_id = None
                try:
                    result = cache.slide(trial, index)
                    repair_scenes([result], package)
                except ValueError:
                    continue
                if result.pattern_id != pattern.id or result.strategy != "native_template":
                    continue
                if not preserves_data_space(scene, result) or candidate_regressions(
                    [scene], [result], package, preserve_structure=True
                ):
                    continue
                key = (family, composition_family(result, package.template))
                if key not in options or design_cost(result, package.template) < design_cost(
                    options[key][2], package.template
                ):
                    options[key] = (pattern.id, None, result, True)
            if scene.strategy == "token_composition" and not scene.pattern_id:
                for pattern in donors:
                    family = families[pattern.id]
                    key = (family, composition_family(scene, package.template))
                    if key in options:
                        continue
                    result = apply_background(scene, package, pattern.id)
                    if result is None or candidate_regressions([scene], [result], package):
                        continue
                    options[key] = ("token:auto", pattern.id, result, True)
        if len(options) == 1:
            blocked.append(index + 1)
        choices.append(options)
    from studio.checks.scene_regions import unused_body_regions

    empty_slots = {
        id(option[2]): unused_body_regions(option[2], package)
        for options in choices
        for option in options.values()
    }
    design_scores = {
        id(o[2]): design_cost(o[2], package.template)
        for options in choices
        for o in options.values()
    }
    geometries = {
        id(o[2]): composition_family(o[2], package.template)
        for options in choices
        for o in options.values()
    }

    def selection_score(row):
        return (
            sum(empty_slots[id(option[2])] for option in row[1]),
            sum(design_scores[id(o[2])] for o in row[1])
            + sequence_cost(row[0]) * 0.25
            + rhythm_cost([geometries[id(o[2])] for o in row[1]]),
            row[2],
            tuple(str(x[0]) for x in row[1]),
        )

    # Bounded beam, deterministic tie-breaks. Cost considers the whole sequence,
    # including its immutable cover and specialised timelines/charts.
    beam = [([], [], 0)]
    for options in choices:
        trials = [
            (fs + [family], selected + [option], changes + int(option[3]))
            for fs, selected, changes in beam
            for (family, _), option in options.items()
        ]
        beam = sorted(
            trials,
            key=selection_score,
        )[:96]
    _, selected, _ = min(beam, key=selection_score) if beam else ([], [], 0)
    plan = variant.model_copy(deep=True)
    scenes = []
    changes = []
    for index, (pid, background_id, scene, changed) in enumerate(selected):
        scenes.append(scene)
        plan.slides[index].pattern_id = pid
        plan.slides[index].background_pattern_id = background_id
        if changed:
            changes.append(
                {
                    "slide": index + 1,
                    "from": original[index].pattern_id,
                    "to": pid,
                    "background_pattern_id": background_id,
                    "source_slide": patterns[background_id or pid].source_slide,
                }
            )
    report = {
        "before": background_report(original, package.template),
        "after": background_report(scenes, package.template),
        "changes": changes,
        "slides_without_safe_alternative": blocked,
        "policy": "source_layouts_and_sanitized_backgrounds_with_quality_guards",
        "design_before": sum(design_cost(s, package.template) for s in original),
        "design_after": sum(design_cost(s, package.template) for s in scenes),
        "rhythm_before": rhythm_cost([composition_family(s, package.template) for s in original]),
        "rhythm_after": rhythm_cost([composition_family(s, package.template) for s in scenes]),
    }
    return plan, scenes, report
