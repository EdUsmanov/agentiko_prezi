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
        }
    assert not (tmp_path / "presentation-studio-source.zip").exists()
