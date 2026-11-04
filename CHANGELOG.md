# Changelog

All notable changes to **Runway** are recorded here (released as *ProInfo* through v1.1.23 —
see the 1.1.24 entry for the rename).
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [1.1.24] — 2026-11-04

Rename release. Headline: **the project is called Runway everywhere.** No behaviour changes.

### Changed — release packaging
- **Everything in a release carries one name, `Runway-v<version>`**: the zip, the folder it
  unpacks to and the exe itself (`Runway-v1.1.24.exe` inside `Runway-v1.1.24\`). The old
  `Runway-<version>-win64.zip` held a bare `Runway.exe`, so two installs side by side were
  indistinguishable in Explorer, the taskbar and a bug report. The name is spelled once, in
  `packaging/build_release.py`; `Runway.spec` imports it.
- **Upgrading needs one manual step, now written down.** The exe keeps its `data` folder
  beside itself and each version unpacks to its own folder, so a new version opens empty until
  the previous `data` folder is moved in. `GETTING_STARTED.txt` says so. On the build machine,
  `build_release.py` carries it over itself — never into the zip — and stops, naming the
  folders, when more than one earlier install holds data rather than guess which one is live.
  A rebuild no longer wipes a `data` folder created by running the exe from its build folder.
- **One version declaration: `runway/__init__.py`.** `pyproject.toml` reads it (`dynamic`), and
  the UI no longer carries its own copy — `/api/health` now reports `code_version` (the code
  on disk) next to `version` (the running process), so the "restart the server / reload the
  page" banner keeps working without a literal to forget. Versions are compared numerically:
  the old string comparison would have ranked `1.1.9` above `1.1.24`.
  `tests/test_version.py` fails the suite on a second version literal, a CHANGELOG whose newest
  section is not the current version, or a version branch that doesn't match it.

### Fixed
- **`Runway-v<version>.exe --version` (and `python main.py --version`) print the version.**
  Both launchers default to `serve` when the first argument isn't a sub-command, so `--version`
  and `--help` were handed to `serve` and rejected as unknown flags — the one command that
  answers "which build is this?" without starting a server didn't work on the exe.

### Changed
- **`ProInfo` → `Runway` across the whole project.** The app, the exe and the UI were already
  branded Runway while the repo, the Python package and the CLI still said `proinfo` — two names
  for one thing, which cost time on every navigation. Now: repository/folder `Runway`, package
  `runway/` (was `proinfo/`), imports `from runway...`, console script **`runway serve`** (was
  `proinfo serve`), distribution name `runway`. 261 occurrences across 61 files; the names that
  were already correct (`Runway.exe`, `RUNWAY_DATA_DIR`, `data/runway.log`, the `runway.*`
  browser-storage keys) are untouched, so **no saved setting or page state is lost**.
- **The database is `data/runway.db`** (was `data/proinfo.db`) and **adopts the old file on the
  first start** — devices, captures, profiles, QC devices and settings all carry over with no
  manual step (`db.adopt_legacy_db`). The `-wal`/`-shm` siblings move with it, so writes that had
  not been checkpointed yet are not lost. Adoption also fires when a *row-empty* `runway.db`
  already exists, which is what you get if the app is started once before migrating — without
  that, a single stray start would have orphaned the history silently. A `runway.db` that holds
  real rows is never overwritten, and a locked file is skipped rather than failing the start.

### Notes
- `ProInfo` (Pro=protocol, Info=information) survives only in this changelog. Releases up to and
  including 1.1.23 were published under that name.
- **After pulling this, restart the app** — a running server is still executing the old
  `proinfo/` module tree, and the database adopts itself on the next start.
- Reinstall if you had an editable/console-script install: `pip install -e ".[dev]"` (the old
  `proinfo` command is gone).

## [1.1.23] — 2026-08-03

Protocol release. Headline: **Zenithra Ax1 sCAN is onboarded** — an AIS-140 base packet that
carries the vehicle CAN bus inline.

### Added
- **New protocol: Zenithra Ax1 sCAN** (`zenithra`) — `$MMI` ASCII/CSV sentences, 38 fields.
  The base packet is AIS-140, so the codec (`runway/codecs/zenithra.py`) *derives* from the
  AIS-140 codec and reuses its framing, field helpers, packet-type/alert vocabularies and
  login/health/emergency builders; what it adds is the `$MMI` layout, which diverges from
  field 26 on (no `emergency_status`, no neighbour-cell triples, no odometer, and
  `frame_number` moved to the very end after the OTA field).
- **CAN data is decoded structurally.** The sentence's CAN field (`;`-separated
  `<hex id>:<hex payload>` frames) is located *by content*, so a firmware that shifts the
  tail still yields its bus data. Each frame arrives in `io["can"]` as
  `{id, id_int, extended, data, dlc}` — raw bytes preserved, plus the numeric id and its
  11-bit/29-bit class — with `io["can_frame_count"]` on every packet (0 when the CAN slot is
  empty, which is a normal packet, not an error). No signal scaling is applied: that needs a
  per-vehicle DBC/CanPlus signature, not a protocol.
- **Protocol repository entries**: `data/protocols/zenithra.json` (declarative field grammar,
  every field mapped against the AIS-140 base, with each assumption flagged) and
  `data/protocols/zenithra.py` (zero-dependency standalone parser for transfer to other
  utilities) — the same pair AIS-140 and Teltonika ship.
- **CAN frames are visualized, not dumped.** A packet's bus frames now render as a table
  (CAN ID · 11/29-bit · DLC · payload bytes) in the **Dashboard inspector** and in the
  **Capture packet view**, and are folded out of the JSON blocks with a `[n frames]`
  placeholder — a single packet carrying two dozen frames used to push every other decoded
  field off the screen. Payloads stay raw on purpose (engineering values need a per-vehicle
  DBC/CanPlus signature, which lives in the Converter). An opt-in **CAN frames** column in
  the Dashboard packet table shows the per-packet frame count.

### Added — bind a vehicle signature to a device
- **A signature bound to a device decodes that device's CAN frames everywhere.** The binding is
  applied by a new pipeline hook (`Pipeline(..., enrich=…)`) that runs once per record *before*
  any sink sees it, so the decoded signals show up on the **Dashboard**, in the **Capture packet
  view**, in a QC session's capture and in the Parser's breakdown — one binding, every surface,
  no per-view wiring. Bindings live in `settings` as `{device_id -> signature}` with `"*"` as the
  any-device default, are looked up per record (so binding takes effect on the **next packet** —
  no restart, no relisten), and are managed on the **Signatures** page (`GET`/`PUT
  /api/signature-bindings`).
- **Decoded signals arrive as `io["can_signals"]`** — `{name, value, can_id, raw}` per signal,
  plus `io["can_signal_count"]` and `io["can_signature"]`. The raw integer and source frame ride
  along so an operator correcting a start bit can see exactly what was read. Dashboard and
  Capture render a **CAN signals** table above the raw frames (and an opt-in *CAN signals* column
  in the Dashboard packet table); both lists are folded out of the JSON dumps with a count.
- **Static capture uses the same decoder**: the Parser's existing Signature control now travels
  with the request (`signature` on `/api/parse`), so a pasted packet reads exactly like a live
  one. Naming no signature falls back to the pasting device's own binding.
- **The signature format's two key styles are kept apart** (`runway/convert/can_signals.py`):
  8-hex-digit keys are CAN frame ids and bind to a device; short decimal keys are Teltonika AVL
  parameter ids and are left to the Parser's AVL view. Without that guard an AVL id like `900`
  would silently bind to CAN id `0x900` and invent signals. Scaling is the canonical DBC
  convention — `physical = raw × multiplier + offset` — matching `CanSignal.decode` and the DBC
  export, and a signature row that overruns its payload is skipped rather than losing the whole
  frame's signals.

### Added — the Parser works for every protocol, not one
- **`Codec.explain()` — a new optional plugin hook.** A codec that can say more than a generic
  walk (bit-level sizes, verified CRC, a parameter catalogue) returns its own breakdown;
  `teltonika.py` does, so its field tree, CRC check and AVL grid are unchanged. Every other
  codec gets the protocol-agnostic walk in `runway/codecs/generic_explain.py` for free. **No
  caller branches on a protocol name any more.**
- **The generic walk is driven by the protocol database.** Framing and values come from the
  codec's own `decode()`; field names, units and value tables come from the declarative grammar
  in `data/protocols/<codec>.json` — so the Parser shows the vendor's field names with per-field
  byte offsets, and clicking a row highlights exactly those characters in the pasted text.
  Grammars are read from the user's repository first and the shipped copy as fallback, so field
  names survive a custom `RUNWAY_DATA_DIR`, a cleared `data/`, and a bare exe.
  CAN payloads are expanded to one row per frame; anything the grammar hasn't caught up with is
  still listed (positionally, or from the decoded record), so a codec with no grammar published
  yet is inspectable the moment it is dropped in.
- **A Protocol selector on the Parser page**, populated from `/api/codecs` — "Auto-detect" by
  default, with any registered codec selectable to force one. The result line names the codec
  that actually read the data, and the Dashboard sync now ingests through that same codec.

### Fixed
- **The Parser could not read any ASCII protocol at all.** Input was filtered down to its
  hex-looking characters before anything parsed it, so a pasted `$MMI,…`/`$GPRMC,…` sentence was
  shredded into nonsense bytes and came back "0/0 packet(s), 0 record(s)"; `/api/parse` then ran
  the Teltonika explainer regardless of what the data was. The endpoint now decides *hex dump vs
  device text* from the content (a paste's missing line terminator is restored for line-framed
  protocols), and picks the codec by detection or by the operator's choice. Text that no codec
  recognises is reported as such instead of being passed off as a decode; an unrecognised **hex**
  dump still falls back to a generic hex view.
- **Auto-detect no longer mis-identifies a Zenithra device as AIS-140.** The device opens the
  connection with a plain AIS-140 `$LGN` login and only then sends `$MMI` data. Since a
  session's codec is chosen once, from the first sample, auto-detect scored `ais140` 0.90 on
  that login and locked the session onto it — every subsequent `$MMI` packet then decoded as
  `message_type: "unknown"` with no fields (seen live on the bench at :4082). The Zenithra
  codec now recognises its own login: the protocol-version field carries the literal `SCAN`
  where AIS-140 sends `AIS140`, which scores 0.95 and wins — while a genuine Locate 140B login
  is untouched and still goes to `ais140`. Covered by a regression test that runs the real
  captured session (login + both `$MMI` packets) through `_pick_codec` end to end.
- The AIS-140 login's firmware-by-content match now accepts a build letter (`5.1.29C`, not
  just `01.3.0`), so firmware shows up in device details for this family.

### Changed
- `Ais140Codec` exposes its sentence-length resync valve as `max_sentence` (was a module
  constant) so a derived sentence family can raise it — Zenithra allows 8 KB because a busy
  CAN bus makes sentences far longer than 2 KB — and its placeholder device id is now derived
  from the codec name (`zenithra-unknown` for the derived codec, `ais140-unknown` unchanged).

### Notes / open items
- Confirmed against a live session: the `$LGN` login plus `$MMI` NR captures with and without
  CAN. Two field readings remain **provisional** and are flagged in both the codec docstring
  and the JSON grammar — field 26 read as `tamper_alert` (assuming AIS-140's `emergency_status`
  is the dropped field) and field 38 read as `frame_number` (not an odometer); neither blocks
  CAN work, so they stay as-is until the vendor doc lands. Only the `NR` packet type has been
  captured; health/emergency sentences have not been seen on this model.
- No QC coverage profile ships for `zenithra` yet, so a QC session on this protocol falls back
  to the generic pass/fail checks instead of a coverage checklist. The downlink command set is
  undocumented for this model, so no `ota_commands` are declared (the sender falls back to its
  free-text/hex mode rather than offering an unverified command).

## [1.1.22] — 2026-07-17

Fix release. Headline: **a QC session now retains its full Summary across any
interruption** — Stop/Resume, a crash, or a full server restart. Resuming yesterday's
session no longer starts the packet tables blank.

### Fixed
- **Resuming a QC session no longer blanks the Summary.** Coverage and device details
  were computed from in-memory records only, so a resume (or a server restart followed
  by Resume) restarted the Summary from zero even though every packet was safely in the
  session's capture file. On resume the capture sink now **rehydrates**: it re-decodes
  its own append-only capture file (`QcCaptureSink.rehydrate`), restoring the packet
  tables, per-item counts and captured samples, device identity, the coverage ledger and
  original receive timestamps — the session carries on exactly where it left off. The
  protocol persisted on the session row is used for re-decoding; sessions that never
  identified fall back to auto-detection, and unreadable lines are skipped.
- **The Summary survives a crash too**: per-device coverage/verdict/packet count are now
  persisted to the DB every few seconds while a session runs (previously only on a clean
  Stop), so a session that dies without one still shows its data in the list, before any
  Resume.

### Changed — QC Capture UI
- **Summary is now the default (and primary) view** of the Live Packets pane; Raw is the
  secondary tab. The Summary/Raw toggle lists Summary first. (An operator who explicitly
  switched to Raw before keeps that choice — only the first-time default changed.)
- **Feed header decluttered**: the "session #N · :port" caption is gone from the QC feed
  header (that identity already lives on the tab and the session strip); the **Checks**
  popup button, and the **Pass w/ conditions** / **Fail** buttons in the Summary panel,
  were removed. (The underlying `/finalize` and `/report` endpoints are unchanged.)

### Changed — Send command
- **IP/port SET drafts pre-fill with the live session's port.** Picking PIP/EIP/GIP in
  the send dialog now drafts `+S*R:SET:EIP#182.71.127.231,<this session's port>;` — the
  host is the bench's constant server address (authored in the protocol definition,
  updated to `182.71.127.231`), the port is whatever port the tab is listening on, so
  the device dials back to the right listener without hand-editing.
- **Send to multiple devices at once**: when several devices are connected on the port,
  the send dialog lists them with tick-boxes (the device whose ✈ was clicked is ticked
  by default) and sends the command to every ticked one, reporting per-device failures.

### Changed — QC report
- **The Summary sheet no longer carries the per-packet coverage columns** (Login …
  Device Tampered). That detail stays on each device's own IMEI sheet; the lot Summary
  keeps one readable row per device (identity, bench columns, required-coverage
  fraction, verdict, remarks) — matching the reference sheet's altitude.

### Changed — QC skip rule
- **Only bench-waivable items can be skipped.** The QC profile now marks items
  `"skippable": true`; a Skip control appears in the Summary only for those, and
  `POST /api/qc/{id}/skip` rejects (HTTP 400) any attempt to skip a mandatory item — so
  the rule is enforced server-side, not just hidden in the UI. For AIS-140 the skippable
  set is the five hard-to-trigger events: **Low battery, Low battery removed, Harsh
  Braking, Harsh Acceleration, Rash Turning** (all now `required` + `skippable`); every
  other item is mandatory. `skippable` is carried through `engine.coverage` and the ledger.

### Packaging — first shareable release
- **`Runway.exe` now seeds the protocol repository on first run.** The protocol
  definitions (the `ota_commands` catalogue behind the send-command dialog, incl. the
  AIS-140 IP/port setters) ship inside the exe and are copied into an empty
  `data/protocols` on startup — so the send catalogue works from a bare exe and survives
  a `data/` reset. Bundled via `packaging/Runway.spec`; seeded by `app.py:_seed_protocols`.
- **Release ZIP refreshed for the QC workflow**: `packaging/build_release.py` now writes a
  QC-first `GETTING_STARTED.txt` (Device QC session → coverage → report → send commands)
  and ships `samples/ais140_sample.log` alongside the Teltonika/NMEA samples.

### Added
- **Coverage ledger** (`runway/qc/ledger.py`): per-device QC state now *accumulates* —
  every record is folded in exactly once as it arrives, so a long session can no longer
  "forget" coverage it already earned when old packets fall out of the bounded in-memory
  window. Also the enforcement point for field validation: one implausible packet marks
  its coverage item INVALID (with the reason) instead of a silent OK.
- **Field validation (Step 2)**: packets matched to a coverage item are sanity-checked
  (coordinate ranges, 0,0-with-fix, event time >1 day in the future). An invalid item is
  a deviation — the device settles as **Conditional Pass** (never a clean PASS) with the
  reason printed on the report's Deviations section.
- **Lot details typed in-app** (▤ Lot details on the QC tab): the bench-only report
  fields — Mobile No, Data on Intouch, All I/O, Harness, Battery Charging, Ignition
  On/Off (Intouch), Plugged/Unplugged Alarm, COTA/FOTA, SMS/GPRS/Serial Command, power
  consumption (mA), Field Testing, Remarks per device, plus Lot/Batch id, Lot Size, PCB
  Number, Observation, Corrective Action and Quality Approved By — are saved with the
  session (`POST /api/qc/{id}/lot`) and **merged into the exported workbook**, so the
  download is the complete deliverable with no hand-editing in Excel.
- **Skip/finalize audit trail**: every skip asks for a reason (one prompt on "Skip
  missing"), and the report's Deviations section prints *what was skipped, why, by whom
  and when*. Finalize actions are audited too (`audit` on the session view).
- **Printed-report archive**: every successful export is saved verbatim under
  `data/reports/` (with a SHA-256 in the session's print log) and the export dialog
  shows "Printed ×N — last …", so the exact handed-over file is always recoverable and
  a re-print of a since-edited session is a conscious act.
- **Environment self-check banner**: every page compares its own version against
  `/api/health` and warns "restart the server" on mismatch (the stale-process trap),
  and warns when the server's Python lacks `openpyxl` (which used to surface only when
  the operator clicked Download). The server also logs a boot-time warning.
- **Daily DB backup**: one consistent copy per day under `data/backups/` (kept 14 days,
  sqlite online-backup API) — serials, verdicts, lot details and audit now live in that
  file, so it must never be the only copy.
- **`run-server.bat` pins the server's Python**: it creates and uses a repo-local
  `.venv` (installing dependencies on first run / when missing) instead of whatever
  `python` is first on PATH — three different Pythons on the bench machine is how the
  openpyxl surprise happened.

### Housekeeping
- `ais140-unknown` added to `UNKNOWN_DEVICE_IDS` — the placeholder could previously
  surface as a real device row in the QC UI before the IMEI was learned.
- Frozen (PyInstaller) builds: `ais140` added to the plugin fallback list so the codec
  still registers when module discovery comes up empty inside the archive, and the
  default QC coverage profiles (`runway/qc/profiles`) are now bundled into the exe —
  previously the packaged build had no coverage catalogue unless a user copy existed
  under `data/qc/profiles`.

[1.1.22]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.22

## [1.1.21] — 2026-07-17

Feature release. Headline: the **downloadable QC report** (XLS/CSV) — the per-lot
deliverable modelled on the bench's reference workbook (LOCATE 140B), exported straight
from the Capture page.

### Added — QC report export (XLS/CSV)
- **⤓ Report** button in the QC tab's feed header opens the export dialog: pick
  **Excel workbook (.xlsx)** or **CSV (.zip — one file per sheet)** and download.
  The format choice is remembered (localStorage).
- **Device selection lives in "Devices on this port"**: the same tick that filters the
  Raw feed now also decides which devices are in the report (all ticked by default).
  The dialog lists exactly who's included — serial badge, IMEI, live verdict — and
  updates as ticks change.
- **Sheet 1 "Summary"**: inspection header block (date, item name from the QC profile,
  session/port/operator, total devices tested, blank **Lot Size**) + one row per
  device — S.No, IMEI, serial, firmware, vendor, packet count, then **one column per
  coverage item** (OK / SKIP / MISSING / — / INVALID), required-coverage fraction,
  verdict (colour-filled), remarks (skipped/invalid items).
- **Reference-sheet placeholders for the bench-only fields**: every column of the lot
  sheet the utility can't observe ships **blank for hand entry** — Mobile No, Data on
  Intouch, All I/O (On Static), Harness, Battery Charging, Ignition On/Off (Intouch),
  Plugged/Unplugged Alarm on Intouch, COTA, FOTA, SMS/GPRS/Serial Command, power
  consumption (mA) with/without ignition, Field Testing — while GSM Fix / GPS Fix /
  Data on Static auto-fill "OK" from the observed traffic. Below the table sits the
  reference sign-off footer (Observation, Firmware Version, PCB Number, Corrective
  Action, Final Result, Quality Tested/Approved By, tested-on IP:Port).
- **One sheet per device, named by its IMEI** — the QC Summary view on paper: device
  details (identity, vitals, last position/battery), then the Packets / Emergency
  server / Alerts tables with status, code, required, count, last-received time and
  the **actual raw packet** (ASCII for text protocols per the profile's `raw_view`,
  spaced hex otherwise). Deviations listed at the end; protocols without a coverage
  profile fall back to the pass/fail checks table.
- Backend: `GET /api/qc/{id}/export?fmt=xlsx|csv&devices=<imei,...>` (empty = all
  devices; works on live *and* stopped sessions). Builder in `runway/qc/report.py`
  (framework-free); new runtime dependency **openpyxl** (lazy-imported — CSV export
  works without it), bundled in the PyInstaller spec.
- Download name mirrors the bench's own files: `<item label> QC-<dd-mm-yyyy>-S<id>`.
- **Completion gate**: the report is the lot's *final* deliverable, so a device still
  **IN PROGRESS** blocks printing. The export dialog highlights the incomplete devices,
  names the missing required items, and offers one-click **Skip missing** (the device
  closes as **Conditional Pass**) — Download stays disabled until every included device
  has settled (pass / conditional / fail / timeout). The API enforces the same rule
  (HTTP 409), and unticking a device simply leaves it out.

### Changed
- The old checks popup's button is now **✓ Checks** (its JSON/TXT downloads are
  unchanged) — "Report" now always means the XLS/CSV deliverable.

### Fixed
- **A QC session that ended without a clean Stop lost its protocol identity** (the
  self-heal path persisted devices/coverage but not `identified_as`/`packet_count`),
  so the Summary and the report fell back to HEX raw packets and 0 packets. Three-layer
  fix: identity is now written to the DB the moment the first packet identifies the
  protocol; the self-heal path persists identity + counters; and on startup, rows
  already damaged get their protocol back by matching their persisted coverage keys
  against the QC profiles.
- **Raw view is now declarative**: the session carries the profile's `raw_view`, the UI
  and the report use it instead of a hardcoded protocol list — and when the identity is
  genuinely unknown, printable packets are sniffed and shown as ASCII rather than hex.

[1.1.21]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.21

## [1.1.20] — 2026-07-16

Feature release. Headline: **two-way communication** — a command picker sends the
Locate 140B's GPRS bench commands to a connected device — plus live **device vitals**
and a dedicated **Emergency server** coverage section.

### Added — downlink command sender (two-way)
- The device row's Send control is now a compact paper-plane **icon** (name appears on a
  ~2s hold-hover tooltip). Clicking opens the restructured send dialog:
  **Send to `<IMEI>`** (the transient ip:port moves to a hover tooltip) → **Command type**
  (SET / GET / CLR) → **Command** list *filtered to the commands supporting that type*
  (e.g. STOP is SET-only) → editable prefilled value. Mode (text/hex) hides for catalogue
  commands — the format is device-defined — and returns for custom entry.
- The 13-command **bench GPRS catalogue** (VRN, STOP, HBL, HAL, RTL, LOGS, LOG2, EMTD,
  HPTI, PIP, EIP, GIP, APN with real SET/GET/CLR strings) lives declaratively in
  `data/protocols/ais140.json` → `ota_commands` — edit the JSON to change the picker.
  That file is now **tracked in git** (previously ignored under `data/`): it's a shared
  cross-tool asset the QC references and the send picker both depend on.

### Added — QC Summary
- **Device details vitals**, live from the newest traffic: last packet type
  (Location live/history · Alarm (id N) · login/health), when it landed on the utility
  (IST), latest lat/long + satellites + fix status, and external/internal battery
  voltages. Position/battery each fall back to the newest record that carries them.
- **Emergency server section** (between Packets and Alerts): EPB traffic split into
  **Emergency ON (EMR)** and **Emergency OFF (SEM)**, both required, with received
  sentences inline and the EPB reference (updated to a real firmware-2.2.5 capture)
  highlighted per row. The old combined "Emergency packet" row is gone.
- Removed the "Test cases" chips from the Summary (still computed backend-side and in
  the report); removed the repetitive device-nav/verdict/session-meta header — the shown
  device is bound by its Serial badge, and session/port/verdict live once in the feed
  header, session strip and status pill ("session #N" now spelled out there).

### Fixed
- Selecting "— custom —" in the send dialog silently resolved to the first catalogue
  command (empty option value coerced to index 0).
- Item labels in the coverage tables now word-wrap (an app-wide `tbody td` nowrap rule
  forbade line breaks); tiny Req/Count cells no longer break "yes" mid-word; the QC tab
  title no longer shows an IMEI; the feed header wraps on narrow windows instead of
  overlapping; the per-row reference-example substitution was off by one token.

[1.1.20]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.20

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

[1.1.14]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.14

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
- **QC coverage profile** (`runway/qc/profiles/<protocol>.json`, overridable under
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

[1.1.8]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.8

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
- Internal connectivity verified: `runway/convert` is isolated (lazily imported by its
  endpoints; not a registered plugin), so the pipeline, dashboard and live store/hub are
  unaffected.

[1.1.0]: https://github.com/alishahbaz7/Runway/releases/tag/v1.1.0
[1.0.0]: https://github.com/alishahbaz7/Runway/releases/tag/v1.0.0
