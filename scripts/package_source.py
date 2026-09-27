"""Package distributable source only; never runtime data, environments or keys."""

from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
DIRECTORIES = {"studio", "web", "prompts", "config", "fonts", "scripts", "tests", "vendor"}
ROOT_FILES = {"LICENSE", "pyproject.toml", "requirements.lock", ".env.example", ".gitignore"}
PUBLIC_FONTS = {
    "Play-Regular.ttf",
    "OFL.txt",
    "Montserrat-Regular.ttf",
    "Montserrat-Medium.ttf",
    "Montserrat-Bold.ttf",
    "Montserrat-OFL.txt",
}


def main():
    files = []
    candidates = list(ROOT.iterdir())
    for name in sorted(DIRECTORIES):
        folder = ROOT / name
        if folder.is_dir() and not folder.is_symlink():
            candidates.extend(folder.rglob("*"))
    for path in candidates:
        relative = path.relative_to(ROOT)
        if not path.is_file() or path.is_symlink() or "__pycache__" in relative.parts:
            continue
        if len(relative.parts) == 1:
            include = path.name in ROOT_FILES or path.suffix == ".md"
        else:
            include = relative.parts[0] in DIRECTORIES and not any(
                p.startswith(".") and p != ".dockerignore" for p in relative.parts
            )
            if relative.parts[0] == "fonts":
                include = len(relative.parts) == 2 and path.name in PUBLIC_FONTS
        if include:
            files.append(path)
    target = ROOT.parent / (ROOT.name + "-source.zip")
    temporary = target.with_suffix(".zip.tmp")
    with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, Path(ROOT.name) / path.relative_to(ROOT))
    with ZipFile(temporary) as archive:
        assert not any(
            any(
                p in {".env", ".venv", "data", "test-results", "__pycache__", ".git"}
                for p in Path(n).parts
            )
            for n in archive.namelist()
        )
        assert archive.testzip() is None
    temporary.replace(target)
    print(f"{target}: {len(files)} files, {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
