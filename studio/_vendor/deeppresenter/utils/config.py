"""Studio compatibility layer, NOT upstream endpoint/client configuration.

The model client is injected by Studio. No endpoint, credential, retry, image,
MCP or shell configuration from upstream is loaded.
"""
import json
from typing import Any

LLM = Any
DeepPresenterConfig = Any


def get_json_from_response(response: str):
    # Fail closed instead of recovering executable-looking or partial JSON.
    return json.loads(response)
