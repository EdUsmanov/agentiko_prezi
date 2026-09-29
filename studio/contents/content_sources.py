"""Pure source-to-slide matching, independent of layout selection."""

import re


def normalized(text):
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def body_and_title_sources(slide, content, labels=None):
    labels = labels or {}
    by_id = {f.id: f for f in content.facts}
    title_ids = [
        fid
        for fid in slide.fact_ids
        if normalized(by_id[fid].text) == normalized(slide.title)
        and (
            not labels.get(fid)
            or " " + normalized(labels[fid]) + " " in " " + normalized(slide.title) + " "
        )
    ]
    table = next((t for t in content.tables if t.id == slide.table_id), None)
    cells = {normalized(c) for row in ([table.headers] + table.rows if table else []) for c in row}
    return [
        by_id[fid]
        for fid in slide.fact_ids
        if fid not in title_ids and (labels.get(fid) or normalized(by_id[fid].text) not in cells)
    ], title_ids


def package_sources(slide, package):
    """A title or data cell must not erase a separately stored editorial owner."""
    labels = {
        row["fact_id"]: row.get("group", "").strip()
        for row in getattr(package, "analysis", {}).get("editorial", {}).get("provenance", [])
    }
    return body_and_title_sources(slide, package.content, labels)
