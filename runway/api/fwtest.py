"""Firmware test — the routes the Firmware test page uses. The work is in ``runway/fwtest/``.

    GET    /api/fwtest/specs                   the test specs (one per device sheet)
    GET    /api/fwtest/listeners               the listeners running, with the devices on each
    GET    /api/fwtest/runs                    every run, with its summary
    POST   /api/fwtest/runs                    a live run: {spec, port, device, expect, title}
    POST   /api/fwtest/runs/file               a run over a capture (multipart "file", spec, device, expect_*)
    GET    /api/fwtest/runs/{id}               one run: its summary, checks, scenarios, where it listens
    GET    /api/fwtest/runs/{id}/packets       its packets, newest first (level=, check=, scenario=, offset=, limit=)
    GET    /api/fwtest/runs/{id}/packets/{n}   one packet, field by field
    PUT    /api/fwtest/runs/{id}/config        the device's settings (intervals, history order, format) or the
                                               values pinned, corrected: the test is judged again from its packets
    POST   /api/fwtest/runs/{id}/stop          stop taking packets (resume picks up again)
    POST   /api/fwtest/runs/{id}/resume
    DELETE /api/fwtest/runs/{id}
    GET    /api/fwtest/runs/{id}/report.xlsx   the run as an Excel workbook
    GET    /api/fwtest/runs/{id}/packets.xlsx?which=all|fail|warn   the packets of a set, explained, for a developer

    The test plan (the device's tracker) of one firmware version — every test reporting that version:
    GET    /api/fwtest/plan?firmware=          every case with its result (packets, its step, or the tester)
    GET    /api/fwtest/plan/firmwares          the versions there are tests of
    PUT    /api/fwtest/plan/case               {firmware, case, values}: status, remarks, tester, dev columns
    POST   /api/fwtest/plan/step               {firmware, case, run, window_s, command}: a bench step starts —
                                               judged over its window; a command is sent to the device first
    POST   /api/fwtest/plan/step/stop          {firmware, case}: the step ends now
    GET    /api/fwtest/plan.xlsx?firmware=     the tracker sheet, results filled in
    POST   /api/fwtest/plan/import             a tracker sheet (multipart "file", firmware): dev columns and results in

A live run is fed by a sink on whichever listener runs on its port — attached by a watcher every few
seconds, so a run carries on across a listener restarted in Capture or a Runway restart. The sink only
reads: the device's relay and ACKs are the listener's, untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..fwtest import diagnosis
from ..fwtest import plan as plans
from ..fwtest import report
from ..fwtest import spec as specs
from ..fwtest.runs import FirmwareTestSink, Runs
from ..fwtest.tracker import Tracker
from ..storage.db import DATA_DIR

log = logging.getLogger("runway.fwtest")
router = APIRouter()
runs = Runs(Path(DATA_DIR) / "fwtest", own_specs=Path(DATA_DIR) / "fwtest" / "specs")
tracker = Tracker(Path(DATA_DIR) / "fwtest" / "tracker")
_attached: dict[str, tuple[str, FirmwareTestSink]] = {}       # run id → (listener job id, its sink)
_watcher: asyncio.Task | None = None


def _app():
    from . import app as appmod          # the listeners live there; imported late (app imports this module)
    return appmod


def _listeners() -> list[dict]:
    a = _app()
    out = []
    for jid in list(a._pipelines):
        job = a._job_view(jid)
        if job.get("kind") != "listen" or job.get("status") != "running":
            continue
        out.append({"job": jid, "host": job.get("host"), "port": job.get("port"), "relay": job.get("relay") or "",
                    "clients": [{"device_id": c.get("device_id"), "peer": c.get("peer"), "connected": c.get("connected"),
                                 "last": c.get("last"), "protocol": c.get("protocol")} for c in job.get("clients") or []]})
    return sorted(out, key=lambda x: x["port"] or 0)


def _attach_all() -> None:
    """Every live run on the listener of its port; stopped runs and vanished listeners let go."""
    a = _app()
    by_port = {}
    for jid, pipe in a._pipelines.items():
        job = a._jobs.get(jid) or {}
        if job.get("kind") == "listen" and job.get("status") == "running":
            by_port[job.get("port")] = (jid, pipe)
    for run in runs.all():
        rid = run.id
        live = run.meta.get("state") == "live" and (run.meta.get("source") or {}).get("kind") == "live"
        want = by_port.get((run.meta.get("source") or {}).get("port")) if live else None
        have = _attached.get(rid)
        if have and (not want or want[0] != have[0]):
            pipe = a._pipelines.get(have[0])
            if pipe is not None:
                pipe.sinks = [s for s in pipe.sinks if s is not have[1]]
                pipe.skip_taps = [t for t in getattr(pipe, "skip_taps", []) if t != have[1].skipped]
            _attached.pop(rid, None)
            have = None
        if want and not have:
            sink = FirmwareTestSink(run)
            want[1].sinks = [*want[1].sinks, sink]       # a new list: the pipeline may be mid-way through the old one
            want[1].skip_taps = [*getattr(want[1], "skip_taps", []), sink.skipped]
            _attached[rid] = (want[0], sink)
            log.info("firmware test %s takes packets from the listener on :%s", rid, run.meta["source"]["port"])


async def _watch() -> None:
    while True:
        try:
            _attach_all()
            _soak_reports()
        except Exception:                      # noqa: BLE001 — the watcher must outlive one bad turn
            log.exception("firmware test watcher")
        await asyncio.sleep(3)


def start() -> None:
    global _watcher
    runs.load()
    if _watcher is None:
        _watcher = asyncio.get_running_loop().create_task(_watch())


def stop() -> None:
    global _watcher
    if _watcher is not None:
        _watcher.cancel()
        _watcher = None
    for run in runs.all():
        run.close()


def _view(run) -> dict:
    v = run.view()
    att = _attached.get(run.id)
    v["listening"] = bool(att)
    return v


def _run(rid: str):
    run = runs.get(rid)
    if run is None:
        raise HTTPException(404, "no such test")
    return run


@router.get("/api/fwtest/specs")
async def fwtest_specs():
    out = []
    for name, p in specs.available(runs.own_specs).items():
        s = specs.load(name, runs.own_specs)
        out.append({"name": s.name, "about": s.about, "packets": [p.label for p in s.packets.values()],
                    "scenarios": len(s.scenarios), "expect": s.expect, "config": specs.clean_config(s, None),
                    "formats": s.formats})
    return out


@router.get("/api/fwtest/listeners")
async def fwtest_listeners():
    return _listeners()


@router.get("/api/fwtest/runs")
async def fwtest_runs():
    return [_view(r) for r in runs.all()]


class LiveBody(BaseModel):
    spec: str
    port: int
    device: str = ""
    title: str = ""
    expect: dict = {}
    config: dict = {}           # the device's settings: ign_on_s, ign_off_s, sleep_after_min, sleep_s, history_order, formats


@router.post("/api/fwtest/runs")
async def fwtest_start(body: LiveBody):
    try:
        run = runs.create(body.spec, {"kind": "live", "port": body.port}, body.expect, body.device, body.title,
                          config=body.config)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _attach_all()
    _usage("fwTestsStarted")
    return _view(run)


@router.post("/api/fwtest/runs/file")
async def fwtest_file(file: UploadFile = File(...), spec: str = Form(...), device: str = Form(""),
                      title: str = Form(""), expect_imei: str = Form(""), expect_vehicle_reg_no: str = Form(""),
                      expect_firmware_version: str = Form(""), config: str = Form("")):
    a = _app()
    data = await file.read()
    raw = a._input_bytes(data.decode("utf-8", "surrogateescape"))
    expect = {"imei": expect_imei, "vehicle_reg_no": expect_vehicle_reg_no, "firmware_version": expect_firmware_version}
    try:
        cfg = json.loads(config) if config.strip() else {}
        run = runs.create(spec, {"kind": "file", "name": file.filename or "capture"}, expect, device,
                          title or (file.filename or ""), config=cfg if isinstance(cfg, dict) else {})
    except ValueError as exc:                        # json.JSONDecodeError is one too
        raise HTTPException(400, str(exc))
    count = await asyncio.to_thread(_feed_file, run, raw)
    if not count:
        runs.delete(run.id)
        raise HTTPException(400, "no packets in this file that the spec's protocol reads")
    _usage("fwTestsStarted")
    return _view(run)


def _feed_file(run, raw: bytes) -> int:
    """Frame the capture with the spec's codec — the way a listener would — and feed each packet."""
    from ..core import registry
    from ..core.models import Session
    codec = registry.get_codec(run.spec.codec or "ais140")
    sess = Session(id="fwtest", transport="file")
    dev = run.meta.get("device")
    buf, n = raw, 0
    while buf:
        out = codec.decode(buf, sess)
        if out.consumed <= 0:
            if buf.strip() and n:                 # a last sentence never finished: kept, and failed
                run.add(buf, None, None, skipped=True)
            break
        if not out.records and buf[:out.consumed].strip():
            run.add(buf[:out.consumed], None, None, skipped=True)      # bytes that are not a packet
        for rec in out.records:
            if rec.raw_hex and (not dev or rec.device_id == dev):
                run.add(bytes.fromhex(rec.raw_hex), None, None)
                n += 1
        buf = buf[out.consumed:]
    run.meta["stopped"] = time.time()
    run.save()
    return n


@router.get("/api/fwtest/runs/{rid}")
async def fwtest_run(rid: str):
    return _view(_run(rid))


@router.get("/api/fwtest/runs/{rid}/packets")
async def fwtest_packets(rid: str, level: str = "", check: str = "", scenario: str = "",
                         offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    run = _run(rid)
    with run.lock:
        return run.gate.packets(level, check, scenario, offset, limit)


@router.get("/api/fwtest/runs/{rid}/packets/{n}")
async def fwtest_packet(rid: str, n: int):
    run = _run(rid)
    with run.lock:
        d = run.gate.detail(n)
    if d is None:
        raise HTTPException(404, "no such packet")
    return d


class ConfigBody(BaseModel):
    config: dict | None = None
    expect: dict | None = None


@router.put("/api/fwtest/runs/{rid}/config")
async def fwtest_config(rid: str, body: ConfigBody):
    run = _run(rid)
    await asyncio.to_thread(run.reconfigure, body.config, body.expect)
    return _view(run)


@router.post("/api/fwtest/runs/{rid}/stop")
async def fwtest_stop(rid: str):
    run = _run(rid)
    run.meta.update(state="stopped", stopped=time.time())
    run.save()
    _attach_all()
    if run.gate.rows:
        await asyncio.to_thread(_save_report, run, "stopped")
    return _view(run)


@router.post("/api/fwtest/runs/{rid}/resume")
async def fwtest_resume(rid: str):
    run = _run(rid)
    if (run.meta.get("source") or {}).get("kind") != "live":
        raise HTTPException(400, "a test of a file has nothing to resume")
    run.meta.update(state="live", stopped=None)
    run.save()
    _attach_all()
    return _view(run)


@router.delete("/api/fwtest/runs/{rid}")
async def fwtest_delete(rid: str):
    run = _run(rid)
    run.meta["state"] = "stopped"
    _attach_all()
    runs.delete(rid)
    return {"deleted": rid}


@router.get("/api/fwtest/runs/{rid}/report.xlsx")
async def fwtest_report(rid: str):
    run = _run(rid)
    v = run.view()
    v["defects"] = (await asyncio.to_thread(_run_defects, run))["defects"]
    data = await asyncio.to_thread(report.workbook, v, run.gate)
    name = f"Firmware test {run.meta.get('device') or ''} {time.strftime('%d-%m-%Y', time.localtime(run.meta['created']))}"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{" ".join(name.split())}.xlsx"'})


@router.get("/api/fwtest/runs/{rid}/packets.xlsx")
async def fwtest_packets_xlsx(rid: str, which: str = Query("all", pattern="^(all|fail|warn)$")):
    """The packets of a set — all, failed, with warnings — each with what is wrong, its cause and fix,
    and every field: the file a developer is handed."""
    run = _run(rid)
    v = run.view()
    data = await asyncio.to_thread(report.packets_workbook, v, run.gate, which)
    words = {"all": "all packets", "fail": "failed packets", "warn": "packets with warnings"}[which]
    name = f"Firmware test {run.meta.get('device') or ''} {time.strftime('%d-%m-%Y', time.localtime(run.meta['created']))} {words}"
    _usage("fwtestPacketsExport")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{" ".join(name.split())}.xlsx"'})


def _usage(name: str) -> None:
    try:
        from .. import usage
        usage.count(name)
    except Exception:                      # noqa: BLE001 — counting never stands in the way
        pass


# ── the test plan of a firmware version ──────────────────────────────────────────────────────────
def _firmware_of(run) -> str:
    fw = run.gate.firmware
    return max(fw, key=fw.get) if fw else ""


def _plan_for(firmware: str):
    """The plan, and this firmware's tests (the plan is the one named like their spec)."""
    tests = [r for r in runs.all() if _firmware_of(r) == firmware]
    spec_name = tests[0].spec.name if tests else "Locate 140 4G"
    try:
        return plans.load(spec_name, runs.own_specs.parent / "plans" if runs.own_specs else None), tests
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/api/fwtest/plan/firmwares")
async def fwtest_plan_firmwares():
    seen: dict[str, int] = {}
    for r in runs.all():
        fw = _firmware_of(r)
        if fw:
            seen[fw] = seen.get(fw, 0) + 1
    return [{"firmware": fw, "tests": n} for fw, n in sorted(seen.items())]


@router.get("/api/fwtest/plan")
async def fwtest_plan(firmware: str, all: bool = False):
    plan, tests = _plan_for(firmware)
    return await asyncio.to_thread(lambda: tracker.view(plan, firmware, tests, include_out=all))


class ScopeBody(BaseModel):
    firmware: str
    out: list[str]


@router.put("/api/fwtest/plan/scope")
async def fwtest_plan_scope(body: ScopeBody):
    """The cases not part of testing, for the whole plan (every firmware version alike) — kept aside."""
    plan, _ = _plan_for(body.firmware)
    known = {c["id"] for c in plans.cases(plan)}
    unknown = [c for c in body.out if c not in known]
    if unknown:
        raise HTTPException(400, "no such case: " + ", ".join(unknown[:5]))
    out = tracker.set_scope(plan["name"], body.out)
    return {"out": out, "in_scope": len(known) - len(out)}


class CaseBody(BaseModel):
    firmware: str
    case: str
    values: dict


@router.put("/api/fwtest/plan/case")
async def fwtest_plan_case(body: CaseBody):
    plan, _ = _plan_for(body.firmware)
    if body.case not in {c["id"] for c in plans.cases(plan)}:
        raise HTTPException(404, "no such case")
    try:
        return tracker.update(plan["name"], body.firmware, body.case, body.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class StepBody(BaseModel):
    firmware: str
    case: str
    run: str
    window_s: float | None = None
    command: str = ""
    hold_ack: bool = False      # Runway does not acknowledge the device's emergency packets during the step


@router.post("/api/fwtest/plan/step")
async def fwtest_plan_step(body: StepBody):
    plan, tests = _plan_for(body.firmware)
    run = next((r for r in tests if r.id == body.run), None) or runs.get(body.run)
    if run is None:
        raise HTTPException(404, "no such test")
    if body.command.strip():
        await _send_to_device(run, body.command.strip())
    entry = tracker.start_step(plan["name"], body.firmware, body.case, run.id, body.window_s, body.command.strip())
    if body.hold_ack:
        _hold_ack(run, entry["step"]["end"])
    return entry


EMERGENCY = ("$EPB,", ",EA,")


def _hold_ack(run, until: float) -> None:
    """Until ``until``, the listener does not acknowledge this device's emergency packets (EPB, EA) — so a
    step sees whether the device sends them again. Only where Runway answers the device itself: a relayed
    device is acknowledged by its platform."""
    a = _app()
    att = _attached.get(run.id)
    pipe = a._pipelines.get(att[0]) if att else None
    if pipe is None:
        raise HTTPException(400, "the test is not taking packets from a listener now")
    dev = run.meta.get("device")

    def allow(frame, records) -> bool:
        if time.time() > until:
            pipe.ack_filter = None
            return True
        for rec in records:
            raw = bytes.fromhex(rec.raw_hex or "").decode("ascii", "replace")
            if rec.device_id == dev and any(m in raw[:40] for m in EMERGENCY):
                return False
        return True

    pipe.ack_filter = allow


class StopBody(BaseModel):
    firmware: str
    case: str


@router.post("/api/fwtest/plan/step/stop")
async def fwtest_plan_step_stop(body: StopBody):
    plan, _ = _plan_for(body.firmware)
    return tracker.stop_step(plan["name"], body.firmware, body.case)


async def _send_to_device(run, command: str) -> None:
    """A command to the test's device, on the connection it has now (as Capture's Send does)."""
    a = _app()
    att = _attached.get(run.id)
    pipe = a._pipelines.get(att[0]) if att else None
    if pipe is None or not hasattr(pipe.source, "send"):
        raise HTTPException(400, "the test is not taking packets from a listener now")
    job = a._job_view(att[0])
    peer = next((c["peer"] for c in reversed(job.get("clients") or [])
                 if c.get("connected") and c.get("device_id") == run.meta.get("device")), None)
    if not peer:
        raise HTTPException(400, "the device is not connected now")
    from ..core import registry
    cls = registry.CODECS.get(run.spec.codec or "")
    builder = getattr(cls, "build_command", None) if cls else None
    data = builder(command) if builder else (command if command.endswith("\r\n") else command + "\r\n").encode("ascii", "replace")
    if not await pipe.source.send(peer, data):
        raise HTTPException(400, f"the device on {peer} could not be reached")


@router.get("/api/fwtest/plan.xlsx")
async def fwtest_plan_xlsx(firmware: str):
    plan, view, found = await asyncio.to_thread(_plan_defects, firmware)
    view["defects"] = found
    view["gate"] = diagnosis.release_gate(view, found)
    data = await asyncio.to_thread(report.tracker_workbook, view)
    name = f"{plan['name']} - Tracker {firmware}"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})


@router.post("/api/fwtest/plan/import")
async def fwtest_plan_import(file: UploadFile = File(...), firmware: str = Form(...)):
    plan, _ = _plan_for(firmware)
    try:
        found = await asyncio.to_thread(report.read_tracker, await file.read())
    except Exception as exc:                    # noqa: BLE001 — not a workbook, or not the tracker
        raise HTTPException(400, f"not a tracker sheet: {exc}")
    known = {c["id"]: c for c in plans.cases(plan)}
    taken = 0
    for cid, values in found.items():
        if cid not in known:
            continue
        if known[cid]["kind"] == "automatic":
            values = {k: v for k, v in values.items() if k != "status"}     # judged from the packets
        if values:
            tracker.update(plan["name"], firmware, cid, values)
            taken += 1
    return {"cases": taken}


# ── defects: what is wrong, why, where, how to fix ─────────────────────────────────────────────
def _run_defects(run, plan=None) -> dict:
    if plan is None:
        try:
            plan = plans.load(run.spec.name, runs.own_specs.parent / "plans" if runs.own_specs else None)
        except ValueError:
            plan = None
    with run.lock:
        return diagnosis.defects(run.gate, plan, _firmware_of(run), run.meta.get("device") or "")


@router.get("/api/fwtest/runs/{rid}/defects")
async def fwtest_run_defects(rid: str):
    run = _run(rid)
    res = await asyncio.to_thread(_run_defects, run)
    for d in res["defects"]:
        d["report"] = diagnosis.report_text(d)
    return res


def _plan_defects(firmware: str):
    plan, tests = _plan_for(firmware)
    view = tracker.view(plan, firmware, tests)
    found = diagnosis.merge([_run_defects(r, plan)["defects"] for r in tests])
    return plan, view, found


@router.get("/api/fwtest/plan/defects")
async def fwtest_plan_defects(firmware: str):
    plan, view, found = await asyncio.to_thread(_plan_defects, firmware)
    for d in found:
        d["report"] = diagnosis.report_text(d)
    return {"firmware": firmware, "defects": found, "gate": diagnosis.release_gate(view, found),
            "areas": diagnosis._areas(found)}


@router.get("/api/fwtest/plan/compare")
async def fwtest_plan_compare(a: str, b: str):
    _, va, da = await asyncio.to_thread(_plan_defects, a)
    _, vb, db = await asyncio.to_thread(_plan_defects, b)
    return diagnosis.compare(va, vb, da, db)


# ── suites, the timeline ─────────────────────────────────────────────────────────────────────────
class SuiteBody(BaseModel):
    firmware: str
    suite: str = ""
    run: str = ""


@router.post("/api/fwtest/plan/suite")
async def fwtest_plan_suite(body: SuiteBody):
    plan, tests = _plan_for(body.firmware)
    run = runs.get(body.run)
    if run is None:
        raise HTTPException(404, "no such test")
    try:
        return tracker.start_suite(plan, body.firmware, body.suite, run.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/fwtest/plan/suite/stop")
async def fwtest_plan_suite_stop(body: SuiteBody):
    plan, _ = _plan_for(body.firmware)
    tracker.stop_suite(plan, body.firmware)
    return {"stopped": True}


@router.get("/api/fwtest/runs/{rid}/timeline")
async def fwtest_timeline(rid: str, points: int = Query(1500, ge=50, le=5000),
                          start: float | None = None, end: float | None = None):
    """The test on one line: every packet (thinned to ``points``, failures always kept) with when it came,
    live or history, its result and ignition; the connections. ``start``/``end`` (epoch s): only the packets
    that reached Runway in that window — a zoomed timeline's detail. ``thinned``: packets were left out.
    A capture (a file test) has no arrival times: its packets stand at the device's own time, in that order
    (``axis``: ``device``)."""
    run = _run(rid)
    with run.lock:
        run.gate._derive()
        live = any(r.get("received") for r in run.gate.rows)
        key = "received" if live else "t"
        rows = [r for r in run.gate.rows if r.get(key)]
        if not live:
            rows.sort(key=lambda r: r["t"])
        conns = {}
        conn_of = [conns.setdefault(r["conn"] or "", len(conns)) for r in rows]   # numbered over the whole test
        sel = [i for i, r in enumerate(rows)
               if (start is None or r[key] >= start) and (end is None or r[key] <= end)]
        step = max(1, len(sel) // points)
        out = []
        for j, i in enumerate(sel):
            r = rows[i]
            if j % step == 0 or r["level"] == "fail" or r["header"] not in ("PVT",):
                out.append([r["n"], round(r[key], 1), r["status"] or "", r["level"], r.get("ign") or "",
                            conn_of[i], r["header"], r["type"] or ""])
    return {"points": out, "connections": len(conns), "thinned": step > 1, "axis": "arrival" if live else "device"}


# ── sheets in, the platform's record, saved reports ─────────────────────────────────────────────
@router.post("/api/fwtest/specs/import")
async def fwtest_spec_import(file: UploadFile = File(...), spec: str = Form(...), header: str = Form("PVT"),
                             source: str = Form("")):
    """A protocol sheet (xlsx) in: the packet ``header``'s fields rebuilt from it, kept as the person's own
    copy of the spec (data/fwtest/specs) — and what changed."""
    from ..fwtest import sheets
    import json as _json
    p = specs.available(runs.own_specs).get(spec)
    if p is None:
        raise HTTPException(404, f"no test spec named {spec!r}")
    try:
        rows = await asyncio.to_thread(sheets.read_sheet, await file.read())
    except Exception as exc:                                  # noqa: BLE001 — not a workbook, or no Field column
        raise HTTPException(400, f"not a protocol sheet: {exc}")
    data = _json.loads(p.read_text(encoding="utf-8"))
    header = header.strip().upper() or "PVT"
    new, diff = sheets.spec_from_sheet(data, header, rows, source.strip() or f"{spec} {header} sheet ({file.filename})")
    try:
        specs.parse(new, p.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    runs.own_specs.mkdir(parents=True, exist_ok=True)
    (runs.own_specs / p.name).write_text(_json.dumps(new, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"spec": spec, "header": header, **diff}


@router.get("/api/fwtest/analyses")
async def fwtest_analyses(device: str = ""):
    """Platform drive logs analysed in Log analysis — this device's first."""
    from ..analysis import store as an_store
    items = await asyncio.to_thread(an_store.entries)
    out = [{"id": e.get("id"), "device": e.get("device"), "file": e.get("file"), "from": e.get("from"), "to": e.get("to")}
           for e in items]
    return sorted(out, key=lambda e: (e["device"] != device, str(e.get("from") or "")))


class PlatformBody(BaseModel):
    analysis: str


@router.post("/api/fwtest/runs/{rid}/platform")
async def fwtest_platform(rid: str, body: PlatformBody):
    """The test against the platform's drive log (Log analysis): every packet stored, read the same (23.06)."""
    from ..analysis import store as an_store
    from ..fwtest import sheets
    run = _run(rid)
    try:
        lg = await asyncio.to_thread(an_store.log, body.analysis)
    except Exception as exc:                                  # noqa: BLE001
        raise HTTPException(404, f"no such platform log: {exc}")
    with run.lock:
        run.gate._derive()
        res = sheets.platform_match(run.gate.rows, lg.rows)
        res["analysis"] = body.analysis
        run.meta["platform"] = res
        run.gate.platform = res
        run.save()
    return res


def _save_report(run, why: str) -> str | None:
    """The test's report written into its folder — every 24 h of a live test, and when it stops."""
    try:
        v = run.view()
        v["defects"] = _run_defects(run)["defects"]
        data = report.workbook(v, run.gate)
    except Exception:                                         # noqa: BLE001 — a report never stops a test
        log.exception("could not write the report of %s", run.id)
        return None
    d = run.folder / "reports"
    d.mkdir(exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M", time.localtime()) + f" {why}.xlsx"
    (d / name).write_bytes(data)
    run.meta["last_report"] = time.time()
    run.save()
    return name


REPORT_EVERY_S = 24 * 3600


def _soak_reports() -> None:
    now = time.time()
    for run in runs.all():
        if run.meta.get("state") != "live" or not run.gate.rows:
            continue
        if now - (run.meta.get("last_report") or run.meta.get("created") or now) >= REPORT_EVERY_S:
            _save_report(run, "day")


@router.get("/api/fwtest/runs/{rid}/reports")
async def fwtest_reports(rid: str):
    run = _run(rid)
    d = run.folder / "reports"
    return sorted((p.name for p in d.glob("*.xlsx")), reverse=True) if d.is_dir() else []


@router.get("/api/fwtest/runs/{rid}/reports/{name}")
async def fwtest_report_file(rid: str, name: str):
    run = _run(rid)
    p = run.folder / "reports" / name
    if "/" in name or "\\" in name or not p.is_file():
        raise HTTPException(404, "no such report")
    return Response(p.read_bytes(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
