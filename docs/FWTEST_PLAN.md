# Firmware test — build plan and progress

The Locate 140 4G test tracker (about 245 cases in 25 areas) built into Runway's Firmware test, in
three phases. **This file is the source of truth for where the work stands**: it is updated at the
end of every slice, so a session that stops (usage limit, new session, context reset) resumes from
here — "continue the firmware test plan" — without re-deriving anything.

How the work runs:

- **Slices.** Each slice ends complete: tests green, the page checked on screen where it has UI, the
  CHANGELOG and this file updated. A stop between slices loses nothing; mid-slice, at most that slice.
- **Status** per slice: `done` · `in progress` · `next` · `waiting` (needs something from the user —
  never guessed; the cases stay manual until it comes).
- **Nothing committed** without the user's word; a commit point is suggested at the end of each phase.

## Decisions

| Date | Decision |
|---|---|
| 18-11-2026 | Three kinds of case: *automatic* (judged from packets), *assisted* (a bench step, then judged from the packets in a time window), *manual* (status and remarks by hand). |
| 18-11-2026 | The tracker is data: one row per case, bound to the check that judges it. A new case is a row. |
| 18-11-2026 | A test plan belongs to a **firmware version** (a campaign of several tests — devices, days), as the tracker's "Tested FW version" column reads. |
| 18-11-2026 | The device's settings are asked, never assumed (intervals, sleep, history order, packet format). |

## Still open (nothing waits on them — the cases stay by hand until they come)

- Packet sheets: health (HLM vs the `$HEL` the device sends), emergency (EPB: EMR, SEM, NM, SP), the
  Amendment 3 alerts DT, OA, BC (what OA and BC are), the sleep-start event (03.06), movement and
  towing (17), restart events (22.04), RFID (19.07–19.10).
- GPRS command formats: get/set settings, outputs and immobilisation, emergency commands 016, 017,
  020, 021, RFID 023.
- Thresholds: supply voltage range (9–36 V?), low-battery voltage, overspeed, harsh-driving limits.
- 16.02 / 23.04 "history sent in order": oldest first (FIFO)? The device sends newest first.

## Phase 1 — the test plan inside Runway

| Slice | What | Status |
|---|---|---|
| P1.1 | The tracker as data: `runway/fwtest/plans/locate140_4g.json` — every case with id, area, group, what, kind, and the check that judges it | done |
| P1.2 | Campaigns: a test plan per firmware version (`data/fwtest/campaigns/`), tests joining it by the firmware their login reports; per case status, tester, date, version, remarks, defect ID, dev columns | done |
| P1.3 | Automatic cases judged from the campaign's tests: Pass / Fail / Not run, with the packets as evidence | done |
| P1.4 | New automatic checks the tracker needs: GPS hopping and distance while parked (01.12–01.15), time to first fix (01.01–01.03), restarts and reset loops (22.04, 22.06), signal strength (15.03) | done |
| P1.5 | The Test plan view: the tracker's table, filters by area / kind / status, manual cases set by hand | done |
| P1.6 | Excel out in the tracker's own layout, with evidence; Excel in, so existing results and dev columns carry over | done |

## Phase 2 — assisted steps

| Slice | What | Status |
|---|---|---|
| P2.1 | Step engine: a case's steps as data (instruction, packets expected, time window); *Run* → countdown → judged | done |
| P2.2 | SOS, ignition, main power, internal battery, tamper, wake triggers, time to fix after cold / warm / hot start | done |
| P2.3 | Runway withholds the ACK on purpose (06.04 acknowledgement, 06.15 retry until acknowledged) | done |

## Phase 3 — commands and several servers

| Slice | What | Status |
|---|---|---|
| P3.1 | Commands from a test: a *By command* case sends the command the tester types (kept per case for the next run) to the device on its live connection, then judges the window | done — the 4G's command formats, once known, can be filled in per case |
| P3.2 | Settings changed by command, then measured (03.23–03.26, 21.02, 21.06, 21.07) | done — the command step, then the interval cases judged from the packets with the new configuration |
| P3.3 | Three listeners at once — InTouch, government, emergency — compared for loss and delay (23.07, 25.05) | done |

## Phases 4–6 — from a gate to the developer's tool (all done, 18-11-2026)

| Slice | What | Status |
|---|---|---|
| P4.1 | Diagnosis per failure: area, severity, cause (proved / likely), where, fix, impact, verify, source | done |
| P4.2 | Defects drafted, copied as a report; Defects sheet | done |
| P4.3 | Severity and impact; fix-first (fewest causes explaining most failing packets) | done |
| P4.4 | Grouping by firmware area | done |
| P4.5 | Firmware against firmware; release gate | done |
| P5.1 | Guided suites (bench steps in order) | done |
| P5.2 | Not exercised yet — what to try next | done |
| P5.3 | Timeline of the test | done |
| P5.4 | Live coach | done |
| P6.1 | Spec from a protocol sheet, with the difference | done |
| P6.2 | The source of every rule | done |
| P6.3 | The platform's record compared (23.06) | done |
| P6.4 | Soak reports written by themselves | done |
| — | Case descriptions (test case, expected behaviour, acceptance criteria) on hold and in the sheet | done |
| — | Whole-device tests: a correct device clean, 16 faults each caught | done |

## Log

| Date | Slice | Note |
|---|---|---|
| 18-11-2026 | — | Plan written. The tracker read: 25 areas, about 55 automatic, 75 assisted, 115 manual cases. |
| 18-11-2026 | P1–P3 | All slices built. The tracker as data: 287 cases (43 from packets, 88 bench steps, 26 by command, 130 by hand), `runway/fwtest/plans/locate140_4g.json`; judged by `plan.py`; kept per firmware version by `tracker.py`; the Test plan view on the Firmware test page; the tracker sheet out and in. The spec gained EPB, HLM and DT / OA / BC (by code) from the AIS-140 standard. On today's V7.06.10 packets: 13 pass, 3 fail, 3 partial, the rest not run yet. 397 tests pass. Uncommitted. |
| 18-11-2026 | Hardening | The user: nothing wrong may slip through the gate. Eight ways it could were closed (unreadable bytes, wrong-IMEI packets, packets outside the sheet, history times, field consistency, stale live, no pass without configuration, clean evidence). Scorecard in the rail; categories in the left pane; one tab per IMEI. 404 tests. |
| 18-11-2026 | P4–P6 | Built and checked on screen with the device's real packets. Two false failures found by the simulated correct device and fixed (frame numbers jumping over history; parked stretches pooled). 429 tests. |
