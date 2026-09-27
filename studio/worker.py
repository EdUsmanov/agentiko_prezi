import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from .config import Settings
from .store import Store
from .pipeline import generate
from pydantic import ValidationError


def main():
    settings = replace(Settings.from_worker_env(), data_dir=Path(sys.argv[2]))
    store = Store(settings.data_dir)
    from .diagnostics import configure, scope, exception

    configure(settings.api_key)
    try:
        with scope(store, sys.argv[1]):
            asyncio.run(generate(store, sys.argv[1], settings))
    except TimeoutError as exc:
        with scope(store, sys.argv[1]):
            exception("generation.timeout", exc)
        store.update(
            sys.argv[1],
            "failed",
            error="Истёк тайм-аут отдельной операции. Подробности в журнале.",
            phase="Операция остановлена",
        )
    except Exception as exc:
        with scope(store, sys.argv[1]):
            exception("generation.failed", exc)
        # Avoid leaking payloads, credentials or stack traces into UI.
        message = (
            str(exc)
            if isinstance(exc, ValueError) and not isinstance(exc, ValidationError)
            else "Ошибка генерации (" + type(exc).__name__ + ")"
        )
        store.update(sys.argv[1], "failed", error=message, phase="Генерация остановлена")


if __name__ == "__main__":
    main()
