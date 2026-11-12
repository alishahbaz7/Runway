"""``x.y.z`` versions, compared as numbers (1.1.9 < 1.1.24). A ``+build`` suffix is ignored; a
leading ``v`` is accepted. Anything else is not a version."""

from __future__ import annotations

import re
from functools import total_ordering
from typing import Any

_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:\+[0-9A-Za-z.-]+)?$")


@total_ordering
class SemVer:
    __slots__ = ("parts",)

    def __init__(self, major: int, minor: int, patch: int):
        self.parts = (major, minor, patch)

    @classmethod
    def parse(cls, text: Any) -> "SemVer":
        v = cls.try_parse(text)
        if v is None:
            raise ValueError(f"not a version: {text!r}")
        return v

    @classmethod
    def try_parse(cls, text: Any) -> "SemVer | None":
        if not isinstance(text, str):            # a version written as a number is not a version
            return None
        m = _PATTERN.match(text.strip())
        return cls(*(int(g) for g in m.groups())) if m else None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SemVer) and self.parts == other.parts

    def __lt__(self, other: "SemVer") -> bool:
        return self.parts < other.parts

    def __hash__(self) -> int:
        return hash(self.parts)

    def __str__(self) -> str:
        return ".".join(str(p) for p in self.parts)

    def __repr__(self) -> str:
        return f"SemVer({self})"
