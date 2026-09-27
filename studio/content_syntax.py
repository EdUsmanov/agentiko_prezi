"""Shared input syntax; no upload or content processing dependencies."""

import re

IMAGE_LINK = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]+)\)")
