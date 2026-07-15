# Runway — Device QC Module (implementation plan)

> **Status: Target 1 implemented** (intake + identify + record). See "Target 1 — as built"
> below for what actually shipped, file-by-file. Targets 2-4 are still just the plan.
>
> **Next: QC validation** (packet coverage, template/parameter checks, alarm validation,
> iNTouch live check, raw-packet reporting) — the full plan is in
> **[QC_VALIDATION.md](QC_VALIDATION.md)**.

Draft plan for a **Quality-Control** workflow: a device is put on a bench, dials into a fixed
port, Runway identifies it, records its traffic, judges pass/fail, produces a report, onboards
it to **iNTouch**, and (optionally) configures it over **RS232**. Built incrementally — not all
at once.

## Goal

Turn Runway from "watch packets" into "**qualify a device**": a repeatable go/no-go check an
operator runs per unit, with a saved record and a shareable report.

## Recommendation: a NEW "QC" section that *reuses* existing building blocks

QC is a distinct **operator workflow** (guided, pass/fail, per-device, with a report + external
onboarding + serial config), so it deserves its own rail section — **Capture → Device QC** — not
to be crammed into the general **Live TCP** viewer. But it should **orchestrate what already
exists**, not duplicate it:

| QC needs | Reuse (don't rebuild) |
|---|---|
| Static intake port, connected clients, live feed | **Live TCP** server (`TcpServerSource`, `/api/listen`, `job.clients`, WS feed) |
| Identify device by protocol / manual selection | **Codecs** + `codec.detect()` (auto) and the Protocol dropdown; the **Protocols** repo is the "protocol doc" |
| Record received packets | **Live store** + **continuous packet logging** (`PacketLog`) + `captures` table |
| (later) forward/persist | existing sinks (`memory_store`, `webhook`, `tcp_relay`, SQLite) |

New pieces QC adds: a **QC session** model, a **checks/criteria engine + report**, an **iNTouch
client**, and a **serial (RS232) source**. The target design in [DESIGN.md](DESIGN.md) already
anticipates serial/USB/BLE device I/O, so the serial source slots into the existing `Source`
contract.

## The five capabilities, split into phases

> We agreed to build in steps. Proposed order (self-contained core first, external integrations
> later since they need outside specs):

| # | Capability | Phase | Depends on / needs |
|---|---|---|---|
| 1 | **Intake + identify** — device lands on a static QC port; auto-identify by protocol (`detect`) or **manual protocol selection** | **Target 1** | reuses Live TCP + codecs |
| 2 | **Record received packets** — per-QC-session capture saved to disk + indexed | **Target 1** | reuses PacketLog + `captures` |
| 3 | **Device report** — run QC checks over the session → pass/fail + exportable report | **Target 2** | needs the **QC pass/fail criteria** from you |
| 4 | **iNTouch onboarding + status** — register the device on iNTouch and verify its status there | **Target 3** | needs **iNTouch API** (base URL, auth, endpoints) |
| 5 | **RS232 configuration** — configure the device for QC over serial | **Target 4** | needs **device serial command set** + `pyserial` |

## Data model (new)

- **QcSession**: `id`, `port`, `protocol` (auto/selected), `device_id/IMEI`, `started`,
  `ended`, `status` (running/pass/fail/aborted), `packet_count`, `capture_path`, `operator`,
  `notes`, `checks[]`, `intouch` (onboard result).
- **QcCheck**: `key`, `label`, `expected`, `actual`, `result` (pass/fail/na), `detail`
  (e.g. "GPS fix acquired", "External voltage in 11–15 V", "≥1 valid location in 60 s",
  "IMEI present", "packet cadence ≤ N s").
- Persist QcSession/checks in the SQLite rail (extend the schema — append DDL, per convention).

## Backend components (new modules)

- `proinfo/qc/engine.py` — evaluate a session's records against a **check list** → results.
- `proinfo/qc/report.py` — render a report (HTML → printable/PDF; JSON/CSV export).
- `proinfo/qc/session.py` — QcSession lifecycle over a listener + capture.
- `proinfo/integrations/intouch.py` — iNTouch client (`onboard(device)`, `status(device)`).
- `proinfo/sources/serial_port.py` — RS232 `Source` (pyserial): read + `send()` config commands.
- API: `POST /api/qc/start` (port, protocol|auto), `POST /api/qc/{id}/stop`,
  `GET /api/qc/{id}` (live status + checks), `GET /api/qc/{id}/report(.pdf|.csv)`,
  `POST /api/qc/{id}/onboard` (iNTouch), `POST /api/qc/{id}/configure` (RS232).

## UI flow (new page: Capture → Device QC)

1. **Start QC**: choose port + protocol (**Auto-detect** or manual) → "Waiting for device…".
2. **Device connects** → show identified protocol + IMEI, live packet feed (reuse Live TCP feed).
3. **Live checks** tick green/red as criteria are met; recording runs in the background.
4. **Finish** → verdict **PASS/FAIL**, **report** (view / download), **Onboard to iNTouch**
   button + status, and (if wired) **Configure via RS232**.

## Open questions / what I'll need from you

- **#3 criteria**: the exact QC pass/fail checks per protocol (thresholds, required fields,
  time window). Can start with a sensible default set and make it editable.
- **#4 iNTouch**: API base URL, auth (token/key), the onboard + status endpoints and payloads.
- **#5 RS232**: the device's serial settings (baud, framing) and the config command set;
  add `pyserial` as an optional dependency.

## Decisions (confirmed)

- **Structure:** a **new "Device QC" section** (Capture → Device QC) that reuses Live TCP /
  codecs / store / logging under the hood.
- **First build = Target 1:** #1 intake + identify (auto or manual protocol) **+** #2 record.
  Report (#3), iNTouch (#4) and RS232 (#5) come later.

## Target 1 — build breakdown (intake + identify + record)

Essentially a guided wrapper over the Live TCP listener + packet logging, with device
**identity** surfaced and a saved **QC session**.

- **Backend**
  - `qc_sessions` table (append DDL): `id, port, protocol_mode(auto|manual), protocol,
    device_id, started, ended, status, packet_count, capture_path`.
  - `POST /api/qc/start {port, protocol}` → starts a `TcpServerSource` listener **and** a
    `PacketLog` capture scoped to the session; creates a QcSession row. (`protocol="auto"`
    → codec auto-detect; else the chosen codec.)
  - Identify: read the first decoded records' `protocol` + `device_id` (IMEI) from the
    session's pipeline/store; store on the QcSession.
  - `POST /api/qc/{id}/stop` → stop listener + log; finalize row.
  - `GET /api/qc` / `GET /api/qc/{id}` → live status (protocol, device_id, packet_count,
    connected, capture_path).
- **UI** — new `qc.html` / `qc.js`, rail item under Capture:
  1. **Start QC**: Port + Protocol (**Auto-detect** or manual pick) → "Waiting for device…".
  2. On connect: show **identified protocol + IMEI**, a live packet feed (reuse the Live TCP
     feed), running **packet count**, and the capture file path.
  3. **Stop** → session saved (identity + capture retained) — the foundation report (#3)
     will later run over this saved session.
- **Reuse:** `TcpServerSource`, `Pipeline`/codec `detect()`, `PacketLog`, `store`; no new
  decode logic.

## Target 1 — as built

Matches the planned breakdown; implemented as new files + small additions to existing ones
(no changes needed to the codecs, Live TCP, or the pipeline's public behavior).

- **`proinfo/storage/db.py`**: new `qc_sessions` table (append DDL) +
  `Storage.qc_sessions()/qc_session()/qc_update()`.
- **`proinfo/qc/session.py`** (new): `QcCaptureSink(Sink)` — plugged into the QC job's
  `Pipeline` alongside the normal sinks. Learns `identified_as`/`device_id` from each
  decoded `UnifiedRecord` (works under `protocol_mode=auto` since identity isn't known
  until the first packets arrive) and appends every record to a per-session capture file
  (flush + throttled `fsync`, same durability approach as `PacketLog`).
- **`proinfo/api/app.py`**: `QcStartBody`; `_qc_sinks` (qc_id → sink, live counters while
  running); `POST /api/qc/start` (starts a `TcpServerSource` + `Pipeline` — reusing
  `_default_sinks()` so QC traffic still reaches the Dashboard — plus the `QcCaptureSink`;
  creates the DB row); `POST /api/qc/{id}/stop` (instant status flip, same pattern as
  `/api/stop/{job_id}`); `GET /api/qc` / `GET /api/qc/{id}` (`_qc_view` merges the DB row
  with live sink counters + the job's `connected` state); `GET /api/qc/{id}/capture`
  (download the capture file). *(Also fixed an unrelated pre-existing wart: two stray
  unused fields (`signature`, `status`) had been left on `LogStartBody`.)*
- **`proinfo/web/qc.html` + `qc.js`** (new), rail item **Capture → Device QC**: Start form
  (Port, IP auto-filled from the detected LAN IP, Protocol Auto-detect/Manual, Operator) →
  session cards (status, uptime, packet count, identified protocol once known, device id,
  Stop, Download capture once stopped) → click a card to focus it and see its live packet
  feed (HEX/ASCII, remembered; click a row for the full packet). Multiple concurrent QC
  sessions supported (bench stations). Feed re-seeds from the WS snapshot on navigation
  (retention hard rule).
- **Tests**: `tests/test_api.py::test_qc_start_identify_record_stop` — starts a QC session
  in `auto` mode, connects a real socket, sends a Teltonika handshake + AVL record, asserts
  `identified_as`/`device_id`/`packet_count`/`connected`, stops, and verifies the capture
  file on disk contains the IMEI and is downloadable. Full suite: **42 passing**.
- Also manually verified end-to-end over the ASGI transport: static `qc.html`/`qc.js` serve,
  a real TCP client identifies as `teltonika`, and the capture file has the expected
  `DD-MMM-YYYY HH:MM:SS:zzz | device_id | raw_hex` lines.

### Fixes from feedback + a dedicated adversarial review pass

- **Resume, not just Start/Stop**: Start/Stop alone meant re-landing a device on the same
  port always created a brand-new QC session row — there was no way to bring the *same*
  session back. Added `POST /api/qc/{id}/resume` (mirrors `/api/resume/{job_id}` for Live
  TCP): same `id`, packet count carries over (doesn't reset to 0), capture file is appended
  to (never truncated). "Start" on the form still always makes a distinct new session by
  design — that's correct for QC's audit-trail model (each physical unit gets its own
  record) — Resume is the explicit action for "bring this exact session back".
- A full line-by-line adversarial re-read of `session.py`/`app.py`/`qc.js` (prompted by
  direct feedback that bugs were being found by the user instead of caught up front) turned
  up two real, previously-unverified bugs, each with a regression test proving the fix:
  - `QcCaptureSink`'s "is this a real device id yet" check only excluded Teltonika's
    `"teltonika-unknown"` placeholder. GT06's `"gt06-unknown"`, NMEA's `"nmea-device"` (NMEA
    carries no device identity at all) and the template's `"device-1"` were **not**
    excluded — so under Auto-detect, those protocols' placeholder strings could show up in
    the QC UI as if they were a real device id. Fixed by completing the exclusion set (and
    simplifying the confusing identified_as if/elif into one condition).
    Test: `test_qc_manual_mode_and_gt06_device_id_not_placeholder` (also the first test to
    exercise `protocol_mode=manual` at all).
  - A QC session's status could get stuck showing `"running"` forever if its listener died
    asynchronously (e.g. the port was already taken) — the failure lands on the job
    machinery, not synchronously on `/api/qc/start`, so nothing propagated it back into
    `qc_sessions.status`. Fixed with a self-heal check in `_qc_view` that reconciles the DB
    row against the live job status the next time the session is viewed/polled.
    Test: `test_qc_self_heals_status_on_bind_failure` — starts two sessions on the same
    port and confirms the second flips to `error` instead of staying `running`. (Writing
    this test caught a bug in the fix itself — the DB update wasn't mirrored into the
    dict being returned from that same call — fixed before it shipped.)
  - Smaller: the QC page's WebSocket could connect and receive its initial snapshot before
    the first `/api/qc` fetch resolved, so `routeRecord` had nothing to match it against and
    silently dropped the re-seed on first load. Fixed by sequencing `connect()` after the
    initial `refreshSessions()` resolves.
- Full suite: **48 passing** (up from 42).

### IMEI as the device's unique identifier

The session id/port are just this run's plumbing; the device_id (IMEI) is the device's real,
durable identity, so it should be what the system recognizes a device *by* — including across
separate sessions/ports/days, not just within one run.

- **`Storage.qc_sessions_by_device(device_id)`**: every QC session ever run for a device id.
- **`_qc_view`**: once `device_id` is known, adds `prior_sessions` — every *other* session for
  that same device id (id/status/started/ended/packet_count/port), so a returning device is
  recognized regardless of which port/session it lands on this time.
- **UI**: the IMEI is now the card's primary headline (session id/port demoted to a secondary
  line) once known; a **"🔁 seen before"** badge appears when `prior_sessions` is non-empty,
  naming the most recent prior session. A search box over the session list filters by IMEI or
  port.
- **Test**: `test_qc_device_id_is_unique_identifier_across_sessions` — same IMEI landing in two
  independent (non-resumed) sessions on different ports is recognized as the same device via
  `prior_sessions`, while an unrelated device's session never shows up in that history.
  (Caught + fixed a cross-test pollution bug while writing it: this test suite shares one
  SQLite DB for its whole run, so reusing another test's IMEI would have broken the "first
  time seen" assertion — each QC test now uses its own distinct IMEI.)
- Full suite: **51 passing**.

## Target 2 — plan (pass/fail checks)

Runs a small set of checks over the records a QC session has decoded so far — ticking live
while the session runs, finalized (and persisted) on Stop. The exportable report
(HTML/PDF/CSV) is a separate follow-up step once live checks prove out; iNTouch (Target 3)
and RS232 (Target 4) are unchanged.

**Default check set** (grounded in fields `UnifiedRecord` already has — see
`proinfo/core/models.py` — not fabricated per-protocol fields):

| key | pass condition | na when |
|---|---|---|
| `imei_present` | `device_id` learned and not a placeholder (reuses `session.py`'s `_UNKNOWN_DEVICE_IDS`) | never |
| `location_fix` | ≥1 record with a valid position (`Position.is_valid`) within `location_window_s` (default **60s**) of session start | — |
| `gps_valid_flag` | ≥1 record with `gps_valid == True` | protocol never populates `gps_valid` at all |
| `packet_cadence` | largest gap between consecutive packets ≤ `cadence_max_s` (default **30s**) | fewer than 2 packets |
| `external_voltage` | every `external_v` reading within `[voltage_min, voltage_max]` | no range configured, or field never present |

**Verdict**: `fail` if any check fails; `pass` if ≥1 check ran and none failed; otherwise no
verdict yet.

**Data model**: append `checks_json TEXT DEFAULT '[]'`, `verdict TEXT DEFAULT ''` to
`qc_sessions`.

**Backend**:
- `proinfo/qc/engine.py` (new, framework-free like `session.py`): `DEFAULT_CHECKS` +
  `evaluate(records, meta, config) -> (checks, verdict)`.
- `QcCaptureSink` keeps the session's decoded records in memory too (bounded, e.g. last
  5000 — bench sessions are short), and recomputes checks on each `handle()`, same
  throttled path as the existing live counters/`on_update`.
- `_qc_view` surfaces live `checks`/`verdict`; `POST /api/qc/{id}/stop` persists the final
  values to the DB row.

**UI**: each check renders as a green/red/gray chip with its detail text on the focused
session; a PASS/FAIL verdict badge appears once stopped.

**Open thresholds (need your input, defaults above are placeholders)**:
- `cadence_max_s` — is 30s the right ceiling for "device is still reporting"?
- `voltage_min`/`voltage_max` — no safe universal default (12V vs 24V vehicle systems); stays
  `na` until you give a real range, per protocol if it varies.

## Not in scope yet

Multi-station QC dashboards, label/QR printing, and a durable QC history browser — after the
core loop proves out.
