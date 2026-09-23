from dataclasses import dataclass, field
from pathlib import Path
import os
import json

ROOT = Path(__file__).resolve().parent.parent
POLICY = json.loads((ROOT / "config/policy.json").read_text())

def load_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key.strip().startswith(("STUDIO_", "LLM_")):
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def model_base_url():
    explicit = os.getenv("STUDIO_MODEL_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    endpoint = os.getenv("LLM_CHAT_COMPLETIONS_URL", "").rstrip("/")
    return endpoint.removesuffix("/chat/completions")

@dataclass(frozen=True)
class Settings:
    data_dir: Path
    mode: str = "extractive"
    base_url: str = ""
    model_id: str = ""
    api_key: str = field(default="", repr=False)
    parameters_b: float = 0
    open_weights: bool = False
    license: str = ""
    stage: str = "selection"
    vk_hosts: tuple[str, ...] = ()
    model_concurrency: int = 1
    thinking: bool | None = None
    structured_output: bool = False
    deadline_seconds: float = POLICY["generation_deadline_seconds"]
    max_upload_bytes: int = POLICY["max_upload_mb"] * 1024 * 1024

    @classmethod
    def from_env(cls):
        load_env()
        return cls(
            data_dir=Path(os.getenv("STUDIO_DATA_DIR", str(ROOT / "data"))).resolve(),
            mode=os.getenv("STUDIO_MODEL_MODE", "extractive"),
            base_url=model_base_url(),
            model_id=os.getenv("STUDIO_MODEL_ID") or os.getenv("LLM_MODEL", ""),
            api_key=os.getenv("STUDIO_MODEL_API_KEY") or os.getenv("LLM_API_KEY", ""),
            parameters_b=float(os.getenv("STUDIO_MODEL_PARAMETERS_B", "0")),
            open_weights=os.getenv("STUDIO_MODEL_OPEN_WEIGHTS", "false").lower() == "true",
            license=os.getenv("STUDIO_MODEL_LICENSE", ""),
            stage=os.getenv("STUDIO_STAGE", "selection"),
            vk_hosts=tuple(filter(None, os.getenv("STUDIO_VK_ALLOWED_HOSTS", "").split(","))),
            model_concurrency=max(1, min(3, int(os.getenv("STUDIO_MODEL_CONCURRENCY", "1")))),
            thinking=None if "STUDIO_MODEL_THINKING" not in os.environ else os.environ["STUDIO_MODEL_THINKING"].lower() == "true",
            structured_output=os.getenv("STUDIO_MODEL_STRUCTURED_OUTPUT", "false").lower() == "true",
        )
