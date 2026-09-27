from collections.abc import Awaitable, Callable
from typing import Any, Protocol


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, mime: str) -> dict[str, Any]: ...
    async def get(self, key: str) -> bytes | None: ...
    async def remove(self, key: str) -> None: ...
    async def list(self, prefix: str) -> list[dict[str, Any]]: ...


class JobQueue(Protocol):
    def start(self, handler: Callable[[str], Awaitable[None]]) -> None: ...
    def notify(self) -> None: ...
    async def stop(self) -> None: ...
    def cancel(self, run_id: str) -> None: ...


class InferenceProvider(Protocol):
    async def complete_structured(
        self,
        *,
        stage: str,
        instructions: str,
        data: Any,
        schema_name: str,
        schema: dict[str, Any],
        images: list[tuple[str, bytes]] | None = None,
        on_stream: Callable[[str, int], None] | None = None,
        options: dict[str, Any] | None = None,
    ) -> Any: ...


class FontDecoder(Protocol):
    async def decode_eot(self, data: bytes) -> dict[str, Any]: ...


class FontResolver(Protocol):
    async def resolve(self, families: set[str]) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...


class FontVariantResolver(Protocol):
    async def resolve_variants(
        self, requests: dict[str, set[tuple[int, str]]]
    ) -> list[dict[str, Any]]: ...


class FileVersionPort(Protocol):
    async def capture(
        self,
        project_id: str,
        path: str,
        content: bytes,
        *,
        source: str = "ai",
        label: str | None = None,
        prompt: str | None = None,
        restore_from: str | None = None,
        parent_id: str | None = None,
    ) -> dict: ...


class DeckRenderer(Protocol):
    async def brand(self, markdown: str, name: str = "Импорт") -> dict[str, Any]: ...
    async def render(
        self,
        deck: dict[str, Any],
        brand: dict[str, Any] | None,
        *,
        baseline: dict[str, Any] | None = None,
        current_html: str | None = None,
        preserve_styles: bool = False,
    ) -> str: ...
    async def export(
        self, html: str, deck: dict[str, Any], brand: dict[str, Any] | None
    ) -> tuple[dict[str, bytes], list[str]]: ...
    async def inspect(self, html: str) -> list[dict[str, Any]]: ...
    async def repair_layout(self, html: str) -> dict[str, Any]: ...


class ProjectRepository(Protocol):
    def get_project(self, project_id: str) -> dict[str, Any] | None: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def add_event(self, run_id: str, event: str, data: dict[str, Any]) -> dict[str, Any]: ...
    def get_stage(self, run_id: str, stage: str, item_key: str = "") -> dict[str, Any] | None: ...
    def put_stage(
        self, run_id: str, stage: str, item_key: str, version: int, value: Any
    ) -> dict[str, Any]: ...
