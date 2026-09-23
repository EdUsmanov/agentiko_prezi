"""Input is data. Regex findings are triage, never the security boundary."""
import hashlib
import re
import stat
from pathlib import PurePosixPath
from zipfile import ZipFile, BadZipFile
from defusedxml import ElementTree as SafeET
from .text_layout import without_word_joiners

class InputRejected(ValueError):
    pass

INJECTION = re.compile(
    r"ignore\s+(all\s+)?(previous|system|prior)|system\s*prompt|developer\s*message|"
    r"игнорир\w*\s+(все\s+)?(предыдущ|инструкц)|системн\w*\s+промпт|"
    r"(?:api[_ -]?key|secret|токен|ключ)\s*[:=]|"
    r"(?:curl|wget)\s+https?://|<\s*/?\s*(?:script|iframe)\b|"
    r"(?:send|отправ\w*)\b.{0,50}\b(?:credentials|secrets|секрет|ключи)", re.I)

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def scan_text(text: str, source: str = "user_text"):
    clean, findings = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if INJECTION.search(without_word_joiners(line)):
            findings.append({"source": source, "line": i, "code": "instruction_in_data", "sha256": digest(line.encode())})
            clean.append("")
        else:
            clean.append(line)
    return "\n".join(clean), findings

def validate_pptx(path):
    warnings = []
    try:
        with ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > 12000 or len({i.filename for i in infos}) != len(infos):
                raise InputRejected("Слишком много или повторяющиеся части PPTX")
            if sum(i.file_size for i in infos) > 300 * 1024 * 1024:
                raise InputRejected("Распакованный PPTX превышает 300 МБ")
            if "ppt/presentation.xml" not in z.namelist():
                raise InputRejected("Файл не является PPTX")
            for info in infos:
                name = info.filename
                if "\\" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or ":" in name:
                    raise InputRejected("Небезопасный путь внутри PPTX")
                if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    raise InputRejected("Ссылки и зашифрованные части не поддерживаются")
                if info.file_size > 60 * 1024 * 1024 or info.file_size / max(info.compress_size, 1) > 500:
                    raise InputRejected("Превышен лимит размера или сжатия части PPTX")
                if any(x in name.lower() for x in ["vbaproject", "activex", "oleobject"]):
                    raise InputRejected("Активное содержимое в PPTX запрещено")
                if name.endswith((".xml", ".rels")):
                    raw = z.read(name)
                    try:
                        root = SafeET.fromstring(raw)
                    except Exception as exc:
                        raise InputRejected("Небезопасный или некорректный XML") from exc
                    if name.endswith(".rels"):
                        for rel in root:
                            if rel.get("TargetMode") == "External":
                                warnings.append("Внешняя ссылка исключена из генерации; сетевой запрос не выполнялся")
            slides = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
            if len(slides) > 200:
                raise InputRejected("Допускается не более 200 исходных слайдов")
    except BadZipFile as exc:
        raise InputRejected("Повреждённый PPTX") from exc
    return sorted(set(warnings))
