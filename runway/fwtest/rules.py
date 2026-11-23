"""Field rules: what a field's value must look like, by name.

A spec field lists its rules — ``{"date": "DDMMYYYY"}``, ``{"range": [0, 359]}``. Each rule is one
function ``(value, argument) -> None`` when the value is right, else the words for what was expected
("a date DDMMYYYY", "0 to 359"), which the flag shows beside what came. A new rule is one decorated
function here and a key in a spec — nothing else changes.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

RULES: dict[str, Callable[[str, object], str | None]] = {}


def rule(name: str):
    def deco(fn):
        RULES[name] = fn
        return fn
    return deco


def _num(v: str) -> float | None:
    try:
        return float(v)
    except ValueError:
        return None


@rule("equals")
def _equals(v, arg):
    return None if v == str(arg) else str(arg)


@rule("one_of")
def _one_of(v, arg):
    return None if v in [str(a) for a in arg] else "one of " + ", ".join(str(a) for a in arg)


@rule("pattern")
def _pattern(v, arg):
    regex, words = (arg["regex"], arg.get("says") or arg["regex"]) if isinstance(arg, dict) else (arg, arg)
    return None if re.fullmatch(regex, v) else words


@rule("int")
def _int(v, arg):
    return None if re.fullmatch(r"-?\d+", v) else "a whole number"


@rule("decimal")
def _decimal(v, arg):
    return None if re.fullmatch(r"-?\d+(\.\d+)?", v) else "a number"


@rule("range")
def _range(v, arg):
    lo, hi = arg
    x = _num(v)
    if x is None:
        return f"{lo} to {hi}"
    return None if lo <= x <= hi else f"{lo} to {hi}"


@rule("digits")
def _digits(v, arg):
    return None if re.fullmatch(rf"\d{{{int(arg)}}}", v) else f"{arg} digits"


@rule("date")
def _date(v, arg):
    fmt = str(arg or "DDMMYYYY")
    if fmt == "DDMMYYYY" and re.fullmatch(r"\d{8}", v):
        try:
            datetime(int(v[4:]), int(v[2:4]), int(v[:2]))
            return None
        except ValueError:
            pass
    return f"a date {fmt}"


@rule("time")
def _time(v, arg):
    fmt = str(arg or "hhmmss")
    if fmt == "hhmmss" and re.fullmatch(r"\d{6}", v):
        if int(v[:2]) < 24 and int(v[2:4]) < 60 and int(v[4:]) < 60:
            return None
    return f"a time {fmt}"


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(digits)):
        d = int(c)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


@rule("imei")
def _imei(v, arg):
    if not re.fullmatch(r"\d{15}", v):
        return "15 digits"
    return None if luhn_ok(v) else "15 digits with a valid check digit"


@rule("hex")
def _hex(v, arg):
    return None if re.fullmatch(r"[0-9A-Fa-f]+", v) else "hexadecimal"


@rule("bits")
def _bits(v, arg):
    if arg:
        return None if re.fullmatch(rf"[01]{{{int(arg)}}}", v) else f"{arg} digits of 0 or 1"
    return None if re.fullmatch(r"[01]+", v) else "digits of 0 or 1"


@rule("parenthesized")
def _paren(v, arg):
    return None if v.startswith("(") and v.endswith(")") else "a value in ( )"


def check(value: str, rules: dict, allow: list | None = None) -> str | None:
    """The first rule the value breaks, as what was expected — or None. Values in ``allow`` (the
    standard's 'x' for no network data, 99 for an unknown signal) pass whatever the rules say."""
    if allow and value in [str(a) for a in allow]:
        return None
    for name, arg in rules.items():
        fn = RULES.get(name)
        if fn is None:
            continue                       # "expect" and other keys the gate reads itself
        want = fn(value, arg)
        if want:
            return want
    return None
