"""Log analysis — a report exported from the platform, read back and judged.

    drivelog.py   read a platform drive-log CSV (any of its column layouts) into plain rows
    analyse.py    the checks: completeness, delivery, GPS, power, driving, payloads → a report
    store.py      the analysed logs kept under data/analysis (the source file and its report)
    excel.py      the report as an Excel workbook

A drive log is what the platform decoded from a device's raw packets: one row per packet, with the
device's time and the platform's arrival time. The analysis is pure computation over those rows;
only store.py touches the disk.
"""
