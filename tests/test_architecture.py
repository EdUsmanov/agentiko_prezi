"""Recursive import boundaries and cache dependencies for our own Python modules."""

import ast
from importlib.util import resolve_name
from pathlib import Path

from studio import cache_version

ROOT = Path(__file__).resolve().parents[1]


def module_dependencies():
    modules = {}
    for path in (ROOT / "studio").rglob("*.py"):
        if "_vendor" in path.parts:
            continue
        parts = path.relative_to(ROOT).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        modules[name] = path
    graph = {}
    for name, path in modules.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        imports = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = resolve_name("." * node.level + (node.module or ""), package)
                if base.startswith("studio.") and not base.startswith("studio._vendor"):
                    assert base in modules, f"{name} imports missing module {base}"
                imports.add(base)
                imports.update(f"{base}.{alias.name}" for alias in node.names)
        graph[name] = (imports & modules.keys()) - {name}
    return graph


def test_studio_module_dependencies_are_acyclic_including_local_imports():
    graph = module_dependencies()
    visited, active = set(), []

    def visit(name):
        assert name not in active, "Circular import: " + " -> ".join(active + [name])
        if name in visited:
            return
        active.append(name)
        for dependency in sorted(graph[name]):
            visit(dependency)
        active.pop()
        visited.add(name)

    for name in sorted(graph):
        visit(name)


def test_application_and_pipeline_do_not_depend_on_http():
    graph = module_dependencies()
    for name, dependencies in graph.items():
        if name in {"studio.app", "studio.cli", "studio.jobs.worker"} or name.startswith(
            "studio.api"
        ):
            continue
        forbidden = {d for d in dependencies if d == "studio.app" or d.startswith("studio.api")}
        if name != "studio.presentation_service":
            forbidden |= dependencies & {"studio.presentation_service", "studio.jobs.runtime"}
        assert not forbidden, f"{name} imports upper layers: {sorted(forbidden)}"
    for name, dependencies in graph.items():
        if name.startswith("studio.api"):
            assert not any(
                d == "studio.pipeline" or d.startswith(("studio.preparation", "studio.generation"))
                for d in dependencies
            ), f"{name} bypasses the application service"


def test_configured_cache_dependencies_exist():
    names = set(cache_version.TEMPLATE_DEPENDENCIES + cache_version.STAGE_BASE_DEPENDENCIES)
    for stage, dependencies in cache_version.STAGE_DEPENDENCIES.items():
        names.update(dependencies)
        names.add(f"prompts/{stage}.md")
    assert not [name for name in sorted(names) if not (ROOT / name).exists()]


def test_http_and_runtime_changes_do_not_invalidate_analysis_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_version, "ROOT", tmp_path)
    studio = tmp_path / "studio"
    (studio / "api").mkdir(parents=True)
    (studio / "jobs").mkdir()
    (studio / "preparation").mkdir()
    (studio / "preparation/template.py").write_text("template analysis v1")
    before = cache_version.analysis_version()
    for name in ("api/templates.py", "app.py", "presentation_service.py", "jobs/runtime.py"):
        (studio / name).write_text("changed server scheduling")
    assert cache_version.analysis_version() == before
    (studio / "preparation/template.py").write_text("template analysis v2")
    assert cache_version.analysis_version() != before


def test_http_frameworks_are_only_imported_at_http_boundary():
    for path in (ROOT / "studio").rglob("*.py"):
        if "_vendor" in path.parts or "api" in path.parts or path.name == "app.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imports = [node.module or ""]
            assert not any(name.split(".")[0] in {"fastapi", "starlette"} for name in imports), path
