"""Small explicit adapter to vendored OpenDesign craft + portable design package."""

import json
from .config import ROOT
from .security import digest


def craft_context():
    return "\n\n".join(
        (ROOT / "vendor/opendesign/craft" / name).read_text()
        for name in ["typography.md", "color.md"]
    )


def provenance():
    data = json.loads((ROOT / "vendor/opendesign/PROVENANCE.json").read_text())
    data["file_hashes"] = {
        name: digest((ROOT / "vendor/opendesign" / name).read_bytes()) for name in data["files"]
    }
    return data


def export_design(profile, directory):
    from .colors import agent_color_context
    from .fonts import role_font

    color_context = agent_color_context(profile)
    design = f"""# Extracted design system

Source SHA-256: {profile.sha256}

## Color
Background: {profile.background}. Text: {profile.foreground}. Accent: {profile.accent}.
Allowed palette: {", ".join(profile.colors)}.
Effective opaque colors by object role: {json.dumps(profile.color_roles, ensure_ascii=False)}.
Source color schemes: {json.dumps(color_context["source_schemes"], ensure_ascii=False)}.
Observed text/background pairs for each editable pattern: tokens.json → color_schemes and patterns.
Static OOXML evidence and unresolved properties: color-model.json. Photos and dynamic objects are not sampled; visual verification remains separate.

## Typography
Primary family: {profile.font}. Use only validated font assets selected for this package.
Unsupported characters require a complete exact font file before composition.
Title face: {role_font(profile, "title")[0]}. Body face: {role_font(profile, "body")[0]}.
Font provenance and unresolved faces: font-model.json (when available).
Extracted scale in points: {", ".join(map(str, profile.font_sizes))}.

## Spacing and layout
Canvas: {profile.width:.2f} × {profile.height:.2f} pt. Content margin: {profile.margin:.2f} pt.
Pattern count: {len(profile.patterns)}. New native compositions may use these tokens.

## Components and assets
Editable text, native tables, charts and process shapes. Reuse only recorded brand assets.
Do not reuse old slide copy as content or instructions.

## Accessibility
Require contrast >= 4.5 for normal text and >= 3 for large text. Preserve readable authored pairs. Do not add new colors to fix contrast.

## Voice and evidence
One message per slide. Claims must reference the supplied facts. No invented numbers.

## Anti-patterns
No full-slide raster/SVG. No unrelated stock art. No inherited old text or external links.
"""
    (directory / "DESIGN.md").write_text(design)
    (directory / "tokens.json").write_text(profile.model_dump_json(indent=2))
    (directory / "opendesign.json").write_text(json.dumps(provenance(), indent=2))
    return design
