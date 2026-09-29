"""Select a working model endpoint before app jobs are resumed."""

import asyncio
from dataclasses import replace
import json
from urllib.parse import urlsplit

import httpx

from studio.config import Settings
from studio.providers.gateway import validate_model_policy


PROBE_TIMEOUT_SECONDS = 10


async def probe_completion(settings: Settings, *, transport=None) -> None:
    """Verify that this credential, model and request format can complete a call."""
    body = {
        "model": settings.model_id,
        "messages": [{"role": "user", "content": 'Reply with only this JSON object: {"ok":true}.'}],
        "temperature": 0,
        "max_tokens": 32,
        "response_format": {"type": "json_object"},
    }
    if urlsplit(settings.base_url).hostname == "openrouter.ai":
        body["reasoning"] = {"enabled": False}
        body["provider"] = {
            "only": list(settings.openrouter_providers),
            "allow_fallbacks": settings.openrouter_allow_fallbacks,
            "require_parameters": True,
        }
    else:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
        async with httpx.AsyncClient(
            timeout=PROBE_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = await client.post(
                settings.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + settings.api_key},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("Model probe returned no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not isinstance(json.loads(content), dict):
        raise ValueError("Model probe returned invalid JSON content")


async def select_startup_provider(settings: Settings, *, transport=None) -> Settings:
    """Keep OpenRouter when healthy; use NeuralDeep if its real probe succeeds."""
    if settings.mode != "api" or urlsplit(settings.base_url).hostname != "openrouter.ai":
        return settings

    fallback_values = (
        settings.fallback_model_base_url,
        settings.fallback_model_id,
        settings.fallback_model_api_key,
    )
    if any(fallback_values) and not all(fallback_values):
        raise ValueError("Настройки резервной модели должны включать адрес, модель и ключ")
    fallback = None
    if all(fallback_values):
        fallback = replace(
            settings,
            base_url=settings.fallback_model_base_url,
            model_id=settings.fallback_model_id,
            api_key=settings.fallback_model_api_key,
            fallback_model_base_url="",
            fallback_model_id="",
            fallback_model_api_key="",
            openrouter_providers=(),
            openrouter_allow_fallbacks=False,
        )
        if urlsplit(fallback.base_url).hostname != "api.neuraldeep.ru":
            raise ValueError("Резервный endpoint должен принадлежать NeuralDeep")
        validate_model_policy(fallback)

    try:
        await probe_completion(settings, transport=transport)
        return settings
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, IndexError):
        if fallback is None:
            raise RuntimeError("OpenRouter недоступен, резервный провайдер не настроен") from None

    try:
        await probe_completion(fallback, transport=transport)
        return fallback
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, IndexError):
        raise RuntimeError("OpenRouter и NeuralDeep недоступны при запуске") from None
