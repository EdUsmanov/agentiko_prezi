import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from .config import Settings
from .store import Store
from .pipeline import generate

def main():
    settings=replace(Settings.from_env(),data_dir=Path(sys.argv[2]))
    store=Store(settings.data_dir)
    try:
        asyncio.run(generate(store,sys.argv[1],settings))
    except TimeoutError:
        store.update(sys.argv[1],"timed_out",error="Превышен общий лимит 300 секунд",phase="Время истекло")
    except Exception as exc:
        # Avoid leaking payloads, credentials or stack traces into UI.
        message=str(exc) if isinstance(exc,ValueError) else "Ошибка генерации ("+type(exc).__name__+")"
        store.update(sys.argv[1],"failed",error=message,phase="Генерация остановлена")

if __name__=="__main__":
    main()
