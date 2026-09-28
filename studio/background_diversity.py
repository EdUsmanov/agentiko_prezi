"""Deck-wide selection of distinct source artwork, subject to existing quality contracts."""

from collections import Counter
from hashlib import sha256
from pathlib import Path


def artwork_family(pattern):
    if pattern is None:
        return "generic"
    path = Path(pattern.background_image) if pattern.background_image else None
    if path and path.is_file():
        from .background_selection import flat_region
        from .models import Box
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
    from .composer import CompositionSession
    from .contracts import candidates
    from .audit import repair_scenes
    from .quality import candidate_regressions
    from .background_selection import background_candidates, apply_background, preserves_data_space

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
        options = {base_family: (scene.pattern_id, scene.background_pattern_id, scene, False)}
        if scene.purpose not in ("cover", "divider") and not any(
            e.image_id for e in scene.elements
        ):
            for pattern in candidates(package, slide, index, prefer_specialized=False):
                family = families[pattern.id]
                if family in options:
                    continue
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
                    [scene], [result], package
                ):
                    continue
                options[family] = (pattern.id, None, result, True)
            if scene.strategy == "token_composition" and not scene.pattern_id:
                for pattern in donors:
                    family = families[pattern.id]
                    if family in options:
                        continue
                    result = apply_background(scene, package, pattern.id)
                    if result is None or candidate_regressions([scene], [result], package):
                        continue
                    options[family] = ("token:auto", pattern.id, result, True)
        if len(options) == 1:
            blocked.append(index + 1)
        choices.append(options)
    # Bounded beam, deterministic tie-breaks. Cost considers the whole sequence,
    # including its immutable cover and specialised timelines/charts.
    beam = [([], [], 0)]
    for options in choices:
        trials = [
            (fs + [family], selected + [option], changes + int(option[3]))
            for fs, selected, changes in beam
            for family, option in options.items()
        ]
        beam = sorted(
            trials,
            key=lambda row: (sequence_cost(row[0]), row[2], tuple(str(x[0]) for x in row[1])),
        )[:96]
    _, selected, _ = (
        min(beam, key=lambda row: (sequence_cost(row[0]), row[2])) if beam else ([], [], 0)
    )
    plan = variant.model_copy(deep=True)
    scenes = []
    changes = []
    for index, (pid, background_id, scene, changed) in enumerate(selected):
        scenes.append(scene)
        if changed:
            plan.slides[index].pattern_id = pid
            plan.slides[index].background_pattern_id = background_id
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
    }
    return plan, scenes, report
