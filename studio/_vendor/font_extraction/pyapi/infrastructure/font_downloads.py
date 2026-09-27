"""Download exact missing font variants from known public font providers."""

from __future__ import annotations

import asyncio
import logging
import re
import struct
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlsplit
from zipfile import BadZipFile, ZipFile

import httpx

from ..domain.font_names import base_font_family
from ..pipeline.reference_fonts import _font_permissions, _may_repackage, _tables
from .font_download_cache import FontDownloadCache
from .open_fonts import GoogleFontsResolver

logger = logging.getLogger(__name__)

APTOS_URL = (
    "https://download.microsoft.com/download/8/6/0/"
    "860a94fa-7feb-44ef-ac79-c072d9113d69/Microsoft%20Aptos%20Fonts.zip"
)
FONTSOURCE_API = "https://api.fontsource.org/v1/fonts"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_FONT_BYTES = 8 * 1024 * 1024
APTOS_FAMILIES = {
    "aptos": "Aptos",
    "aptos display": "Aptos-Display",
    "aptos mono": "Aptos-Mono",
    "aptos narrow": "Aptos-Narrow",
    "aptos serif": "Aptos-Serif",
}
WEIGHT_NAMES = {400: "", 300: "Light", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}


def _font_names(data: bytes) -> dict[int, set[str]]:
    tables = _tables(data)
    offset, size = tables.get(b"name", (0, 0))
    if size < 6:
        raise ValueError("Downloaded font has no name table")
    _, count, string_offset = struct.unpack_from(">HHH", data, offset)
    if 6 + count * 12 > size:
        raise ValueError("Downloaded font name table is truncated")
    result: dict[int, set[str]] = {}
    for index in range(count):
        platform, _, _, name_id, length, record_offset = struct.unpack_from(
            ">HHHHHH", data, offset + 6 + index * 12
        )
        if name_id not in {1, 2, 4, 6, 16, 17} or platform not in {0, 1, 3}:
            continue
        start = offset + string_offset + record_offset
        if start + length > offset + size:
            continue
        try:
            label = data[start : start + length].decode(
                "utf-16-be" if platform in {0, 3} else "mac_roman"
            )
        except UnicodeError:
            continue
        result.setdefault(name_id, set()).add(label.strip().casefold())
    return result


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _matches(data: bytes, family: str, weight: int, style: str) -> bool:
    try:
        if not _may_repackage(_font_permissions(data)):
            return False
        names = _font_names(data)
        base = base_font_family(family)
        regular_names = names.get(1, set()) | names.get(16, set())
        full_names = names.get(4, set()) | names.get(6, set())
        exact = _normalized_name(family)
        base_name = _normalized_name(base)
        if base_name not in {_normalized_name(name) for name in regular_names} and not any(
            _normalized_name(name).startswith(base_name) for name in full_names
        ):
            return False
        if exact != base_name and not any(
            _normalized_name(name).startswith(exact) for name in regular_names | full_names
        ):
            return False
        os2_offset, os2_size = _tables(data)[b"OS/2"]
        if os2_size < 6:
            return False
        binary_weight = struct.unpack_from(">H", data, os2_offset + 4)[0]
        if max(100, min(900, round(binary_weight / 100) * 100)) != weight:
            return False
        labels = names.get(2, set()) | names.get(17, set())
        italic = any("italic" in label or "oblique" in label for label in labels)
        return italic == (style == "italic")
    except (KeyError, ValueError, struct.error):
        return False


async def _download(client: httpx.AsyncClient, url: str, limit: int) -> bytes:
    data = bytearray()
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        length = response.headers.get("content-length")
        if length and int(length) > limit:
            raise ValueError("Font download exceeds size limit")
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("Font download exceeds size limit")
    return bytes(data)


class DownloadableFontResolver:
    """Try Google Fonts, Fontsource non-Google fonts, then Microsoft's Aptos package."""

    def __init__(self, client: httpx.AsyncClient | None = None, cache_dir: Path | None = None):
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30),
            follow_redirects=False,
            headers={"User-Agent": "Agentico/1.0"},
        )
        self.owns_client = client is None
        self.google = GoogleFontsResolver(self.client)
        self.disk_cache = FontDownloadCache(
            cache_dir or Path.home() / ".cache" / "agentico" / "font-downloads"
        )
        self._aptos_archive: bytes | None = None
        self._cache: dict[tuple[str, int, str], dict] = {}

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def resolve(self, families: set[str]) -> list[dict]:
        return await self.resolve_variants(
            {family: {(400, "normal"), (700, "normal")} for family in families}
        )

    async def resolve_variants(self, requests: dict[str, set[tuple[int, str]]]) -> list[dict]:
        pending = {
            family: {variant for variant in variants if (family, *variant) not in self._cache}
            for family, variants in requests.items()
        }
        for family, variants in pending.items():
            for weight, style in variants:
                cached = self.disk_cache.get(family, weight, style)
                if cached is not None and _matches(cached["data"], family, weight, style):
                    self._cache[(family, weight, style)] = cached
        pending = {
            family: {variant for variant in variants if (family, *variant) not in self._cache}
            for family, variants in pending.items()
        }
        pending = {family: variants for family, variants in pending.items() if variants}
        if pending:
            await self._resolve_pending(pending)
        return [
            item
            for family in sorted(requests)
            for weight, style in sorted(requests[family])
            if (item := self._cache.get((family, weight, style))) is not None
        ]

    async def _resolve_pending(self, pending: dict[str, set[tuple[int, str]]]) -> None:
        found: dict[tuple[str, int, str], dict] = {}
        providers = (self.google.resolve_variants, self._fontsource, self._aptos)
        for provider in providers:
            remaining = {
                family: {v for v in variants if (family, *v) not in found}
                for family, variants in pending.items()
            }
            remaining = {family: variants for family, variants in remaining.items() if variants}
            if not remaining:
                break
            try:
                records = await provider(remaining)
            except (httpx.HTTPError, ValueError, BadZipFile) as error:
                logger.warning(
                    "Font provider failed",
                    extra={
                        "event": "pipeline.font.download_failed",
                        "stage": "reference-fonts",
                        "error": str(error),
                    },
                )
                continue
            for item in records:
                key = (item["family"], item["weight"], item["style"])
                if key in found or key[0] not in remaining or key[1:] not in remaining[key[0]]:
                    continue
                if _matches(item["data"], *key):
                    found[key] = item
                else:
                    logger.warning(
                        "Downloaded font does not match requested variant",
                        extra={
                            "event": "pipeline.font.download_mismatch",
                            "stage": "reference-fonts",
                            "item_key": key[0],
                        },
                    )
        for family, variants in pending.items():
            for variant in variants:
                key = (family, *variant)
                if key in found:
                    self._cache[key] = found[key]
                    try:
                        self.disk_cache.put(found[key])
                    except OSError:
                        logger.warning(
                            "Verified font could not be cached",
                            extra={
                                "event": "pipeline.font.cache_write_failed",
                                "stage": "reference-fonts",
                                "item_key": family,
                            },
                            exc_info=True,
                        )

    async def _fontsource(self, requests: dict[str, set[tuple[int, str]]]) -> list[dict]:
        tasks = [self._fontsource_family(family, requests[family]) for family in sorted(requests)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        records = []
        for family, result in zip(sorted(requests), results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "Fontsource lookup failed",
                    extra={
                        "event": "pipeline.font.fontsource_failed",
                        "stage": "reference-fonts",
                        "item_key": family,
                        "error": str(result),
                    },
                )
            else:
                records.extend(result)
        return records

    async def _fontsource_family(self, family: str, variants: set[tuple[int, str]]) -> list[dict]:
        candidate = base_font_family(family)
        response = await self.client.get(FONTSOURCE_API, params={"family": candidate})
        response.raise_for_status()
        entries = response.json()
        entry = next(
            (row for row in entries if row.get("family", "").casefold() == candidate.casefold()),
            None,
        )
        if entry is None or entry.get("type") != "other":
            return []
        identifier = entry["id"]
        if not identifier.replace("-", "").isalnum():
            return []
        details = await self.client.get(f"{FONTSOURCE_API}/{quote(identifier)}")
        details.raise_for_status()
        metadata = details.json()
        if metadata.get("family", "").casefold() != candidate.casefold():
            return []
        subsets = metadata.get("subsets", [])
        if len(subsets) != 1:
            return []
        subset = subsets[0]
        records = []
        for weight, style in sorted(variants):
            url = (
                metadata.get("variants", {})
                .get(str(weight), {})
                .get(style, {})
                .get(subset, {})
                .get("url", {})
                .get("ttf")
            )
            if not url:
                continue
            address = urlsplit(url)
            if address.scheme != "https" or address.hostname != "cdn.jsdelivr.net":
                continue
            if not address.path.startswith(f"/fontsource/fonts/{identifier}@"):
                continue
            data = await _download(self.client, url, MAX_FONT_BYTES)
            records.append(
                {
                    "family": family,
                    "resolvedFamily": candidate,
                    "weight": weight,
                    "style": style,
                    "source": "fontsource",
                    "data": data,
                }
            )
        return records

    async def _aptos(self, requests: dict[str, set[tuple[int, str]]]) -> list[dict]:
        candidates = {
            family: APTOS_FAMILIES.get(base_font_family(family).casefold()) for family in requests
        }
        if not any(candidates.values()):
            return []
        if self._aptos_archive is None:
            self._aptos_archive = await _download(self.client, APTOS_URL, MAX_ARCHIVE_BYTES)
        records = []
        with ZipFile(BytesIO(self._aptos_archive)) as package:
            for family, prefix in candidates.items():
                if prefix is None:
                    continue
                for weight, style in requests[family]:
                    suffix = WEIGHT_NAMES.get(weight)
                    if suffix is None or (prefix != "Aptos" and weight not in {400, 700}):
                        continue
                    name = (
                        "-".join(
                            part
                            for part in (prefix, suffix, "Italic" if style == "italic" else "")
                            if part
                        )
                        + ".ttf"
                    )
                    if name not in package.namelist():
                        continue
                    info = package.getinfo(name)
                    if info.file_size > MAX_FONT_BYTES:
                        continue
                    records.append(
                        {
                            "family": family,
                            "resolvedFamily": base_font_family(family),
                            "weight": weight,
                            "style": style,
                            "source": "microsoft-aptos",
                            "data": package.read(info),
                        }
                    )
        return records
