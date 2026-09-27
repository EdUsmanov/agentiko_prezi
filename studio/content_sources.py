"""Pure source-to-slide matching, independent of layout selection."""

import re


def normalized(text):
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def body_and_title_sources(slide, content):
    by_id = {f.id: f for f in content.facts}
    title_ids = [
        fid for fid in slide.fact_ids if normalized(by_id[fid].text) == normalized(slide.title)
    ]
    table = next((t for t in content.tables if t.id == slide.table_id), None)
    cells = {normalized(c) for row in ([table.headers] + table.rows if table else []) for c in row}
    return [
        by_id[fid]
        for fid in slide.fact_ids
        if fid not in title_ids and normalized(by_id[fid].text) not in cells
    ], title_ids
