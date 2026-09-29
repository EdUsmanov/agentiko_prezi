"""Report visible evidence organization without changing the authored scenes."""

import hashlib
import json
from collections import Counter
from itertools import combinations

from studio.checks.audit import audit_scenes
from studio.checks.quality import _organization, meaningful_diversity
from studio.checks.scene_regions import unused_body_regions as unused_body_regions


def geometry_signature(scenes):
    """Stable structural fingerprint; movement, styling and metadata do not count."""
    value = [_organization(scene) for scene in scenes]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def error_keys(scenes, package):
    return Counter(
        (f.code, f.slide, f.element) for f in audit_scenes(scenes, package) if f.severity == "error"
    )


def preserves_quality(before, after, package):
    from studio.checks.quality import candidate_regressions

    from studio.composition.background_selection import preserves_data_space

    return all(
        preserves_data_space(a, b) for a, b in zip(before, after)
    ) and not candidate_regressions(before, after, package, audit=audit_scenes)


def ensure_diversity(decks, package):
    """Verify actual composition; a constrained source may have fewer than three layouts."""
    signatures = {key: geometry_signature(scenes) for key, scenes in decks.items()}
    comparison = meaningful_diversity(decks, package.template)
    keys = list(decks)
    passing_pairs = {
        frozenset(pair["variants"]) for pair in comparison["pairs"] if pair["verified"]
    }
    distinct = next(
        (
            size
            for size in range(len(keys), 0, -1)
            if any(
                all(frozenset(pair) in passing_pairs for pair in combinations(group, 2))
                for group in combinations(keys, size)
            )
        ),
        0,
    )
    verified = bool(keys) and distinct == len(keys) and comparison["verified"]
    findings = []
    if not verified:
        missing = [
            " + ".join(pair["variants"]) for pair in comparison["pairs"] if not pair["verified"]
        ]
        findings.append(
            {
                "code": "composition_diversity",
                "severity": "warning",
                "message": "Не удалось подтвердить разные визуальные композиции для всех вариантов. "
                + ("Совпадают: " + "; ".join(missing) + ". " if missing else "")
                + "Исходное содержание сохранено; доступные варианты требуют просмотра.",
            }
        )
    return {
        "policy": "visual-organization-v3",
        "distinct": distinct,
        "expected": len(decks),
        "verified": verified,
        "adjustments": [],
        "signatures": signatures,
        "findings": findings,
    }
