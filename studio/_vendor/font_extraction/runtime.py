"""Wire the bundled extractor to its local EOT decoder and font sources."""

from contextlib import asynccontextmanager

from decoder import EmbeddedFontDecoder
from delivery import FontExtractionService

from pyapi.infrastructure.font_downloads import DownloadableFontResolver


@asynccontextmanager
async def font_service():
    resolver = DownloadableFontResolver()
    try:
        yield FontExtractionService(EmbeddedFontDecoder(), resolver)
    finally:
        await resolver.close()
