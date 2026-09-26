"""Adapters for the colleague's unmodified portable background and zone packages.

No optional VL client is started here: provider policy stays with ModelGateway.
Authored fields remain the editable contract; a free rectangle is evidence, not
permission to replace a multi-column layout with invented coordinates.
"""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path

from ._vendor.portable_background_extractor.bgextract import inspect_template_backgrounds, extract_background_pptx
from ._vendor.portable_background_extractor.bgextract.roles import protected_background_regions, apply_background_roles
from ._vendor.portable_text_zone_finder.textzone import analyze_image
from .powerpoint import open_presentation
from .security import validate_pptx


def extract_backgrounds(profile, source, directory):
    validate_pptx(Path(source))
    raw = Path(source).read_bytes()
    prs = open_presentation(BytesIO(raw))
    if not len(prs.slides):
        # POTX may contain only authored layouts. A temporary exemplar lets the
        # extractor inspect its inheritance; it is never added to the user file.
        prs.slides.add_slide(prs.slide_masters[0].slide_layouts[0])
        stream = BytesIO(); prs.save(stream); raw = stream.getvalue()
    model = inspect_template_backgrounds(raw)
    protect_field_surfaces(model, prs, profile.patterns)
    cleaned = extract_background_pptx(raw, model)
    folder = Path(directory) / "template-layers"; folder.mkdir(exist_ok=True)
    target = folder / "background-source.pptx"
    target.write_bytes(cleaned)
    validate_pptx(target)
    profile.background_source = str(target)
    report = Path(directory) / "background-model.json"
    report.write_text(json.dumps(model, ensure_ascii=False, indent=2))
    return model


def zone_metadata(pattern, profile, model):
    if pattern.source_slide:
        slide = model["slides"][pattern.source_slide - 1]
    else:
        prs = open_presentation(profile.background_source)
        layout = prs.slide_masters[pattern.master_index].slide_layouts[pattern.layout_index]
        parts = [str(layout.part.partname).lstrip("/"), str(layout.slide_master.part.partname).lstrip("/")]
        objects = [row for part in parts for row in model["parts"].get(part, [])]
        slide = {"objects": objects, "protectedRegions": protected_background_regions(
            objects, profile.width * 12700, profile.height * 12700)}
    return slide


def inspect_text_zone(image, pattern, profile, model, vl_cells=None):
    size = (1280, round(1280 * profile.height / profile.width))
    image = image.convert("RGB").resize(size)
    slide = zone_metadata(pattern, profile, model)
    result = analyze_image(image, slide, vl_cells=vl_cells)
    result["coordinate_space"] = {"width": size[0], "height": size[1], "unit": "pixels", "box": "left,top,right,bottom"}
    result["application"] = "advisory; authored editable fields retained"
    pattern.safe_text_zone = result
    return result


def clean_editable_source(prs, patterns):
    """Use the SAME peer classification for PPTAgent without flattening groups.

    The full extractor is used for rendered backgrounds. On the editing source,
    apply its decisions directly to native XML so group transforms and object
    identities remain stable. Authored contract fields are protected explicitly.
    """
    stream = BytesIO(); prs.save(stream)
    model = inspect_template_backgrounds(stream.getvalue())
    protect_field_surfaces(model, prs, patterns)
    protected = {}
    for pattern in patterns:
        surface = (prs.slides[pattern.source_slide - 1] if pattern.source_slide else
            prs.slide_masters[pattern.master_index].slide_layouts[pattern.layout_index])
        key = str(surface.part.partname).lstrip("/")
        protected.setdefault(key, set()).update(str(f["shape_id"]) for f in pattern.fields if f["role"]!="image")
    for part in prs.part.package.iter_parts():
        key = str(part.partname).lstrip("/")
        if key not in model["parts"] or not hasattr(part, "_element"):
            continue
        roles = deepcopy(model["parts"][key])
        for row in roles:
            if row["id"] in protected.get(key, set()):
                row["action"] = "keep"
                row.pop("replacementText", None)
        apply_background_roles(part._element, roles)
    return model


def protect_field_surfaces(model, prs, patterns):
    """Clear sample wording, but retain the authored field's fill and geometry.

    A white text card is both a content placeholder and a reusable surface.
    Dropping the whole object would put its black text directly on a dark brand
    background. Keep that native surface; never keep sample pictures/charts.
    """
    protected={}
    graphic_ids={}
    for pattern in patterns:
        surface=(prs.slides[pattern.source_slide-1] if pattern.source_slide else
                 prs.slide_masters[pattern.master_index].slide_layouts[pattern.layout_index])
        if pattern.source_slide and pattern.graphic_order_verified and len(pattern.body_zones)>=2:
            key=str(surface.part.partname).lstrip('/')
            if pattern.graphic_kind!='none':graphic_ids.setdefault(key,set()).update(map(str,pattern.graphic_shape_ids))
            # No generic 'keep all vectors' fallback: unverified shapes may encode old data.
        protected.setdefault(str(surface.part.partname).lstrip("/"),set()).update(
            str(f["shape_id"]) for f in pattern.fields if f["role"]!="image")
    for part in prs.part.package.iter_parts():
        key=str(part.partname).lstrip("/")
        if key not in model["parts"] or not hasattr(part,"_element"):
            continue
        ids=protected.setdefault(key,set())
        if not key.startswith("ppt/slides/"):
            for shape in part._element.iter("{http://schemas.openxmlformats.org/presentationml/2006/main}sp"):
                if shape.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}ph") is not None:
                    props=shape.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr")
                    if props is not None: ids.add(props.get("id"))
        for row in model["parts"][key]:
            # Only semantically verified compositions reuse vector paths,
            # nodes and connectors. Photos, charts, embedded data stay excluded.
            if row['id'] in graphic_ids.get(key,set()) and row['type'] in ('sp','cxnSp','grpSp'):
                row.update(action='clear-text' if row['type'] in ('sp','grpSp') else 'keep',
                    role='decoration',reason='verified_composition_vector_graphics')
                row.pop('replacementText',None)
            if row["id"] in ids and row["type"]=="sp":
                row.update(action="clear-text",role="background",reason="authored_editable_field_surface")
                row.pop("replacementText",None)
    by_id={(part,row["id"]):row for part,rows in model["parts"].items() for row in rows}
    for slide in model["slides"]:
        slide["objects"]=[{**row,**by_id.get((row["part"],row["id"]),{})} for row in slide["objects"]]
        slide["protectedRegions"]=protected_background_regions(slide["objects"],prs.slide_width,prs.slide_height)
    return model
