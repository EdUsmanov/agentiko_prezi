"""Offline font/export regression on a supplied template, with synthetic text."""

import argparse
import asyncio
from pathlib import Path
import json
import shutil
import tempfile
import time
from zipfile import ZipFile
from studio.config import ROOT, Settings
from studio.pipeline import prepare, load_package, generate
from studio.security import digest
from studio.jobs.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    parent = ROOT / "test-results"
    parent.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="font-smoke-", dir=parent))
    store = Store(directory)
    settings = Settings(data_dir=directory)
    original = digest(args.template.read_bytes())
    job = store.create("preparation", {"template_name": args.template.name})
    shutil.copyfile(args.template, store.directory(job["id"]) / "input.pptx")
    text = "# Проверка типографики\n" + "\n".join(
        f"## Этап {i}\nКоманда проверяет пункт {i} и сохраняет исходные данные."
        for i in range(1, 7)
    )
    prepare(store, job["id"], text, "Команда", "", 3, settings)
    ready = store.get(job["id"])
    if ready["state"] != "ready":
        raise RuntimeError(json.dumps(ready, ensure_ascii=False))
    package = load_package(store, job["id"])
    run = store.create("generation", {"package_id": job["id"], "deadline_at": time.time() + 300})
    asyncio.run(generate(store, run["id"], settings))
    result = store.get(run["id"])
    assert result["state"] in ("completed", "needs_review"), result
    for variant in ("executive", "analytical", "story"):
        target = store.directory(run["id"]) / variant
        with ZipFile(target / "deck.pptx") as archive:
            assert not any(name.startswith("ppt/fonts/") for name in archive.namelist())
        assert "data:font" not in (target / "deck.html").read_text()
    assert digest(args.template.read_bytes()) == original
    print(
        json.dumps(
            {
                "directory": str(directory),
                "preparation": job["id"],
                "generation": run["id"],
                "state": result["state"],
                "seconds": result["elapsed_seconds"],
                "errors": result["errors"],
                "roles": {
                    role: next(
                        a["requested"] for a in package.template.font_assets if a["id"] == asset
                    )
                    for role, asset in package.template.font_roles.items()
                },
                "missing": package.template.missing_fonts,
                "native_pptx_render": result["native_pptx_render"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
