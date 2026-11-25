"""The gate against whole devices: a correct Locate 140 4G must pass with no flag at all (no false
failure), and the same device with every known fault must have each one found, explained, and, where
Runway can prove the cause, proved. Plus the release gate, firmware against firmware, the test plan's
descriptions and suites, a protocol sheet in, and the platform's record compared. All built in code."""

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fwsim import CONFIG, FAULTS, Device, day

from runway.fwtest import diagnosis, insights, sheets
from runway.fwtest import plan as plans
from runway.fwtest.gate import Gate
from runway.fwtest.runs import Runs
from runway.fwtest.spec import SHIPPED, load
from runway.fwtest.tracker import Tracker

SPEC = load("Locate 140 4G")
PLAN = plans.load("Locate 140 4G")
CASE = {c["id"]: c for c in plans.cases(PLAN)}


def run_gate(dev, config=CONFIG):
    g = Gate(SPEC, config=config)
    for raw, received, conn, skipped in day(dev):
        g.feed(raw, received, conn, skipped=skipped)
    g.summary()
    return g


def flags(g):
    return [(r["n"], f["key"], f["text"]) for r in g.rows for f in r["flags"]]


# ── a correct device: nothing may be flagged ──────────────────────────────────────────────────────
def test_a_correct_device_passes_with_no_flag_at_all():
    g = run_gate(Device())
    # the one flag a correct day carries: TL on MoveE's event ID, a warning the spec declares for now
    interim = [f for r in g.rows if r["type"] == "TL" for f in r["flags"]]
    assert interim and all((f["key"], f["level"]) == ("alert_type", "warn") for f in interim)
    assert [f for f in flags(g) if not (f[1] == "alert_type" and "MoveE" in f[2])] == []
    s = g.summary()
    assert s["verdict"] == "pass" and s["scenarios_seen"] == s["scenarios_total"] == 28
    assert set(s["checksum"]) == {"ok"}


def test_a_correct_device_has_no_defect_and_no_failing_case():
    g = run_gate(Device())
    # the one defect: TL on MoveE's event ID for now — minor, and said as such
    assert [(d["key"], d["why"], d["severity"]) for d in diagnosis.defects(g, PLAN)["defects"]] == [("alert_type", "interim", "minor")]
    auto = [c for c in plans.cases(PLAN) if c["kind"] == "automatic"]
    failing = [(c["id"], plans.judge(c, g)["note"]) for c in auto if plans.judge(c, g)["status"] in ("fail", "partial")]
    assert failing == []
    passing = {c["id"] for c in auto if plans.judge(c, g)["status"] == "pass"}
    for must in ("01.06", "01.12", "01.13", "03.01", "03.02", "03.03", "03.05", "09.01", "10.01", "14.06", "16.01",
                 "16.02", "23.01", "23.02", "23.03", "23.04", "23.05", "24.01", "24.02", "24.03", "25.01", "25.02",
                 "25.03", "25.04"):
        assert must in passing, (must, plans.judge(CASE[must], g))
    # the simulated day toggles digital input 2 and analog input 1 only: exactly the rest is said to be left
    assert [w for w, _ in insights.untried(g)] == ["digital input 1 changing", "digital input 3 changing",
                                                  "digital input 4 changing", "analog input 2 changing"]


# ── each known fault, alone: found, and explained ─────────────────────────────────────────────────
EXPECT = {
    "kept_live": ("checksum", "kept_live", True), "login_star": ("checksum", "star_included", True),
    "pvt_dollar": ("checksum", "dollar_included", True),
    "hel_no_cs": ("checksum", "missing", True), "fields51": ("field_count", None, True),
    "short_date": ("field.date", None, False), "broken_time": ("field.time", None, False),
    "lifo": ("history_order", None, True), "no_login_on_reconnect": ("login_first", None, True),
    "repeat_frame": ("frame_live", None, True), "bd_power_1": ("consistency", None, True),
    "wrong_imei": ("identity", None, True), "garbage": ("not_packet", None, True), "cut": ("not_packet", None, True),
    "unknown": ("unknown_packet", None, True), "stale_live": ("live_stale", None, True),
    "time_repeat": ("time_repeat", "duplicate", True), "stuck_clock": ("time_repeat", "clock", False),
}


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_each_fault_is_found_and_explained(fault):
    key, why, proved = EXPECT[fault]
    g = run_gate(Device(faults={fault}))
    found = diagnosis.defects(g, PLAN)["defects"]
    d = next((d for d in found if d["key"] == key and d["why"] == why), None)
    assert d is not None, (fault, [(x["key"], x["why"]) for x in found])
    assert d["proved"] is proved and d["cause"] and d["fix"] and d["impact"]
    assert "Fix:" in diagnosis.report_text(d)
    if key in g.spec.levels:
        # the device's spec weighs this check a warning: each such packet is a warning that was a failure,
        # and every plan case that names the check still fails on it — the release gate still sees it
        flags = [f for r in g.rows for f in r["flags"] if f["key"] == key]
        assert flags and all(f["level"] == "warn" and f.get("was") == "fail" for f in flags)
        named = [c for c in plans.cases(PLAN) if key in (c.get("judge") or {}).get("checks", [])]
        assert all(plans.judge(c, g)["status"] == "fail" for c in named), [c["id"] for c in named]
    else:
        assert g.summary()["verdict"] == "fail"


def test_every_fault_at_once_fix_first_and_the_release_gate(tmp_path):
    runs, tr = Runs(tmp_path / "runs"), Tracker(tmp_path / "tracker")
    good = runs.create("Locate 140 4G", {"kind": "live", "port": 6001}, config=CONFIG)
    bad = runs.create("Locate 140 4G", {"kind": "live", "port": 6002}, config=CONFIG)
    for r, dev in ((good, Device(fw="V7.6.0")), (bad, Device(faults=set(FAULTS), fw="V7.6.1"))):
        for raw, received, conn, skipped in day(dev):
            r.add(raw, received, conn, skipped=skipped)
    found = diagnosis.defects(bad.gate, PLAN)
    keys = {(d["key"], d["why"]) for d in found["defects"]}
    for key, why, _ in EXPECT.values():
        assert (key, why) in keys, (key, why)
    ff = found["fix_first"]
    assert ff["causes"] and ff["explained"] >= 0.8 * ff["failing"]
    assert found["areas"]["history"]["defects"] >= 3                       # the history path, named as such
    va, vb = tr.view(PLAN, "V7.6.0", [good]), tr.view(PLAN, "V7.6.1", [bad])
    da, db = diagnosis.defects(good.gate, PLAN)["defects"], found["defects"]
    gate_bad = diagnosis.release_gate(vb, db)
    assert not gate_bad["ready"] and any("critical defect" in r for r in gate_bad["reasons"])
    cmp = diagnosis.compare(va, vb, da, db)
    assert cmp["regressed"] and len(cmp["defects"]["new"]) == len(db) - len(da) and not cmp["defects"]["fixed"]
    assert {r["id"] for r in cmp["regressed"]} >= {"23.01", "23.05"}


def test_no_pass_without_the_configuration():
    s = run_gate(Device(), config={}).summary()
    assert s["verdict"] == "incomplete" and set(s["unchecked"]) == {"ign_on_s", "ign_off_s", "history_order"}


def test_the_interval_set_is_held():
    g = run_gate(Device(), config={**CONFIG, "ign_on_s": 20})              # the device reports every 10 s
    early = [f for r in g.rows for f in r["flags"] if f["key"] == "interval"]
    assert early and all(f["why"] == "early" and f["state"] == "ignition on" for f in early)


# ── the test plan: every case described, suites whole ─────────────────────────────────────────────
def test_every_case_has_its_description_and_the_suites_are_whole():
    assert plans.check_plan(PLAN, SPEC) == []
    for a in PLAN["areas"]:
        for c in a["cases"]:
            d = plans.describe(c, SPEC, a)
            assert d["test"].startswith(c["id"]) and d["expected"] and d["accept"] and d["how"], c["id"]
    assert {s["key"] for s in PLAN["suites"]} >= {"power", "ignition", "sos", "tamper", "driving", "network"}


def test_a_suite_runs_its_steps_in_order(tmp_path):
    runs, tr = Runs(tmp_path / "runs"), Tracker(tmp_path / "tracker")
    run = runs.create("Locate 140 4G", {"kind": "live", "port": 6001}, config=CONFIG)
    T = 1795000000.0
    tr.start_suite(PLAN, "V7.6.0", "power", run.id, now=T)
    dev = Device()
    dev.connect(T)
    dev.alert(T + 20, "BD", "03", power="0")
    for raw, received, conn, _ in dev.events:
        run.add(raw, received, conn)
    v = tr.view(PLAN, "V7.6.0", [run], now=T + 30)                       # step 1 judged: step 2 starts
    assert v["suite"]["i"] == 1 and v["suite"]["steps"][0]["status"] == "pass"
    dev.events.clear()
    dev.alert(T + 60, "BR", "06", power="1")
    for raw, received, conn, _ in dev.events:
        run.add(raw, received, conn)
    v = tr.view(PLAN, "V7.6.0", [run], now=T + 70)
    assert v["suite"]["done"] and [s["status"] for s in v["suite"]["steps"]] == ["pass", "pass"]


# ── a protocol sheet in, the platform's record compared ───────────────────────────────────────────
def test_a_protocol_sheet_rebuilds_the_spec_and_says_what_changed():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Sr No", "Field", "Datatype", "Packet"])
    sheet = [("Start Character", "String", "$"), ("Header", "String", "PVT"), ("Vendor ID", "String", "GTRP"),
             ("Firmware Version", "String", "V7.6.0"), ("IMEI", "Integer", "356938035643809"),
             ("Date", "Integer", "29072026"), ("Time", "Integer", "035636"), ("Speed", "Float", "0"),
             ("New Field", "Integer", "5"), ("Checksum", "String", "85")]
    for i, (f, dt, ex) in enumerate(sheet, 1):
        ws.append([i, f, dt, ex])
    buf = io.BytesIO()
    wb.save(buf)
    rows = sheets.read_sheet(buf.getvalue())
    spec = json.loads((SHIPPED / "locate140_4g.json").read_text(encoding="utf-8"))
    new, diff = sheets.spec_from_sheet(spec, "PVT", rows, "Test sheet")
    fields = new["packets"]["PVT"]["field_list"]
    assert [f["name"] for f in fields] == ["vendor_id", "firmware_version", "imei", "date", "time", "speed", "new_field"]
    by = {f["name"]: f for f in fields}
    assert by["vendor_id"]["rules"] == {"equals": "GTRP"}                 # a field the spec knew keeps its rules
    assert by["new_field"]["rules"] == {"int": True} and by["new_field"]["source"] == "Test sheet, field 7"
    assert "New Field" in diff["added"] and diff["fields"] == 7 and diff["was"] == 51


def test_the_platform_record_is_compared():
    g = run_gate(Device())
    pvt = [r for r in g.rows if r["header"] == "PVT"]
    ist = timezone(timedelta(hours=5, minutes=30))
    log = [{"t": datetime.fromtimestamp(r["t"], ist).replace(tzinfo=None), "lat": r["lat"], "lon": r["lon"],
            "speed": r["speed"], "ign": int(r["ign"])} for r in pvt]
    assert sheets.platform_match(g.rows, log)["status"] == "pass"
    lost = log[:10] + log[11:]
    lost[20] = {**lost[20], "speed": lost[20]["speed"] + 20}
    res = sheets.platform_match(g.rows, lost)
    assert res["status"] == "fail" and res["missing"] == 1 and res["differ"] == 1
