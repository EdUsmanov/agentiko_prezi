"""Bounded subprocess adapter. It inherits no model keys or Node injection flags."""

import os
from pathlib import Path
import shutil
import subprocess

MAX_FONT_BYTES = 16 * 1024 * 1024
DECODE_TIMEOUT = 10


def decode_mtx(raw):
    if len(raw) > MAX_FONT_BYTES:
        raise ValueError("встроенный шрифт превышает 16 МБ")
    executable = os.environ.get("STUDIO_NODE_EXECUTABLE") or shutil.which("node")
    if not executable:
        raise ValueError("для сжатого шрифта нужен Node.js; задайте STUDIO_NODE_EXECUTABLE")
    # NODE_OPTIONS/NODE_PATH and API credentials must not reach the decoder.
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP")
        if key in os.environ
    }
    try:
        result = subprocess.run(
            [
                executable,
                "--max-old-space-size=128",
                "--stack-size=1024",
                str(Path(__file__).with_suffix(".mjs")),
            ],
            input=raw,
            capture_output=True,
            timeout=DECODE_TIMEOUT,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills and reaps the child before raising.
        raise ValueError("декодирование встроенного шрифта превысило 10 секунд") from exc
    except OSError as exc:
        raise ValueError("не удалось запустить Node.js для сжатого шрифта") from exc
    if (
        result.returncode
        or not 12 <= len(result.stdout) <= MAX_FONT_BYTES
        or not result.stdout.startswith(b"\x00\x01\x00\x00")
    ):
        raise ValueError("сжатый встроенный шрифт повреждён или превышает лимиты")
    return result.stdout
