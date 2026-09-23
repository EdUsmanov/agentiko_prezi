import re
from .models import ContentModel, Fact, TableData, Constraints
from .security import scan_text, InputRejected

def plain_inline(value):
    # Remove paired presentation markup, never punctuation within the fact itself.
    value = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m[1] or m[2], value)
    value = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1 — \2", value)
    return re.sub(r"`([^`]+)`", r"\1", value).strip()

def slide_heading(section):
    match = re.match(r"^(?:слайд|slide)\s+\d+\s*[.:)\-—–]?\s*(.*)$", section, re.I)
    return match[1].strip() if match else None

def parse_constraints(slides: int | None, audience: str, instructions: str):
    mode = "exact" if slides else "default"
    limit = slides or 10
    # Only the dedicated instruction field can change the output contract.
    match = re.search(r"(?:не\s+более|не\s+больше|до|максимум|at most|up to)\s+(\d+)\s*(?:слайд|slides?)", instructions, re.I)
    exact = re.search(r"(?:ровно|exactly)\s+(\d+)\s*(?:слайд|slides?)", instructions, re.I)
    if match:
        limit, mode = int(match[1]), "maximum"
    if exact:
        limit, mode = int(exact[1]), "exact"
    return Constraints(slides=limit, audience=audience, instructions=instructions, count_mode=mode)

def parse_content(text: str) -> ContentModel:
    if len(text) > 120_000:
        raise InputRejected("Текст превышает 120 000 символов")
    clean, quarantined = scan_text(text)
    lines = clean.splitlines()
    facts, tables, seen = [], [], set()
    title, section = "", ""
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if not raw or re.fullmatch(r"(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,}", raw):
            i += 1
            continue
        if raw.startswith("#") or slide_heading(plain_inline(raw)) is not None:
            heading = plain_inline(raw.lstrip("# "))
            title = title or heading
            section = heading
            i += 1
            continue
        if "|" in raw and i+1 < len(lines) and re.fullmatch(r"[\s|:\-]+", lines[i+1]) and "-" in lines[i+1]:
            headers = [c.strip() for c in raw.strip("|").split("|")]
            rows, j = [], i+2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if len(cells) != len(headers):
                    raise InputRejected("Количество ячеек таблицы не совпадает с заголовком")
                rows.append(cells)
                j += 1
            if not rows or len(headers) > 8 or len(rows) > 100:
                raise InputRejected("Таблица должна содержать 1–100 строк и до 8 колонок")
            tid = f"t{len(tables)+1}"
            tables.append(TableData(id=tid, headers=headers, rows=rows, section=section))
            facts.append(Fact(id=f"f{len(facts)+1}", text=f"{section or 'Данные'}: " + "; ".join(" — ".join(row) for row in rows), section=section, source=tid, line=i+1))
            i = j
            continue
        value = plain_inline(re.sub(r"^\s*[-*•]\s+", "", raw))
        # Split long paragraphs only at sentence boundaries, never fabricate facts.
        chunks = re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z])", value)
        for chunk in chunks:
            key = re.sub(r"\s+", " ", chunk).casefold()
            if key in seen:
                continue
            seen.add(key)
            facts.append(Fact(id=f"f{len(facts)+1}", text=chunk, section=section, line=i+1))
        title = title or value[:100]
        i += 1
    if not facts:
        raise InputRejected("После проверки нет текста для презентации. Добавьте содержательные материалы.")
    if len(facts) > 300:
        raise InputRejected("Больше 300 смысловых фрагментов. Сократите входной текст.")
    warnings = []
    if quarantined:
        warnings.append(f"Изолировано подозрительных строк: {len(quarantined)}. Они не переданы планировщику.")
    if len(facts) < 10:
        warnings.append("Мало исходного материала: автономный режим не дополняет его вымышленными фактами.")
    return ContentModel(title=title, facts=facts, tables=tables, warnings=warnings, quarantined=quarantined)

def numeric_column(table):
    for ci in range(1, len(table.headers)):
        values, suffixes = [], set()
        for row in table.rows:
            match = re.fullmatch(r"\s*([−\-+]?\d[\d\s]*(?:[.,]\d+)?)\s*(%|₽|руб\.?|млн|тыс\.?)?\s*", row[ci])
            if not match:
                break
            values.append(float(match[1].replace(" ", "").replace(",", ".").replace("−", "-")))
            suffixes.add(match[2] or "")
        if len(values) == len(table.rows) and len(suffixes) == 1 and len(values) <= 6 and all(v >= 0 for v in values) and max(values, default=0) > 0:
            return ci, values, next(iter(suffixes))
    return None
