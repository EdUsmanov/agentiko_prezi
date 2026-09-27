# Font extraction kit provenance

Source: user-supplied `font-extraction-kit (1).zip`, SHA-256 `846fad2abffcabdc5adf3ed1f4781b5c37d917ee88a196066f9642c85b898315`.

The bundled source and sample OOXML dataset were copied from that archive. Studio changes `decoder.py` and `decoder.mjs` to limit EOT/MTX input, output, process time, Node heap and inherited environment. It also reads embedded weight from the font binary rather than the PPTX slot and checks internal face identity when selecting assets. `MANIFEST.json` contains checksums of the resulting listed files. The integration adapter is `studio/font_extraction.py` and is not part of the supplied archive.

The included `vendor/mtx-decompressor/LICENSE` is MPL-2.0. No presentation or font binaries from user data are shipped.
