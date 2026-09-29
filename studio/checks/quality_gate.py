"""Publication gate using the same report as manifest and UI."""

from studio.diagnostics import event
from studio.checks.quality import quality_report


class QualityGateRejected(ValueError):
    """A complete, reviewable audit rejected publication of draft files."""


def require_publishable(manifest):
    report = quality_report(manifest)
    previous = manifest.get("quality_report", {})
    if previous.get("findings") == report["findings"] and previous.get("repair_comparison"):
        report["repair_comparison"] = previous["repair_comparison"]
    manifest["quality_report"] = report
    if report["errors"]:
        for finding in report["findings"]:
            if finding["severity"] == "error":
                event("quality.rejected", level="error", finding=finding)
        raise QualityGateRejected(
            f"Проверка результата обнаружила {report['errors']} ошибок. Готовые презентации не опубликованы. "
            "Черновики и подробный отчёт сохранены для диагностики; причины доступны в журнале."
        )
