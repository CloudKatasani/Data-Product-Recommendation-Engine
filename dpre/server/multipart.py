"""A small multipart/form-data parser (stdlib only).

Manual mode accepts file uploads from the browser, and the engine ships without
third-party dependencies, so the parser lives here rather than in a framework.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_DISPOSITION = re.compile(rb'name="([^"]*)"(?:;\s*filename="([^"]*)")?', re.IGNORECASE)


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
            return value.encode("utf-8")
    return None


def parse(body: bytes, content_type: str) -> list[Part]:
    boundary = boundary_of(content_type)
    if not boundary:
        return []
    delimiter = b"--" + boundary
    parts: list[Part] = []
    for raw in body.split(delimiter):
        raw = raw.strip(b"\r\n")
        if not raw or raw == b"--":
            continue
        header_blob, _, content = raw.partition(b"\r\n\r\n")
        if not _:
            continue
        name, filename, ctype = "", "", ""
        for line in header_blob.split(b"\r\n"):
            lowered = line.lower()
            if lowered.startswith(b"content-disposition:"):
                match = _DISPOSITION.search(line)
                if match:
                    name = match.group(1).decode("utf-8", errors="replace")
                    filename = (match.group(2) or b"").decode("utf-8", errors="replace")
            elif lowered.startswith(b"content-type:"):
                ctype = line.split(b":", 1)[1].strip().decode("utf-8", errors="replace")
        parts.append(Part(name=name, filename=filename,
                          content=content.rstrip(b"\r\n"), content_type=ctype))
    return parts
