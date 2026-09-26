"""Bind authored field typography to an already validated exact font asset."""


def field_style(pattern, role, index=0):
    field = next((f for f in pattern.fields if f['role'] == role and f['index'] == index), {})
    return field.get('style', {})


def styled_profile(profile, style, role):
    if not style or not style.get('family'):
        return profile, {}
    style = dict(style)
    requested = style['family']
    for flag, suffix in (('bold', 'Bold'), ('italic', 'Italic')):
        if style.get(flag) and suffix.casefold() not in requested.casefold():
            requested += ' ' + suffix
    style['requested_font'] = requested
    from .fonts import font_asset
    asset = font_asset(profile, requested)
    if asset is None:
        style['unresolved_font'] = True
        return profile, style
    updated = profile.model_copy()
    updated.font, updated.font_file = asset['requested'], asset['path']
    updated.font_roles = {**profile.font_roles, role: asset['id']}
    if role in ('body', 'table', 'chart') and style.get('size'):
        updated.body_size = style['size']
    return updated, style
