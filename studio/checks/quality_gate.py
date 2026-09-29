"""Finalize the quality report before publishing reviewable files."""

from studio.diagnostics import event
from studio.checks.quality import quality_report


class QualityGateRejected(ValueError):
    """Legacy failure type retained for review of older blocked jobs."""


def require_publishable(manifest):
    report = quality_report(manifest)
    previous = manifest.get("quality_report", {})
    if previous.get("findings") == report["findings"] and previous.get("repair_comparison"):
        report["repair_comparison"] = previous["repair_comparison"]
    manifest["quality_report"] = report
    if report["errors"]:
        for finding in report["findings"]:
            if finding["severity"] == "error":
                event("quality.needs_review", level="error", finding=finding)
    return report
