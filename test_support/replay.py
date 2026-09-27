"""Strict HTTP-boundary model replay. No recording or network fallback in tests."""

import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
import base64
from io import BytesIO
from PIL import Image
import httpx


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def request_contract(body, *, image_matching="pixels"):
    """Pin prompts, schemas, source payloads and generation settings, not auth headers.

    Browser cassettes can compare PNG dimensions because Office rasterization varies
    across platforms. Text, image count/order and all other request fields stay exact.
    """
    if image_matching not in {"pixels", "dimensions"}:
        raise ValueError("Unknown image matching policy")
    body = json.loads(json.dumps(body))
    messages = body.pop("messages")
    contract = {"parameters": body, "messages": []}
    for message in messages:
        content = message["content"]
        if message["role"] == "system":
            content = {"sha256": hashlib.sha256(content.encode()).hexdigest()}
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if not url.startswith("data:image/png;base64,"):
                        raise AssertionError("Replay accepts only inline PNG evidence")
                    raw = base64.b64decode(url.split(",", 1)[1], validate=True)
                    with Image.open(BytesIO(raw)) as image:
                        evidence = {"width": image.width, "height": image.height}
                    if image_matching == "pixels":
                        evidence["sha256"] = hashlib.sha256(raw).hexdigest()
                    part["image_url"]["url"] = evidence
        contract["messages"].append({**message, "content": content})
    return contract


class Replay:
    def __init__(self, path: Path):
        self.document = json.loads(path.read_text())
        if self.document["schema_version"] != 1:
            raise ValueError("Unknown cassette schema")
        self.lock = Lock()
        self.pending = defaultdict(deque)
        self.calls = []
        self.mismatches = []
        for entry in self.document["exchanges"]:
            self.pending[fingerprint(entry["request"])].append(entry)

    def respond(self, body):
        contract = request_contract(
            body, image_matching=self.document.get("image_matching", "pixels")
        )
        key = fingerprint(contract)
        stage = body.get("response_format", {}).get("json_schema", {}).get("name", "unknown")
        with self.lock:
            if not self.pending[key]:
                self.mismatches.append({"stage": stage, "request_hash": key, "request": contract})
                # Authentication-style failure is terminal in production retry policy.
                return (
                    401,
                    {"error": {"message": "Unexpected replay request", "code": "replay_mismatch"}},
                    {},
                )
            entry = self.pending[key].popleft()
            self.calls.append({"stage": stage, "status": entry["status"]})
            return entry["status"], entry["response"], entry.get("headers", {})

    def transport(self):
        def handle(request):
            if request.url.host != "replay.invalid" or request.url.path != "/v1/chat/completions":
                raise AssertionError("Replay transport blocked an unexpected endpoint")
            status, body, headers = self.respond(json.loads(request.content))
            return httpx.Response(status, json=body, headers=headers, request=request)

        return httpx.MockTransport(handle)

    def assert_consumed(self):
        remaining = sum(len(entries) for entries in self.pending.values())
        assert not self.mismatches, (
            f"Unexpected replay requests: {[(m['stage'], m['request_hash']) for m in self.mismatches]}"
        )
        assert remaining == 0, f"Unconsumed replay responses: {remaining}"


def completion(content, *, finish_reason="stop"):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content
                    if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=False),
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }
