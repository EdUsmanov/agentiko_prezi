"""Resolve exact open font binaries from the official Google Fonts service."""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlencode, urlsplit

import httpx

from studio._vendor.font_extraction.pyapi.domain.font_names import base_font_family

logger = logging.getLogger(__name__)

CSS_URL = "https://fonts.googleapis.com/css2"
MAX_CSS_BYTES = 256 * 1024
MAX_FONT_BYTES = 8 * 1024 * 1024
FONT_FACE = re.compile(r"@font-face\s*\{(?P<body>.*?)\}", re.DOTALL)
PROPERTY = re.compile(r"([a-z-]+)\s*:\s*([^;]+)", re.IGNORECASE)
FONT_URL = re.compile(
    r"url\((?P<url>https://fonts\.gstatic\.com/[^)]+)\)\s*format\(['\"]truetype['\"]\)",
    re.IGNORECASE,
)


class GoogleFontsResolver:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30),
            follow_redirects=False,
            headers={"User-Agent": "Agentico/1.0"},
        )
        self.owns_client = client is None
        self._cache: dict[tuple[str, int, str], dict | None] = {}

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
        results = await asyncio.gather(
            *(
                self._resolve_with_retry(family, pending[family])
                for family in sorted(pending)
                if pending[family]
            ),
            return_exceptions=True,
        )
        for family, result in zip(
            (family for family in sorted(pending) if pending[family]), results, strict=True
        ):
            if isinstance(result, BaseException):
                logger.warning(
                    "Open font resolution failed",
                    extra={
                        "event": "pipeline.font.open_resolution_failed",
                        "stage": "reference-fonts",
                        "item_key": family,
                        "error": str(result),
                    },
                )
                continue
            for variant in pending[family]:
                self._cache[(family, *variant)] = next(
                    (item for item in result if (item["weight"], item["style"]) == variant),
                    None,
                )
        return [
            item
            for family in sorted(requests)
            for weight, style in sorted(requests[family])
            if (item := self._cache.get((family, weight, style))) is not None
        ]

    async def _resolve_with_retry(self, family: str, variants: set[tuple[int, str]]) -> list[dict]:
        for attempt in range(3):
            try:
                return await self._resolve_family_variants(family, variants)
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * 2**attempt)
        return []

    async def _resolve_family_variants(
        self, family: str, variants: set[tuple[int, str]]
    ) -> list[dict]:
        if not variants:
            return []
        candidate = base_font_family(family)
        axes = ";".join(
            f"{int(style == 'italic')},{weight}"
            for weight, style in sorted(variants, key=lambda item: (item[1] == "italic", item[0]))
        )
        query = urlencode({"family": f"{candidate}:ital,wght@{axes}", "display": "swap"})
        response = await self.client.get(f"{CSS_URL}?{query}")
        if response.status_code == 400:
            if len(variants) > 1:
                records = []
                for variant in sorted(variants):
                    records.extend(await self._resolve_family_variants(family, {variant}))
                return records
            return []
        response.raise_for_status()
        if len(response.content) > MAX_CSS_BYTES:
            raise ValueError("Google Fonts stylesheet exceeds the size limit")
        records = []
        downloaded: dict[str, bytes] = {}
        for match in FONT_FACE.finditer(response.text):
            properties = {
                key.casefold(): value.strip().strip("'\"")
                for key, value in PROPERTY.findall(match.group("body"))
            }
            url_match = FONT_URL.search(properties.get("src", ""))
            if (
                not url_match
                or properties.get("font-family", "").casefold() != candidate.casefold()
            ):
                continue
            weight = int(properties.get("font-weight", "400"))
            style = properties.get("font-style", "normal")
            if (weight, style) not in variants:
                continue
            url = url_match.group("url")
            if urlsplit(url).hostname != "fonts.gstatic.com":
                raise ValueError("Google Fonts returned an unexpected asset host")
            if url not in downloaded:
                font = await self.client.get(url)
                font.raise_for_status()
                if len(font.content) > MAX_FONT_BYTES:
                    raise ValueError("Google font binary exceeds the size limit")
                downloaded[url] = font.content
            records.append(
                {
                    "family": family,
                    "resolvedFamily": candidate,
                    "weight": weight,
                    "style": style,
                    "source": "google-fonts",
                    "data": downloaded[url],
                }
            )
        return records
