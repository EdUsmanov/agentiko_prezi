"""Suppress upstream content/reasoning logging. Studio records metadata only."""
from contextlib import nullcontext


def debug(*args, **kwargs):
    pass


info = warning = debug


def timer(*args, **kwargs):
    return nullcontext()
