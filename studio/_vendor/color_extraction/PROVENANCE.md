# Colleague's color-extraction-kit

Source: user-supplied local `color-extraction-kit`, received 2026-09-24.
Original kit `verify.py` passed its manifest and synthetic table test before integration.

Vendored only the `pyapi` package, namespaced as `studio._vendor.color_extraction`.
Adaptations: safe defusedxml parsing; reject negative theme-style indexes.
The service adapter additionally validates the full PPTX/POTX archive before use.
There are no network, OCR, LLM or executable-document operations in this extractor.

The supplied kit contains no LICENSE. Permission to integrate locally was supplied
by the user; an explicit redistribution license from its author is still required
before publishing this code as part of the open-source competition submission.
Do not assume that the project's license grants rights to this third-party code.
