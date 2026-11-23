"""A firmware test spec: the device's sheet as data (``specs/<name>.json``).

    packets      header → {label, fields (the count, or null when the sheet gives none),
                 field_list: [{index, name, label, rules, allow, level}]}; index -1 is the last field
    checksum     {"algorithm": "xor"} — the XOR of every byte from '$' up to '*', two hex digits
    terminator   what ends every packet ("\\r\\n")
    expect       the values a run may pin (IMEI, number plate, firmware); else learned from the login
    status_by_alert, alert_ids, ignition_by_type   the sequence checks' tables
    alert_ids_interim   packet type → {alert ID: why} — an ID a packet uses for now (another event's):
                 a warning, not a failure, until the packet gets its own ID (then a row of alert_ids)
    scenarios    [{key, label, match: {header, packet_type, packet_status, alert_id}}]
    settings     live_max_delay_s, future_tolerance_s
    levels       check → "warn" / "fail": how much that check weighs for this device, over the gate's
                 default (CHECKS). A test-plan case that names the check still judges it in full.

Specs ship in this package; ``data/fwtest/specs`` holds the person's own, which win by file name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .rules import RULES

SHIPPED = Path(__file__).resolve().parent / "specs"
_KNOWN_KEYS = {"expect"}


@dataclass(frozen=True)
class FieldSpec:
    index: int
    name: str
    label: str
    rules: dict
    allow: tuple = ()
    level: str = "fail"
    formats: tuple = ()            # only in these formats of its packet ("51"); empty = in every one
    source: str = ""               # the authority for this field's rules: "Locate 140 4G PVT sheet, field 9"


@dataclass(frozen=True)
class PacketSpec:
    header: str
    label: str
    fields: int | None
    field_list: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class Spec:
    name: str
    file: str
    codec: str
    about: str
    checksum: dict
    terminator: str
    login: str
    packets: dict[str, PacketSpec]
    scenarios: tuple[dict, ...]
    settings: dict = field(default_factory=dict)
    expect: dict = field(default_factory=dict)
    status_by_alert: dict = field(default_factory=dict)
    alert_ids: dict = field(default_factory=dict)
    alert_ids_interim: dict = field(default_factory=dict)   # packet type → {alert ID: why it is only a warning}
    ignition_by_type: dict = field(default_factory=dict)
    when: tuple = ()                                  # consistency: [{"if": {field: value}, "then": {field: rules}, "says"}]
    config: dict = field(default_factory=dict)       # the device's settings a test is asked for, and their defaults
    formats: dict = field(default_factory=dict)      # header → {format: words} — the layouts a firmware may be set to
    levels: dict = field(default_factory=dict)       # check → "warn" / "fail" for this device, over the gate's default


CONFIG_KEYS = {"ign_on_s", "ign_off_s", "sleep_after_min", "sleep_s", "history_order", "formats"}


def clean_config(spec: "Spec", config: dict | None) -> dict:
    """The spec's defaults with a run's settings over them: intervals as numbers (None = not set),
    history order "fifo" / "lifo" / "" (not set), a format per header among the spec's choices."""
    out = {**{k: None for k in ("ign_on_s", "ign_off_s", "sleep_after_min", "sleep_s")},
           "history_order": "", "formats": {}, **(spec.config or {})}
    out["formats"] = dict(out.get("formats") or {})
    for k, v in (config or {}).items():
        if k not in CONFIG_KEYS:
            continue
        if k == "formats":
            for h, f in (v or {}).items():
                if str(f) in (spec.formats.get(h) or {}):
                    out["formats"][h] = str(f)
        elif k == "history_order":
            out[k] = v if v in ("fifo", "lifo") else ""
        else:
            try:
                out[k] = float(v) if v not in (None, "") and float(v) > 0 else None
            except (TypeError, ValueError):
                out[k] = None
    return out


def parse(data: dict, file: str = "") -> Spec:
    """A spec from its JSON; ValueError names what is wrong (a spec half-read tests nothing)."""
    if not isinstance(data, dict) or not data.get("name"):
        raise ValueError("a spec needs a name")
    packets = {}
    for header, p in (data.get("packets") or {}).items():
        fields = []
        for f in p.get("field_list") or []:
            if not isinstance(f.get("index"), int) or not f.get("name"):
                raise ValueError(f"{header}: every field needs an index and a name")
            unknown = set(f.get("rules") or {}) - set(RULES) - _KNOWN_KEYS
            if unknown:
                raise ValueError(f"{header} {f['name']}: no rule named {', '.join(sorted(unknown))}")
            fields.append(FieldSpec(index=f["index"], name=f["name"], label=f.get("label") or f["name"],
                                    rules=dict(f.get("rules") or {}), allow=tuple(str(a) for a in f.get("allow") or ()),
                                    level=f.get("level") or "fail", formats=tuple(str(x) for x in f.get("formats") or ()),
                                    source=f.get("source") or (f"{p['source']}, field {f['index'] if f['index'] > 0 else 'last'}"
                                                               if p.get("source") else "")))
        packets[header] = PacketSpec(header=header, label=p.get("label") or header, fields=p.get("fields"),
                                     field_list=tuple(fields))
    settings = {"live_max_delay_s": 60, "live_stale_s": 600, "future_tolerance_s": 60, **(data.get("settings") or {})}
    levels = {str(k): v for k, v in (data.get("levels") or {}).items()}
    bad = {k: v for k, v in levels.items() if v not in ("warn", "fail")}
    if bad:
        raise ValueError("levels: " + ", ".join(f"{k} is {v!r}, not warn or fail" for k, v in bad.items()))
    return Spec(name=data["name"], file=file, codec=data.get("codec") or "", about=data.get("about") or "",
                checksum=data.get("checksum") or {}, terminator=data.get("terminator") or "",
                login=data.get("login") or "LGN",
                packets=packets, scenarios=tuple(data.get("scenarios") or ()), settings=settings,
                expect=data.get("expect") or {}, status_by_alert=data.get("status_by_alert") or {},
                alert_ids={k: [int(a) for a in v] for k, v in (data.get("alert_ids") or {}).items()},
                alert_ids_interim={k: {int(a): str(w) for a, w in (v or {}).items()}
                                   for k, v in (data.get("alert_ids_interim") or {}).items()},
                ignition_by_type=data.get("ignition_by_type") or {}, config=data.get("config") or {},
                when=tuple(data.get("when") or ()),
                formats=data.get("formats") or {}, levels=levels)


def available(own_dir: Path | None = None) -> dict[str, Path]:
    """Spec name → its file: the shipped ones, then the person's own (theirs win, by file name)."""
    files: dict[str, Path] = {}
    for d in (SHIPPED, own_dir):
        if d and d.is_dir():
            for p in sorted(d.glob("*.json")):
                files[p.name] = p
    out = {}
    for p in files.values():
        try:
            out[parse(json.loads(p.read_text(encoding="utf-8")), p.name).name] = p
        except (ValueError, OSError):
            continue
    return out


def load(name: str, own_dir: Path | None = None) -> Spec:
    p = available(own_dir).get(name)
    if p is None:
        raise ValueError(f"no test spec named {name!r}")
    return parse(json.loads(p.read_text(encoding="utf-8")), p.name)
