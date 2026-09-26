# Portable template tools — provenance and host integration

Source: colleague-provided folders in /Users/edward/Downloads, imported 2026-09-25.
All Python modules, CLI entry points, selftests and READMEs retain their original
content and behavior. Import added one trailing newline to each file; therefore
the byte hashes below describe the ORIGINAL files, not the imported copies.
All 25 imported files were compared against the originals with that sole
trailing-newline difference accounted for.
AGENTS.md is not part of the runtime; document instructions do not override user instructions.

## Compatibility

The extractor declares Pillow >=12,<13; the finder declares Pillow >=10,<12.
The host uses Pillow 12.3.0 and NumPy 2.4.6. Both original selftests passed
(extractor: 10 unittest checks; finder: its full selftest including CLI).
Original requirements files are retained as provenance, NOT installed together.
Host dependencies are declared in the application's pyproject.toml / requirements.lock.

## Adapter contract

studio/portable_templates.py is the ONLY host adapter. The original packages
remain unchanged. Cleanup uses the peer's background classifier and extractor.
Authored text-field surfaces (fills/geometry) and inherited placeholder providers
are retained while sample wording is cleared. PPTAgent editing keeps native
group transforms and IDs; it applies the same classification directly to OOXML,
rather than substituting flattened pixels or a different generation engine.

The zone finder runs locally on rendered backgrounds. Images and protected
regions use the same 1280px coordinate space. A null box is preserved as an
honest abstention. A free rectangle is diagnostic evidence, not permission to
destroy an authored multi-column layout or invent new editable fields.
Its optional standalone VL clients are NOT called by the application.
No additional model/provider was silently enabled.

Reports: background-model.json, text-zones.json and per-pattern safe_text_zone.
Original input files remain unchanged.

## Original file hashes (SHA-256)


- portable_background_extractor/README.md: e693196b959420df92a0c35d7bd6fe5c4096cbf4e05a2e80193b31d9d77e553a
- portable_background_extractor/bgextract/__init__.py: ef31180104927646ac55e3be947ef28530bb3000f351b7ae832e1e23be2742e7
- portable_background_extractor/bgextract/archive_safety.py: 71229b57a1eadfe506d03c160d8e70a6329862f55cb127e882b21fbbe4c6b359
- portable_background_extractor/bgextract/cleanup.py: 3caff71710b5b25419b0e90d8fae3c15a565f8f975c89f71828237959d1b40f8
- portable_background_extractor/bgextract/context.py: 14e1cce6f265ff16c98b014d0098d0cc7d8ea80af34ebc42cc1ffe2f7e6f9a4c
- portable_background_extractor/bgextract/features.py: 044a2b1f0064a628fc945d3ea05910cf4736780a2879a18f7a6dd7a614a1b510
- portable_background_extractor/bgextract/layout.py: 938405fef94be14f5cd2610e560e1a7491606f9b7bc878c91f0f54bd540500b6
- portable_background_extractor/bgextract/model.py: b76561fea70d47ef3a5fdaf8960e8f40a92851e3207cfc8a21580819a7cf6742
- portable_background_extractor/bgextract/package.py: a8cd0ae70099b7da3e0293cfa9ddf7b63e5a6537a7b0d7dcb0b85372f57f2d2a
- portable_background_extractor/bgextract/raster_cleanup.py: 82885362925cda1e47262ab23cbe0f986c0ca482654d5eff62d674e030dacb85
- portable_background_extractor/bgextract/raster_regions.py: 5d61a09df349639917dff98ecd4da67e15aa2e037a514020239afa82dd2abb9d
- portable_background_extractor/bgextract/resolution.py: 6e3d546c26b7fdae8afa9dd2f58f92aa77e71d45726960ea928f629aec45af7d
- portable_background_extractor/bgextract/roles.py: f673c27881a131fe0c64fb776410eb85b5a9c310d4dac8910260e1380be0f4a2
- portable_background_extractor/bgextract/subject_fragments.py: e29dc5337485c129656b8fbc821dd4e2b9974afac2c2f9cb322ea0b3dc5c6652
- portable_background_extractor/bgextract/vl_regions.py: a0d52cfa6f6f9e4f795e8efc88144296fe90ae942043ef3ed3c2f5449608ef12
- portable_background_extractor/extract_backgrounds.py: 1959088c8a8154ba10dc6805e9cc4d670c8f9605e63c54561b6cb2bf41966c2c
- portable_background_extractor/requirements.txt: ce70627d95a7879c8c0d6ebde1c8b1aff9d7ad9636f3aca63998d9d5660a23f7
- portable_background_extractor/selftest.py: 6edc1cfdc025546135770cefeedb4f850184c94273197c165b6b59c727d2f8a9
- portable_text_zone_finder/README.md: a9bc9a468941cf9fb3e886a99c9f49952d788a1ac76b2ff590ea8dfde52fae44
- portable_text_zone_finder/find_text_zones.py: af5489bf6aba7a5ebe873339d77a12155091fdd85def6292ba859de1df44fc50
- portable_text_zone_finder/requirements.txt: 670a43791604a1d0d16ea2393a3daa7f21dcbe3cf531d4d0cf55a23cda97f939
- portable_text_zone_finder/selftest.py: d224e41d660cbf3cd42902460186a57cd1ac4bcb4d269bcc01e828925490b70f
- portable_text_zone_finder/textzone/__init__.py: ebc32002afca35168540ad9f58f6d35fe927c2b377bae113ff4464cfccdcff82
- portable_text_zone_finder/textzone/core.py: 825faff37746473991c63b0a5a78e580a76e36315374583e1cac55cc6eceb733
- portable_text_zone_finder/textzone/vl.py: 8d6b387fe0adb81faebb142f6715dfa292092c16721207b180a83054f3aa9f0c
