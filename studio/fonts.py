from functools import lru_cache
from pathlib import Path
from PIL import ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from .config import ROOT
import hashlib
import threading
import os
import re
import unicodedata

_lock = threading.Lock()

def font_roots():
    home = Path.home()
    roots = [ROOT / "fonts", home / "Library/Fonts", Path("/Library/Fonts"),
             Path("/System/Library/Fonts"), home / ".local/share/fonts", home / ".fonts",
             Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    if os.name == "nt":
        roots.extend([Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                      Path(os.environ.get("LOCALAPPDATA", str(home))) / "Microsoft/Windows/Fonts"])
    return roots


def font_key(name):
    return re.sub(r"[\s_-]+", " ", unicodedata.normalize("NFKC", name).strip()).casefold()


def font_catalog():
    # Rescan names/stats cheaply so adding a font does not require a restart.
    # Cache the expensive metadata parsing by file fingerprint, not forever.
    files = []
    for root in font_roots():
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.suffix.lower() != ".ttf":
                    continue
                try:
                    stat = path.stat()
                    if path.is_file():
                        files.append((str(path), stat.st_mtime_ns, stat.st_size))
                except OSError:
                    continue
    return dict(_font_catalog(tuple(files)))


@lru_cache(maxsize=2)
def _font_catalog(files):
    entries = {}
    for path, _, _ in files:
        try:
            name, style = ImageFont.truetype(path, 16).getname()
            aliases = [name + " " + style]
            # Do not silently resolve a family to Bold/Italic when Regular is absent.
            if style.casefold() in ("regular", "normal", "book", "roman"):
                aliases.append(name)
            for alias in aliases:
                entries.setdefault(font_key(alias), path)
        except (OSError, ValueError):
            continue
    return entries

def resolve_font(name):
    return font_catalog().get(font_key(name), "")

@lru_cache(maxsize=64)
def pdf_font(path):
    if not path:
        raise ValueError("Шрифт шаблона недоступен. Установите TTF в fonts/ и повторите анализ.")
    name = "F" + hashlib.sha256(path.encode()).hexdigest()[:12]
    with _lock:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, path))
    return name

def wrap_text(text, font_path, size, width):
    name = pdf_font(font_path)
    result = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            if pdfmetrics.stringWidth(word, name, size) > width:
                if current:
                    result.append(current)
                    current = ""
                for char in word:
                    if current and pdfmetrics.stringWidth(current + char, name, size) > width:
                        result.append(current)
                        current = ""
                    current += char
                continue
            candidate = (current + " " + word).strip()
            if current and pdfmetrics.stringWidth(candidate, name, size) > width:
                result.append(current)
                current = word
            else:
                current = candidate
        result.append(current)
    return result
