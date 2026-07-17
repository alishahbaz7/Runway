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
    "voltage_min": None,         # external_v range — na until an operator configures it
    "voltage_max": None,
    "stale_timeout_s": 600,      # no data for this long (required items still missing) -> timeout
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


# --------------------------------------------------------------------------- #
# Field validation (Step 2) — a packet that MATCHES a coverage item must also carry
# sane fields, otherwise "OK" would silently certify garbage. One failing packet marks
# the item INVALID (a deviation -> Conditional Pass, visible with its reason), it never
# stays a silent OK. Deliberately conservative: only checks that hold for every
# protocol (coordinate ranges, impossible timestamps) — protocol-specific rules can
# come from the profile later.
# --------------------------------------------------------------------------- #
def validate_record(r: UnifiedRecord) -> Optional[str]:
    """Reason this record's fields are implausible, or None when it looks sane."""
    try:
        pos = r.position
        if pos is not None and pos.latitude is not None:
            if not (-90.0 <= pos.latitude <= 90.0) or not (-180.0 <= pos.longitude <= 180.0):
                return f"position out of range ({pos.latitude}, {pos.longitude})"
            if r.gps_valid and pos.latitude == 0.0 and pos.longitude == 0.0:
                return "0,0 position while claiming a GPS fix"
        if r.event_time is not None and r.received_at is not None:
            drift = (r.event_time - r.received_at).total_seconds()
            if drift > 86400:                    # event stamped >1 day in the future
                return "event time more than a day ahead of receive time"
    except Exception:
        return None                              # never let validation break decoding
    return None


# --------------------------------------------------------------------------- #
# Coverage — did the device exercise each packet type / alert the QC profile expects?
# The catalogue (which items, their match rule, required/optional) comes from the per-
# protocol QC profile; this stays pure (the API loads the profile and passes it in).
# --------------------------------------------------------------------------- #
def _cov_match(rec: UnifiedRecord, match: dict) -> bool:
    """Does a record satisfy a coverage item's match rule? Supported keys: message_type,
    alert_id, packet_type (the codec exposes the latter two in ``io`` for AIS-140)."""
    if not match:
        return False
    io = rec.io or {}
    for k, v in match.items():
        if k == "message_type":
            if (rec.message_type or "") != v:
                return False
        elif k == "alert_id":
            if io.get("alert_id") != v:
                return False
        elif k == "packet_type":
            if io.get("packet_type") != v:
                return False
        else:
            return False
    return True


def coverage(records: list[UnifiedRecord], catalog: list[dict]) -> list[dict]:
    """For each expected item in the catalogue, how this session has covered it:
    ``seen``/``count`` + the most-recent matching raw packet (the captured sample) so the
    UI can show it on hover. ``valid`` stays ``None`` until the field validator (Step 2)."""
    out = []
    for item in (catalog or []):
        matched = [r for r in records if _cov_match(r, item.get("match") or {})]
        last = matched[-1] if matched else None
        reasons = [reason for r in matched if (reason := validate_record(r))]
        out.append({
            "key": item["key"], "label": item.get("label", item["key"]),
            "required": bool(item.get("required")),
            # display metadata carried from the profile (UI sections + alert headers)
            "group": item.get("group", ""), "code": item.get("code", ""),
            "alert_id": (item.get("match") or {}).get("alert_id"),
            "seen": bool(matched), "count": len(matched),
            "last_raw_hex": (last.raw_hex if last else "") or "",
            "last_seen": (_iso(last) if last else None),
            "valid": (False if reasons else True) if matched else None,
            "invalid_reason": reasons[-1] if reasons else "",
        })
    return out


def _iso(r: UnifiedRecord) -> Optional[str]:
    t = r.event_time or r.received_at
    return t.isoformat() if t else None


def overall_verdict(cov: list[dict], meta: dict) -> str:
    """Coverage-driven session state (used when a QC profile exists for the protocol):

    * ``""``               — no packets yet.
    * ``in_progress``      — receiving, required set not complete, device still alive.
    * ``timeout``          — no data for ``stale_timeout_s`` with required items missing
      (the operator can Resume).
    * ``pass``             — every required item seen (and valid, once Step 2 lands).
    * ``conditional_pass`` — all non-skipped required seen, but some were skipped/optional
      (or, later, had deviations the operator accepted).
    * a finalized operator verdict overrides everything.
    """
    fin = meta.get("finalized_verdict")
    if fin:
        return fin
    packet_count = meta.get("packet_count", 0)
    if not cov or not packet_count:
        return "in_progress" if packet_count else ""
    skipped = set(meta.get("skipped") or [])
    required = [c for c in cov if c["required"] and c["key"] not in skipped]
    missing = [c for c in required if not c["seen"]]
    if not missing:
        # invalid packets are deviations too (Step 2): everything arrived, but not all of
        # it was sane -> Conditional Pass, with the reasons on the Deviations list
        has_invalid = any(c.get("valid") is False for c in cov if c["key"] not in skipped)
        return "conditional_pass" if (skipped or has_invalid) else "pass"
    now, last = meta.get("now"), meta.get("last_packet_epoch")
    stale = meta.get("stale_timeout_s") or DEFAULT_CONFIG["stale_timeout_s"]
    if now and last and (now - last) >= stale:
        return "timeout"
    return "in_progress"
