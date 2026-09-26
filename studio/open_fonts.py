"""Preparation-only, fixed-provider download. Never follows URLs from documents.

Only unmodified static OFL TrueType faces are supported. Variable fonts and
other licenses remain unresolved rather than being converted or substituted.
"""
import json
import re
import struct
import threading
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
import httpx
from fontTools.ttLib import TTLibError
from .config import ROOT
from .fonts import font_key
from .embedded_fonts import inspect_font
from .font_identity import requested_face, matches_face
from .security import digest

BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
CACHE = ROOT / "data/local-fonts/google"
_lock = threading.Lock()
CSS_URL = "https://fonts.googleapis.com/css2"
FONT_FACE = re.compile(r"@font-face\s*\{(.*?)\}", re.S)
PROPERTY = re.compile(r"([a-z-]+)\s*:\s*([^;]+)", re.I)
FONT_URL = re.compile(r"url\(['\"]?(https://fonts\.gstatic\.com/[^)'\"\s]+)['\"]?\)\s*format\(['\"]truetype['\"]\)",re.I)


def allowed_url(url):
    address=urlsplit(url)
    if address.scheme!='https' or address.username or address.password or address.port or address.fragment:
        return False
    return ((address.hostname=='raw.githubusercontent.com' and url.startswith(BASE)) or
            (address.hostname=='fonts.googleapis.com' and address.path=='/css2') or
            (address.hostname=='fonts.gstatic.com' and address.path.startswith('/s/')))


def fetch_url(client, url, limit):
    if not allowed_url(url):
        raise ValueError("Неразрешённый адрес шрифта")
    started=time.monotonic()
    with client.stream("GET", url) as response:
        response.raise_for_status()
        length=response.headers.get('content-length')
        if length and int(length)>limit:
            raise ValueError("Файл шрифта превышает лимит")
        data=bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            if time.monotonic()-started>30 or len(data)+len(chunk)>limit:
                raise ValueError("Ответ шрифта превышает лимит размера или времени")
            data.extend(chunk)
        return bytes(data)


def fetch(client, suffix, limit):
    return fetch_url(client,BASE+quote(suffix,safe="/"),limit)


def css_face(client, requested):
    family,weight,italic=requested_face(requested)
    query=urlencode({'family':f'{family}:ital,wght@{int(italic)},{weight}','display':'swap'})
    css=fetch_url(client,CSS_URL+'?'+query,256_000).decode('utf-8')
    faces=FONT_FACE.findall(css)
    if len(faces)>16:
        raise ValueError("Слишком много вариантов в ответе Google Fonts")
    for block in faces:
        props={k.lower():v.strip().strip("'\"") for k,v in PROPERTY.findall(block)}
        match=FONT_URL.search(props.get('src',''))
        if (not match or props.get('unicode-range') or
            font_key(props.get('font-family',''))!=font_key(family) or
            props.get('font-weight')!=str(weight) or
            props.get('font-style','normal')!=('italic' if italic else 'normal')):
            continue
        raw=fetch_url(client,match[1],16*1024*1024)
        inspect_font(raw)
        if matches_face(raw,requested):
            return raw,match[1]
    raise ValueError("Точное полное статическое начертание не найдено")


def cached_face(folder, requested):
    license_path=folder/'OFL.txt'
    if not license_path.is_file() or license_path.stat().st_size>100_000 or b'SIL OPEN FONT LICENSE' not in license_path.read_bytes():
        return ''
    for path in sorted(folder.glob('*.json')):
        try:
            if path.stat().st_size>8000:
                continue
            meta=json.loads(path.read_text())
            if meta.get('requested')!=requested or meta.get('license')!='OFL-1.1' or not allowed_url(meta.get('source','')):
                continue
            font=path.with_suffix('.ttf')
            if not 12<=font.stat().st_size<=16*1024*1024:
                continue
            raw=font.read_bytes()
            if digest(raw)!=meta.get('sha256'):
                continue
            inspect_font(raw)
            if matches_face(raw,requested):
                return str(font)
        except (OSError,ValueError,TypeError,AttributeError,KeyError,struct.error,TTLibError):
            continue
    return ''


def publish(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as output:
        temporary=Path(output.name)
        try:
            output.write(data)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def download_face(requested):
    # No arbitrary hosts, redirects, query strings, path separators or proxy env.
    if not re.fullmatch(r"[A-Za-z0-9 _-]{1,100}", requested):
        return "", "Название не подходит для поиска в каталоге открытых шрифтов"
    family,_,_=requested_face(requested)
    slug = re.sub(r"[ _-]", "", family).lower()
    try:
        with _lock, httpx.Client(timeout=10, follow_redirects=False, trust_env=False,
                               headers={'User-Agent':'Agentico/1.0'}) as client:
            folder=CACHE/slug
            cached=cached_face(folder,requested)
            if cached:
                return cached,''
            metadata = fetch(client, slug + "/METADATA.pb", 256_000).decode("utf-8")
            if not re.search(r'^license: "OFL"$', metadata, re.M):
                return "", "Лицензия каталога не поддерживается"
            candidates = []
            for block in re.findall(r"^fonts \{(.*?)^\}", metadata, re.M | re.S):
                fields = dict(re.findall(r'^\s*(name|full_name|filename): "([^"\n]+)"', block, re.M))
                full = fields.get("full_name", "")
                aliases = {font_key(full), font_key(re.sub(r" Regular$", "", full))}
                if font_key(requested) in aliases and re.fullmatch(r"[A-Za-z0-9_.-]+\.ttf", fields.get("filename", "")):
                    candidates.append(fields["filename"])
            license_raw = fetch(client, slug + "/OFL.txt", 100_000)
            if b"SIL OPEN FONT LICENSE" not in license_raw:
                return "", "Текст лицензии не подтверждён"
            if len(set(candidates))==1:
                filename = candidates[0]
                source=BASE+slug+"/"+filename
                raw = fetch(client, slug + "/" + filename, 16 * 1024 * 1024)
            else:
                raw,source=css_face(client,requested)
            inspect_font(raw)
            if not matches_face(raw,requested):
                return "", "Семейство, вес или курсив внутри TTF не совпадают с запросом"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (digest(raw) + ".ttf")
            # Only validated bytes enter the discovery cache; atomic publication.
            publish(folder / "OFL.txt",license_raw)
            publish(target,raw)
            publish(target.with_suffix(".json"),json.dumps({"source": source,
                "sha256": digest(raw), "license": "OFL-1.1", "requested": requested}).encode())
            return str(target), ""
    except (httpx.HTTPError, ValueError, OSError, KeyError, TypeError, AttributeError, struct.error, TTLibError) as exc:
        return "", "Открытый шрифт не загружен (" + type(exc).__name__ + ")"
