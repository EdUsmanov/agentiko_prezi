"""Portable OOXML background extraction for PPTX and POTX files."""

from studio._vendor.portable_background_extractor.bgextract.model import inspect_template_backgrounds
from studio._vendor.portable_background_extractor.bgextract.package import extract_background_pptx

__all__ = ["inspect_template_backgrounds", "extract_background_pptx"]

