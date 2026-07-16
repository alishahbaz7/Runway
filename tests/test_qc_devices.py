"""Persistent, IMEI-keyed QC device registry (proinfo/storage/db.py's qc_devices table):
a stable serial number per unique device (never reassigned on reconnect), and a "last
known QC status" that only updates on a FINAL verdict — never clobbered by a still
in-progress session — so a returning device's prior result stays visible until its new
run finalizes.
"""

from __future__ import annotations

from proinfo.storage.live import db


def test_same_imei_keeps_its_serial_new_imei_increments():
    a, b = "990000000000001", "990000000000002"
    r1 = db.qc_device_touch(a)
    r2 = db.qc_device_touch(a)             # reconnect — same IMEI
    assert r2["serial"] == r1["serial"], "a reconnecting device must keep its serial"

    r3 = db.qc_device_touch(b)             # a genuinely new device
    assert r3["serial"] == r1["serial"] + 1, "a new IMEI gets the next serial, not a reused one"

    r4 = db.qc_device_touch(a)             # reconnecting again, after another device registered
    assert r4["serial"] == r1["serial"], "serial stays stable even after other devices register"


def test_in_progress_verdict_never_overwrites_the_registry():
    imei = "990000000000003"
    db.qc_device_touch(imei, qc_id=101, verdict="pass")     # a prior, FINAL result
    row = db.qc_device_touch(imei)                          # a new session just touching (no verdict yet == in_progress)
    assert row["last_qc_id"] == 101 and row["last_verdict"] == "pass", \
        "an in-progress touch (verdict=None) must not clobber the last FINAL result"


def test_final_verdict_overwrites_the_prior_one():
    imei = "990000000000004"
    db.qc_device_touch(imei, qc_id=201, verdict="fail")
    row = db.qc_device_touch(imei, qc_id=202, verdict="pass")
    assert row["last_qc_id"] == 202 and row["last_verdict"] == "pass", \
        "a new FINAL verdict replaces the previous one"


def test_qc_device_registry_is_read_only_lookup():
    imei = "990000000000005"
    assert db.qc_device_registry(imei) is None, "unknown device -> no row, no side effect"
    db.qc_device_touch(imei)
    before = db.qc_device_registry(imei)
    after = db.qc_device_registry(imei)
    assert before == after, "a read-only lookup must not bump last_seen or otherwise mutate the row"
