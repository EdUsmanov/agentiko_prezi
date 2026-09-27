"""Invoke the bundled Node decoder only for embedded EOT/MTX font parts."""

import asyncio
import base64
import json
import os
import shutil
from pathlib import Path

MAX_EOT_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
DECODE_TIMEOUT = 10


class EmbeddedFontDecoder:
    def __init__(self) -> None:
        self.script = Path(__file__).resolve().parent / "decoder.mjs"

    async def decode_eot(self, data: bytes) -> dict:
        if len(data) > MAX_EOT_BYTES:
            raise ValueError("Embedded font exceeds the decoder limit")
        executable = os.environ.get("NODE_EXECUTABLE") or shutil.which("node")
        if not executable:
            raise RuntimeError("Node.js 24 is required for embedded EOT/MTX fonts")
        environment = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP")
            if key in os.environ
        }
        process = await asyncio.create_subprocess_exec(
            executable,
            "--max-old-space-size=128",
            str(self.script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            output, errors = await asyncio.wait_for(
                process.communicate(base64.b64encode(data)), DECODE_TIMEOUT
            )
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(f"Embedded font decoder failed: {errors.decode(errors='replace')[-1000:]}")
        if len(output) > MAX_RESPONSE_BYTES:
            raise ValueError("Embedded font decoder output exceeds the limit")
        return json.loads(output)
