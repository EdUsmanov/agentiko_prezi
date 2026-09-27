from zipfile import ZipFile

from scripts import package_source


def test_source_archive_is_isolated_and_excludes_runtime(tmp_path, monkeypatch):
    root = tmp_path / "isolated-experiment"
    root.mkdir()
    for name in (
        "studio/app.py",
        "scripts/runtime/Dockerfile",
        "scripts/runtime/.dockerignore",
        ".env.example",
        ".env",
        "experiment-data/job.json",
        ".experiment-runtime/key.json",
        "data/private.txt",
        "README.md",
        "requirements-dev.txt",
        "requirements-e2e.txt",
        "e2e/test_browser_flows.py",
        "test_support/replay.py",
        "tests/fixtures/model_responses/example.json",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    (root / "studio/linked.py").symlink_to(root / ".env")
    monkeypatch.setattr(package_source, "ROOT", root)
    package_source.main()
    with ZipFile(tmp_path / "isolated-experiment-source.zip") as archive:
        assert set(archive.namelist()) == {
            "isolated-experiment/studio/app.py",
            "isolated-experiment/scripts/runtime/Dockerfile",
            "isolated-experiment/scripts/runtime/.dockerignore",
            "isolated-experiment/.env.example",
            "isolated-experiment/README.md",
            "isolated-experiment/requirements-dev.txt",
            "isolated-experiment/requirements-e2e.txt",
            "isolated-experiment/e2e/test_browser_flows.py",
            "isolated-experiment/test_support/replay.py",
            "isolated-experiment/tests/fixtures/model_responses/example.json",
        }
    assert not (tmp_path / "presentation-studio-source.zip").exists()
