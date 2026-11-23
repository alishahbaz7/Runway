"""The test plan of one firmware version — the tracker, kept and judged.

    data/fwtest/tracker/<plan> <firmware>.json    the tester's and the developers' entries per case:
        status, remarks / defect ID, tester, date, dev owner / status / version / remarks — and the
        case's step: when it ran, on which test, the window it is judged over, the command it sent

Every test (run) whose packets report that firmware version belongs to it. A case's result:

    automatic   plan.judge over each of those tests, combined (any test failing fails it)
    assisted /  judged over its step's window only — the packets that came between *Run* and its end
    command     (``Window``); until it runs, the tester's own status (or Not run)
    manual      the tester's status

``several servers`` (23.07, 25.05) is judged across the tests on different ports, over the time they
overlap: every frame number one server got, the others got too.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter
from pathlib import Path

from . import plan as plans

log = logging.getLogger("runway.fwtest")
STEP_WINDOW_S = 180                    # an assisted step is judged over this long after Run, unless it says
EDITABLE = ("status", "remarks", "tester", "dev_owner", "dev_status", "dev_fw", "dev_remarks")
STATUSES = ("pass", "fail", "partial", "not_run", "blocked", "na")


class Window:
    """A test seen through a time window: only the packets received between ``start`` and ``end``."""

    def __init__(self, gate, start: float, end: float):
        gate._derive()
        self.spec, self.config, self.firmware, self.learned = gate.spec, gate.config, gate.firmware, gate.learned
        self.rows = [r for r in gate.rows if r.get("received") and start <= r["received"] <= end]
        self.headers = Counter(r["header"] for r in self.rows)
        counts = Counter(k for r in self.rows for k in r.get("scen") or ())
        self.scenarios = {k: {**s, "count": counts.get(k, 0)} for k, s in gate.scenarios.items()}
        self.conns = Counter(r["conn"] or "" for r in self.rows)

    def _derive(self):
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", text).strip() or "unknown"


class Tracker:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.lock = threading.Lock()

    def _path(self, plan_name: str, firmware: str) -> Path:
        return self.root / f"{_slug(plan_name)} {_slug(firmware)}.json"

    def entries(self, plan_name: str, firmware: str) -> dict:
        p = self._path(plan_name, firmware)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, plan_name: str, firmware: str, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        p = self._path(plan_name, firmware)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(p)

    def update(self, plan_name: str, firmware: str, case_id: str, values: dict) -> dict:
        with self.lock:
            data = self.entries(plan_name, firmware)
            e = data.setdefault(case_id, {})
            for k in EDITABLE:
                if k in values:
                    v = str(values[k] or "").strip()
                    if k == "status" and v and v not in STATUSES:
                        raise ValueError(f"no status {v!r}")
                    if v:
                        e[k] = v
                    else:
                        e.pop(k, None)
            if "status" in values:
                e["date"] = time.strftime("%d-%b-%Y")
            self._save(plan_name, firmware, data)
            return e

    def start_step(self, plan_name: str, firmware: str, case_id: str, run_id: str, window_s: float | None = None,
                   command: str = "", now: float | None = None) -> dict:
        with self.lock:
            data = self.entries(plan_name, firmware)
            e = data.setdefault(case_id, {})
            start = now or time.time()
            e["step"] = {"run": run_id, "start": start, "end": start + (window_s or STEP_WINDOW_S)}
            if command:
                e["step"]["command"] = command
                e["command"] = command                    # kept for the next run of the case
            self._save(plan_name, firmware, data)
            return e

    def stop_step(self, plan_name: str, firmware: str, case_id: str, now: float | None = None) -> dict:
        with self.lock:
            data = self.entries(plan_name, firmware)
            e = data.get(case_id) or {}
            if e.get("step"):
                e["step"]["end"] = min(e["step"]["end"], now or time.time())
            self._save(plan_name, firmware, data)
            return e

    # ── scope: the cases that are part of testing (per plan, every firmware version alike) ─────────
    def _scope_path(self, plan_name: str) -> Path:
        return self.root / f"{_slug(plan_name)} scope.json"

    def out_of_scope(self, plan_name: str) -> set[str]:
        try:
            return set(json.loads(self._scope_path(plan_name).read_text(encoding="utf-8")).get("out") or [])
        except (OSError, ValueError):
            return set()

    def set_scope(self, plan_name: str, out: list[str]) -> list[str]:
        """The cases not part of testing — kept aside, never deleted: put back any time."""
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            p = self._scope_path(plan_name)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"out": sorted(set(out))}, indent=1), encoding="utf-8")
            tmp.replace(p)
        return sorted(set(out))

    # ── suites: bench steps one after another, each started when the one before is judged ─────────
    def start_suite(self, plan: dict, firmware: str, key: str, run_id: str, now: float | None = None) -> dict:
        suite = next((s for s in plan.get("suites") or [] if s["key"] == key), None)
        if suite is None:
            raise ValueError(f"no suite {key!r}")
        now = now or time.time()
        with self.lock:
            data = self.entries(plan["name"], firmware)
            data["_suite"] = {"key": key, "run": run_id, "i": 0, "started": now}
            self._save(plan["name"], firmware, data)
        step = suite["steps"][0]
        self.start_step(plan["name"], firmware, step["case"], run_id, step.get("window_s"), now=now)
        return self.entries(plan["name"], firmware)["_suite"]

    def stop_suite(self, plan: dict, firmware: str) -> None:
        with self.lock:
            data = self.entries(plan["name"], firmware)
            data.pop("_suite", None)
            self._save(plan["name"], firmware, data)

    def _advance(self, plan: dict, firmware: str, results: dict, now: float) -> dict | None:
        """The suite running: when its step is judged, the next one starts. Its state, for the page."""
        data = self.entries(plan["name"], firmware)
        su = data.get("_suite")
        if not su:
            return None
        suite = next((s for s in plan.get("suites") or [] if s["key"] == su["key"]), None)
        if suite is None:
            return None
        steps = suite["steps"]
        cur = steps[su["i"]] if su["i"] < len(steps) else None
        if cur and results.get(cur["case"], {}).get("status") not in ("running", None):
            su["i"] += 1
            with self.lock:
                d2 = self.entries(plan["name"], firmware)
                d2["_suite"] = su
                self._save(plan["name"], firmware, d2)
            if su["i"] < len(steps):
                nxt = steps[su["i"]]
                self.start_step(plan["name"], firmware, nxt["case"], su["run"], nxt.get("window_s"), now=now)
        return {"key": su["key"], "label": suite["label"], "i": su["i"], "done": su["i"] >= len(steps),
                "steps": [{**s, "status": results.get(s["case"], {}).get("status", "not_run")} for s in steps]}

    # ── reading ───────────────────────────────────────────────────────────────────────────────
    def view(self, plan: dict, firmware: str, runs: list, now: float | None = None, spec=None,
             include_out: bool = False) -> dict:
        """Every case in scope with its result; ``runs`` are this firmware's tests (Run objects).
        ``include_out``: the cases out of scope too (``in_scope`` False) — for choosing the scope."""
        now = now or time.time()
        data = self.entries(plan["name"], firmware)
        out_ids = self.out_of_scope(plan["name"])
        out_areas, tally = [], Counter()
        by_id = {r.id: r for r in runs}
        for area in plan["areas"]:
            rows = []
            for c in area["cases"]:
                if c["id"] in out_ids and not include_out:
                    continue
                e = data.get(c["id"], {}) if not c["id"].startswith("_") else {}
                try:
                    res = self._result(c, e, runs, by_id, now)
                except Exception as exc:              # one case that cannot be judged never breaks the plan
                    log.exception("could not judge case %s", c["id"])
                    res = {"status": "not_run", "note": f"Could not be judged: {exc.__class__.__name__}: {exc}",
                           "filter": {}, "source": ""}
                if c["id"] not in out_ids:
                    tally[res["status"]] += 1
                row = {**c, "result": res, "entry": {k: v for k, v in e.items() if k != "step"}, "step": e.get("step")}
                sp = spec or (runs[0].spec if runs else None)
                if sp is not None:
                    row["about"] = plans.describe(c, sp, area)
                row["in_scope"] = c["id"] not in out_ids
                rows.append(row)
            if rows:
                out_areas.append({**{k: v for k, v in area.items() if k != "cases"}, "cases": rows})
        kinds = Counter(c["kind"] for a in plan["areas"] for c in a["cases"] if c["id"] not in out_ids)
        results = {c["id"]: c["result"] for a in out_areas for c in a["cases"]}
        suite = self._advance(plan, firmware, results, now)
        return {"plan": plan["name"], "firmware": firmware, "areas": out_areas, "tally": dict(tally),
                "kinds": dict(kinds), "runs": [r.id for r in runs], "total": sum(kinds.values()), "suite": suite,
                "out_of_scope": len(out_ids & {c["id"] for a in plan["areas"] for c in a["cases"]}),
                "suites": [{"key": s["key"], "label": s["label"], "steps": len(s["steps"])} for s in plan.get("suites") or []]}

    def _result(self, case: dict, e: dict, runs: list, by_id: dict, now: float) -> dict:
        kind = case["kind"]
        j = case.get("judge") or {}
        step = e.get("step")
        if kind == "automatic":
            if j.get("measure") == "multi_server":
                return {**several_servers(runs), "source": "packets"}
            res = plans.combine([(r.id, plans.judge(case, r.gate)) for r in runs])
            return {**res, "source": "packets"}
        if step and step.get("run") in by_id:
            run = by_id[step["run"]]
            running = now < step["end"]
            if j.get("measure") == "multi_server":
                res = several_servers(runs, step["start"], min(now, step["end"]))
            elif j:
                with run.lock:
                    res = plans.judge(case, Window(run.gate, step["start"], min(now, step["end"])))
            else:
                res = {"status": "not_run", "note": "Nothing to judge it by in the packets", "filter": {}}
            if running and res["status"] != "pass":
                left = int(step["end"] - now)
                return {"status": "running", "note": f"Waiting for the packets — {left} s left. {res['note']}".strip(),
                        "filter": res.get("filter") or {}, "run": run.id, "source": "step"}
            if not running and res["status"] == "not_run":
                res = {**res, "status": "fail", "note": "Nothing came in the step's window. " + res["note"]}
            return {**res, "run": run.id, "source": "step"}
        if e.get("status"):
            return {"status": e["status"], "note": "", "filter": {}, "source": "tester"}
        return {"status": "not_run", "note": "", "filter": {}, "source": ""}


def several_servers(runs: list, start: float | None = None, end: float | None = None) -> dict:
    """The same device on several listeners (InTouch, government, emergency): over the time all of them
    took packets, every frame number one got, each of the others got too."""
    live = [r for r in runs if (r.meta.get("source") or {}).get("kind") == "live"]
    ports = {}
    for r in live:
        ports.setdefault(r.meta["source"]["port"], r)
    if len(ports) < 2:
        return {"status": "not_run", "note": "Needs tests on two or more servers' ports at once", "filter": {}}
    sets, spans = {}, []
    for port, r in ports.items():
        rows = [x for x in r.gate.rows if x.get("received") and (start is None or start <= x["received"] <= end)]
        if not rows:
            return {"status": "not_run", "note": f"No packets on :{port} in that time", "filter": {}}
        spans.append((rows[0]["received"], rows[-1]["received"]))
        sets[port] = {(x["header"], x["frame"], x["time"]): x["received"] for x in rows if x["frame"]}
    lo, hi = max(s for s, _ in spans), min(e for _, e in spans)
    if hi <= lo:
        return {"status": "not_run", "note": "The servers' tests do not overlap in time", "filter": {}}
    common = {k for s in sets.values() for k, t in s.items() if lo <= t <= hi}
    missing = {p: sum(1 for k in common if k not in s) for p, s in sets.items()}
    delays = []
    for k in common:
        times = [s[k] for s in sets.values() if k in s]
        if len(times) > 1:
            delays.append(max(times) - min(times))
    worst = max(delays) if delays else 0
    words = ", ".join(f":{p} missed {n}" for p, n in missing.items() if n)
    if words:
        return {"status": "fail", "note": f"{len(common):,} packets over the overlap; {words}", "filter": {}}
    return {"status": "pass", "note": f"{len(common):,} packets on every server; at most {worst:.1f} s apart", "filter": {}}
