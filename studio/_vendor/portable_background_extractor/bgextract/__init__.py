"""Portable OOXML background extraction for PPTX and POTX files."""

from .model import inspect_template_backgrounds
from .package import extract_background_pptx

__all__ = ["inspect_template_backgrounds", "extract_background_pptx"]

