"""Devices side by side — benchmarking several devices fitted in the same vehicle (the user,
17-11-2026: "during testing and evaluation we install multiple devices in the same vehicle").

Each log is judged over the time all of them cover — its report on that stretch, with its own
intervals — so no device looks better for having been watched longer. Every figure that tells one
device from another is a row of METRICS, one column per device, its best and worst marked where
the row has a better side. Positions and speeds are compared with the first device's at the same
moment (interpolated between its two nearest packets): how far apart the devices put one vehicle.

Pure computation: reports and rows in, plain dicts out.
"""

from __future__ import annotations

import bisect
import math
from .scorecard import ABOUT, METRICS

MAX_DEVICES = 8
MATCH_GAP_S = 60          # the first device's packets either side of a moment no further apart than this
MAX_MATCHES = 20000       # positions compared per device (evenly spread when there are more)


def _m(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742000 * math.asin(math.sqrt(max(0.0, a)))


def _q(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))]


def _valid(r: dict) -> bool:
    return r.get("lat") is not None and r.get("lon") is not None and not (r["lat"] == 0 and r["lon"] == 0)


def agreement(ref: list[dict], other: list[dict]) -> dict:
    """Where ``other`` put the vehicle against where ``ref`` did at the same moments."""
    if not ref or not other:
        return {"matched": 0}
    times = [r["t"] for r in ref]
    step = max(1, len(other) // MAX_MATCHES)
    apart, dspeed = [], []
    for b in other[::step]:
        i = bisect.bisect_left(times, b["t"])
        if i < len(ref) and ref[i]["t"] == b["t"]:
            r0 = r1 = ref[i]
            f = 0.0
        elif 0 < i < len(ref):
            r0, r1 = ref[i - 1], ref[i]
            span = (r1["t"] - r0["t"]).total_seconds()
            if span > MATCH_GAP_S:
                continue
            f = (b["t"] - r0["t"]).total_seconds() / span if span else 0.0
        else:
            continue
        lat = r0["lat"] + (r1["lat"] - r0["lat"]) * f
        lon = r0["lon"] + (r1["lon"] - r0["lon"]) * f
        apart.append(_m(lat, lon, b["lat"], b["lon"]))
        if b.get("speed") is not None and r0.get("speed") is not None and r1.get("speed") is not None:
            dspeed.append(abs(b["speed"] - (r0["speed"] + (r1["speed"] - r0["speed"]) * f)))
    return {"matched": len(apart), "apart_median_m": _round(_q(apart, 0.5)), "apart_p90_m": _round(_q(apart, 0.9)),
            "apart_max_m": _round(max(apart) if apart else None), "speed_diff_median": _round(_q(dspeed, 0.5), 1)}


def _round(x, nd=0):
    return None if x is None else (round(x) if nd == 0 else round(x, nd))


def _clean(rep: dict, rows: list[dict]) -> list[dict]:
    excluded = set((rep.get("gps") or {}).get("excluded") or [])
    return [r for r in rows if _valid(r) and r["k"] not in excluded]


def compare(entries: list[dict], reports: list[dict], rows: list[list[dict]], window: dict) -> dict:
    """``entries``, ``reports`` and ``rows`` per device, in the order chosen (the first is the
    reference for positions); ``window``: the stretch they were judged over."""
    clean = [_clean(rep, rs) for rep, rs in zip(reports, rows)]
    for i, rep in enumerate(reports):
        sats = sorted(r["sats"] for r in rows[i] if r.get("sats") is not None)
        extra = {"sats_median": sats[len(sats) // 2] if sats else None}
        if i:
            extra.update(agreement(clean[0], clean[i]))
        rep["_extra"] = extra
    metrics = []
    for key, group, label, unit, better, read in METRICS:
        vals = []
        for rep in reports:
            try:
                v = read(rep)
            except (KeyError, TypeError, IndexError):
                v = None
            vals.append(round(v, 2) if isinstance(v, float) else v)
        if all(v is None for v in vals):
            continue
        best, worst = [], []
        have = [(i, v) for i, v in enumerate(vals) if isinstance(v, (int, float))]
        if better and len(have) >= 2 and len({v for _, v in have}) > 1:
            score = (lambda v: -abs(v - 100)) if better == "100" else (lambda v: v) if better == "high" else (lambda v: -v)
            hi = max(score(v) for _, v in have)
            lo = min(score(v) for _, v in have)
            best = [i for i, v in have if score(v) == hi]          # a tie: every device level with it
            worst = [i for i, v in have if score(v) == lo]
        metrics.append({"key": key, "group": group, "label": label, "unit": unit, "better": better,
                        "values": vals, "best": best, "worst": worst, "about": ABOUT.get(key, "")})
    devices = []
    for e, rep in zip(entries, reports):
        s = rep.get("summary") or {}
        devices.append({"id": e.get("id"), "device": e.get("device") or (rep.get("meta") or {}).get("device"),
                        "file": e.get("file"), "first": s.get("first"), "last": s.get("last"),
                        "status": rep.get("status"), "status_word": rep.get("status_word"), "issues": rep.get("issues", 0),
                        "areas": [{k: a.get(k) for k in ("id", "label", "status", "status_word", "figure", "caption")}
                                  for a in rep.get("areas", [])],
                        "firmware": s.get("firmware") or [],
                        "profile": ((rep.get("generation") or {}).get("profile") or {}).get("name"),
                        "modes": (rep.get("generation") or {}).get("modes") or [],
                        "agreement": {k: v for k, v in rep["_extra"].items() if k != "sats_median"}})
    series = [{"bucket_s": (rep.get("series") or {}).get("bucket_s"), "points": (rep.get("series") or {}).get("points") or [],
               "track": (rep.get("series") or {}).get("track") or [], "breaks": (rep.get("series") or {}).get("breaks") or []}
              for rep in reports]
    for rep in reports:
        rep.pop("_extra", None)
    return {"window": window, "devices": devices, "metrics": metrics, "series": series}
