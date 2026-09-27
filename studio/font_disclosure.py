"""Disclose explicit font and symbol replacements without local paths."""

from .fonts import font_runs, role_font, resolve_font, font_key, SYMBOLS


def substitutions(text, family, path):
    symbols = {
        char
        for fragment, face in font_runs(text, path)
        if face != str(path)
        for char in fragment
        if char in SYMBOLS
    }
    return [
        dict(
            symbol=char,
            codepoint=f"U+{ord(char):04X}",
            template_font=family,
            fallback_font="Montserrat",
            scope="symbol_only",
        )
        for char in sorted(symbols)
    ]


def unique(records):
    seen = set()
    result = []
    for record in records:
        key = (
            record.get("scope", "symbol_only"),
            record.get("symbol"),
            record["template_font"],
            record["fallback_font"],
        )
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def preparation_substitutions(package):
    texts = {
        "title": "\n".join(
            [package.content.title]
            + [
                s.title
                for v in (package.prepared_plans.variants if package.prepared_plans else [])
                for s in v.slides
            ]
        ),
        "body": "\n".join(f.text for f in package.content.facts),
        "table": "\n".join(
            c for t in package.content.tables for row in [t.headers] + t.rows for c in row
        ),
    }
    return unique(
        [
            *package.template.font_replacements,
            *(
                record
                for role, text in texts.items()
                for record in substitutions(text, *role_font(package.template, role))
            ),
        ]
    )


def exported_substitutions(prs, profile):
    """Read explicit fallback runs/bullets in the actual exported presentation."""
    from .template_geometry import walk_shapes

    records = []
    for slide in prs.slides:
        for shape, _ in walk_shapes(slide.shapes):
            frames = (
                [shape.text_frame]
                if shape.has_text_frame
                else [c.text_frame for row in shape.table.rows for c in row.cells]
                if shape.has_table
                else []
            )
            for frame in frames:
                for paragraph in frame.paragraphs:
                    primary = paragraph.font.name or next(
                        (
                            r.font.name
                            for r in paragraph.runs
                            if r.font.name and font_key(r.font.name) != "montserrat"
                        ),
                        profile.font,
                    )
                    if font_key(primary) == "montserrat":
                        continue  # Montserrat is the authored font here, not a substitution.
                    path = next(
                        (a["path"] for a in profile.font_assets if a["requested"] == primary), None
                    )
                    path = path or resolve_font(primary) or role_font(profile)[1]
                    used = "".join(
                        r.text
                        for r in paragraph.runs
                        if font_key(r.font.name or "") == "montserrat"
                    )
                    ppr = paragraph._p.pPr
                    if ppr is not None:
                        ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
                        bullet, face = ppr.find(ns + "buChar"), ppr.find(ns + "buFont")
                        if (
                            bullet is not None
                            and face is not None
                            and font_key(face.get("typeface", "")) == "montserrat"
                        ):
                            used += bullet.get("char", "")
                    if used:
                        records.extend(substitutions(used, primary, path))
    used_fonts = {
        font_key(run.font.name or paragraph.font.name or "")
        for slide in prs.slides
        for shape, _ in walk_shapes(slide.shapes)
        for frame in (
            [shape.text_frame]
            if shape.has_text_frame
            else [cell.text_frame for row in shape.table.rows for cell in row.cells]
            if shape.has_table
            else []
        )
        for paragraph in frame.paragraphs
        for run in paragraph.runs
        if any(char not in SYMBOLS and not char.isspace() for char in run.text)
    }
    for replacement in profile.font_replacements:
        asset = next(
            (a for a in profile.font_assets if a["requested"] == replacement["fallback_font"]),
            None,
        )
        if asset and font_key(asset["family"]) in used_fonts:
            records.append(replacement)
    return unique(records)


def warnings(records, planned=False):
    verb = "будет использован" if planned else "использован"
    result = []
    for r in unique(records):
        if r.get("scope") == "font":
            detail = " Начертание также изменено." if r.get("style_changed") else ""
            result.append(
                f"Вместо шрифта «{r['template_font']}» автоматически {verb} "
                f"«{r['fallback_font']}»: в исходном файле нет символов нового текста. "
                "Оформление и переносы строк могут отличаться от шаблона." + detail
            )
        else:
            result.append(
                f"Для символа {r['symbol']} ({r['codepoint']}) {verb} {r['fallback_font']}: "
                f"в шрифте {r['template_font']} этого символа нет. "
                "Подстановка касается только символа, не остального текста. "
                "Это исключение из правила «только шрифты шаблона»."
            )
    return result
