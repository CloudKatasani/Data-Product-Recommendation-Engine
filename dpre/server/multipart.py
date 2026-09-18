"""A small multipart/form-data parser (stdlib only).

Manual mode accepts file uploads from the browser, and the engine ships without
third-party dependencies, so the parser lives here rather than in a framework.
Because it reads attacker-controlled bytes it is bounded rather than trusting
(R-30): the body, the number of parts and the size of any one part are capped,
and a body whose boundary is missing or malformed yields no parts instead of a
traceback. ``docs/security.md`` states the policy for this vendored parser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_DISPOSITION = re.compile(rb'name="([^"]*)"(?:;\s*filename="([^"]*)")?', re.IGNORECASE)

#: Ceilings that apply even when the caller passes none. They are generous
#: enough for a real Collibra export and small enough that a single request
#: cannot exhaust a review server's memory.
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
MAX_PARTS = 64
MAX_HEADER_BYTES = 16 * 1024


class MultipartError(ValueError):
    """Raised when a body is not parseable within the configured bounds."""


@dataclass
class Part:
    name: str
    filename: str
    content: bytes
    content_type: str = ""

    @property
    def is_file(self) -> bool:
        return bool(self.filename)

    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def boundary_of(content_type: str) -> bytes | None:
    for chunk in (content_type or "").split(";"):
        chunk = chunk.strip()
        if chunk.lower().startswith("boundary="):
            value = chunk.split("=", 1)[1].strip().strip('"')
            if not value or len(value) > 200:
                return None
            return value.encode("utf-8", errors="ignore")
    return None


def parse(body: bytes, content_type: str, *, max_bytes: int = DEFAULT_MAX_BYTES,
          max_parts: int = MAX_PARTS) -> list[Part]:
    """Split a multipart body into parts, refusing anything over the bounds.

    The caller has already bounded ``Content-Length``; this second check exists
    because the parser is also reachable from tests and from any future
    non-HTTP caller, and a vendored parser should not rely on its caller.
    """
    if len(body) > max_bytes:
        raise MultipartError(
            f"multipart body is {len(body)} bytes; the limit is {max_bytes}")
    boundary = boundary_of(content_type)
    if not boundary:
        return []
    delimiter = b"--" + boundary
    parts: list[Part] = []
    for raw in body.split(delimiter):
        raw = raw.strip(b"\r\n")
        if not raw or raw == b"--":
            continue
        if len(parts) >= max_parts:
            raise MultipartError(f"multipart body carries more than {max_parts} parts")
        header_blob, separator, content = raw.partition(b"\r\n\r\n")
        if not separator or len(header_blob) > MAX_HEADER_BYTES:
            continue
        name, filename, ctype = "", "", ""
        for line in header_blob.split(b"\r\n"):
            lowered = line.lower()
            if lowered.startswith(b"content-disposition:"):
                match = _DISPOSITION.search(line)
                if match:
                    name = match.group(1).decode("utf-8", errors="replace")[:256]
                    filename = (match.group(2) or b"").decode("utf-8", errors="replace")[:256]
            elif lowered.startswith(b"content-type:"):
                ctype = line.split(b":", 1)[1].strip().decode("utf-8", errors="replace")[:128]
        parts.append(Part(name=name, filename=filename,
                          content=content.rstrip(b"\r\n"), content_type=ctype))
    return parts
