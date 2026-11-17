"""Log analysis: a drive log built here (the platform's real column layout, made-up values — no
device's data in the repo) carries every problem the checks look for, and each must be found.
Then the API: upload, list, report, rows, settings, judging again, Excel, delete."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from runway.analysis import analyse as an
from runway.analysis import drivelog
from runway.analysis import modes as md

HEADER = ("timestampStr,insertTimeStr,speed,latitude,longitude,numberOfSatellites,gsmlevel,mnc,mcc,cellId,type,"
          "digitalInput1,digitalInput2,digitalInput3,digitalInput4,digitalInput5,digitalOutput1,digitalOutput2,"
          "analogInput1,powerSupplyVoltage,internalBatteryVoltage,canSpeed,blinker,power,version,greenDriveType,"
          "greenDriveValue,appData,history,movementSensor,temperatureSensor,RFID,panic,otherData,canData,"
          "internalBatteryLevel,altitude,externalSensorStr,meta_data").split(",")
NAME = "123456_Mon Nov 16 00_00_00 IST 2026_Tue Nov 17 00_00_00 IST 2026_driveLogs.csv"
T0 = datetime(2026, 11, 16, 0, 0, 0)
LAT, LON = 28.5, 77.2
STEP = 0.0001250            # ~50 km/h at 1 s per row, heading north


def build() -> str:
    rows = []
    frame = [1000]

    def add(t, speed, lat, lon, ign, sats=12, typ="0", delay=2, history=False, green="", alert=0.0, supply=None,
            frame_skip=0, no_frame=False):
        if not no_frame:
            frame[0] += 1 + frame_skip
        rows.append({
            "timestampStr": f"{t:%Y-%m-%d %H:%M:%S}", "insertTimeStr": f"{t + timedelta(seconds=delay):%Y-%m-%d %H:%M:%S}",
            "speed": f"{speed:.1f}", "latitude": f"{lat:.6f}", "longitude": f"{lon:.6f}", "numberOfSatellites": str(sats),
            "gsmlevel": "28", "type": typ, "digitalInput1": str(ign),
            "powerSupplyVoltage": f"{supply if supply is not None else (14200.0 if ign else 11700.0)}",
            "internalBatteryVoltage": "4100.0", "power": "1", "history": "true" if history else "false",
            "greenDriveType": green, "canData": f"{{hvBatteryTemp=33.0, batteryHighTempAlert={alert}}}",
            "altitude": "220.0",
            "meta_data": "" if no_frame else f"{{frameNumber={frame[0]:06d}, AISPacketType={'H' if history else 'L'}}}",
        })

    lat = LAT
    t = T0
    # trip 1: ten minutes at 50 km/h, with a repeated position, a driving event and an overspeed burst
    for i in range(600):
        speed = 90.0 if 200 <= i < 230 else 50.0
        if i != 300:                                   # row 300 repeats row 299's position
            lat += STEP
        skip = 1 if i in (100, 201, 302, 403) else 3 if i == 555 else 0     # 4 periodic holes, 1 real one
        add(t, speed, lat, LON, 1, green="1" if i == 450 else "", alert=1.0 if i % 2 else 0.0, frame_skip=skip,
            history=(i % 50 == 0), delay=200 if i == 120 else 2)
        t += timedelta(seconds=1)
    # 20 minutes of nothing with the ignition on — data lost
    t += timedelta(minutes=20)
    for i in range(300):
        lat += STEP
        add(t, 50.0, lat, LON, 1)
        t += timedelta(seconds=1)
    # parked, ignition off: two minutes of rows, a row without a position, then 30 minutes of sleep
    for i in range(120):
        add(t, 0.0, lat, LON, 0)
        t += timedelta(seconds=1)
    add(t, 0.0, 0.0, 0.0, 0, sats=0, typ="5", no_frame=True)
    t += timedelta(minutes=30)
    # ignition on again: the first fix is a stale position 22 km away, with no satellites
    add(t, 0.0, lat + 0.2, LON, 1, sats=0)
    t += timedelta(seconds=1)
    for i in range(120):
        lat += STEP
        add(t, 50.0, lat, LON, 1)
        t += timedelta(seconds=1)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in HEADER})
    return buf.getvalue()


@pytest.fixture(scope="module")
def report():
    log = drivelog.read(build().encode(), NAME)
    rep = an.analyse(log)
    return rep


def ids(rep, level=None):
    return {f["id"] for f in rep["findings"] if level is None or f["level"] == level}


def test_the_file_name_says_device_and_period():
    n = drivelog.parse_name(NAME)
    assert n["device"] == "123456" and n["from"] == T0 and n["to"] == T0 + timedelta(days=1)


def test_a_csv_that_is_not_a_drive_log_is_refused():
    with pytest.raises(ValueError):
        drivelog.read(b"a,b,c\n1,2,3\n", "x.csv")


def test_gaps_are_loss_with_ignition_on_and_sleep_with_it_off(report):
    kinds = [(g["kind"], round(g["seconds"] / 60)) for g in report["gaps"]]
    assert ("loss", 20) in kinds and ("sleep", 30) in kinds
    assert report["summary"]["loss_s"] == pytest.approx(20 * 60 + 1, abs=2)
    assert "loss_min" in ids(report, "bad")


def test_periodic_frame_holes_are_not_counted_as_loss(report):
    fr = report["frames"]
    assert fr["periodic_holes"] == 4 and fr["lost"] == 3


def test_a_stale_fix_at_power_on_is_found_and_kept_off_the_track(report):
    g = report["gps"]
    assert g["stale_points"] == 1 and g["stale"][0]["metres"] > 20000
    assert g["jump_km"] > 40                                   # out to the stale point and back
    track_lats = [p[0] for seg in report["series"]["track"] for p in seg]
    assert max(track_lats) < LAT + 0.15                        # the stale point (+0.2°) is not drawn


def test_a_repeated_position_while_moving_is_found(report):
    assert report["gps"]["frozen"] == 1


def test_delivery_late_rows_and_rows_without_a_position(report):
    d = report["delivery"]
    assert d["late"] >= 1 and d["no_position"] == 1
    assert d["latency"]["max"] == 200


def test_driving_trips_overspeed_and_events(report):
    dr = report["driving"]
    assert len(dr["trips"]) == 3
    assert len(dr["overspeed"]) == 1 and dr["overspeed"][0]["max"] == 90
    assert len(dr["events"]) == 1 and dr["events"][0]["code"] == "1"
    assert 13 < dr["distance_km"] < 15.5                     # 1,020 rows × ~13.9 m


def test_power_resting_voltage_and_the_alert_that_does_not_follow_its_value(report):
    assert report["power"]["resting"] == 11.7 and "resting_v" in ids(report, "warn")
    assert any(a["alert"] == "batteryHighTempAlert" for a in report["payload"]["alert_mismatch"])


def test_statuses_and_the_detail_tables(report):
    assert report["status"] == "bad" and report["status_word"] == "Poor"
    areas = {a["id"]: a for a in report["areas"]}
    assert areas["data"]["status"] == "bad" and areas["gps"]["status"] == "bad"
    assert all("figure" in a for a in report["areas"])
    assert {t["type"] for t in report["types"]} == {"0", "5"}
    assert any(c["column"] == "otherData" for c in report["columns"])
    assert report["findings"][0]["level"] == "bad"             # worst first
    assert report["flag_counts"]["stale"] == 1


def test_a_short_layout_still_reads():
    """The export's narrowest layout (12 columns): no frame numbers, no history — checks that need
    them stay quiet, the rest still run."""
    cols = ["timestampStr", "insertTimeStr", "speed", "latitude", "longitude", "numberOfSatellites", "gsmlevel",
            "type", "digitalInput1", "digitalInput2", "powerSupplyVoltage", "internalBatteryLevel"]
    lines = [",".join(cols)]
    for i in range(200):
        t = T0 + timedelta(seconds=10 * i)
        lines.append(f"{t:%Y-%m-%d %H:%M:%S},{t + timedelta(seconds=3):%Y-%m-%d %H:%M:%S},40,"
                     f"{LAT + i * 0.001:.6f},{LON},9,25,0,1,0,13800,80")
    rep = an.analyse(drivelog.read("\n".join(lines).encode(), "short.csv"))
    assert rep["frames"]["numbered"] == 0 and rep["driving"]["trips"]
    assert rep["status"] in ("ok", "warn", "bad")


# ── the API ──────────────────────────────────────────────────────────────────────────────────────

def _client():
    from runway.api.app import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_upload_report_rows_settings_excel_delete():
    async with _client() as c:
        bad = await c.post("/api/analysis", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
        assert bad.status_code == 400
        r = await c.post("/api/analysis", files={"file": (NAME, build().encode(), "text/csv")})
        assert r.status_code == 200, r.text
        e = r.json()
        aid = e["id"]
        assert e["device"] == "123456" and e["status"] == "bad"
        items = (await c.get("/api/analysis")).json()["items"]
        assert any(i["id"] == aid for i in items)
        rep = (await c.get(f"/api/analysis/{aid}")).json()
        assert rep["areas"] and "_flags" not in rep
        stale = (await c.get(f"/api/analysis/{aid}/rows", params={"flag": "stale"})).json()
        assert stale["total"] == 1 and stale["rows"][0]["sats"] == 0
        k = stale["rows"][0]["k"]
        page = (await c.get(f"/api/analysis/{aid}/rows", params={"at": k, "limit": 50})).json()
        assert any(x["k"] == k for x in page["rows"])
        one = (await c.get(f"/api/analysis/{aid}/rows/{k}")).json()
        assert one["columns"]["numberOfSatellites"] == "0" and "stale" in one["flags"]
        # judged again with a higher overspeed limit: the overspeed finding goes
        again = (await c.post(f"/api/analysis/{aid}/analyse", json={"settings": {"overspeed_kmh": 100}})).json()
        assert again["id"] == aid
        rep2 = (await c.get(f"/api/analysis/{aid}")).json()
        assert not any(f["id"] == "overspeed_episodes" for f in rep2["findings"])
        put = await c.put("/api/analysis/settings", json={"settings": {"trip_stop_min": 10}})
        assert put.json()["settings"]["trip_stop_min"] == 10
        assert (await c.put("/api/analysis/settings", json={"settings": {"trip_stop_min": "x"}})).status_code == 400
        await c.put("/api/analysis/settings", json={"settings": {}})
        x = await c.get(f"/api/analysis/{aid}/report.xlsx")
        assert x.status_code == 200 and x.content[:2] == b"PK"
        assert (await c.delete(f"/api/analysis/{aid}")).status_code == 200
        assert (await c.get(f"/api/analysis/{aid}")).status_code == 404
        assert (await c.get("/api/analysis/..%2Fetc")).status_code == 404


# ── the device: AIS-140 alerts, restarts, engine starts, network, reporting interval ────────────────

AIS_HEADER = ("timestampStr,insertTimeStr,speed,latitude,longitude,numberOfSatellites,firmwareVersion,otherData,type,"
              "digitalInput1,digitalInput2,powerSupplyVoltage,deviceResponse,internalBatteryVoltage,power,panic,meta_data,"
              "internalBatteryLevel,altitude,analogInput1,externalSensorStr,greenDriveType,greenDriveValue,history,"
              "digitalOutput1,digitalInput3,digitalInput4,digitalOutput2,gsmlevel,mnc,mcc,cellId").split(",")


def build_ais140() -> str:
    rows, frame = [], [0]
    lat = [LAT]

    def add(t, ign, speed=0.0, code="NR", aid="1", supply=12800.0, response="", restart=False, cell="661F", green=""):
        frame[0] = 1 if restart else frame[0] + 1
        if speed:
            lat[0] += STEP * speed / 50 * 10
        rows.append({"timestampStr": f"{t:%Y-%m-%d %H:%M:%S}", "insertTimeStr": f"{t + timedelta(seconds=2):%Y-%m-%d %H:%M:%S}",
                     "speed": f"{speed:.1f}", "latitude": f"{lat[0]:.6f}", "longitude": f"{LON:.6f}", "numberOfSatellites": "14",
                     "firmwareVersion": "7.5.1A", "otherData": f"{{PacketType={code}, deviceTamper=C, alertId={aid}}}", "type": "0",
                     "digitalInput1": str(ign), "powerSupplyVoltage": f"{supply}", "deviceResponse": response,
                     "internalBatteryVoltage": "4100.0", "power": "1", "meta_data": f"{{frameNumber={frame[0]:06d}, AISPacketType=L}}",
                     "internalBatteryLevel": "100", "history": "false", "gsmlevel": "24", "greenDriveType": green,
                     "mcc": "404" if cell != "0" else "0", "mnc": "56" if cell != "0" else "0", "cellId": cell})

    t = T0
    add(t, 1, code="IN", aid="7", supply=6500.0)                     # engine starting: the supply falls
    t += timedelta(seconds=1)
    add(t, 0, code="DSEN", aid="38", supply=6100.0)                  # … low enough that it announces deep sleep
    t += timedelta(seconds=1)
    add(t, 0, code="IF", aid="8")                                    # the ignition flickers off …
    t += timedelta(seconds=4)
    add(t, 1, code="IF", aid="8")                                    # … on again, with an IF alert: disagrees
    for i in range(60):                                              # 10 minutes driving, every 10 s
        t += timedelta(seconds=10)
        add(t, 1, speed=40.0, cell="0" if i < 20 else "661F", green="2" if i == 30 else "",
            code="HB" if i == 30 else "NR", aid="13" if i == 30 else "1")
    t += timedelta(seconds=10)
    add(t, 0, code="IF", aid="8")
    for i in range(30):                                              # parked, every 30 s
        t += timedelta(seconds=30)
        add(t, 0)
    t += timedelta(minutes=10)                                       # silent, then the device starts again
    add(t, 0, code="DBG", aid="99", response="(BTC:3;FH:NONE;RST:0x0C000000)*AB", restart=True)
    for i in range(30):
        t += timedelta(seconds=30)
        add(t, 0)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=AIS_HEADER, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in AIS_HEADER})
    return buf.getvalue()


@pytest.fixture(scope="module")
def ais():
    return an.analyse(drivelog.read(build_ais140().encode(), "777001_Mon Nov 16 00_00_00 IST 2026_Tue Nov 17 00_00_00 IST 2026_driveLogs.csv"))


def test_a_restart_is_not_lost_frames_and_its_boot_report_is_read(ais):
    fr, dv = ais["frames"], ais["device"]
    assert fr["runs"] == 2 and fr["lost"] == 0 and fr["duplicates"] == 0
    assert len(dv["restarts"]) == 1 and dv["restarts"][0]["gap_before"] >= 600
    assert dv["boots"][0]["facts"] == {"BTC": "3", "FH": "NONE", "RST": "0x0C000000"}


def test_ais140_alerts_are_named_and_checked_against_the_ignition(ais):
    dv = ais["device"]
    codes = dict(dv["codes"])
    assert codes["IN"] == 1 and codes["IF"] == 3 and codes["HB"] == 1 and codes["DSEN"] == 1
    assert dv["unknown_codes"] == []                               # DBG: Zenithra's debug report, named
    assert any(a["name"] == "Debug report (DBG)" for a in dv["alerts"])
    assert dv["ignition"]["disagree"] == 1                         # the IF sent with the ignition on
    assert len(dv["flicker"]) >= 1
    ev = ais["driving"]["events"]
    assert ev and ev[0]["name"] == "Harsh braking (HB)"            # greenDriveType named by its alert
    assert any(a["id"] == "device" for a in ais["areas"])


def test_the_supply_at_engine_start_the_cell_and_the_reporting_interval(ais):
    assert ais["power"]["starts"][0]["lowest"] == 6.1                 # the deep-sleep row, mid-start
    assert ais["power"]["cuts"] == []                              # a start dip is not a power cut
    assert "start_v" in ids(ais, "bad")
    nw = ais["network"]
    assert nw["missing"] == 20 and round(nw["missing_pct"]) == round(100 * 20 / nw["considered"])
    cad = ais["summary"]["cadence"]
    assert cad["on"]["seconds"] == 10 and cad["off"]["seconds"] == 30
    assert ais["summary"]["firmware"] == ["7.5.1A"]
    line = ais["power"]["line"]
    assert line[0][1] == 6.5 and line[0][2] == 4.1 and line[0][3] == 1   # main, internal, ignition


def test_our_imu_packets_are_not_device_alerts():
    rep = an.analyse(drivelog.read(build().encode(), NAME))
    assert rep["device"]["alerts_total"] == 0


def test_green_drive_2_is_harsh_braking_even_without_its_alert():
    """The user, 16-11-2026: greenDriveType 2 is harsh braking. Named events are judged; code 1 is not yet."""
    rows = list(csv.DictReader(io.StringIO(build())))
    rows[450]["greenDriveType"] = "2"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    rep = an.analyse(drivelog.read(buf.getvalue().encode(), NAME))
    ev = rep["driving"]["events"]
    assert ev[0]["code"] == "2" and ev[0]["name"] == "Harsh braking"
    f = next(f for f in rep["findings"] if f["id"] == "driving_events")
    assert "Harsh braking ×1" in f["text"] and f["level"] == "ok"


def test_deep_sleep_is_judged_by_what_followed_and_why_it_was_sent(ais):
    """The user, 16-11-2026: DSEN (DeepSleepENable) is the last packet before the device sleeps, sent when
    its internal battery is low and the main supply is disconnected."""
    ds = ais["device"]["deep_sleep"]
    assert len(ds) == 1 and ds[0]["during_start"] and not ds[0]["slept"]      # it went on reporting
    assert ds[0]["main_gone"] and not ds[0]["internal_low"]                   # the internal battery was full
    found = {f["id"]: f for f in ais["findings"]}
    assert found["deep_sleep_awake"]["level"] == "warn" and "while the engine was starting" in found["deep_sleep_awake"]["text"]
    assert "internal battery not low" in found["deep_sleep_why"]["text"]
    assert ais["device"]["unknown_codes"] == []


def test_green_drive_1_is_harsh_acceleration():
    rep = an.analyse(drivelog.read(build().encode(), NAME))
    assert rep["driving"]["events"][0]["name"] == "Harsh acceleration"


async def test_delete_several_or_all():
    async with _client() as c:
        made = []
        for _ in range(3):
            r = await c.post("/api/analysis", files={"file": (NAME, build().encode(), "text/csv")})
            made.append(r.json()["id"])
        d = (await c.post("/api/analysis/delete", json={"ids": made[:2] + ["nope"]})).json()
        assert d["deleted"] == made[:2]
        left = [i["id"] for i in (await c.get("/api/analysis")).json()["items"]]
        assert made[2] in left and made[0] not in left
        d = (await c.post("/api/analysis/delete", json={"all": True})).json()
        assert made[2] in d["deleted"]
        assert (await c.get("/api/analysis")).json()["items"] == []


def test_a_missing_battery_is_critical():
    """The user, 16-11-2026: a missing internal or external battery must be flagged — critical."""
    rows = list(csv.DictReader(io.StringIO(build_ais140())))
    for r in rows[10:16]:
        r["internalBatteryVoltage"] = "0.0"                         # the internal battery reads 0 V …
    for r in rows[40:44]:
        r["powerSupplyVoltage"], r["power"] = "0.0", "0"             # … and the main battery is gone, mid-drive
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=AIS_HEADER, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    rep = an.analyse(drivelog.read(buf.getvalue().encode(), "x_driveLogs.csv"))
    pw = rep["power"]
    assert len(pw["internal_missing"]) == 1 and pw["internal_missing"][0]["rows"] == 6
    assert len(pw["cuts"]) == 1 and pw["cuts"][0]["rows"] == 4 and pw["cuts"][0]["lowest"] == 0
    found = {f["id"]: f for f in rep["findings"]}
    assert found["internal_missing"]["level"] == "bad" and found["main_missing"]["level"] == "bad"
    assert "Main battery missing 1 time" in found["main_missing"]["text"]
    assert next(a for a in rep["areas"] if a["id"] == "power")["status"] == "bad"
    assert {m["kind"] for m in rep["series"]["markers"]} >= {"battery"}
    assert rep["flag_counts"]["internal_missing"] == 6 and rep["flag_counts"]["main_missing"] == 4



def test_missing_data_is_a_break_on_the_track():
    """The user, 17-11-2026: show missing data on the map with dotted lines — each gap is a break
    from the last position before it to the first after it, told lost (ignition on) or asleep."""
    rows = list(csv.DictReader(io.StringIO(build())))
    for r in rows[600:]:                                 # the vehicle went on 1.1 km while nothing came
        if float(r["latitude"]):
            r["latitude"] = f"{float(r['latitude']) + 0.01:.6f}"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    report = an.analyse(drivelog.read(buf.getvalue().encode(), NAME))
    br = report["series"]["breaks"]
    lost = [b for b in br if b["kind"] == "loss"]
    assert len(lost) == 1 and lost[0]["seconds"] == pytest.approx(20 * 60 + 1, abs=2) and lost[0]["metres"] > 10
    assert all(set(b) >= {"from", "to", "start", "end", "duration", "k", "k_from"} for b in br)


def test_one_trip_is_judged_alone_and_points_at_the_same_rows(report):
    """The user, 17-11-2026: choosing a trip shows the whole summary for that trip alone."""
    whole = report["driving"]["trips"]
    log = drivelog.read(build().encode(), NAME)
    t1, t3 = whole[0], whole[2]
    one = an.analyse(log, span=(datetime.fromisoformat(t1["start"]), datetime.fromisoformat(t1["end"])))
    assert one["summary"]["first"] == t1["start"] and one["summary"]["last"] == t1["end"]
    assert len(one["driving"]["trips"]) == 1 and one["driving"]["trips"][0]["k"] == t1["k"]
    assert len(one["driving"]["overspeed"]) == 1 and len(one["driving"]["events"]) == 1
    assert one["driving"]["events"][0]["k"] == report["driving"]["events"][0]["k"]
    assert not [g for g in one["gaps"] if g["kind"] == "loss"]          # the loss came after this trip
    assert one["gps"]["stale_points"] == 0 and one["frames"]["lost"] == 3
    assert "first_row" not in ids(one)                                   # a trip is not the export's period
    three = an.analyse(log, span=(datetime.fromisoformat(t3["start"]), datetime.fromisoformat(t3["end"])))
    assert three["driving"]["trips"][0]["k"] == t3["k"] and three["summary"]["rows"] < 130
    assert all(m.get("k", t3["k"]) >= t3["k"] for m in three["series"]["markers"])


async def test_the_api_gives_a_trip_its_own_report_rows_and_workbook():
    async with _client() as c:
        aid = (await c.post("/api/analysis", files={"file": (NAME, build().encode(), "text/csv")})).json()["id"]
        whole = (await c.get(f"/api/analysis/{aid}")).json()
        t = whole["driving"]["trips"][1]
        span = {"start": t["start"], "end": t["end"]}
        part = (await c.get(f"/api/analysis/{aid}/part", params=span)).json()
        assert part["scope"] == span and part["entry"]["id"] == aid and "_flags" not in part
        assert part["summary"]["rows"] == t["rows"] < whole["summary"]["rows"]
        rows = (await c.get(f"/api/analysis/{aid}/rows", params={**span, "limit": 1000})).json()
        assert rows["total"] == t["rows"] and rows["rows"][0]["k"] == t["k"]
        x = await c.get(f"/api/analysis/{aid}/report.xlsx", params={**span, "trip": 2})
        assert x.status_code == 200 and x.content[:2] == b"PK" and "trip 2" in x.headers["content-disposition"]
        assert (await c.get(f"/api/analysis/{aid}/part", params={"start": "x", "end": "y"})).status_code == 400
        assert (await c.get(f"/api/analysis/nope/part", params=span)).status_code == 404
        await c.delete(f"/api/analysis/{aid}")


# ── packet generation by mode (the user, 17-11-2026): active, standby, sleep ─────────────────────────

def build_modes(dsen_at: int | None = None) -> bytes:
    """Ten minutes on at 10 s; off: standby at 30 s for 5 min, then a sleep ping every 15 min for
    2 h with one missing and two sent later (history, 10 min late); on again at 10 s."""
    rows = []

    def add(t, ign, other="", history=False):
        lag = 600 if history else 3
        rows.append({"timestampStr": f"{t:%Y-%m-%d %H:%M:%S}", "insertTimeStr": f"{t + timedelta(seconds=lag):%Y-%m-%d %H:%M:%S}",
                     "speed": "30.0" if ign else "0.0", "latitude": f"{LAT:.6f}", "longitude": f"{LON:.6f}",
                     "numberOfSatellites": "12", "type": "0", "digitalInput1": str(ign), "otherData": other,
                     "history": "true" if history else "false"})

    for i in range(61):
        add(T0 + timedelta(seconds=10 * i), 1)
    off = T0 + timedelta(seconds=610)
    add(off, 0)
    for k in range(1, 11):
        add(off + timedelta(seconds=30 * k), 0, "{PacketType=DSEN, alertId=38}" if dsen_at == 30 * k else "")
    asleep = off + timedelta(seconds=300)
    for j in range(1, 9):
        if j != 4:                                                   # one sleep ping missing
            add(asleep + timedelta(seconds=900 * j), 0, history=j in (2, 3))
    back = asleep + timedelta(seconds=900 * 8 + 60)
    for i in range(30):
        add(back + timedelta(seconds=10 * i), 1)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in HEADER})
    return buf.getvalue().encode()


def _mode(rep, m):
    return next(g for g in rep["generation"]["modes"] if g["mode"] == m)


def test_each_mode_is_learned_from_the_log_and_judged():
    rep = an.analyse(drivelog.read(build_modes(), NAME))
    gen = rep["generation"]
    assert gen["values"] == {"active_s": 10, "standby_s": 30, "standby_min": 5.0, "sleep_s": 900}
    assert set(gen["source"].values()) == {"log"} and gen["profile"] is None
    active, standby, sleep = _mode(rep, "active"), _mode(rep, "standby"), _mode(rep, "sleep")
    # due = each stretch's length / the interval + its first packet: an exact device makes 100%
    assert active["packets"] == 91 and active["expected"] == 91 and active["sufficiency_pct"] == 100
    assert active["diff"] == 0 and active["level"] == "ok" and active["periods"] == 2
    assert active["gen"]["median"] == active["gen"]["min"] == active["gen"]["max"] == 10 and active["gen"]["jitter"] == 0
    assert standby["packets"] == 11 and standby["sufficiency_pct"] == 100 and standby["on_time_pct"] == 100
    assert standby["standby_lasted"]["median"] == 300                       # off at 0, last standby packet at 5 min
    assert sleep["packets"] == 7 and sleep["expected"] == 8 and sleep["sufficiency_pct"] == 87.5
    assert sleep["level"] == "bad" and sleep["missed"] == 1 and sleep["missed_steps"] == 1   # below 90%: too few
    assert sleep["longest_s"] == 1800 and sleep["gen"]["avg"] == 1050
    assert sleep["live"] == 5 and sleep["history"] == 2 and sleep["history_pct"] == 28.6
    assert sleep["delay_live"]["median"] == 3 and sleep["delay_history"]["median"] == 600
    assert sleep["delay"]["min"] == 3 and sleep["delay"]["max"] == 600
    assert any("history" in x for x in sleep["notes"]) and any("short" in x or "missed" in x for x in sleep["notes"])
    found = {f["id"]: f for f in rep["findings"]}
    assert found["enough_sleep"]["level"] == "bad" and "7 of 8 packets due" in found["enough_sleep"]["text"]
    assert found["enough_sleep"]["view"] == "modes" and found["profile"]["level"] == "info"
    assert [s["mode"] for s in gen["spans"]] == ["active", "standby", "sleep", "active"]


def test_a_device_profile_sets_the_intervals_and_too_many_is_found():
    prof = md.clean_profile({"name": "Bench", "devices": "999, 123456", "active_s": "10", "standby_s": 30,
                             "standby_min": 5, "sleep_s": "1800"})
    assert md.match([prof], "123456", []) is prof and md.match([prof], "777", ["1.0"]) is None
    assert md.match([{**prof, "devices": "", "firmware": "7.5, 8"}], "777", ["7.5.1A"])["name"] == "Bench"
    rep = an.analyse(drivelog.read(build_modes(), NAME), profiles=[prof])
    sleep = _mode(rep, "sleep")
    assert rep["generation"]["profile"]["name"] == "Bench" and sleep["source"] == "profile"
    assert sleep["expected"] == 4.5 and sleep["sufficiency_pct"] == 155.6 and sleep["level"] == "warn"   # too many
    assert "too many" in next(f for f in rep["findings"] if f["id"] == "enough_sleep")["text"]


def test_the_deep_sleep_packet_starts_sleep_at_once():
    rep = an.analyse(drivelog.read(build_modes(dsen_at=120), NAME))
    assert _mode(rep, "standby")["packets"] == 5                           # off, 30, 60, 90, 120 s — then asleep


def test_a_trip_is_judged_by_its_whole_logs_intervals():
    log = drivelog.read(build_modes(), NAME)
    whole = an.analyse(log, profiles=[md.clean_profile({"name": "P", "devices": "123456", "active_s": 20})])
    t = whole["driving"]["trips"][-1]
    g = whole["generation"]
    one = an.analyse(log, span=(datetime.fromisoformat(t["start"]), datetime.fromisoformat(t["end"])),
                     fixed={"values": g["values"], "source": g["source"], "profile": g["profile"]})
    assert one["generation"]["values"] == g["values"] and _mode(one, "active")["interval_s"] == 20
    assert one["generation"]["profile"]["name"] == "P"


def test_a_log_saved_again_from_excel_is_refused_with_the_reason():
    text = "timestampStr,insertTimeStr,latitude,longitude,speed\n16-11-2026 18:07,16-11-2026 18:08,28.5,77.2,0\n"
    with pytest.raises(ValueError, match="Excel"):
        drivelog.read(text.encode(), "x_driveLogs.csv")


async def test_profiles_are_kept_and_used_and_the_workbook_has_the_modes():
    import openpyxl
    async with _client() as c:
        bad = await c.put("/api/analysis/profiles", json={"profiles": [{"name": "No match", "active_s": 10}]})
        assert bad.status_code == 400
        ok = await c.put("/api/analysis/profiles", json={"profiles": [
            {"name": "Bench", "devices": "123456", "active_s": 10, "standby_s": 30, "standby_min": 5, "sleep_s": 900},
            {"name": "", "devices": "", "firmware": ""}]})
        assert [p["name"] for p in ok.json()["profiles"]] == ["Bench"]
        assert (await c.get("/api/analysis")).json()["profiles"][0]["sleep_s"] == 900
        aid = (await c.post("/api/analysis", files={"file": (NAME, build_modes(), "text/csv")})).json()["id"]
        rep = (await c.get(f"/api/analysis/{aid}")).json()
        assert rep["generation"]["profile"]["name"] == "Bench"
        x = await c.get(f"/api/analysis/{aid}/report.xlsx")
        wb = openpyxl.load_workbook(io.BytesIO(x.content))
        assert "Modes" in wb.sheetnames and wb["Modes"]["A2"].value == "Active"
        await c.put("/api/analysis/profiles", json={"profiles": []})
        await c.delete(f"/api/analysis/{aid}")


def test_the_track_marks_history_and_keeps_a_long_believable_hop(report):
    """The user, 17-11-2026: live solid, history dimmed and thin, missing dotted — so each track point
    says whether it was history; and a long hop at a believable speed is driving, never a gap in the line."""
    pts = [p for seg in report["series"]["track"] for p in seg]
    assert all(len(p) == 5 for p in pts) and any(p[4] for p in pts) and any(not p[4] for p in pts)
    rows = []
    for i in range(40):                                          # every 70 s at 120 km/h: 2.3 km a hop
        t = T0 + timedelta(seconds=70 * i)
        rows.append({"timestampStr": f"{t:%Y-%m-%d %H:%M:%S}", "insertTimeStr": f"{t:%Y-%m-%d %H:%M:%S}",
                     "speed": "120.0", "latitude": f"{LAT + i * 0.021:.6f}", "longitude": f"{LON:.6f}",
                     "numberOfSatellites": "12", "type": "0", "digitalInput1": "1"})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in HEADER})
    rep = an.analyse(drivelog.read(buf.getvalue().encode(), NAME))
    assert len(rep["series"]["track"]) == 1 and not rep["series"]["breaks"]


# ── comparison: devices fitted in one vehicle (the user, 17-11-2026) ─────────────────────────────────

NAME_B = NAME.replace("123456", "654321")


def build_second_device() -> bytes:
    """The same drive seen by a second device: ~11 m north of the first, starting a minute later and
    missing two minutes of the first trip."""
    rows = list(csv.DictReader(io.StringIO(build())))
    keep = []
    for i, r in enumerate(rows):
        if i < 60 or 300 <= i < 420:
            continue
        if float(r["latitude"]):
            r["latitude"] = f"{float(r['latitude']) + 0.0001:.6f}"
        keep.append(r)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    w.writerows(keep)
    return buf.getvalue().encode()


async def test_devices_in_one_vehicle_are_compared_over_their_common_time():
    async with _client() as c:
        a = (await c.post("/api/analysis", files={"file": (NAME, build().encode(), "text/csv")})).json()["id"]
        b = (await c.post("/api/analysis", files={"file": (NAME_B, build_second_device(), "text/csv")})).json()["id"]
        r = await c.get("/api/analysis/compare", params=[("ids", a), ("ids", b)])
        assert r.status_code == 200, r.text
        cmp_ = r.json()
        w = cmp_["window"]
        assert w["common"] and w["start"] == (T0 + timedelta(seconds=60)).isoformat()      # the later start
        assert [d["device"] for d in cmp_["devices"]] == ["123456", "654321"]
        m = {x["key"]: x for x in cmp_["metrics"]}
        assert m["rows"]["values"][0] > m["rows"]["values"][1]
        assert m["loss"]["best"] == [0] and m["loss"]["worst"] == [1]                        # the second lost 2 min
        assert m["apart_median"]["values"][0] is None and 9 <= m["apart_median"]["values"][1] <= 13
        assert m["apart_median"]["best"] == []                                               # one value: nothing to rank
        assert cmp_["devices"][1]["agreement"]["matched"] > 500
        assert len(cmp_["series"]) == 2 and cmp_["series"][0]["track"]
        x = await c.get("/api/analysis/compare.xlsx", params=[("ids", a), ("ids", b)])
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(x.content))
        assert wb.sheetnames == ["Scorecard", "Modes", "Devices"] and wb["Scorecard"]["D3"].value == "123456"
        assert (await c.get("/api/analysis/compare", params=[("ids", a)])).status_code == 400
        assert (await c.get("/api/analysis/compare", params=[("ids", a), ("ids", "nope")])).status_code == 404
        await c.post("/api/analysis/delete", json={"ids": [a, b]})


def test_agreement_interpolates_the_first_devices_position():
    from runway.analysis import compare as cmpmod
    ref = [{"t": T0 + timedelta(seconds=10 * i), "lat": LAT + 0.001 * i, "lon": LON, "speed": 40.0} for i in range(10)]
    other = [{"t": T0 + timedelta(seconds=10 * i + 5), "lat": LAT + 0.001 * i + 0.0005, "lon": LON, "speed": 42.0} for i in range(9)]
    ag = cmpmod.agreement(ref, other)
    assert ag["matched"] == 9 and ag["apart_median_m"] == 0 and ag["speed_diff_median"] == 2.0


def test_a_log_has_its_own_scorecard_judged_by_the_limits(report):
    """The user, 17-11-2026: the comparison's scorecard for one device too — each measure judged."""
    sc = {x["key"]: x for x in report["scorecard"]}
    assert sc["loss"]["value"] == pytest.approx(20, abs=0.1) and sc["loss"]["level"] == "bad"     # 20 min lost: poor
    assert sc["loss"]["limit"] == "1 · 10 min"
    assert sc["stale_jumps"]["level"] in ("warn", "bad") and sc["rows"]["level"] is None          # a fact, not judged
    assert sc["active_on_time"]["limit"].startswith("below 90")
    assert sc["cuts"]["limit"] == "Poor from 1" and sc["cuts"]["level"] == "ok"
    assert {x["group"] for x in report["scorecard"]} >= {"Data", "Modes", "Delivery", "GPS", "Driving", "Power"}
    from runway.analysis import excel
    import openpyxl
    rep = dict(report)
    rep.pop("_flags", None)
    wb = openpyxl.load_workbook(io.BytesIO(excel.workbook(rep)))
    assert wb.sheetnames[:2] == ["Summary", "Scorecard"] and wb["Scorecard"]["B2"].value == "Packets"


def test_a_gap_says_where_it_began_and_ended(report):
    """The user, 17-11-2026: missing data with its start and end time and place, duration and distance."""
    loss = next(g for g in report["gaps"] if g["kind"] == "loss")
    assert loss["from"] and loss["to"] and loss["k_from"] == loss["k"] - 1
    assert 10 <= loss["metres"] <= 20                            # the build resumes one ~14 m step on from where it stopped


def test_the_on_time_margin_grows_with_the_interval():
    """The user, 17-11-2026: ±1 s at 5–20 s, ±5 s once pings are over 10 min apart — by band, editable."""
    assert [md.margin_for(x, {}) for x in (5, 10, 20, 30, 120, 300, 900)] == [1, 1, 1, 2, 2, 3, 5]
    assert md.margin_for(900, {"margin_over": 8}) == 8
    rep = an.analyse(drivelog.read(build_modes(), NAME))
    assert _mode(rep, "active")["tolerance_s"] == 1 and _mode(rep, "sleep")["tolerance_s"] == 5
    sc = {x["key"]: x for x in rep["scorecard"]}
    assert sc["sleep_late"]["value"] == 0 and "900 s ± 5 s" in sc["sleep_on_time"]["limit"]
    assert sc["active_early"]["level"] == "ok" and sc["active_early"]["unit"] == "%"


def test_every_measure_and_area_says_what_it_is_for(report):
    """The user, 17-11-2026: a held pointer on a measure or a tile explains what it is used for."""
    from runway.analysis import scorecard
    assert not [k for k, *_ in scorecard.METRICS if not scorecard.ABOUT.get(k)]
    assert all(x["about"] for x in report["scorecard"]) and all(a["about"] for a in report["areas"])
