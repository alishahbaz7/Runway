"""CoverageLedger — incremental, unbounded-safe per-device QC state.

The capture sink keeps a bounded in-memory record window (``MAX_CHECK_RECORDS``): fine
for "latest packet" vitals, WRONG for anything the verdict depends on. Recomputing
coverage over that window meant a long session forgot its own history — a login seen at
minute 0 "unhappened" once 5000 later packets pushed it out of the window, silently
flipping a passing device back to IN PROGRESS. The ledger fixes that class of bug:
every record is observed exactly once as it arrives, and per-device state only ever
accumulates — seen stays seen, counts keep counting, identity sticks.

Also the enforcement point for **field validation** (Step 2): each record matched to a
coverage item is validated (``engine.validate_record``); one bad packet marks the item
INVALID with a reason, which surfaces as a deviation (Conditional Pass, never a silent
"OK").

Framework-free like the rest of ``qc/`` — plain dicts in, plain dicts out.
"""

from __future__ import annotations

from typing import Optional

from ..core.models import UnifiedRecord
from .engine import _cov_match, validate_record
from .session import UNKNOWN_DEVICE_IDS


def _iso(t) -> Optional[str]:
    return t.isoformat() if t else None


class CoverageLedger:
    def __init__(self):
        self.devices: dict[str, dict] = {}      # did -> accumulated state

    # ------------------------------------------------------------------ #
    def _dev(self, did: str) -> dict:
        d = self.devices.get(did)
        if d is None:
            d = self.devices[did] = {
                "packet_count": 0,
                "peers": set(),
                "firmware_version": "", "vendor_id": "",
                "last_recv_epoch": None,
                "items": {},                    # coverage key -> accumulated entry
                "last": {},                     # newest packet (type/alert/when)
                "last_pos": None,               # newest record carrying a position
                "last_batt": None,              # newest record carrying voltages
            }
        return d

    def observe(self, rec: UnifiedRecord, catalog: Optional[list]) -> None:
        """Fold one record into the ledger (vitals always; coverage when a catalog is
        known). Call exactly once per record as it arrives."""
        did = rec.device_id
        if not did or did in UNKNOWN_DEVICE_IDS:
            return
        d = self._dev(did)
        d["packet_count"] += 1
        if rec.peer:
            d["peers"].add(rec.peer)
        io = rec.io or {}
        d["firmware_version"] = io.get("firmware_version") or d["firmware_version"]
        d["vendor_id"] = io.get("vendor_id") or d["vendor_id"]
        if rec.received_at is not None:
            e = rec.received_at.timestamp()
            if d["last_recv_epoch"] is None or e > d["last_recv_epoch"]:
                d["last_recv_epoch"] = e
        d["last"] = {
            "message_type": rec.message_type or "",
            "alert_id": io.get("alert_id"),
            "alarms": list(rec.alarms or []),
            "packet_status": io.get("packet_status") or "",
            "received_at": _iso(rec.received_at),
        }
        if rec.position is not None and rec.position.latitude is not None:
            at = rec.event_time or rec.received_at
            d["last_pos"] = {"lat": rec.position.latitude, "lon": rec.position.longitude,
                             "satellites": rec.satellites, "gps_valid": rec.gps_valid,
                             "at": _iso(at)}
        if rec.external_v is not None or rec.battery_v is not None:
            d["last_batt"] = {"external_v": rec.external_v, "internal_v": rec.battery_v}
        if catalog:
            self._observe_items(d, rec, catalog)

    def replay_items(self, records: list[UnifiedRecord], catalog: Optional[list]) -> None:
        """Backfill coverage for records that arrived BEFORE the protocol (and so the
        catalog) was identified. Touches only the coverage items — vitals/counters were
        already observed live, so nothing double-counts."""
        if not catalog:
            return
        for rec in records:
            did = rec.device_id
            if did and did not in UNKNOWN_DEVICE_IDS:
                self._observe_items(self._dev(did), rec, catalog)

    @staticmethod
    def _observe_items(d: dict, rec: UnifiedRecord, catalog: list) -> None:
        for item in catalog:
            if not _cov_match(rec, item.get("match") or {}):
                continue
            e = d["items"].setdefault(item["key"], {
                "count": 0, "last_raw_hex": "", "last_seen": None,
                "valid": None, "invalid_reason": "",
            })
            e["count"] += 1
            e["last_raw_hex"] = rec.raw_hex or e["last_raw_hex"]
            e["last_seen"] = _iso(rec.event_time or rec.received_at) or e["last_seen"]
            reason = validate_record(rec)
            if reason:                                # one bad packet -> INVALID, sticky
                e["valid"] = False
                e["invalid_reason"] = reason
            elif e["valid"] is None:
                e["valid"] = True

    # ------------------------------------------------------------------ #
    def device_ids(self) -> list[str]:
        return list(self.devices)                    # insertion order = first-seen order

    def coverage(self, did: str, catalog: Optional[list]) -> list[dict]:
        """This device's coverage in the same shape ``engine.coverage`` emits."""
        d = self.devices.get(did) or {"items": {}}
        out = []
        for item in (catalog or []):
            e = d["items"].get(item["key"])
            out.append({
                "key": item["key"], "label": item.get("label", item["key"]),
                "required": bool(item.get("required")),
                "group": item.get("group", ""), "code": item.get("code", ""),
                "alert_id": (item.get("match") or {}).get("alert_id"),
                "seen": e is not None, "count": e["count"] if e else 0,
                "last_raw_hex": e["last_raw_hex"] if e else "",
                "last_seen": e["last_seen"] if e else None,
                "valid": e["valid"] if e else None,
                "invalid_reason": (e or {}).get("invalid_reason", ""),
            })
        return out

    def vitals(self, did: str) -> dict:
        """Identity + latest-traffic snapshot for one device (the Summary panel's
        "Device details" block)."""
        d = self.devices.get(did)
        if d is None:
            return {}
        last = dict(d["last"])
        if d["last_pos"] is not None:
            last["position"] = d["last_pos"]
        if d["last_batt"] is not None:
            last["battery"] = d["last_batt"]
        return {
            "packet_count": d["packet_count"],
            "peers": sorted(d["peers"]),
            "firmware_version": d["firmware_version"], "vendor_id": d["vendor_id"],
            "last_recv_epoch": d["last_recv_epoch"],
            "last_packet": last,
        }
