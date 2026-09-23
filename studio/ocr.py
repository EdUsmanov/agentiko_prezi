"""Optional NeuralDeep OCR transport, NOT enabled in the contest pipeline.

Contract: https://neuraldeep.ru/llms-full.txt, OCR section (2026-09-23).
Submission and retrieval are deliberately separate: never automatically repeat
a quota-charging POST after a timeout. Callers must persist the returned ticket.
"""
import asyncio
from dataclasses import dataclass, field
import json
import re
from urllib.parse import urlsplit
import httpx


class OcrError(ValueError):
    pass


@dataclass(frozen=True)
class OcrTicket:
    id: str
    page_count: int
    scan_pages_charged: int | None = None


@dataclass
class NeuralDeepOcr:
    base_url: str
    api_key: str = field(repr=False)
    transport: object = field(default=None, repr=False)
    max_upload_bytes: int = 20 * 1024 * 1024
    max_response_bytes: int = 1024 * 1024

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        url = urlsplit(self.base_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise OcrError("OCR endpoint должен быть серверным HTTPS URL без credentials")
        if not self.api_key:
            raise OcrError("Не задан ключ OCR")

    async def _request(self, method, path, **kwargs):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False,
                                         trust_env=False, transport=self.transport) as client:
                async with client.stream(method, self.base_url + path,
                        headers={"Authorization": "Bearer " + self.api_key}, **kwargs) as response:
                    if response.status_code >= 300:
                        # Do not reflect upstream response bodies, URLs or credentials.
                        raise OcrError(f"OCR HTTP {response.status_code}; автоматический повтор не выполнялся")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > self.max_response_bytes:
                            raise OcrError("Ответ OCR превышает защитный лимит")
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise OcrError("Неверный формат ответа OCR")
                    return result
        except httpx.RequestError:
            raise OcrError("Сетевая ошибка OCR; состояние загрузки может быть неизвестно. Не повторяйте загрузку автоматически") from None

    @staticmethod
    def _job_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise OcrError("Некорректный идентификатор OCR-задачи")
        return value

    async def submit(self, document: bytes, *, profile="fast") -> OcrTicket:
        """Quota-charging operation. Accept bytes, never a user-supplied URL/path."""
        if profile not in ("fast", "pro"):
            raise OcrError("Неизвестный профиль OCR")
        if not document or len(document) > self.max_upload_bytes:
            raise OcrError("Превышен размер документа OCR или документ пуст")
        if document.startswith(b"%PDF-"):
            name, mime = "document.pdf", "application/pdf"
        elif document.startswith(b"\x89PNG\r\n\x1a\n"):
            name, mime = "document.png", "image/png"
        elif document.startswith(b"\xff\xd8\xff"):
            name, mime = "document.jpg", "image/jpeg"
        else:
            raise OcrError("Адаптер принимает только PDF, PNG и JPEG")
        response = await self._request("POST", "/ocr/extract",
            files={"file": (name, document, mime)}, data={"model_profile": profile})
        jid = self._job_id(response.get("id"))
        pages, charged = response.get("page_count"), response.get("scan_pages_charged")
        if type(pages) is not int or pages < 1 or (charged is not None and (type(charged) is not int or charged < 0)):
            raise OcrError("OCR вернул некорректный учёт страниц; повторная загрузка не выполнялась")
        return OcrTicket(jid, pages, charged)

    async def result(self, job_id: str, *, timeout=300, poll_interval=1):
        """Resume retrieval of an existing ticket without charging another upload.

        Returned markdown is UNTRUSTED; route it through content.parse_content,
        never insert it into a system prompt or render as raw HTML.
        """
        jid = self._job_id(job_id)
        if not 0 < timeout <= 3600 or not 1 <= poll_interval <= 60:
            raise OcrError("Недопустимое время ожидания OCR")
        async with asyncio.timeout(timeout):
            while True:
                status = await self._request("GET", f"/ocr/jobs/{jid}")
                state = status.get("status")
                if state == "completed":
                    break
                if state in ("failed", "error", "cancelled") or not isinstance(state, str) or not state:
                    raise OcrError("OCR-задача завершилась ошибкой или вернула неизвестный статус")
                await asyncio.sleep(poll_interval)
            response = await self._request("GET", f"/ocr/jobs/{jid}/result", params={"format": "markdown"})
            content = response.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 120_000:
                raise OcrError("Пустой, слишком длинный или некорректный текст OCR")
            return content
