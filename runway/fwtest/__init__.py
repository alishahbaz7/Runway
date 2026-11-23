"""Firmware test — every packet a device sends, held against its sheet.

When a firmware is released, a device running it is pointed at Runway (or a capture is dropped in)
and each packet goes through a gate: its framing and checksum, every field against the device's
sheet (a *spec*: ``specs/<name>.json``), and the order of packets — login first, frame numbers,
times, alerts. Anything that deviates is a flag; the scenarios exercised are counted; a report
closes the test.

    rules.py   field rules by name ("date", "imei", "range", …) — a new rule is one function
    spec.py    a spec: packets, fields and their rules, scenarios, sequence settings
    gate.py    the gate: feed it packets, read flags, checks, scenarios and the verdict
    runs.py    a test run on disk (its packets as received; the gate is rebuilt from them)
    report.py  the run as an Excel workbook

Pure like ``core/`` and ``codecs/``: no FastAPI, no sockets — the API attaches a run to a listener.
"""
