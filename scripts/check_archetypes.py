"""Opt-in live semantic contract check; synthetic text only, no uploaded documents."""

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time

from studio.archetypes import analyze_content_archetypes
from studio.config import Settings, ROOT
from studio.content import parse_content
from studio.gateway import ModelGateway

CASES = {
    "speaker": """# Спикер
## Анна Смирнова
Руководитель команды разработки. Представляет результаты проекта.""",
    "comparison": """# Сравнение вариантов по цене
## Вариант А
Цена составляет 100 рублей.
## Вариант Б
Цена составляет 200 рублей.""",
    "process": """# Порядок обработки заявки
Сначала принять заявку.
Затем проверить обязательные поля.
После проверки передать заявку исполнителю.""",
}


async def check(settings):
    result = []
    for expected, raw in CASES.items():
        package = SimpleNamespace(content=parse_content(raw), analysis={})
        gateway = ModelGateway(settings)
        started = time.monotonic()
        report = await analyze_content_archetypes(package, gateway)
        purposes = [u["purpose"] for u in report["units"]]
        passed = report["status"] == "completed" and expected in purposes
        result.append(
            {
                "expected": expected,
                "purposes": purposes,
                "passed": passed,
                "status": report["status"],
                "seconds": round(time.monotonic() - started, 3),
                "calls": gateway.calls,
            }
        )
    return {"passed": all(row["passed"] for row in result), "cases": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Send synthetic cases to the configured API"
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly allow API requests")
    settings = Settings.from_env()
    if settings.mode != "api":
        parser.error("An explicitly configured API model is required")
    (ROOT / "test-results").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="archetypes-live-", dir=ROOT / "test-results"))
    settings = replace(settings, data_dir=directory / "data")
    report = asyncio.run(check(settings))
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {"report": str(directory / "report.json"), **report}, ensure_ascii=False, indent=2
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
