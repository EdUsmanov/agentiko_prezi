"""Deterministic limits for untrusted OOXML/ZIP input."""

import zipfile
from pathlib import PurePosixPath

MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 200


def validate_archive(package: zipfile.ZipFile) -> None:
    members = package.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValueError("Archive contains too many files")
    total = 0
    for member in members:
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Archive contains an unsafe path")
        if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError("Archive member is too large")
        total += member.file_size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("Archive expands beyond the configured limit")
        if (
            member.file_size > 1024 * 1024
            and member.compress_size > 0
            and member.file_size / member.compress_size > MAX_ARCHIVE_COMPRESSION_RATIO
        ):
            raise ValueError("Archive compression ratio is unsafe")

