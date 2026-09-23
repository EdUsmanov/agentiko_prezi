import asyncio
import json
from urllib.parse import urlsplit
import httpx
from .config import Settings, ROOT, POLICY
from .opendesign import craft_context

class ModelPolicyError(ValueError):
    pass

def validate_model_policy(settings: Settings):
    if settings.mode == "extractive":
        if settings.stage == "final":
            raise ModelPolicyError("В финальном режиме требуется настроенный VK Inference")
        return
    if settings.mode != "api":
        raise ModelPolicyError("Неизвестный режим модели")
    url = urlsplit(settings.base_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ModelPolicyError("Некорректный адрес inference endpoint")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ModelPolicyError("Внешний inference endpoint должен использовать HTTPS")
    if not settings.open_weights or not (0 < settings.parameters_b <= POLICY["max_model_parameters_b"]) or settings.license not in POLICY["allowed_model_licenses"]:
        raise ModelPolicyError("Укажите открытые веса, размер до 35B и лицензию Apache-2.0/MIT")
    if not settings.model_id:
        raise ModelPolicyError("Не задан MODEL_ID")
    if any(x in settings.model_id.lower() for x in ("gpt-", "claude", "gemini", "o1-", "o3-")):
        raise ModelPolicyError("Закрытые модели в конкурсной сборке запрещены")
    if settings.stage == "final" and url.hostname not in settings.vk_hosts:
        raise ModelPolicyError("Финал допускает только явно настроенный VK endpoint")

class ModelGateway:
    def __init__(self, settings):
        validate_model_policy(settings)
        self.settings = settings
        self.limiter = asyncio.Semaphore(settings.model_concurrency)
        self.usage = []

    async def json_request(self, prompt_name, payload, timeout=60, schema=None):
        if self.settings.mode != "api":
            raise ModelPolicyError("Модель не подключена")
        system = (ROOT / "prompts" / f"{prompt_name}.md").read_text()
        if prompt_name == "planner":
            system += "\nDesign craft (template contract takes priority):\n" + craft_context()
        if schema:
            system += "\nOutput JSON schema:\n" + json.dumps(schema, ensure_ascii=False)
        # No tools or function calls are exposed to the model. Data never enters system.
        body = {"model": self.settings.model_id, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"untrusted_input": payload}, ensure_ascii=False)}],
            "temperature": .25, "max_tokens": 10000, "response_format": {"type": "json_object"}}
        headers = {"Authorization": f"Bearer {self.settings.api_key}"} if self.settings.api_key else {}
        async with asyncio.timeout(timeout):
            async with self.limiter:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                    response = await client.post(self.settings.base_url.rstrip("/") + "/chat/completions", json=body, headers=headers)
                    response.raise_for_status()
                    result = response.json()
        self.usage.append(result.get("usage", {}))
        value = result["choices"][0]["message"]["content"]
        if not isinstance(value, str) or len(value) > 200_000:
            raise ValueError("Некорректный ответ модели")
        if value.startswith("```json") and value.rstrip().endswith("```"):
            value = value[7:].rstrip()[:-3]
        return json.loads(value)
