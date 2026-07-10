"""QC pass/fail checks — evaluates a session's decoded records against a small default
check list. Framework-free (like ``session.py``): takes plain ``UnifiedRecord``s + a meta
dict, returns plain dicts, no FastAPI/DB imports.

Each check function returns ``(result, detail)`` with ``result`` one of:
* ``"pass"`` / ``"fail"`` — the check ran and has an answer.
* ``"na"``   — the check doesn't apply (e.g. the protocol never carries this field, or
  no threshold is configured), so it's excluded from the pass/fail verdict.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..core.models import UnifiedRecord
from .session import UNKNOWN_DEVICE_IDS

DEFAULT_CONFIG = {
    "location_window_s": 60,     # a valid GPS fix must appear within this many seconds of session start
    "cadence_max_s": 30,         # largest allowed gap between consecutive packets
    "voltage_min": None,         # external_v range — na until an operator configures it
    "voltage_max": None,
}


def _record_epoch(r: UnifiedRecord) -> Optional[float]:
    t = r.event_time or r.received_at
    return t.timestamp() if t is not None else None


def _check_imei_present(records: list[UnifiedRecord], meta: dict, config: dict) -> tuple[str, str]:
    device_id = meta.get("device_id") or ""
    if device_id and device_id not in UNKNOWN_DEVICE_IDS:
        return "pass", f"device id: {device_id}"
    return "fail", "no IMEI/device id learned yet"


def _check_location_fix(records: list[UnifiedRecord], meta: dict, config: dict) -> tuple[str, str]:
    window_s = config["location_window_s"]
    started_at = meta.get("started_at")
    for r in records:
        if r.position and r.position.is_valid:
            t = _record_epoch(r)
            if started_at is None or t is None or t - started_at <= window_s:
                return "pass", f"fix at {r.position.latitude:.5f},{r.position.longitude:.5f}"
    return "fail", f"no valid GPS fix within {window_s}s of session start"


def _check_gps_valid_flag(records: list[UnifiedRecord], meta: dict, config: dict) -> tuple[str, str]:
    if not any(r.gps_valid is not None for r in records):
        return "na", "protocol does not report a gps_valid flag"
    if any(r.gps_valid for r in records):
        return "pass", "gps_valid seen true"
    return "fail", "gps_valid never true"


def _check_packet_cadence(records: list[UnifiedRecord], meta: dict, config: dict) -> tuple[str, str]:
    max_s = config["cadence_max_s"]
    times = sorted(t for t in (_record_epoch(r) for r in records) if t is not None)
    if len(times) < 2:
        return "na", "fewer than 2 packets"
    worst = max(b - a for a, b in zip(times, times[1:]))
    if worst <= max_s:
        return "pass", f"largest gap {worst:.0f}s"
    return "fail", f"largest gap {worst:.0f}s > {max_s}s"


def _check_external_voltage(records: list[UnifiedRecord], meta: dict, config: dict) -> tuple[str, str]:
    vmin, vmax = config["voltage_min"], config["voltage_max"]
    if vmin is None or vmax is None:
        return "na", "no voltage range configured"
    values = [r.external_v for r in records if r.external_v is not None]
    if not values:
        return "na", "protocol never reports external_v"
    bad = [v for v in values if not (vmin <= v <= vmax)]
    if bad:
        return "fail", f"{len(bad)}/{len(values)} readings outside {vmin}-{vmax}V"
    return "pass", f"all {len(values)} readings within {vmin}-{vmax}V"


# (key, label, check function) — order is display order.
DEFAULT_CHECKS: list[tuple[str, str, Callable]] = [
    ("imei_present", "IMEI present", _check_imei_present),
    ("location_fix", "Valid GPS fix", _check_location_fix),
    ("gps_valid_flag", "GPS valid flag", _check_gps_valid_flag),
    ("packet_cadence", "Packet cadence", _check_packet_cadence),
    ("external_voltage", "External voltage in range", _check_external_voltage),
]


def _verdict(checks: list[dict]) -> str:
    if any(c["result"] == "fail" for c in checks):
        return "fail"
    if any(c["result"] == "pass" for c in checks):
        return "pass"
    return ""


def evaluate(records: list[UnifiedRecord], meta: dict, config: Optional[dict] = None) -> tuple[list[dict], str]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    checks = []
    for key, label, fn in DEFAULT_CHECKS:
        result, detail = fn(records, meta, cfg)
        checks.append({"key": key, "label": label, "result": result, "detail": detail})
    return checks, _verdict(checks)
