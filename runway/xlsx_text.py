"""Text an Excel cell can hold.

A device's damaged bytes (a control character inside a field) reach the examples, flags and raw
columns of the reports; openpyxl refuses such a character and the whole export failed with an
internal error. Every workbook Runway writes passes its values through ``cell`` / ``row``: each
illegal character becomes ``\\xNN`` — the evidence stays readable instead of vanishing.
Pure (no openpyxl import), so it serves every exporter.
"""
from __future__ import annotations

import re

# the characters XML 1.0 (and so a worksheet) cannot hold — openpyxl's ILLEGAL_CHARACTERS_RE
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MAX = 32767                     # the most characters an Excel cell holds


def cell(v):
    if not isinstance(v, str):
        return v
    v = _ILLEGAL.sub(lambda m: f"\\x{ord(m.group()):02x}", v)
    return v if len(v) <= MAX else v[:MAX - 1] + "…"


def row(values) -> list:
    return [cell(v) for v in values]
