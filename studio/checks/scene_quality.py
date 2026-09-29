"""Absolute scene quality checks; no repair or layout selection dependencies."""


def scene_quality_findings(scenes, package):
    """Absolute checks remain visible even if a bad baseline was not worsened."""
    from studio.composition.background_selection import background_is_safe
    from studio.models import Finding
    from studio.checks.scene_regions import unused_body_regions

    findings = []
    for index, scene in enumerate(scenes, 1):

        def warn(code, message):
            findings.append(Finding(code=code, severity="warning", slide=index, message=message))

        if not background_is_safe(scene, package):
            findings.append(
                Finding(
                    code="background_conflict",
                    severity="error",
                    slide=index,
                    message="Выбранный фон несовместим с содержимым или его контрастом.",
                )
            )
        for ei, e in enumerate(scene.elements):
            if (
                e.kind in ("text", "table", "chart")
                and (e.source_ids or e.role == "title")
                and e.size < (18 if e.role == "title" else 16) - 0.1
            ):
                findings.append(
                    Finding(
                        code="readability",
                        severity="warning",
                        slide=index,
                        element=ei,
                        message="Текст или подписи данных меньше порога читаемости: проверьте слайд.",
                    )
                )
        if unused_body_regions(scene, package):
            warn(
                "unused_template_regions",
                "В выбранном макете остались незаполненные содержательные поля.",
            )
        pattern = next((p for p in package.template.patterns if p.id == scene.pattern_id), None)
        if pattern:
            for row in pattern.safe_text_zone.get("field_checks", []):
                if row["status"] == "unknown":
                    warn("unsafe_text_zone", "Безопасность текста на сложном фоне не подтверждена.")
                    break
            if any(e.field_style.get("unresolved_font") for e in scene.elements):
                warn(
                    "local_font_unresolved",
                    "Точное начертание исходного поля недоступно; использован шрифт роли.",
                )
    return findings
