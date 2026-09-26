# MTX decoder provenance

Source: user-supplied `font-extraction 3/vendor/mtx-decompressor`, received
2026-09-24. The supplied MANIFEST.json matched all 38 source files.
Original index.mjs SHA-256:
`12bfb0a420bea3b45a8c0ceac48fce0557b9f59313fb86d11d81cdf9978db4af`.
The supplied Mozilla Public License 2.0 is preserved as LICENSE.

Local modifications (also MPL-2.0): every explicit Uint8Array/Int16Array allocation now
checks a 16 MiB per-buffer and 96 MiB cumulative allocation budget. No font
data, permissions or names are changed. This is not a general process sandbox
or an OS-level total-RSS limit. The caller separately limits V8 heap and time.

Studio uses its own stdin/stdout adapter and retains its stricter EOT/fsType,
font-identity and glyph checks. The colleague's full pyapi pipeline was not
vendored: it labels regular slots as weight 400 even when the TTF is Medium.
The Google Fonts CSS fallback was adapted into Studio's existing fixed-host,
OFL-verified downloader; Fontsource and automatic Aptos downloads were not added.
