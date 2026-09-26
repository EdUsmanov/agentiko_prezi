from PIL import Image
from studio.artwork import constrain_body_zones
from studio.models import Box, Pattern


def pattern(zones):
    title = Box(x=20, y=5, w=300, h=30)
    return Pattern(id='native-test', role='statement', source_slide=1, source_layout='Authored',
        title_zone=title, body_zones=zones, text_zones=[title] + zones,
        safe_text_zone={'box': None, 'reason': 'vl_required_for_complex_surface'},
        fields=[{'role': 'title', 'index': 0, 'shape_id': 2, 'box': title.model_dump()},
                *[{'role': 'body', 'index': i, 'shape_id': 10+i,
                   'box': box.model_dump(), 'font_size': 24} for i, box in enumerate(zones)]])


def test_inherited_art_constrains_field_even_when_global_zone_is_unknown():
    # Pixels simulate a sanitized native render with bottom master decoration.
    image = Image.new('RGB', (800, 400), 'white')
    image.paste((30, 40, 50), (0, 300, 800, 400))
    p = pattern([Box(x=20, y=50, w=360, h=150)])
    original = p.body_zones[0].model_copy()
    changes = constrain_body_zones(p, image, scale=2)
    assert changes and p.body_zones[0].y+p.body_zones[0].h <= 150
    assert p.body_zones[0].x == original.x and p.body_zones[0].w == original.w
    assert p.fields[1]['box'] == p.body_zones[0].model_dump()
    assert p.text_zones[1] == p.body_zones[0]
    assert p.safe_text_zone['box'] is None
    assert p.safe_text_zone['body_adjustments'] == changes
    assert changes[0]['before'] == original.model_dump()


def test_separate_cards_identity_fonts_and_undecorated_field_preserved():
    image = Image.new('RGB', (400, 200), '#FFF9EC')
    image.paste((30, 40, 50), (20, 160, 180, 200))
    left = Box(x=20, y=50, w=160, h=150)
    right = Box(x=220, y=50, w=160, h=150)
    p = pattern([left, right])
    title = p.title_zone.model_copy()
    changes = constrain_body_zones(p, image, scale=1)
    assert len(changes) == 1 and changes[0]['index'] == 0
    assert p.body_zones[0].y+p.body_zones[0].h <= 160
    assert p.body_zones[1] == right and p.title_zone == title
    assert [f['shape_id'] for f in p.fields] == [2, 10, 11]
    assert [f['font_size'] for f in p.fields[1:]] == [24, 24]
    assert p.text_zones == [title] + p.body_zones


def test_plain_background_is_not_a_reason_to_relayout():
    p = pattern([Box(x=20, y=50, w=360, h=150)])
    before = p.body_zones[0].model_copy()
    assert constrain_body_zones(p, Image.new('RGB', (400, 200), '#FFF9EC'), scale=1) == []
    assert p.body_zones == [before]


def test_background_compilation_applies_constraints_and_persists_diagnostics(prepared, tmp_path, monkeypatch):
    import json
    import studio.artwork as artwork
    from studio.native_template import compile_backgrounds
    _, store, package = prepared
    calls = []
    original = artwork.constrain_body_zones
    def inspect(pattern, image, scale=1.5):
        calls.append(pattern.id)
        return original(pattern, image, scale)
    monkeypatch.setattr(artwork, 'constrain_body_zones', inspect)
    compile_backgrounds(package.template, store.directory(package.id)/'input.pptx', tmp_path)
    assert calls == [p.id for p in package.template.patterns]
    report = json.loads((tmp_path/'text-zones.json').read_text())
    assert all('body_adjustments' in report[key] for key in calls)
    for p in package.template.patterns:
        assert all(f['box'] == p.body_zones[f['index']].model_dump()
                   for f in p.fields if f['role'] == 'body')
