"""Deck-wide selection of distinct source artwork, subject to existing quality contracts."""

from collections import Counter
from hashlib import sha256
from pathlib import Path


def artwork_family(pattern):
    if pattern is None:
        return "generic"
    path = Path(pattern.background_image) if pattern.background_image else None
    if path and path.is_file():
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
    families = [artwork_family(patterns.get(s.pattern_id)) for s in scenes]
    counts = Counter(families)
    return {
        "source_slides": [
            getattr(patterns.get(s.pattern_id), "source_slide", None) for s in scenes
        ],
        "families": families,
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

    cache = composition_cache or CompositionSession(package)
    original = cache.variant(variant)
    repair_scenes(original, package)
    patterns = {p.id: p for p in package.template.patterns}
    families = {p.id: artwork_family(p) for p in package.template.patterns}
    choices = []
    blocked = []
    for index, (slide, scene) in enumerate(zip(variant.slides, original)):
        base_family = families.get(scene.pattern_id, "generic")
        options = {base_family: (scene.pattern_id, scene, False)}
        if scene.purpose not in ("cover", "divider") and not any(
            e.image_id for e in scene.elements
        ):
            for pattern in candidates(package, slide, index, source_slides_only=True):
                family = families[pattern.id]
                if family in options:
                    continue
                trial = variant.model_copy(deep=True)
                trial.slides[index].pattern_id = pattern.id
                try:
                    result = cache.slide(trial, index)
                    repair_scenes([result], package)
                except ValueError:
                    continue
                if result.pattern_id != pattern.id or result.strategy != "native_template":
                    continue
                if candidate_regressions([scene], [result], package):
                    continue
                options[family] = (pattern.id, result, True)
        if len(options) == 1:
            blocked.append(index + 1)
        choices.append(options)
    # Bounded beam, deterministic tie-breaks. Cost considers the whole sequence,
    # including its immutable cover and specialised timelines/charts.
    beam = [([], [], 0)]
    for options in choices:
        trials = [
            (fs + [family], selected + [option], changes + int(option[2]))
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
    for index, (pid, scene, changed) in enumerate(selected):
        scenes.append(scene)
        if changed:
            plan.slides[index].pattern_id = pid
            changes.append(
                {
                    "slide": index + 1,
                    "from": original[index].pattern_id,
                    "to": pid,
                    "source_slide": patterns[pid].source_slide,
                }
            )
    report = {
        "before": background_report(original, package.template),
        "after": background_report(scenes, package.template),
        "changes": changes,
        "slides_without_safe_alternative": blocked,
        "policy": "source_artwork_with_quality_guards",
    }
    return plan, scenes, report
