"""Publication gate using the same report as manifest and UI."""

from .diagnostics import event
from .quality import quality_report


def require_publishable(manifest):
    report = quality_report(manifest)
    manifest["quality_report"] = report
    if report["errors"]:
        for finding in report["findings"]:
            if finding["severity"] == "error":
                event("quality.rejected", level="error", finding=finding)
        raise ValueError(
            f"Проверка результата обнаружила {report['errors']} ошибок. Готовые презентации не опубликованы. "
            "Черновики и подробный отчёт сохранены для диагностики; причины доступны в журнале."
        )
