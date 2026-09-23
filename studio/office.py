"""Bounded native PPTX rendering. No shell, macros, shared Office profile or remote resources."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.sax.saxutils import escape

def executable():
    configured = os.getenv("STUDIO_SOFFICE")
    if configured:
        return configured if Path(configured).is_file() else None
    candidates = [shutil.which("soffice"), "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        str(Path.home()/".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice")]
    return next((p for p in candidates if p and Path(p).is_file()), None)

def to_pdf(pptx, directory, timeout=45, font_file=None):
    binary = executable()
    if not binary:
        return False
    directory = Path(directory).resolve()
    with tempfile.TemporaryDirectory(prefix="studio-office-") as temporary:
        # Each conversion gets a separate profile (also safe for three parallel variants).
        profile = Path(temporary)/"profile"
        env={k:v for k,v in os.environ.items() if k in ("PATH","HOME","TMPDIR","LANG","LC_ALL","FONTCONFIG_FILE","FONTCONFIG_PATH")}
        if font_file:
            # Document-scoped font directory; no global font installation or cache mutation.
            config=Path(temporary)/"fonts.conf"
            config.write_text('<?xml version="1.0"?><fontconfig><dir>'+escape(str(Path(font_file).resolve().parent))+
                '</dir><cachedir>'+escape(str(Path(temporary)/"font-cache"))+'</cachedir></fontconfig>')
            env["FONTCONFIG_FILE"]=str(config)
            env["FONTCONFIG_PATH"]=temporary
        result = subprocess.run([binary, "-env:UserInstallation="+profile.as_uri(), "--headless",
            "--nologo", "--nodefault", "--nofirststartwizard", "--convert-to", "pdf:impress_pdf_Export",
            "--outdir", str(directory), str(Path(pptx).resolve())],
            capture_output=True, timeout=timeout, check=False,
            env=env)
        output = directory/(Path(pptx).stem+".pdf")
        if result.returncode != 0 or not output.is_file() or output.stat().st_size < 100:
            raise ValueError("LibreOffice не смог отрисовать PPTX; альтернативное превью не выдаётся за точное")
    return True
