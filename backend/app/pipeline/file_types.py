"""File type detection from content ("magic bytes"), never from the file name.

A renamed or mislabelled file is caught here instead of failing somewhere downstream.
"""

from dataclasses import dataclass

HEAD_BYTES = 1024  # PDF allows its signature anywhere in the first 1024 bytes


@dataclass(frozen=True, slots=True)
class FileType:
    mime_type: str
    extension: str


PDF = FileType("application/pdf", ".pdf")
PNG = FileType("image/png", ".png")
JPEG = FileType("image/jpeg", ".jpg")

SUPPORTED_DESCRIPTION = "PDF, PNG, JPEG"


def detect(head: bytes) -> FileType | None:
    """`head` = the first HEAD_BYTES of the file. None = unsupported type."""
    if b"%PDF-" in head[:HEAD_BYTES]:
        return PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    return None
