from functools import lru_cache
from pathlib import Path
from PIL import ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from .config import ROOT
import hashlib
import threading

_lock = threading.Lock()

@lru_cache(maxsize=1)
def font_catalog():
    entries = {}
    roots = [ROOT / "fonts", Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"), Path("/usr/share/fonts/truetype")]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.ttf")):
            try:
                name, style = ImageFont.truetype(str(path), 16).getname()
                if name.casefold() not in entries or style.lower() in ("regular", "normal", "book"):
                    entries[name.casefold()] = str(path)
            except (OSError, ValueError):
                continue
    return entries

def resolve_font(name):
    return font_catalog().get(name.casefold(), "")

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
