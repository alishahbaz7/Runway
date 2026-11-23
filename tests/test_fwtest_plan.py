"""The test plan — the Locate 140 4G tracker as data, judged from packets, kept per firmware version:
automatic cases from every test of the version, bench steps over their window, several servers compared,
the ACK held back on purpose, and the tracker sheet out and in. Packets are built in code (made-up IMEI)."""

import io

import pytest
from test_fwtest import IMEI, T0, login, pvt, sentence

from runway.fwtest import plan as plans
from runway.fwtest.gate import Gate
from runway.fwtest.runs import Runs
from runway.fwtest.spec import load
from runway.fwtest.tracker import Tracker, Window, several_servers

PLAN = plans.load("Locate 140 4G")
CASES = {c["id"]: c for c in plans.cases(PLAN)}


def test_the_tracker_is_whole():
    assert len(PLAN["areas"]) == 25 and len(CASES) == 287
    assert plans.check_plan(PLAN, load("Locate 140 4G")) == []            # every check, scenario, measure exists
    assert {c["kind"] for c in CASES.values()} == {"automatic", "assisted", "command", "manual"}
    assert CASES["23.05"]["what"] == "Packet format and checksum as per the MMI protocol"


def _gate(*packets, config=None):
    g = Gate(load("Locate 140 4G"), config=config)
    for i, p in enumerate(packets):
        g.feed(p, T0 + i, "c")
    return g


def test_automatic_cases_judged_from_packets():
    g = _gate(login(), pvt(frame=1), pvt(frame=2, at=T0 + 10, checksum="00"))
    assert plans.judge(CASES["01.06"], g)["status"] == "pass"                # fix validity in every packet
    res = plans.judge(CASES["23.05"], g)                                       # format and checksum
    assert res["status"] == "fail" and res["filter"] == {"check": "checksum"}
    assert plans.judge(CASES["16.02"], g)["note"] == "Configuration not given: history order"
    part = plans.judge(CASES["23.03"], g)                                      # every alarm
    assert part["status"] == "not_run"
    g.feed(pvt(frame=3, ptype="IF", alert="08", ign="0", at=T0 + 20), T0 + 20, "c")
    assert plans.judge(CASES["09.01"], g)["status"] == "partial"             # ignition off seen, on not yet


def test_a_case_passes_only_on_packets_that_pass():
    """Evidence must be clean: ignition off was seen, but in a packet with a wrong checksum."""
    g = _gate(login(), pvt(frame=1, ptype="IF", alert="08", ign="0", checksum="00"))
    res = plans.judge(CASES["09.01"], g)
    assert res["status"] == "fail" and "break the sheet" in res["note"]


def test_parked_position_and_distance():
    g = _gate(login(), *[pvt(frame=i, at=T0 + 10 * i, ign="0") for i in range(1, 8)])
    assert plans.judge(CASES["01.12"], g)["status"] == "pass"
    assert "no odometer" in plans.judge(CASES["01.15"], g)["note"].lower()


def test_a_bench_step_is_judged_over_its_window(tmp_path):
    runs, tr = Runs(tmp_path / "runs"), Tracker(tmp_path / "tracker")
    run = runs.create("Locate 140 4G", {"kind": "live", "port": 5999})
    run.add(login(), T0, "c")
    run.add(pvt(frame=1, ptype="IN", alert="07", ign="1"), T0 + 5, "c")      # before the step: does not count
    tr.start_step(PLAN["name"], "V7.6.0", "03.07", run.id, window_s=60, now=T0 + 10)
    running = tr.view(PLAN, "V7.6.0", [run], now=T0 + 20)
    case = next(c for a in running["areas"] for c in a["cases"] if c["id"] == "03.07")
    assert case["result"]["status"] == "running"
    run.add(pvt(frame=2, ptype="IN", alert="07", ign="1", at=T0 + 30), T0 + 30, "c")
    done = tr.view(PLAN, "V7.6.0", [run], now=T0 + 40)
    case = next(c for a in done["areas"] for c in a["cases"] if c["id"] == "03.07")
    assert case["result"]["status"] == "pass" and case["result"]["source"] == "step"
    tr.start_step(PLAN["name"], "V7.6.0", "03.10", run.id, window_s=30, now=T0 + 50)   # wake on SOS: none came
    late = tr.view(PLAN, "V7.6.0", [run], now=T0 + 200)
    sos = next(c for a in late["areas"] for c in a["cases"] if c["id"] == "03.10")
    assert sos["result"]["status"] == "fail" and "Nothing came" in sos["result"]["note"]


def test_the_testers_status_and_the_tracker_sheet(tmp_path):
    tr = Tracker(tmp_path)
    tr.update(PLAN["name"], "V7.6.0", "04.01", {"status": "pass", "remarks": "Blinks 1 Hz", "tester": "QA"})
    view = tr.view(PLAN, "V7.6.0", [])
    led = next(c for a in view["areas"] for c in a["cases"] if c["id"] == "04.01")
    assert led["result"] == {"status": "pass", "note": "", "filter": {}, "source": "tester"}
    from runway.fwtest import report
    data = report.tracker_workbook(view)
    back = report.read_tracker(data)
    assert back["04.01"]["status"] == "pass" and back["04.01"]["remarks"] == "Blinks 1 Hz"
    with pytest.raises(ValueError):
        tr.update(PLAN["name"], "V7.6.0", "04.01", {"status": "great"})


def test_several_servers_compared(tmp_path):
    runs = Runs(tmp_path)
    a = runs.create("Locate 140 4G", {"kind": "live", "port": 6001})
    b = runs.create("Locate 140 4G", {"kind": "live", "port": 6002})
    for r in (a, b):
        r.add(login(), T0, "c")
    for i in range(1, 6):
        a.add(pvt(frame=i, at=T0 + 10 * i), T0 + 10 * i, "c")
        if i != 3:                                                          # :6002 misses frame 3
            b.add(pvt(frame=i, at=T0 + 10 * i), T0 + 10 * i + 0.5, "c")
    res = several_servers([a, b])
    assert res["status"] == "fail" and ":6002 missed 1" in res["note"]


def test_a_window_sees_only_its_packets():
    g = _gate(login(), pvt(frame=1, ptype="IN", alert="07"))
    w = Window(g, T0 + 1, T0 + 1)
    assert [r["header"] for r in w.rows] == ["PVT"] and w.scenarios["in"]["count"] == 1


def test_the_ack_can_be_held_back():
    from runway.core.pipeline import Pipeline
    p = Pipeline(source=None, codec="ais140", sinks=[])
    assert p.ack_filter is None
    p.ack_filter = lambda frame, records: False
    assert p._ack_ok(None, []) is False
    p.ack_filter = lambda frame, records: 1 / 0                               # broken: the ACK still goes
    assert p._ack_ok(None, []) is True


def test_the_plan_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from runway.api import app as appmod
    from runway.api import fwtest as api
    monkeypatch.setattr(api, "runs", Runs(tmp_path / "fwtest"))
    monkeypatch.setattr(api, "tracker", Tracker(tmp_path / "tracker"))
    with TestClient(appmod.app) as c:
        text = (login() + pvt(frame=1) + pvt(frame=2, at=T0 + 10, checksum="00")
                + sentence("HEL,GTRP,V7.6.0," + IMEI + ",31,10")).decode()
        c.post("/api/fwtest/runs/file", data={"spec": "Locate 140 4G"}, files={"file": ("b.txt", text, "text/plain")})
        assert c.get("/api/fwtest/plan/firmwares").json() == [{"firmware": "V7.6.0", "tests": 1}]
        v = c.get("/api/fwtest/plan", params={"firmware": "V7.6.0"}).json()
        assert v["total"] == 287 and sum(v["tally"].values()) == 287
        assert c.put("/api/fwtest/plan/case", json={"firmware": "V7.6.0", "case": "04.02",
                                                    "values": {"status": "fail", "remarks": "BUG-12"}}).status_code == 200
        x = c.get("/api/fwtest/plan.xlsx", params={"firmware": "V7.6.0"})
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(x.content))
        score = list(wb["Scorecard"].iter_rows(values_only=True))
        assert score[0][:3] == ("Area", "Category", "Cases")
        total = next(r for r in score if r[1] == "All")
        assert score.index(total) == 26 and total[2] == 287                           # head, 25 areas, all
        assert any(r[1] == "Release gate" and r[2] == "Not ready" for r in score)
        assert "Defects" in wb.sheetnames
        assert next(r for r in score if r[0] == "04")[4] == 1                          # 04.02 failed by hand
        rows = list(wb["Tracker"].iter_rows(values_only=True))
        assert rows[1][:3] == ("ID", "Area", "What to test")
        assert next(r for r in rows if r[0] == "04.02")[8] == "Fail"
        imp = c.post("/api/fwtest/plan/import", data={"firmware": "V7.6.0"},
                     files={"file": ("t.xlsx", x.content, "application/octet-stream")})
        assert imp.json()["cases"] >= 1


def test_a_case_that_cannot_be_judged_never_breaks_the_plan(tmp_path, monkeypatch):
    """A plan newer than the code running (it names a measure the code lacks), or a judge that fails:
    the case says why it is not judged; the plan view still answers (it was an HTTP 500 on 19-11-2026)."""
    case = {"id": "99.01", "group": "G", "what": "W", "kind": "automatic", "judge": {"measure": "from_the_future"}}
    res = plans.judge(case, _gate(login()))
    assert res["status"] == "not_run" and "restart Runway" in res["note"]
    runs, tr = Runs(tmp_path / "runs"), Tracker(tmp_path / "tracker")
    run = runs.create("Locate 140 4G", {"kind": "live", "port": 5999})
    run.add(login(), T0, "c")

    def boom(case, gate):
        raise RuntimeError("bad judge")
    monkeypatch.setattr(plans, "judge", boom)
    view = tr.view(PLAN, "V7.6.0", [run])
    fix = next(c for a in view["areas"] for c in a["cases"] if c["id"] == "01.06")
    assert fix["result"]["status"] == "not_run" and "bad judge" in fix["result"]["note"]


def test_cases_out_of_scope_leave_the_plan_and_come_back(tmp_path):
    tr = Tracker(tmp_path)
    tr.set_scope(PLAN["name"], ["04.01", "04.02", "08.10"])
    v = tr.view(PLAN, "V7.6.0", [])
    ids = {c["id"] for a in v["areas"] for c in a["cases"]}
    assert not ids & {"04.01", "04.02", "08.10"} and v["total"] == 284 and v["out_of_scope"] == 3
    assert sum(v["tally"].values()) == 284
    every = tr.view(PLAN, "V7.6.0", [], include_out=True)                 # choosing the scope: all of them
    flags = {c["id"]: c["in_scope"] for a in every["areas"] for c in a["cases"]}
    assert flags["04.01"] is False and flags["04.03"] is True and len(flags) == 287
    tr.set_scope(PLAN["name"], [])                                         # put back
    assert tr.view(PLAN, "V7.6.0", [])["total"] == 287

