"""Opaque user raster assets: no OCR, no vision planning, no document URLs.

Pixels may contain instructions; they NEVER become model control data. Only the
read-only visual audit can see them as part of rendered slides, with no tools.
"""

from studio.contents.content_syntax import IMAGE_LINK as IMAGE_LINK
from io import BytesIO
from pathlib import Path
import re
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from studio.models import UploadedImage
from studio.security import InputRejected, digest, scan_text

MAX_IMAGES = 12
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_PIXELS = 20_000_000


def sanitize_image(raw, name, directory, index):
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise InputRejected("Картинка должна занимать не более 8 МБ")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if (
                    source.format not in ("PNG", "JPEG", "WEBP")
                    or getattr(source, "n_frames", 1) != 1
                ):
                    raise InputRejected(
                        "Нужна статическая PNG, JPEG или WebP; SVG и анимация не поддерживаются"
                    )
                if source.width * source.height > MAX_PIXELS or min(source.size) < 1:
                    raise InputRejected("В картинке более 20 миллионов пикселей")
                source.load()
                normalized = ImageOps.exif_transpose(source).convert("RGBA")
                normalized.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                # A new pixel-only object discards EXIF, ICC, comments, XMP and PNG text.
                clean = Image.frombytes("RGBA", normalized.size, normalized.tobytes())
                output = BytesIO()
                clean.save(output, format="PNG")
                data = output.getvalue()
                if len(data) > MAX_IMAGE_BYTES:
                    raise InputRejected("Нормализованная PNG превышает 8 МБ; уменьшите изображение")
                width, height = clean.size
    except InputRejected:
        raise
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise InputRejected("Повреждённое или небезопасное изображение") from exc
    name = Path(name.replace("\\", "/")).name[:160]
    if not name or scan_text(name, "image_name")[1]:
        raise InputRejected("Небезопасное имя изображения; переименуйте файл")
    folder = directory / "input-images"
    folder.mkdir(exist_ok=True)
    path = folder / (digest(data) + ".png")
    path.write_bytes(data)
    path.chmod(0o600)
    return UploadedImage(
        id=f"img{index}",
        name=name,
        path=str(path.resolve()),
        sha256=digest(data),
        width=width,
        height=height,
    )


def bind_image_sections(images, text):
    from studio.contents.parsing import plain_inline, slide_heading

    clean, _ = scan_text(text)
    by_name = {image.name.casefold(): image for image in images}
    linked = set()
    section = ""
    for line in clean.splitlines():
        raw = line.strip()
        if raw.startswith("#") or slide_heading(plain_inline(raw)) is not None:
            section = plain_inline(raw.lstrip("# "))
        for match in IMAGE_LINK.finditer(line):
            target = match[2].strip()
            # Only exact uploaded filenames; never follow file://, data: or HTTP.
            image = (
                by_name.get(target.casefold())
                if "/" not in target and "\\" not in target and ":" not in target
                else None
            )
            if image is None and not re.match(r"^(?:https?://|file:|data:)", target, re.I):
                raise InputRejected(
                    "Ссылка на картинку не соответствует загруженному файлу. Используйте точное имя без пути: "
                    + target[:160]
                )
            if image is not None:
                if image.id in linked:
                    raise InputRejected(
                        "Картинка упомянута несколько раз. Сейчас каждый файл размещается один раз в каждом варианте: "
                        + image.name
                    )
                linked.add(image.id)
                image.section = section
                image.caption = plain_inline(match[1])[:200]
    return images


def assign_images(package, variant):
    result = [[] for _ in variant.slides]
    facts = {f.id: f for f in package.content.facts}
    for image in package.images:
        matching = [
            i
            for i, slide in enumerate(variant.slides)
            if image.section and any(facts[f].section == image.section for f in slide.fact_ids)
        ]
        candidates = matching or [
            i
            for i, s in enumerate(variant.slides)
            if s.layout != "divider" and s.purpose != "cover" and not s.table_id
        ]
        candidates = candidates or [
            i for i, s in enumerate(variant.slides) if s.layout != "divider"
        ]
        available = [i for i in candidates if len(result[i]) < 4]
        if not available:
            raise InputRejected(
                "На слайд допускается до четырёх картинок. Увеличьте число слайдов или распределите ссылки по разделам Markdown."
            )
        index = min(available, key=lambda i: (len(result[i]), i))
        result[index].append(image)
    return result
