# Third-party notices

- `studio/_vendor/font_extraction`: complete pipeline from the user-supplied `font-extraction-kit (1).zip` (SHA-256 `846fad2abffcabdc5adf3ed1f4781b5c37d917ee88a196066f9642c85b898315`). The EOT/MTX decoder includes an MPL-2.0 component and its supplied `LICENSE`. Studio adds bounded decoder execution; details are in `PROVENANCE.md`. No user presentation or font binaries are bundled with this kit.

- `studio/_vendor/color_extraction`: colleague's user-supplied color-extraction-kit. No redistribution license was included. Local integration authorized by the user; obtain the author's explicit open-source license before public distribution. Adaptations are listed in its `PROVENANCE.md`.

- `studio/_vendor/deeppresenter`: selected DeepPresenter Agent/Design/types/constants from icip-cas/PPTAgent, tag `v1.1.38`, commit `2e68c095a86bdbb91635dc4d91dad4662aba163c`. MIT, copyright 2025 ICIP-CAS. License and exact adaptations are preserved in its `LICENSE` and `PROVENANCE.md`. The restricted fork does not bundle upstream Docker/MCP, Research or the classic eval executor.

- `vendor/opendesign/craft/typography.md`, `color.md`: OpenDesign, https://github.com/nexu-io/open-design, commit `f166c02636e60bc3e3a474fe79cf269aa7f053ea`. Apache-2.0, full license preserved in `vendor/opendesign/LICENSE`. These are craft references, not a bundled daemon. Files are copied without intentional changes.
- `fonts/Play-Regular.ttf`: Play font by its respective authors, distributed through Google Fonts, https://github.com/google/fonts/tree/main/ofl/play. SIL Open Font License, full notice in `fonts/OFL.txt`.
- `fonts/Montserrat-{Regular,Medium,Bold}.ttf`: unmodified static fonts from https://github.com/JulietaUla/Montserrat, commit `555facfb2a18c72c3c0380f0d9c0f060453a9058`. SIL Open Font License 1.1, preserved in `fonts/Montserrat-OFL.txt`.
- Runtime dependencies retain their upstream licenses and notices. Installed versions are pinned in `requirements.lock`. Distribute dependency notices with packaged binaries.
- The three organizer PPTX files are user-provided inputs, not licensed as project source. They are stored only in ignored runtime data; do not publish them without appropriate rights.
