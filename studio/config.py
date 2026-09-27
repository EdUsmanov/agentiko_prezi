from dataclasses import asdict, dataclass, field
from pathlib import Path
import os
import json
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
POLICY = json.loads((ROOT / "config/policy.json").read_text())


def load_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if (
                    key.strip().startswith(("STUDIO_", "LLM_"))
                    or key.strip() == "OPENROUTER_API_KEY"
                ):
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
    thinking_token_budget: int = 2048
    structured_output: bool = False
    openrouter_providers: tuple[str, ...] = ("alibaba",)
    openrouter_allow_fallbacks: bool = False
    deadline_seconds: float | None = POLICY["generation_deadline_seconds"]
    max_upload_bytes: int = POLICY["max_upload_mb"] * 1024 * 1024
    engine: str = "native"
    visual_review: bool = False
    download_fonts: bool = False
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1", "testserver")

    def worker_environment(self):
        # Freeze the server-owned configuration. A child must not re-read a
        # changed .env or silently inherit different provider/privacy settings.
        values = asdict(self)
        values["data_dir"] = str(self.data_dir)
        return {**os.environ, "STUDIO_WORKER_SETTINGS": json.dumps(values)}

    @classmethod
    def from_worker_env(cls):
        raw = os.environ.get("STUDIO_WORKER_SETTINGS")
        if raw is None:
            return cls.from_env()  # Direct CLI worker invocation.
        values = json.loads(raw)
        values["data_dir"] = Path(values["data_dir"])
        for name in ("vk_hosts", "openrouter_providers", "allowed_hosts"):
            values[name] = tuple(values[name])
        return cls(**values)

    @classmethod
    def from_env(cls):
        load_env()
        base_url = model_base_url()
        openrouter = urlsplit(base_url).hostname == "openrouter.ai"
        return cls(
            data_dir=Path(os.getenv("STUDIO_DATA_DIR", str(ROOT / "data"))).resolve(),
            mode=os.getenv("STUDIO_MODEL_MODE", "extractive"),
            base_url=base_url,
            model_id=os.getenv("STUDIO_MODEL_ID") or os.getenv("LLM_MODEL", ""),
            api_key=(
                os.getenv("OPENROUTER_API_KEY", "")
                if openrouter
                else os.getenv("STUDIO_MODEL_API_KEY") or os.getenv("LLM_API_KEY", "")
            ),
            parameters_b=float(os.getenv("STUDIO_MODEL_PARAMETERS_B", "0")),
            open_weights=os.getenv("STUDIO_MODEL_OPEN_WEIGHTS", "false").lower() == "true",
            license=os.getenv("STUDIO_MODEL_LICENSE", ""),
            stage=os.getenv("STUDIO_STAGE", "selection"),
            vk_hosts=tuple(filter(None, os.getenv("STUDIO_VK_ALLOWED_HOSTS", "").split(","))),
            model_concurrency=max(1, min(3, int(os.getenv("STUDIO_MODEL_CONCURRENCY", "1")))),
            thinking=None
            if "STUDIO_MODEL_THINKING" not in os.environ
            else os.environ["STUDIO_MODEL_THINKING"].lower() == "true",
            thinking_token_budget=max(
                128, min(8192, int(os.getenv("STUDIO_THINKING_TOKEN_BUDGET", "2048")))
            ),
            openrouter_providers=tuple(
                x.strip()
                for x in os.getenv("STUDIO_OPENROUTER_PROVIDERS", "alibaba").split(",")
                if x.strip()
            ),
            openrouter_allow_fallbacks=os.getenv(
                "STUDIO_OPENROUTER_ALLOW_FALLBACKS", "false"
            ).lower()
            == "true",
            structured_output=os.getenv("STUDIO_MODEL_STRUCTURED_OUTPUT", "false").lower()
            == "true",
            engine=os.getenv(
                "STUDIO_ENGINE",
                "deeppresenter" if os.getenv("STUDIO_MODEL_MODE") == "api" else "native",
            ),
            visual_review=os.getenv("STUDIO_VLM_ENABLED", "false").lower() == "true",
            download_fonts=os.getenv("STUDIO_DOWNLOAD_FONTS", "true").lower() == "true",
            allowed_hosts=(
                tuple(
                    x.strip() for x in os.getenv("STUDIO_ALLOWED_HOSTS", "").split(",") if x.strip()
                )
                or ("127.0.0.1", "localhost", "::1", "testserver")
            ),
        )
