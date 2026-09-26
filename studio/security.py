"""Input is data. Regex findings are triage, never the security boundary."""
import hashlib
import re
import stat
import unicodedata
from pathlib import PurePosixPath
from zipfile import ZipFile, BadZipFile
from defusedxml import ElementTree as SafeET
from .text_layout import without_word_joiners

class InputRejected(ValueError):
    pass

PPTX_MAIN='application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml'
POTX_MAIN='application/vnd.openxmlformats-officedocument.presentationml.template.main+xml'


def presentation_content_type(archive):
    try:
        root=SafeET.fromstring(archive.read('[Content_Types].xml'))
    except Exception as exc:
        raise InputRejected('Некорректное описание формата PowerPoint') from exc
    ns='{http://schemas.openxmlformats.org/package/2006/content-types}'
    main=[n.get('ContentType') for n in root if n.tag==ns+'Override' and n.get('PartName')=='/ppt/presentation.xml']
    if root.tag!=ns+'Types' or len(main)!=1 or main[0] not in (PPTX_MAIN,POTX_MAIN):
        raise InputRejected('Поддерживаются только PPTX и POTX без макросов')
    if any('macroenabled' in n.get('ContentType','').lower() or 'vbaproject' in n.get('ContentType','').lower() for n in root):
        raise InputRejected('Активное содержимое в PowerPoint запрещено')
    return main[0]

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
        # Detection-only normalization: preserve original legitimate source text.
        probe=unicodedata.normalize('NFKC',without_word_joiners(line))
        probe=''.join(c for c in probe if unicodedata.category(c)!='Cf')
        if INJECTION.search(probe):
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
                raise InputRejected("Слишком много или повторяющиеся части файла PowerPoint")
            if sum(i.file_size for i in infos) > 300 * 1024 * 1024:
                raise InputRejected("Распакованный шаблон превышает 300 МБ")
            if "ppt/presentation.xml" not in z.namelist():
                raise InputRejected("Файл не является PPTX/POTX")
            for info in infos:
                name = info.filename
                if "\\" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or ":" in name:
                    raise InputRejected("Небезопасный путь внутри файла PowerPoint")
                if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    raise InputRejected("Ссылки и зашифрованные части не поддерживаются")
                if info.file_size > 60 * 1024 * 1024 or info.file_size / max(info.compress_size, 1) > 500:
                    raise InputRejected("Превышен лимит размера или сжатия части файла PowerPoint")
                if any(x in name.lower() for x in ["vbaproject", "activex", "oleobject"]):
                    raise InputRejected("Активное содержимое в PowerPoint запрещено")
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
            presentation_content_type(z)
    except BadZipFile as exc:
        raise InputRejected("Повреждённый файл PowerPoint") from exc
    return sorted(set(warnings))
