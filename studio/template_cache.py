"""Private, integrity-checked template snapshots, independent of user text/slide count."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .models import TemplateProfile
from .cache_version import template_version
from .config import ROOT


TEMPLATE_REPORT_KEYS = {
    "version",
    "model_mode",
    "model_id",
    "technical",
    "template_semantics",
    "visual_model_review",
    "native_render",
    "warnings",
    "template_graphics",
    "text_zone_review",
    "raster_review",
    "template_text_adaptations",
    "template_timeline_adaptations",
    "template_data_regions",
    "template_roomy_regions",
    "template_cover_adaptations",
}


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def map_strings(value, transform):
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        return [map_strings(v, transform) for v in value]
    if isinstance(value, dict):
        return {k: map_strings(v, transform) for k, v in value.items()}
    return value


def font_inventory(settings):
    roots = [
        ROOT / "fonts",
        ROOT / "data/local-fonts",
        Path.home() / "Library/Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
    ]
    root = getattr(settings, "data_dir", None)
    if root:
        roots.append(Path(root) / "local-fonts")
    found = {}
    for root in roots:
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in (
                    ".ttf",
                    ".otf",
                    ".woff",
                    ".woff2",
                    ".ttc",
                ):
                    stat = path.stat()
                    found[str(path.resolve())] = (stat.st_size, stat.st_mtime_ns)
    return found


class TemplateCache:
    def __init__(self, settings):
        self.settings = settings
        root = getattr(settings, "data_dir", None)
        self.root = Path(root) / "template-cache" if root else None

    def identity(self, sha):
        names = (
            "mode",
            "model_id",
            "base_url",
            "thinking",
            "thinking_token_budget",
            "structured_output",
            "openrouter_providers",
            "openrouter_allow_fallbacks",
            "visual_review",
            "download_fonts",
        )
        return {
            "sha256": sha,
            "version": template_version(),
            "settings": {n: getattr(self.settings, n, None) for n in names},
            "fonts": font_inventory(self.settings),
        }

    def location(self, sha):
        identity = self.identity(sha)
        return self.root / hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def restore(self, source, directory):
        if self.root is None:
            return None
        directory = Path(directory).resolve()
        sha = digest_file(source)
        entry = self.location(sha)
        try:
            manifest = json.loads((entry / "snapshot.json").read_text())
            if manifest["identity"] != json.loads(json.dumps(self.identity(sha))):
                return None
            files = manifest["files"]
            for relative, expected in files.items():
                path = (entry / "files" / relative).resolve()
                if (
                    not path.is_relative_to((entry / "files").resolve())
                    or not (directory / relative).resolve().is_relative_to(directory)
                    or digest_file(path) != expected
                ):
                    return None

            def decode(value):
                if value.startswith("@template/"):
                    rel = value[len("@template/") :]
                    target = (directory / rel).resolve()
                    if rel not in files or not target.is_relative_to(directory):
                        raise ValueError("Invalid cached artifact")
                    return str(target)
                return value

            profile = TemplateProfile.model_validate(map_strings(manifest["profile"], decode))
            if profile.sha256 != sha:
                return None
            for path, expected in manifest["external_files"].items():
                if digest_file(path) != expected:
                    return None
            # Validate every file and reference before materialising any state.
            for relative in files:
                target = directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                data = (entry / "files" / relative).read_bytes()
                if target.suffix == ".json":
                    data = json.dumps(
                        map_strings(json.loads(data), decode), ensure_ascii=False, indent=2
                    ).encode()
                target.write_bytes(data)
            from .diagnostics import event

            event("template.cache_hit", template_sha256=sha)
            return profile, deepcopy(manifest["analysis"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, profile, directory, analysis):
        if (
            self.root is None
            or profile.font_replacements
            or any(f.get("required_for_generation", True) for f in profile.missing_fonts)
        ):
            return False
        if analysis.get("template_semantics", {}).get("status") not in ("completed", "not_run"):
            return False
        if any(
            p.get("vl_status") in ("failed", "not_run")
            for p in analysis.get("text_zone_review", {}).get("patterns", [])
        ):
            return False
        directory = Path(directory).resolve()
        identity = self.identity(profile.sha256)
        entry = self.location(profile.sha256)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        files = set()
        external = {}

        def encode(value):
            if value.startswith(str(directory) + os.sep) and Path(value).is_file():
                rel = str(Path(value).resolve().relative_to(directory))
                files.add(rel)
                return "@template/" + rel
            return value

        encoded = map_strings(profile.model_dump(), encode)
        for name in (
            "font-model.json",
            "layout-font-model.json",
            "color-model.json",
            "background-model.json",
            "text-zones.json",
            "text-zone-review.json",
        ):
            if (directory / name).is_file():
                files.add(name)
        for asset in profile.font_assets:
            path = Path(asset["path"]).resolve()
            if not path.is_relative_to(directory):
                external[str(path)] = asset["sha256"]
        path = Path(profile.font_file).resolve()
        if path.is_file() and not path.is_relative_to(directory):
            external[str(path)] = digest_file(path)
        with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=self.root) as tmp:
            staging = Path(tmp)
            hashes = {}
            pending = list(files)
            while pending:
                rel = pending.pop()
                target = staging / "files" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                source = directory / rel
                data = source.read_bytes()
                if source.suffix == ".json":
                    known = set(files)
                    data = json.dumps(
                        map_strings(json.loads(data), encode), ensure_ascii=False, indent=2
                    ).encode()
                    pending.extend(files - known)
                target.write_bytes(data)
                hashes[rel] = hashlib.sha256(data).hexdigest()
            snapshot = {
                "identity": identity,
                "profile": encoded,
                "analysis": {
                    k: deepcopy(v) for k, v in analysis.items() if k in TEMPLATE_REPORT_KEYS
                },
                "files": hashes,
                "external_files": external,
            }
            (staging / "snapshot.json").write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2)
            )
            (staging / "snapshot.json").chmod(0o600)
            if entry.exists():
                # Existing content-addressed snapshots are immutable. A corrupt
                # entry is quarantined, never overwritten while another reader uses it.
                try:
                    current = json.loads((entry / "snapshot.json").read_text())
                    valid = current["identity"] == json.loads(json.dumps(identity)) and all(
                        (entry / "files" / rel)
                        .resolve()
                        .is_relative_to((entry / "files").resolve())
                        and digest_file(entry / "files" / rel) == expected
                        for rel, expected in current["files"].items()
                    )
                    if valid:
                        return True
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                quarantine = entry.with_name(entry.name + ".invalid-" + str(os.getpid()))
                try:
                    entry.rename(quarantine)
                except FileNotFoundError:
                    pass
            try:
                staging.rename(entry)
            except FileExistsError:
                pass
        return True
