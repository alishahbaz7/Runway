"""Telemetry store — what a listener receives, kept for internal use and for the IMU page.

Two tables in their own file, ``data/telemetry.db`` — not ``runway.db``, which holds settings,
devices and QC and is backed up whole every day, while this grows by ~36,000 IMU rows an hour
per device:

* ``packets``     one row per decoded record: device, device time, type, status, frame number,
                  position/speed/ignition, and the raw bytes as received.
* ``imu_samples`` one row per IMU sample: the packet's time plus the sample's place in it
                  (``n`` of ``of_n``). ``amag`` (|a|) is stored so long ranges can be thinned in
                  SQL.

**Sample timing** follows the device (sCAN firmware, ``SET IMUPKT:1,<ignOnSec>,<ignOffSec>``,
default ``1,10,2``): a packet's ``N`` samples are spread evenly over the packet interval — gap =
interval / N, 100 ms at the fastest — and the interval depends on ignition (10 s ON -> 500 ms
apart, 2 s OFF -> 100 ms apart). Windows are back to back: each packet covers the time since the
previous one. A sample's time is worked out when it is read (:func:`sample_time_sql`), never
stored, from the interval for its packet's ignition state: measured from the spacing of the IMU
packets in range (the median, so the device clock's 1 s resolution washes out) or, with too few
packets to measure, the configured ``IMUPKT`` values — a config change never needs a rewrite.

All times are epoch seconds (UTC). Every call is synchronous and thread-safe; async callers
run it in an executor (see ``sinks/telemetry_store.py``).
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
from typing import Any

from ..core.models import UnifiedRecord
from .db import DATA_DIR

DEFAULT_PATH = os.path.join(DATA_DIR, "telemetry.db")

_DDL = """
CREATE TABLE IF NOT EXISTS packets (
    id            INTEGER PRIMARY KEY,
    device_id     TEXT NOT NULL,
    protocol      TEXT,
    message_type  TEXT,
    packet_status TEXT,
    frame_number  INTEGER,
    event_time    REAL NOT NULL,
    received_at   REAL,
    lat           REAL,
    lon           REAL,
    speed_kph     REAL,
    ignition      INTEGER,
    imu_count     INTEGER NOT NULL DEFAULT 0,
    raw           BLOB
);
CREATE INDEX IF NOT EXISTS idx_packets_device_time ON packets(device_id, event_time);
CREATE TABLE IF NOT EXISTS imu_samples (
    packet_id   INTEGER NOT NULL,
    device_id   TEXT NOT NULL,
    packet_time REAL NOT NULL,
    n           INTEGER NOT NULL,
    of_n        INTEGER NOT NULL,
    ax REAL, ay REAL, az REAL, pitch REAL, roll REAL, yaw REAL,
    amag        REAL
);
CREATE INDEX IF NOT EXISTS idx_imu_device_time ON imu_samples(device_id, packet_time);
CREATE TABLE IF NOT EXISTS device_summary (
    device_id   TEXT PRIMARY KEY,
    protocol    TEXT,
    packets     INTEGER NOT NULL DEFAULT 0,
    imu_samples INTEGER NOT NULL DEFAULT 0,
    first       REAL,
    last        REAL
);
"""

AXES = ("ax", "ay", "az", "pitch", "roll", "yaw", "amag")

# Per device-minute: sample count, packet count and min / max / sum per axis, updated as packets
# arrive. Long ranges (a bucket of a minute or more: 24 h and up) are drawn from it — a month is
# 43,200 rows a device instead of ~26 million samples — and it outlives the samples' retention,
# so an old day still shows at minute resolution. A packet counts towards the minute of its own
# time (its samples spread over its interval, 2-10 s, before that).
# Speed rides along from the same IMU packets (spd_n of them reported one), so a long range has
# its speed too after the packets' retention.
_SPEED_COLS = (("spd_n", "INTEGER NOT NULL DEFAULT 0"), ("spd_min", "REAL"), ("spd_max", "REAL"),
               ("spd_sum", "REAL NOT NULL DEFAULT 0"))
_MINUTE_DDL = (
    "CREATE TABLE IF NOT EXISTS imu_minute (device_id TEXT NOT NULL, minute INTEGER NOT NULL, "
    "n INTEGER NOT NULL, pk INTEGER NOT NULL, "
    + ", ".join(f"{a}_min REAL, {a}_max REAL, {a}_sum REAL" for a in AXES) + ", "
    + ", ".join(f"{c} {decl}" for c, decl in _SPEED_COLS)
    + ", PRIMARY KEY (device_id, minute)) WITHOUT ROWID"
)
_MINUTE_UPSERT = (
    "INSERT INTO imu_minute (device_id, minute, n, pk, "
    + ", ".join(f"{a}_min, {a}_max, {a}_sum" for a in AXES)
    + ", spd_n, spd_min, spd_max, spd_sum) VALUES (?, ?, ?, 1, " + ", ".join("?, ?, ?" for _ in AXES)
    + ", ?, ?, ?, ?) "
    "ON CONFLICT(device_id, minute) DO UPDATE SET n = n + excluded.n, pk = pk + 1, "
    + ", ".join(f"{a}_min = MIN({a}_min, excluded.{a}_min), {a}_max = MAX({a}_max, excluded.{a}_max), "
                f"{a}_sum = {a}_sum + excluded.{a}_sum" for a in AXES)
    # MIN/MAX of two values is NULL when either is: fall back to whichever is known
    + ", spd_n = spd_n + excluded.spd_n, spd_sum = spd_sum + excluded.spd_sum, "
    "spd_min = COALESCE(MIN(spd_min, excluded.spd_min), spd_min, excluded.spd_min), "
    "spd_max = COALESCE(MAX(spd_max, excluded.spd_max), spd_max, excluded.spd_max)"
)
_SUMMARY_UPSERT = (
    "INSERT INTO device_summary (device_id, protocol, packets, imu_samples, first, last) "
    "VALUES (?, ?, 1, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET protocol = excluded.protocol, "
    "packets = packets + 1, imu_samples = imu_samples + excluded.imu_samples, "
    "first = MIN(first, excluded.first), last = MAX(last, excluded.last)"
)
MINUTE_KEEP_DAYS = 365
TIMING_DEFAULT = {"ign_on_s": 10.0, "ign_off_s": 2.0, "anchor": "last", "measure": True}
MIN_GAP = 0.1                              # the device samples on a 100 ms timer tick


def sample_time_sql(anchor: str) -> str:
    """SQL for a sample's time over ``imu_samples s JOIN packets p``, with the packet interval
    for each ignition state bound as ``:iv_on`` / ``:iv_off`` (seconds). ``anchor`` says which
    sample the packet's time stamps: ``"last"`` (the window ends at it) or ``"first"``."""
    offset = "(s.n - 1)" if anchor == "first" else "(s.n - s.of_n)"
    return f"(s.packet_time + {offset} * {GAP_SQL})"


# A sample's own spacing: its packet's interval over that packet's sample count (an ignition
# change can cut a window short — 18 samples were seen on the live device). The chart breaks a
# line only where the time to the next sample exceeds this, so a hole shows as a hole.
GAP_SQL = "((CASE p.ignition WHEN 0 THEN :iv_off ELSE :iv_on END) * 1.0 / s.of_n)"


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


class TelemetryStore:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_DDL)
            conn.execute(_MINUTE_DDL)
            have = {r[1] for r in conn.execute("PRAGMA table_info(imu_minute)")}
            if "spd_n" not in have:                       # a v1.1.25 database: no speed yet
                for col, decl in _SPEED_COLS:
                    conn.execute(f"ALTER TABLE imu_minute ADD COLUMN {col} {decl}")
                self._backfill_speed(conn)
            self._backfill(conn)
            self._conn = conn
        return self._conn

    @staticmethod
    def _backfill(conn: sqlite3.Connection) -> None:
        """A database written before the summaries existed gets them built once, on open."""
        if conn.execute("SELECT 1 FROM device_summary LIMIT 1").fetchone() is None:
            conn.execute("INSERT INTO device_summary (device_id, protocol, packets, imu_samples, first, last) "
                         "SELECT device_id, MAX(protocol), COUNT(*), SUM(imu_count), MIN(event_time), "
                         "MAX(event_time) FROM packets GROUP BY device_id")
        if conn.execute("SELECT 1 FROM imu_minute LIMIT 1").fetchone() is None:
            conn.execute(
                "INSERT INTO imu_minute (device_id, minute, n, pk, "
                + ", ".join(f"{a}_min, {a}_max, {a}_sum" for a in AXES) + ") "
                "SELECT device_id, CAST(packet_time / 60 AS INTEGER), COUNT(*), COUNT(DISTINCT packet_id), "
                + ", ".join(f"MIN({a}), MAX({a}), SUM({a})" for a in AXES)
                + " FROM imu_samples GROUP BY device_id, CAST(packet_time / 60 AS INTEGER)")
            TelemetryStore._backfill_speed(conn)
        conn.commit()

    @staticmethod
    def _backfill_speed(conn: sqlite3.Connection) -> None:
        conn.execute(
            "WITH s AS (SELECT device_id, CAST(event_time / 60 AS INTEGER) AS m, COUNT(speed_kph) AS c, "
            "MIN(speed_kph) AS lo, MAX(speed_kph) AS hi, COALESCE(SUM(speed_kph), 0) AS sm FROM packets "
            "WHERE imu_count > 0 GROUP BY device_id, m) "
            "UPDATE imu_minute SET spd_n = s.c, spd_min = s.lo, spd_max = s.hi, spd_sum = s.sm FROM s "
            "WHERE imu_minute.device_id = s.device_id AND imu_minute.minute = s.m")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- write --------------------------------------------------------------- #
    def write(self, rec: UnifiedRecord) -> None:
        if not rec.device_id:
            return
        t = (rec.event_time or rec.received_at)
        if t is None:
            return
        io = rec.io or {}
        samples = io.get("imu") or []
        pos = rec.position
        try:
            frame = int(io.get("frame_number"))
        except (TypeError, ValueError):
            frame = None
        with self._lock:
            db = self._db()
            cur = db.execute(
                "INSERT INTO packets (device_id, protocol, message_type, packet_status, "
                "frame_number, event_time, received_at, lat, lon, speed_kph, ignition, "
                "imu_count, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec.device_id, rec.protocol, rec.message_type, io.get("packet_status"), frame,
                 t.timestamp(), rec.received_at.timestamp() if rec.received_at else None,
                 pos.latitude if pos else None, pos.longitude if pos else None, rec.speed_kph,
                 None if rec.ignition is None else int(rec.ignition), len(samples),
                 bytes.fromhex(rec.raw_hex) if rec.raw_hex else None))
            pt = t.timestamp()
            db.execute(_SUMMARY_UPSERT, (rec.device_id, rec.protocol, len(samples), pt, pt))
            if samples:
                of_n = len(samples)
                rows = [(cur.lastrowid, rec.device_id, pt, s["n"], of_n, s["ax"], s["ay"], s["az"],
                         s["pitch"], s["roll"], s["yaw"],
                         math.sqrt(s["ax"] ** 2 + s["ay"] ** 2 + s["az"] ** 2)) for s in samples]
                db.executemany(
                    "INSERT INTO imu_samples (packet_id, device_id, packet_time, n, of_n, "
                    "ax, ay, az, pitch, roll, yaw, amag) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                agg: list = []
                for i in range(len(AXES)):
                    vals = [r[5 + i] for r in rows]
                    agg += [min(vals), max(vals), sum(vals)]
                spd = rec.speed_kph
                db.execute(_MINUTE_UPSERT, (rec.device_id, int(pt // 60), of_n, *agg,
                                            int(spd is not None), spd, spd, spd or 0.0))
            db.commit()

    # -- read ---------------------------------------------------------------- #
    def devices(self) -> list[dict[str, Any]]:
        # From the running summary, not a scan of every packet: at 20 devices x 30 days the scan
        # took ~22 s, and the IMU page asks for this list every 2 s while following live.
        with self._lock:
            rows = self._db().execute(
                "SELECT device_id, packets, imu_samples, first, last, protocol FROM device_summary "
                "ORDER BY last DESC").fetchall()
        return [{"device_id": d, "packets": p, "imu_samples": s or 0, "first": a, "last": b,
                 "protocol": proto} for d, p, s, a, b, proto in rows]

    # -- retention ------------------------------------------------------------ #
    def purge(self, keep_days: float, minute_keep_days: float = MINUTE_KEEP_DAYS,
              batch: int = 20000) -> dict[str, int]:
        """Drop samples and packets older than ``keep_days`` (minute summaries after
        ``minute_keep_days``), device by device through the (device, time) indexes — a bare time
        filter would scan every row each hour — and a batch at a time so a write never waits
        long. Freed pages are reused, so the file stops growing rather than shrinking. The device
        summary keeps its lifetime counts."""
        import time as _time

        removed = {"imu_samples": 0, "packets": 0, "imu_minute": 0}
        now = _time.time()
        cut, minute_cut = now - keep_days * 86400, int((now - minute_keep_days * 86400) // 60)
        with self._lock:
            devices = [r[0] for r in self._db().execute("SELECT device_id FROM device_summary")]
        for dev in devices:
            for table, col, limit in (("imu_samples", "packet_time", cut), ("packets", "event_time", cut),
                                      ("imu_minute", "minute", minute_cut)):
                while True:
                    with self._lock:
                        db = self._db()
                        if table == "imu_minute":          # WITHOUT ROWID; one row a minute, in one go
                            n = db.execute("DELETE FROM imu_minute WHERE device_id = ? AND minute < ?",
                                           (dev, limit)).rowcount
                        else:
                            n = db.execute(f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                                           f"WHERE device_id = ? AND {col} < ? LIMIT {batch})",
                                           (dev, limit)).rowcount
                        db.commit()
                    removed[table] += n
                    if table == "imu_minute" or n < batch:
                        break
        return removed

    def size(self) -> int:
        return sum(os.path.getsize(p) for p in (self.path, self.path + "-wal") if os.path.exists(p))

    def _intervals(self, db: sqlite3.Connection, device_id: str, start: float, end: float,
                   timing: dict) -> dict[str, dict]:
        """The IMU packet interval per ignition state for ``[start, end]``: measured from the
        spacing of consecutive IMU packets in the same state, else the configured value."""
        rows = db.execute(
            "SELECT event_time, ignition FROM packets WHERE device_id = ? AND imu_count > 0 "
            "AND event_time BETWEEN ? AND ? ORDER BY event_time",
            (device_id, start - 300, end + 300)).fetchall()
        gaps: dict[int, list[float]] = {0: [], 1: []}
        for (t0, ig0), (t1, ig1) in zip(rows, rows[1:]):
            if ig0 == ig1 and ig0 in (0, 1) and 0.5 < t1 - t0 <= 600:
                gaps[ig0].append(t1 - t0)
        out = {}
        for key, ig, conf in (("on", 1, timing["ign_on_s"]), ("off", 0, timing["ign_off_s"])):
            measured = timing.get("measure", True) and len(gaps[ig]) >= 3
            # never faster than the 100 ms tick: 20 samples need at least 2 s
            iv = max(MIN_GAP * 20, _median(gaps[ig]) if measured else float(conf))
            out[key] = {"interval": iv, "gap": iv / 20,
                        "source": "measured" if measured else "setting", "packets": len(gaps[ig])}
        return out

    def imu_series(self, device_id: str, start: float, end: float, points: int = 1200,
                   timing: dict | None = None) -> dict[str, Any]:
        """Samples in ``[start, end]`` — all of them when they fit in ``points``, else thinned
        to ``points`` time buckets each carrying min / max / mean per axis — plus the packets
        in the range (NR, history, frame gaps), summary figures and the timing used."""
        timing = {**TIMING_DEFAULT, **(timing or {})}
        anchor = "first" if timing["anchor"] == "first" else "last"
        points = max(50, min(int(points), 5000))
        if (end - start) / points >= 60:              # a point per minute or coarser: summaries
            return self._minute_series(device_id, start, end, points, timing, anchor)
        out = self._sample_series(device_id, start, end, points, timing, anchor)
        if not out["samples"]:                        # samples past retention: minute detail still
            minutes = self._minute_series(device_id, start, end, points, timing, anchor)
            if minutes["samples"]:
                return minutes
        return out

    def _minute_series(self, device_id: str, start: float, end: float, points: int,
                       timing: dict, anchor: str) -> dict[str, Any]:
        width = max(60.0, (end - start) / points)
        agg = ", ".join(f"MIN({a}_min), MAX({a}_max), SUM({a}_sum)" for a in AXES)
        args = {"dev": device_id, "t0": start, "w": width, "m0": int(start // 60), "m1": int(end // 60)}
        with self._lock:
            db = self._db()
            rows = db.execute(
                f"SELECT CAST((minute * 60 + 30 - :t0) / :w AS INTEGER) AS b, SUM(n), SUM(pk), {agg}, "
                "SUM(spd_n), MIN(spd_min), MAX(spd_max), SUM(spd_sum) "
                "FROM imu_minute WHERE device_id = :dev AND minute BETWEEN :m0 AND :m1 "
                "GROUP BY b ORDER BY b", args).fetchall()
            peak = db.execute("SELECT minute, amag_max FROM imu_minute WHERE device_id = :dev AND "
                              "minute BETWEEN :m0 AND :m1 ORDER BY amag_max DESC LIMIT 1", args).fetchone()
            ext = db.execute("SELECT " + ", ".join(f"MAX(MAX(ABS({a}_min), ABS({a}_max)))"
                                                   for a in ("pitch", "roll", "yaw"))
                             + " FROM imu_minute WHERE device_id = :dev AND minute BETWEEN :m0 AND :m1",
                             args).fetchone()
            pk = db.execute("SELECT COUNT(*), SUM(packet_status = 'H') FROM packets WHERE device_id = ? "
                            "AND event_time BETWEEN ? AND ?", (device_id, start, end)).fetchone()
            used = self._intervals(db, device_id, end - 3600, end, timing)
        series = {a: {"min": [r[3 + 3 * i] for r in rows], "max": [r[4 + 3 * i] for r in rows],
                      "mean": [r[5 + 3 * i] / r[1] if r[1] else None for r in rows]}
                  for i, a in enumerate(AXES)}
        times = [start + (r[0] + 0.5) * width for r in rows]
        spd = [(t, r[-3], r[-2], r[-1] / r[-4]) for t, r in zip(times, rows) if r[-4]]
        speed = {"t": [x[0] for x in spd], "min": [x[1] for x in spd], "max": [x[2] for x in spd],
                 "mean": [x[3] for x in spd], "w": width}
        return {"device_id": device_id, "start": start, "end": end, "mode": "minutes", "speed": speed,
                "timing": {"anchor": anchor, **used}, "samples": sum(r[1] for r in rows),
                "t": times, "g": [width] * len(rows), "w": width,
                "series": series, "packets": pk[0], "imu_packets": sum(r[2] for r in rows),
                "history_packets": pk[1] or 0, "events": [], "gaps": [], "missing_frames": None,
                "peak": {"t": peak[0] * 60 + 30, "amag": peak[1]} if peak else None,
                "extremes": {"pitch_abs": ext[0], "roll_abs": ext[1], "yaw_abs": ext[2]}
                if ext and ext[0] is not None else None}

    def _sample_series(self, device_id: str, start: float, end: float, points: int,
                       timing: dict, anchor: str) -> dict[str, Any]:
        t_sql = sample_time_sql(anchor)
        margin = 900.0                     # packet_time is indexed; a sample may sit ±1 window off
        src = "imu_samples s JOIN packets p ON p.id = s.packet_id"
        where = (f"s.device_id = :dev AND s.packet_time BETWEEN :t0 - {margin} AND :t1 + {margin} "
                 f"AND {t_sql} BETWEEN :t0 AND :t1")
        cols = ", ".join(f"s.{a}" for a in AXES)
        with self._lock:
            db = self._db()
            used = self._intervals(db, device_id, start, end, timing)
            args = {"dev": device_id, "t0": start, "t1": end,
                    "iv_on": used["on"]["interval"], "iv_off": used["off"]["interval"]}
            total = db.execute(f"SELECT COUNT(*) FROM {src} WHERE {where}", args).fetchone()[0]
            if total <= points:
                rows = db.execute(f"SELECT {t_sql} AS t, {cols}, {GAP_SQL} FROM {src} "
                                  f"WHERE {where} ORDER BY t", args).fetchall()
                series = {a: {"mean": [r[i + 1] for r in rows]} for i, a in enumerate(AXES)}
                times, mode, width = [r[0] for r in rows], "samples", None
            else:
                width = (end - start) / points
                agg = ", ".join(f"MIN(s.{a}), MAX(s.{a}), AVG(s.{a})" for a in AXES)
                rows = db.execute(
                    f"SELECT CAST(({t_sql} - :t0) / :w AS INTEGER) AS b, {agg}, MAX({GAP_SQL}) "
                    f"FROM {src} WHERE {where} GROUP BY b ORDER BY b", {**args, "w": width}).fetchall()
                times = [start + (r[0] + 0.5) * width for r in rows]
                series = {a: {"min": [r[1 + 3 * i] for r in rows], "max": [r[2 + 3 * i] for r in rows],
                              "mean": [r[3 + 3 * i] for r in rows]} for i, a in enumerate(AXES)}
                mode = "buckets"
            gaps = [r[-1] for r in rows]          # per point: its samples' own spacing (s)
            peak = db.execute(f"SELECT {t_sql}, s.amag FROM {src} WHERE {where} "
                              "ORDER BY s.amag DESC LIMIT 1", args).fetchone()
            ext = db.execute(f"SELECT MAX(ABS(s.pitch)), MAX(ABS(s.roll)), MAX(ABS(s.yaw)) "
                             f"FROM {src} WHERE {where}", args).fetchone()
            pkts = db.execute(
                "SELECT event_time, message_type, packet_status, frame_number, speed_kph, "
                "ignition, imu_count FROM packets WHERE device_id = :dev "
                "AND event_time BETWEEN :t0 AND :t1 ORDER BY event_time", args).fetchall()
        return {"device_id": device_id, "start": start, "end": end, "mode": mode,
                "timing": {"anchor": anchor, **used}, "samples": total, "t": times, "g": gaps, "w": width,
                "series": series, **_packet_view(pkts), "speed": _speed_view(pkts, start, end, points),
                "peak": {"t": peak[0], "amag": peak[1]} if peak else None,
                "extremes": {"pitch_abs": ext[0], "roll_abs": ext[1], "yaw_abs": ext[2]}
                if ext and ext[0] is not None else None}


def _speed_view(pkts: list[tuple], start: float, end: float, points: int) -> dict[str, Any]:
    """Speed over the range from every packet that reports it (IMU and NR alike): each packet's
    own value when they fit in ``points``, else min / max / mean per bucket — the same buckets
    as the samples, so the two line up."""
    pts = [(p[0], p[4]) for p in pkts if p[4] is not None]
    if len(pts) <= points:
        return {"t": [t for t, _ in pts], "mean": [v for _, v in pts], "w": None}
    width = (end - start) / points
    acc: dict[int, list[float]] = {}
    for t, v in pts:
        acc.setdefault(int((t - start) // width), []).append(v)
    keys = sorted(acc)
    return {"t": [start + (b + 0.5) * width for b in keys], "mean": [sum(acc[b]) / len(acc[b]) for b in keys],
            "min": [min(acc[b]) for b in keys], "max": [max(acc[b]) for b in keys], "w": width}


def _packet_view(pkts: list[tuple]) -> dict[str, Any]:
    """Markers for the time axis: non-IMU packets (with speed), history packets, and holes in
    the shared frame counter — the packets that never arrived — plus their counts."""
    events: list[dict] = []
    imu_packets = history = 0
    for t, kind, status, _frame, speed, ign, n_imu in pkts:
        if n_imu:
            imu_packets += 1
        if status == "H":
            history += 1
        if not n_imu or status == "H":
            events.append({"t": t, "kind": kind, "history": status == "H", "speed": speed,
                           "ignition": None if ign is None else bool(ign)})
    gaps, missing = [], 0
    framed = sorted((f, t) for t, *_rest in pkts if (f := _rest[2]) is not None)
    for (f0, t0), (f1, t1) in zip(framed, framed[1:]):
        if 1 < f1 - f0 < 100000:                  # a large jump is a counter reset, not a hole
            gaps.append({"from": t0, "to": t1, "frames": f1 - f0 - 1})
            missing += f1 - f0 - 1
    return {"packets": len(pkts), "imu_packets": imu_packets, "history_packets": history,
            "events": events[:5000], "gaps": gaps[:2000], "missing_frames": missing}


telemetry = TelemetryStore()
