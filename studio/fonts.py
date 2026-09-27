from functools import lru_cache
from pathlib import Path
from PIL import ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from .config import ROOT
from .text_layout import layout_words
import hashlib
import threading
import os
import re
import unicodedata

_lock = threading.Lock()

# This is symbol substitution only, never a replacement for missing letters.
SYMBOLS = frozenset("←↑→↓↔↕↖↗↘↙⇒⇐⇔•")


def symbol_font():
    return str(ROOT / "fonts" / "Montserrat-Regular.ttf")


@lru_cache(maxsize=128)
def _coverage(path, stamp, size):
    from fontTools.ttLib import TTFont as FontToolsFont

    with FontToolsFont(path, lazy=True) as font:
        return frozenset((font.getBestCmap() or {}).keys())


def coverage(path):
    stat = Path(path).stat()
    return _coverage(str(path), stat.st_mtime_ns, stat.st_size)


def font_runs(text, path):
    """Yield exact text segments and the font that measures AND renders them."""
    primary = coverage(path)
    fallback = symbol_font()
    supported = coverage(fallback) if Path(fallback).is_file() else frozenset()
    current, buffer = None, []
    for char in text:
        selected = (
            fallback
            if char in SYMBOLS and ord(char) not in primary and ord(char) in supported
            else str(path)
        )
        if selected != current and buffer:
            yield "".join(buffer), current
            buffer = []
        current = selected
        buffer.append(char)
    if buffer:
        yield "".join(buffer), current


def text_width(text, path, size):
    return sum(
        pdfmetrics.stringWidth(value, pdf_font(face), size) for value, face in font_runs(text, path)
    )


def font_roots():
    home = Path.home()
    roots = [
        ROOT / "fonts",
        ROOT / "data/local-fonts",
        home / "Library/Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        home / ".local/share/fonts",
        home / ".fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    ]
    roots.extend(
        Path("/Applications") / app / "Contents/Resources/DFonts"
        for app in ("Microsoft PowerPoint.app", "Microsoft Word.app", "Microsoft Excel.app")
    )
    if os.name == "nt":
        roots.extend(
            [
                Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", str(home))) / "Microsoft/Windows/Fonts",
            ]
        )
    return roots


def role_font(profile, role="body"):
    bindings = profile.font_roles
    key = "title" if role in ("title", "subheading") else role
    asset_id = bindings.get(key) or bindings.get("body")
    asset = next((a for a in profile.font_assets if a["id"] == asset_id), None)
    return (asset["requested"], asset["path"]) if asset else (profile.font, profile.font_file)


def font_asset(profile, requested):
    return next((a for a in profile.font_assets if a["requested"] == requested), None) or next(
        (a for a in profile.font_assets if requested in a.get("template_aliases", [])), None
    )


def element_font(profile, element):
    requested = getattr(element, "field_style", {}).get("requested_font")
    asset = font_asset(profile, requested) if requested else None
    if asset:
        return asset["requested"], asset["path"]
    role = element.kind if element.kind in ("table", "chart") else element.role
    return role_font(profile, role)


def profile_font_files(profile):
    files = [a["path"] for a in profile.font_assets] or [profile.font_file]
    if Path(symbol_font()).is_file():
        files = files + [symbol_font()]
    return list(dict.fromkeys(files))


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
    _name = pdf_font(font_path)
    result = []
    for paragraph in text.split("\n"):
        current = ""
        for units in layout_words(paragraph):
            word = "".join(units)
            if text_width(word, font_path, size) > width:
                if current:
                    result.append(current)
                    current = ""
                for unit in units:
                    if current and text_width(current + unit, font_path, size) > width:
                        result.append(current)
                        current = ""
                    current += unit
                continue
            candidate = (current + " " + word).strip()
            if current and text_width(candidate, font_path, size) > width:
                result.append(current)
                current = word
            else:
                current = candidate
        result.append(current)
    return result


def table_cell_fits(text, font_path, size, width, height, bold=False):
    # A regular face plus a fixed margin can still underestimate a bold header.
    # Measure the actual installed bold face when it exists; never substitute
    # another family or embed this measurement font into the output.
    if bold:
        stat = Path(font_path).stat()
        font_path = _bold_measurement_face(str(font_path), stat.st_mtime_ns, stat.st_size)
    width *= 0.94 if bold else 1
    if any(text_width(word, font_path, size) > width for word in text.split()):
        return False
    return len(wrap_text(text, font_path, size, width)) * size * 1.25 <= height


@lru_cache(maxsize=128)
def _bold_measurement_face(path, stamp, size):
    family, style = ImageFont.truetype(path, 16).getname()
    if "bold" in style.casefold():
        return path
    return resolve_font(family + " Bold") or path
