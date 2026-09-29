"""Scene-level layout fallback; pixel safety stays independent of the auditor."""

from studio.composition.background_selection import preserves_data_space


def master_fallback(variant, package, index, scene, image_groups, compose):
    """Authored layouts keep priority; safe masters precede a generic fallback."""
    from studio.composition.contracts import candidates
    from studio.checks.audit import audit_scenes, repair_scenes
    from studio.checks.quality import candidate_regressions
    from studio.checks.repair_policy import FIT_CODES

    baseline = scene.model_copy(deep=True)
    repair_scenes([baseline], package)
    if scene.pattern_id and not any(f.code in FIT_CODES for f in audit_scenes([baseline], package)):
        return None
    if any(e.image_id for e in scene.elements):
        return None
    for pattern in candidates(package, variant.slides[index], index, prefer_specialized=False):
        if pattern.source_slide:
            continue
        trial = variant.model_copy(deep=True)
        trial.slides[index].pattern_id = pattern.id
        trial.slides[index].background_pattern_id = None
        try:
            result = compose(trial, package, index, image_groups)
            repair_scenes([result], package)
        except ValueError:
            continue
        if result.pattern_id != pattern.id or any(
            f.code in FIT_CODES for f in audit_scenes([result], package)
        ):
            continue
        if preserves_data_space(baseline, result) and not candidate_regressions(
            [baseline], [result], package
        ):
            return result
    return None
