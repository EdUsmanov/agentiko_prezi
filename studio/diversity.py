"""Visible geometry diversity, independent of model compliance and layout labels."""
import hashlib
import json
from collections import Counter
from .audit import audit_scenes, repair_scenes
from .models import Finding

def geometry_signature(scenes):
    # Ignore titles, text, filenames, pattern IDs and metadata: these cannot fake diversity.
    value = [[{"kind": e.kind, "box": {k: round(v, 2) for k,v in e.box.model_dump().items()},
               "size": round(e.size, 2)} for e in s.elements
              if e.role not in ("title", "footer", "brand", "template_background")
              and e.kind in ("text", "table", "chart")] for s in scenes]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def error_keys(scenes, package):
    return Counter((f.code, f.slide, f.element) for f in audit_scenes(scenes, package) if f.severity == "error")

def unused_body_regions(scene, package):
    """Count unoccupied authored content slots, not intentional cover whitespace."""
    pattern=next((p for p in package.template.patterns if p.id==scene.pattern_id),None)
    if not pattern or scene.purpose in ('cover','divider') or pattern.role in ('cover','divider'):
        return 0
    content=[e for e in scene.elements if (e.source_ids or e.image_id)
             and e.role not in ('title','footer','brand','template_background')]
    def occupied(zone):
        for element in content:
            box=element.box
            overlap=max(0,min(zone.x+zone.w,box.x+box.w)-max(zone.x,box.x))*max(
                0,min(zone.y+zone.h,box.y+box.h)-max(zone.y,box.y))
            if min(zone.w*zone.h,box.w*box.h)>0 and overlap>=.5*min(zone.w*zone.h,box.w*box.h):
                return True
        return False
    zones=list(pattern.body_zones)
    # A large source text field removed as 'unused' can leave a conspicuous
    # panel (e.g. an Education code sample). Do not select it for variety alone.
    canvas=getattr(package.template,'width',0)*getattr(package.template,'height',0)
    if canvas>0:
        from .models import Box
        for field in pattern.fields:
            if field.get('role')=='unused' and field.get('box'):
                zone=Box.model_validate(field['box'])
                if zone.w*zone.h>=canvas*.08:
                    zones.append(zone)
    return sum(not occupied(zone) for zone in zones)


def preserves_quality(before, after, package):
    from .quality import candidate_regressions
    return not candidate_regressions(before, after, package, audit=audit_scenes)


def ensure_diversity(decks, package):
    """Resolve collisions within existing safe zones, without deleting content.

    A pathological template may admit no safe alternative. Keep the valid plan and
    export with a quality finding in that case, never reject/fabricate a model plan.
    """
    from .quality import meaningful_diversity
    accepted = {}
    def distinct(candidate):
        return all(meaningful_diversity({'before':value,'after':candidate},package.template)['verified']
                   for value in accepted.values())
    seen = set(); changes = []; findings = []
    for key, scenes in decks.items():
        signature = geometry_signature(scenes)
        if signature in seen or not distinct(scenes):
            baseline_errors = error_keys(scenes, package)
            resolved = False
            layout_trial = [s.model_copy(deep=True) for s in scenes]
            layout_changes = []
            # Prefer an actually different authored layout before resizing
            # content inside a single layout. Identical model choices are safe.
            from .composer import compose
            from .models import SlidePlan
            tables={t.id for t in package.content.tables}
            facts={f.id:f for f in package.content.facts}
            for i,scene in enumerate(scenes):
                if scene.layout=='divider' or any(e.image_id or e.series_values for e in scene.elements):
                    continue
                table_id=next((facts[f].source for f in scene.source_ids if facts[f].source in tables),None)
                original_pattern=next((p for p in package.template.patterns if p.id==scene.pattern_id),None)
                if original_pattern and original_pattern.role=='cover':
                    continue
                for pattern in package.template.patterns:
                    if not pattern.title_zone or not pattern.body_zones or pattern.id==scene.pattern_id or pattern.role=='cover':
                        continue
                    if original_pattern and pattern.master_index!=original_pattern.master_index:
                        continue
                    candidate=[s.model_copy(deep=True) for s in layout_trial]
                    plan=SlidePlan(title=scene.title,fact_ids=scene.source_ids,layout=scene.layout,table_id=table_id,pattern_id=pattern.id,purpose=scene.purpose)
                    try:
                        candidate[i]=compose(plan,package,i,key)
                    except ValueError:
                        continue
                    from .table_style import apply_table_style
                    for element in candidate[i].elements:
                        if element.kind=='table':
                            apply_table_style(element,pattern)
                    title_floor=min(16,min((e.size for e in scene.elements if e.role=='title'),default=16))
                    if any(e.role=='title' and e.size<title_floor for e in candidate[i].elements):
                        continue
                    repair_scenes(candidate,package)
                    signature_new=geometry_signature(candidate)
                    slide_changed=meaningful_diversity({'before':[scenes[i]],'after':[candidate[i]]},package.template)['verified']
                    if (slide_changed and error_keys(candidate,package)<=baseline_errors
                            and preserves_quality(scenes,candidate,package)):
                        # Accumulate safe changes across content slides; one different
                        # table cannot satisfy the whole-deck diversity contract.
                        layout_trial=candidate
                        layout_changes.append({'slide':i+1,'pattern_id':pattern.id})
                        if signature_new not in seen and distinct(candidate):
                            decks[key]=candidate;signature=signature_new;resolved=True
                            changes.append({'variant':key,'strategy':'alternate_native_layouts','slides':layout_changes[:]})
                        break
                if resolved:
                    break
            # Large, visible changes, not 1-pixel offsets. All boxes stay inside
            # their original template zones; titles and artwork remain untouched.
            for ratio, anchor in ((.84, 1), (.84, .5), (.72, 0), (.72, 1)):
                if resolved:
                    break
                candidate = [s.model_copy(deep=True) for s in scenes]
                for scene in candidate:
                    for e in scene.elements:
                        if e.source_ids and e.kind in ("text", "table", "chart"):
                            width = e.box.w * ratio
                            e.box.x += (e.box.w-width)*anchor
                            e.box.w = width
                repair_scenes(candidate, package)
                candidate_signature = geometry_signature(candidate)
                if (candidate_signature not in seen and distinct(candidate) and error_keys(candidate, package) <= baseline_errors
                        and preserves_quality(scenes,candidate,package)):
                    decks[key] = candidate
                    signature = candidate_signature
                    changes.append({"variant": key, "strategy": "in_zone_reflow", "width_ratio": ratio, "anchor": anchor})
                    resolved = True
                    break
            if not resolved:
                # A dense slide must not veto safe, visible changes on all other
                # slides. Keep its content unchanged and reflow only safe slides.
                for ratio,anchor in ((.84,1),(.84,.5),(.72,0),(.72,1)):
                    candidate=[s.model_copy(deep=True) for s in scenes]
                    changed=[]
                    for i,scene in enumerate(scenes):
                        trial=scene.model_copy(deep=True)
                        for e in trial.elements:
                            if e.source_ids and e.kind in ('text','table','chart'):
                                if e.role=='title':
                                    continue
                                width=e.box.w*ratio
                                e.box.x+=(e.box.w-width)*anchor;e.box.w=width
                        repair_scenes([trial],package)
                        if (error_keys([trial],package)<=error_keys([scene],package)
                                and preserves_quality([scene],[trial],package)):
                            candidate[i]=trial;changed.append(i+1)
                    candidate_signature=geometry_signature(candidate)
                    if (changed and candidate_signature not in seen and distinct(candidate) and error_keys(candidate,package)<=baseline_errors
                            and preserves_quality(scenes,candidate,package)):
                        decks[key]=candidate;signature=candidate_signature;resolved=True
                        changes.append({'variant':key,'strategy':'safe_slide_reflow','width_ratio':ratio,'anchor':anchor,'slides':changed})
                        break
            if not resolved:
                findings.append(Finding(code="composition_diversity", severity="warning",
                    message=f"{key}: безопасной отличающейся композиции не найдено. Содержание сохранено; требуется проверка шаблона."))
        seen.add(signature)
        accepted[key] = decks[key]
    return {"policy": "meaningful-geometry-v2", "distinct": len(seen), "expected": len(decks),
        "verified": len(seen) == len(decks) and meaningful_diversity(decks,package.template)["verified"], "adjustments": changes,
        "signatures": {key: geometry_signature(value) for key,value in decks.items()},
        "findings": [f.model_dump() for f in findings]}
