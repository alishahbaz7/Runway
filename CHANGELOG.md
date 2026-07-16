# Changelog

All notable changes to **Runway** (package `proinfo`) are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [1.1.14] — 2026-07-16

Feature release. Headline: the QC feed gains an inline **Raw | Summary** view (no more
popup), and a **persistent, IMEI-keyed device registry** gives every device a stable
serial number and automatic recall of its last QC result.

### Added — QC Summary view (inline, replaces the details popup)
- **Raw | Summary toggle** right on the Live Packets pane: *Raw* is the packet stream,
  *Summary* shows the selected device's full qualification view (device details, Packets +
  Alerts coverage, deviations, test cases, Pass-with-conditions / Fail actions) inline in
  the same pane — live-updating, no modal overlay. The status pill (`IN PROGRESS · 2/13`)
  stays visible in both views and jumps to Summary on click.
- **Multi-device Raw selection**: each QC device row has a checkbox (plus All/None) to show
  every device, a partial set, or a single device in the Raw stream — same pattern as the
  Testing tab's per-peer filtering. Row-click still separately selects the Summary device.
- **Received column** in the coverage tables shows an inline preview of the actual latest
  captured packet per item (click for the full parsed/ASCII/HEX view), replacing the old
  "view" button; the Skip checkbox hides once an item has been received.
- **Per-row reference tooltips**: hovering an Item name (and only the name, after a ~2s
  rest) shows its description plus the reference sentence rewritten for *that* row — the
  expected packet-type code and alert id substituted in and highlighted (e.g. Emergency
  state ON highlights `EA`,`10`), instead of one generic example for every alert.
- Coverage tables use fixed column widths with proper word-wrap (Received gets the
  majority share); alert rows read `Label (id - CODE)`, e.g. `Connect back to main
  battery (6 - BR)`.

### Added — persistent device registry (IMEI-keyed)
- New durable `qc_devices` table: every device that ever identifies on a QC session gets a
  **stable serial number** (`#1`, `#2`, …) — the same IMEI keeps its number across
  reconnects, sessions, and restarts; a genuinely new IMEI gets the next one. Shown as a
  badge in the device list and in the Summary device details.
- **Automatic QC-status recall**: the moment a known IMEI connects to any listener, the
  Summary panel shows its last FINAL result ("Previously tested: CONDITIONAL (session
  #N)") while the new run is still in progress. In-progress sessions never overwrite the
  registry — only final verdicts (pass / conditional pass / fail / timeout) are recorded.

### Fixed
- Coverage-table Item labels never wrapped (an app-wide `tbody td { white-space: nowrap }`
  rule silently forbade line breaks) and overflowed under the Req column; they now wrap
  within their own cell.
- The QC tab title no longer shows an IMEI (a port can host many devices) — protocol only.
- On narrow windows the feed-header controls could overlap and block clicks; the header
  now wraps onto a second row.
- The per-row reference example previously corrupted two fields via an off-by-one token
  substitution (the `$NMP` header occupies token 0); fixed and verified against the real
  sentence layout.

[1.1.14]: https://github.com/alishahbaz7/ProInfo/releases/tag/v1.1.14

## [1.1.8] — 2026-07-15

Feature release. Headline: **Device QC** grows from a basic pass/fail into a real
per-device qualification workflow, and **Live TCP + Device QC merge into one Capture
page** with a per-tab Testing/QC mode.

### Added — Capture page (Live TCP + Device QC merged)
- **One page, one core module** (`capture.html`/`capture.js`) replaces the separate Live TCP
  and Device QC pages — `tcp.html`/`qc.html` now redirect here. Each session tab picks
  **Testing** (raw inspection, any protocol, log/download — today's Live TCP behavior) or
  **QC** (validated per a coverage profile) when it's started; both kinds of tabs coexist,
  each with its own feed chrome.
- Shared core: browser-style tabs, per-port/session device list, colour-coded live feed
  (live/history/alarm/unknown, ⓘ legend popover), packet/send modals, collapsible device
  list, draft/"+" new-session flow — implemented once instead of duplicated across pages.
- Rail collapses "Live TCP" + "Device QC" into a single **Capture** item.

### Added — Device QC: per-device coverage & verdict
- **Per-device grouping (by IMEI)**: a device that reconnects (new peer/port) merges into
  one row instead of showing as a duplicate; QC coverage, checks, and verdict are computed
  **per physical device**, not lumped across the whole port session. Multiple devices on one
  QC port each get their own coverage view — click a device row to select it (drives the
  feed, the summary pill, and the details modal).
- **Coverage-driven verdict states**: `in_progress → timeout → pass / conditional_pass /
  fail`, replacing a flat pass/fail. A session goes to **timeout** after ~10 minutes of no
  data (regardless of whether a device ever identified) and can be **Resumed**. A required
  item the operator marks **optional/skip** no longer blocks completion — it settles as
  **Conditional Pass** instead, with every skip/deviation listed for the record.
  Operator actions: **Pass with conditions** / **Mark Fail** / clear back to automatic.
- **QC details modal redesign**: device details (IMEI / firmware / vendor id), coverage
  split into **Packets** and **Alerts** sections (alert rows show `Label (id - CODE)`,
  e.g. `Connect back to main battery (6 - BR)`), a **Received** column linking to the
  captured sample packet, and hover-to-see the item's description + reference packet +
  what was actually received. Actions live in the modal header (top-right) to keep the
  body free for coverage. A single verdict+coverage pill (e.g. `IN PROGRESS · 2/13`)
  replaces the old separate checks-chip row and duplicate verdict badge.
- **QC coverage profile** (`proinfo/qc/profiles/<protocol>.json`, overridable under
  `data/qc/profiles/`): declares the expected packet types + alert ids for a protocol,
  which are required vs optional, and display grouping — editable without code changes.
- New endpoints: `POST /api/qc/{id}/skip`, `POST /api/qc/{id}/finalize` (both per-device);
  `/api/qc` and the report now return per-device `devices[]` with their own coverage.

### Fixed
- **AIS-140 / Locate 140B codec**: real firmware 2.2.5 sends the health packet as `$HEL`
  (the v1.9 doc only names `$HLP`/`$HLM`) — health packets were misclassified as
  `unknown`; now parsed correctly. `alert_id`, `packet_type`, `vendor_id` and
  `firmware_version` are now exposed on every PVT/NMP record (needed for coverage keying
  and the device-details panel).
- **Live TCP / Capture: sessions could not be closed while a device stayed connected.**
  `TcpServerSource.close()` called `server.wait_closed()`, which blocks forever with a live
  socket still open — a cancelled session's task never left the running set, so stop+close
  looped on "stop the session first". `close()` now drops every live connection (and bounds
  the wait with a timeout) so teardown always completes.
- QC sessions surviving a server restart (stale `job_id`, "running" forever) are now
  reconciled on startup and self-heal on view; closing a tab no longer resurrects via a
  late poll/WebSocket update.

[1.1.8]: https://github.com/alishahbaz7/ProInfo/releases/tag/v1.1.8

## [1.1.0] — 2026-06-30

Feature release. Headline: a **Live TCP server**. Also adds the **Protocols** repository,
the **Converter** (CanPlus XML → JSON/DBC), and a **standalone `Runway.exe`** build.

### Added — Live TCP server (headline feature)
- **Capture → Live TCP** page: start a TCP listener (codec + port + bind address), see all
  running listeners (status, uptime, records, bytes, connected clients), and stop them — all
  updating live over the WebSocket.
- **Connected-clients list** per listener: the TCP source now emits connect/disconnect events
  to the hub (`{"type":"conn"}`); the running-job view reports live `records`/`bytes`/`peers`.
- **Live decoded-packet feed** on the page (time / device / type / position / speed).
- **Downlink / send command** to a connected device — `POST /api/send/{job_id}` (`text`
  built via the codec's `build_command`, or raw `hex`); `TcpServerSource.send(peer, data)`.
- **Remembered settings**: last codec/port/bind stored in `Config`; `GET /api/listen/defaults`
  pre-fills the Start form (no auto-start — fresh-start convention kept).
- Decoded live traffic continues to flow to the Dashboard store + WebSocket as before.
- **Resume / Remove**: a stopped listener can be resumed **in place** (same card, same
  config) instead of spawning a new one (`POST /api/resume/{id}`); finished/stopped cards
  can be removed (`DELETE /api/jobs/{id}`).
- **Raw view for unknown packets**: the live feed shows each packet's raw bytes with a
  **HEX / ASCII** toggle — so traffic from an unmatched protocol is still readable.
- Labels: **Codec → Protocol**, **Bind → IP**; the IP field auto-fills with this machine's
  detected LAN IP and the page shows where devices should dial in. Protocol defaults to
  **Auto**; the **last-used port** is remembered.
- Fixes: a stopped listener's uptime now **freezes** (it no longer keeps counting after the
  server/listener stops). Tests run against an isolated `RUNWAY_DATA_DIR` temp folder so they
  never overwrite your real remembered settings.
- **Generic listener**: explicit "Generic (raw)" protocol option; generic/unmatched rows show
  **timestamp + the complete packet** (no wrap, horizontal scroll for any length); click a row
  for the full HEX/ASCII view.
- UX: listeners show a single consistent `IP:port`; **Stop/Resume** stay in one position with a
  spaced, confirming **Remove**; live card refresh + instant Stop; the packet feed persists
  across navigation (re-seeds from the server snapshot). The HEX/ASCII view mode is remembered.
- **Per-client control**: pick which connected clients' packets appear in the feed (hide ≠
  stop) with per-client checkboxes + All/None; each client shows a **packet count** and
  **idle-since-last-packet**, tracked **server-side** so they survive page reloads.
- **Continuous packet logging** (server-side): **Start/Stop log** streams every new packet to
  a file in a **chosen folder + filename** (TXT/CSV, ASCII/HEX), timestamped
  `DD-MMM-YYYY HH:MM:SS:zzz`. Durable — **flushed per packet + fsync'd** (crash/power-safe),
  **append-only** (never truncates). Separate from the one-time **Download**; a **log-file**
  button downloads the server file. Endpoints: `POST /api/log/start|stop`, `GET /api/log/status|file`.
- Feed: sticky **`Timestamp | Payload`** header; ASCII view renders `CR`/`LF` as line breaks
  so multi-sentence dumps read cleanly. Destructive **Close / Clear / Stop** highlighted red.

### Added — Standalone build
- **`Runway.exe`** one-file Windows build via PyInstaller (`packaging/Runway.spec` +
  `packaging/runway_main.py`) — teammates run it with no Python install; double-click starts
  the server + opens the dashboard. `DATA_DIR`/`WEB_DIR` are now frozen-aware and plugin
  discovery has a frozen fallback so codecs still load inside the exe. See `packaging/README.md`.

## [1.0.0] — 2026-06-30

First release. Runway — "where telematics data lands & takes off" — parses, visualizes and
relays telematics packets from any IoT tracking device, regardless of OEM/protocol.

### Core
- **Source → Codec → Sink pipeline** with a normalized `UnifiedRecord` contract; streaming,
  framing-aware decode (`decode(buffer, session) → DecodeOutput(consumed, …)`), per-session
  codec caching, and plugin auto-discovery (`@register_codec/source/sink`).
- **Codecs:** Teltonika (AVL Codec 8 / 8E / 16 + GPRS 12 / 13 / 14, IMEI handshake,
  CRC-16/IBM, server ACK), GT06 / Concox, NMEA-0183, generic hex, and a copy-me template.
- **Sources/sinks:** file replay, TCP server; console, in-memory store, WebSocket broadcast,
  webhook, TCP relay.

### App & UI
- **FastAPI** REST + WebSocket server hosting a single-page UI with a collapsible left rail
  (Runway banner, Live/Demo mode).
- **Dashboard:** live map, device registry with multi-select visibility + source/liveness
  badges, recent-packets window, click-to-highlight on the map.
- **Packet Parser:** multi-tab (one independent session per device), paste/upload hex,
  streaming decode with live progress, resilient skip-and-resync on corrupt frames,
  transposed signal grid, AVL-ID → name resolution, foldable map, selective CSV export,
  Summary panel; state persists per browser session.
- **Configuration → Signatures:** repository to add/preview/edit/delete decode signatures,
  with signature-driven sub-signal visualization in the parser.
- **Configuration → Protocols:** format-agnostic repository of transferable parser files
  (`.py`, `.json`, `.yaml`, …) — drop a file in, another utility can parse with it.
- **Tools → Converter:** upload a CanPlus `*.Config.XML` vehicle signature, view/edit the
  CAN signals, and export to **JSON** and **DBC**.
- **Diagnostics → Logs:** rotating activity log + a kept errors/warnings log, with a viewer
  (source/level filter, search, auto-refresh, download, clear) and a global exception handler.

### Persistence
- Thread-safe SQLite storage rail (`Storage` + `Config`) under `data/` (git-ignored).
  A fresh server/browser session starts clean; the parser retains within a session.

### Notes
- Internal connectivity verified: `proinfo/convert` is isolated (lazily imported by its
  endpoints; not a registered plugin), so the pipeline, dashboard and live store/hub are
  unaffected.

[1.1.0]: https://github.com/alishahbaz7/ProInfo/releases/tag/v1.1.0
[1.0.0]: https://github.com/alishahbaz7/ProInfo/releases/tag/v1.0.0
