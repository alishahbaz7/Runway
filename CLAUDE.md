# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Runway ("where telematics data lands & takes off") — a unified utility to **parse, visualize and
relay** telematics packets from any IoT tracking device, regardless of OEM/protocol.

The repo, the Python package (`runway/`), the CLI (`runway serve`) and the UI all use the one
name **Runway**. It was called *ProInfo* (Pro=protocol, Info=information) up to v1.1.23; that
name is gone from the code — only the CHANGELOG records it.

The repo today is the **v0.1 spike**: a flat `runway/` package that proves the
Source → Codec → Sink design with a working CLI, dashboard and tests. The agreed target
architecture (edge/hub run-modes, package split, serial/BLE/USB device I/O) lives in
**[docs/DESIGN.md](docs/DESIGN.md)** — read it before any large or structural change; it is the
source of truth for direction. The clean Phase-1 implementation brief (paste into a fresh
workspace) is **[docs/PHASE1_KICKOFF.md](docs/PHASE1_KICKOFF.md)**.

## Commands

```bash
pip install -e ".[dev]"        # install (FastAPI, uvicorn, pydantic, httpx, pytest, pytest-asyncio)
python -m pytest -q            # run all tests
python -m pytest tests/test_codecs.py::test_gt06_login_then_location   # single test

runway serve                  # dashboard + REST + WS on :8080  (or: python -m runway.cli serve)
runway replay <file> --codec gt06 [--fmt auto] [--hex]   # parse a log/.loc/dump file
runway listen --codec teltonika --port 5050              # live TCP listener
runway codecs                 # list registered codecs
```

**`python main.py` (the user runs it from VS Code) is the one copy, always the latest code**
(`serve --take-over`): a Runway on its port running older code — a release, another checkout, this
checkout before the last edit (`/api/health` `root` + `started` against the newest source file) — is
stopped (`POST /api/app/quit`, loopback only; a release before 1.1.28 lacks it, so the process owning
the port is ended) and its running listeners are started again in the new copy with their relay
rules and store. The same code running is only brought forward. So never leave a second Runway of
your own running for the user (a demo on another port) — point them at `main.py`; check things in
isolated copies on scratch ports and stop them. A run from source shows "Branch v<x.y.z>" in the
banner (`install.branch()`, read from `.git/HEAD`); a release "Version <x.y.z>". Its data folder is
the repo's `data/`, not a release install's.

`pytest` is configured with `asyncio_mode=auto` (in `pyproject.toml`), so async tests need no
decorator. `python-multipart` is required for the upload endpoint and `pytest-asyncio` for the
pipeline tests — both come with the `[dev]`/runtime install.

## Architecture (the parts that span files)

**The pipeline** (`runway/core/pipeline.py`) is the engine that wires everything:
`Source → (buffer per session) → Codec.decode → UnifiedRecord → fan-out to Sinks`. Understanding
these four contracts (`runway/core/interfaces.py`) is enough to navigate the whole system:

- **Source** (`runway/sources/`) yields `RawFrame` byte chunks (file replay, TCP). The target
  design extends this with `discover()`/`send()`/`request()` for serial/BLE commissioning.
- **Codec** (`runway/codecs/`) turns bytes into `UnifiedRecord`s. **This is where new protocols
  are added.**
- **Sink** (`runway/sinks/`) consumes records (console, store, WS broadcast, webhook, TCP relay).
- **UnifiedRecord** (`runway/core/models.py`) is the normalization contract — every codec emits
  it, every sink consumes it, so adding a protocol never touches anything downstream.

**Plugin auto-discovery** (`runway/core/registry.py`): plugins self-register with
`@register_codec` / `@register_source` / `@register_sink`. `registry.load_plugins()` imports every
module under the three packages so those decorators run. **Adding a protocol = drop a file in
`runway/codecs/` with `@register_codec` — nothing else changes.** Copy
`runway/codecs/custom_template.py` to start (guide: [docs/ADDING_A_PROTOCOL.md](docs/ADDING_A_PROTOCOL.md)).

**Streaming + framing contract** (critical and non-obvious): `Codec.decode(buffer, session)`
returns `DecodeOutput(records, consumed, ack)`. `consumed == 0` means "need more bytes" — the
pipeline keeps the buffer and calls again on the next frame. This is how a packet split across two
reads is handled. A codec instance is created **once per session and cached**, so any cross-packet
state (e.g. the IMEI learned from a GT06 login that later location packets omit) must live in
`session.state` / `session.device_id`, **never on `self` or in module globals**.

**AIS-140 family: layouts by shape, custom packets by identity** (`ais140.py`, `zenithra.py`,
`ais140_custom.py`): the normal/alert sentence comes in several layouts sharing fields 1-25.
Each is an `NmpLayout` row in `NMP_LAYOUTS` / `MMI_LAYOUTS`, keyed by `(field count, last
field parenthesized)`; one builder (`Ais140Codec._nmp_record`) serves them all. Our own
packets (IMU = type `IMU`, alert id `200`) ride those layouts and put their data in the
parenthesized field; `CUSTOM_PACKETS` maps `(packet_type, alert_id)` → payload parser. They
must stay inside the session's codec, never a codec of their own — they share the connection
with normal packets and a session's codec is chosen once. Add a layout or a custom packet as a
table row / one decorated function, not a branch (guide: [docs/ADDING_A_PROTOCOL.md](docs/ADDING_A_PROTOCOL.md)).
The login is a table too (`LOGIN_LAYOUTS`, by field count: the doc's 9, the 8 of the Locate 140 4G
and Zenithra — no vendor; any other shape is read by content). The Packet parser's names come from
the grammar (`data/protocols/<codec>.json`), not the codec: a new layout needs its `variants` entry
there too (keyed by field count), or the parser shows it as `field_1` … — or, worse, with another
layout's names one field off. A release copies these definitions into `data/protocols` and
refreshes each untouched copy on later starts (`install.seed_definitions`, with the hash of every
shipped version from `build_release.protocol_history`); an edited one is the person's and stays.

**Relay + store: device data lands on Runway, forked two ways** (`sources/tcp_server.py`,
`storage/outbox.py`, `storage/telemetry.py`, `sinks/telemetry_store.py`): a listener with relay
rules (`routes={"all": "host:port", "devices": {imei: "host:port" | "off"}}`) gives every device
its own upstream link (`_Link`, one per device + target, shared across that device's
reconnects) to the server its rule names, and passes the server's replies down. Every byte is
appended to the **outbox** (`data/relay_outbox.db`, one SQLite queue per device + target, all
access on its single-thread `EXECUTOR`) in the read loop *before* the pipeline sees it, and the
link sends from the queue — so a platform outage loses nothing, decoding/storage never slows the
platform feed, and a backlog survives a restart (`_resume_backlog`). Rows are deleted only
`GRACE` (2 s) after they were written with the link still up — at-least-once: the last ~2 s
before a drop arrive twice. A link outlives its device's connection until its queue is empty; a
new link sends the device's stored login (`hello`) first unless the queue already starts with
it. The queue is capped (2 GB, oldest dropped and counted). Rules change while running
(`set_routes`, `PUT /api/listen/{id}/routes`); the pipeline tells the source each connection's
device via `Source.identified()` *before* that record's ACK, because the route decides who ACKs:
Runway ACKs only devices it does not relay — never ACK on a relayed device's server's behalf.
The pipeline frees a connection's session/codec on `RawFrame.closed` (a listener runs for weeks
and devices re-dial from a new source port every time). A cellular device that loses its data
session never closes the old connection, so the listener closes dead ones itself
(`close_dead`, every 15 s): an older connection of a device that has dialled in again once it is
quiet for 30 s, any connection silent for `idle_timeout` (10 min), plus TCP keepalive. A link
whose server dropped it with nothing queued waits (`idle`) until the device sends — every dial
opens with the device's login, so never dial just to stay connected. `store` adds the `STORE_SINKS` —
`telemetry_store` (`data/telemetry.db`, separate from `runway.db`; what the IMU page reads) and
`archive_store` (`storage/archive.py`: the packets exactly as received, one gzipped file per
device per day — the long-term record and the source for *Replay to platform*; decode from it
rather than storing decoded copies) — also switchable live (`PUT /api/listen/{id}/store` swaps
`pipeline.sinks` as a new list). Rules or store make the listener an autostart service
(`AUTOSTART_KEY` in settings, spawned in `lifespan`). IMU sample times are computed at read time
(`sample_time_sql`) from the `imu_timing` setting — never store derived sample times.

**Running 24x7** (20 devices for weeks): `telemetry.db` keeps running summaries written with each
packet — `device_summary` (the device list; a scan of `packets` took ~22 s at that scale) and
`imu_minute` (per device-minute count + min/max/sum per axis). `imu_series` reads `imu_minute`
when a point covers a minute or more (24 h and up: 0.02 s instead of 2.7 s) or when the range's
samples are past retention. Hourly housekeeping (`_housekeep_once` in `api/app.py`) gzips
finished archive days and purges by the `storage_retention` setting (samples + packets 7 days,
minutes 365, archive forever by default). Purges go device by device through the
`(device_id, time)` indexes, in batches — a bare time filter scans every row each hour. Any new
per-packet table needs a retention rule here and an index that serves its purge.

**Firmware test** (`runway/fwtest/`, `api/fwtest.py`, `web/firmware.*`): a device on a new firmware,
every packet held against its sheet. The sheet is a *spec* (`fwtest/specs/<name>.json`; the person's
own in `data/fwtest/specs` win by file name): per header the field count and a field list (index, -1 =
the last field; `rules` by name from `rules.py` — a new rule is one decorated function; `allow` for the
standard's `x` / `99`), the checksum algorithm, the terminator, and the tables the sequence checks read
(`status_by_alert`, `alert_ids`, `ignition_by_type`) — a device whose sheet differs changes its spec,
never `gate.py`. How much a check weighs for a device is the spec's `levels` (check → warn / fail over
`CHECKS`); a flag it lowers keeps `was`, and a plan case that names the check judges `was` — a
warning on the packets never passes the case that tests exactly that behaviour. Fields are taken by index from the start (and -1 from the end), so a packet with an
extra field is flagged once for its count and still checked field by field. `Gate.feed(raw,
received, conn)` returns the packet's row with its flags; each flag's `key` groups it into one row
of the checks table (`CHECKS` holds every non-field check's label, meaning and level). A run on disk
(`runs.py`, `data/fwtest/<id>/`) keeps only `meta.json` and the packets as received (`packets.log`):
the gate is rebuilt from them at start, never stored judged. A live run is fed by a
`FirmwareTestSink` that `api/fwtest.py` adds to the pipeline of the listener on the run's port (a
watcher every 3 s, so it survives a listener restarted in Capture or a Runway restart); it only
reads — relay and ACKs stay the listener's. A file run frames the capture with the spec's codec, and
is not held to CR LF (`Gate(wire=False)`): a file's line endings are its editor's. `insights.py` turns
the gate into the rail's Insights (plain words, worst first, each with the filter that shows its
packets) — a check flags *why* where it can (`flag(why=…)`, e.g. a checksum `kept_live`), and an
insight is built from that, never from a flag's wording; a check without wording of its own still
gets a generic insight. A running test's verdict is `in_progress` (`Run.view`; `outcome` holds the
result so far). The page is Log analysis's frame: tabs (`runway.fwtest.tabs`), the list, the rail.
**The device's configuration** (`spec.clean_config`: ign_on_s, ign_off_s, sleep_after_min, sleep_s,
history_order fifo/lifo, formats per header — a spec offers its formats, a field may belong to one)
is asked when a test starts and kept in its meta; `Run.reconfigure` judges again from packets.log.
Anything not given is not checked — never assume a device's setting. The interval check needs the
whole timeline (history arrives late and fills gaps), so it is not judged in `feed`: `_derive()`
recomputes it (flags marked `derived`) whenever packets came, and checks and scenarios' failed
counts are counted from the rows each time (`_checks()`), not kept as running totals. Margins are
Log analysis's (`analysis.modes.margin_for`).
**The test plan** (`fwtest/plans/<name>.json`, `plan.py`, `tracker.py`, built from the device's tracker
sheet — progress in [docs/FWTEST_PLAN.md](docs/FWTEST_PLAN.md)): areas → cases, each *automatic*,
*assisted*, *command* or *manual*, with a `judge` (checks, states, needs, scenarios, config, measure);
`plan.check_plan` fails a test when a plan names a check, scenario or measure that does not exist. A
plan belongs to a firmware version: every test whose packets report it (`api/fwtest._firmware_of`);
entries per version live in `data/fwtest/tracker/`. An automatic case is judged on each test and
combined (any failing fails); a step is judged over its window only (`tracker.Window`), so a packet
before *Run* never passes it. Runway's ACK can be held back during a step (`Pipeline.ack_filter`;
only where Runway, not a relayed platform, acknowledges). New case = a plan row; a new kind of
evidence = a `MEASURES` function. **The gate's promise is that nothing wrong passes** — keep it when
changing anything here: bytes the codec skips reach the test (`Pipeline.skip_taps` → `Gate.feed(skipped=True)`,
a `not_packet` failure; a file's too); the sink tests every packet on the device's connections, not only
those carrying its IMEI; a header outside the sheet fails; consistency rules are spec rows (`when`);
`summary()` never says pass while `unchecked` settings remain; `plan.judge` passes a scenario case only
when its evidence packets pass. A false pass is a worse bug than a false failure — and a false failure
costs trust: `tests/test_fwtest_devices.py` runs a simulated correct device (`tests/fwsim.py`, a full day)
that must raise no flag at all, and each known fault alone that must be found and explained. A new
check needs both: the correct day still clean, and a fault switch that it catches.
`diagnosis.py` turns flags into defects (DIAG rows: area, severity, cause, proved, where, fix, impact);
judge over the whole timeline anything history can change after arrival (`_derive`: intervals, live frame
numbers). Suites are plan rows (`suites`), advanced as the plan is read (`Tracker._advance`). The shipped spec
is the Locate 140 4G; its findings on V7.06.10 are in the CHANGELOG (1.1.28). Tests build packets in
code with the real XOR and a made-up IMEI.

**Log analysis** (`runway/analysis/`, `api/analysis.py`, `web/analysis.*`): a platform report read
back and judged. `drivelog.py` reads the drive-log CSV — its layout varies (12 to 92 columns), so
`COLUMNS` maps column name → row field and reader, and rows are sorted by device time.
`analyse.py` is pure computation: every check becomes a finding (`area`, `level`, `text`, and the
detail `view` + `filter` that shows its rows); the limits a status is judged by are `LIMITS`, the
tunables `SETTINGS`, packet-type and driving-event names `PACKET_TYPES` / `DRIVING_EVENTS` (empty
until the platform's meaning is confirmed — never guess a name into them); AIS-140 alerts are named
from `AIS140_ALERTS` (the standard's table; `ROUTINE_CODES` keeps NR and our IMU packets out). Frame
numbers are analysed per *run* — a run ends where the counter falls back to the start (a restart) — so
a restart's repeats are never loss. Per-row flags go to the row view. A trip is analysed on its own (`analyse(log, span=…)` → `part()`): its rows keep their `"k"` from the whole log, so every check must index rows and per-row lists by `r["k"]`, never by position (`ign_k` is the ignition read that way); `store.part` caches the last few, never on disk. Packet generation by mode (`modes.py`: active / standby / sleep — interval, packets made ÷ due, steps on time, delay) takes each interval from a device profile (the user's, config `analysis_profiles`, matched by device number then firmware prefix; the built-in `PROFILES` stays empty until a device's figures are confirmed) else learns it from the log; a trip is judged by its whole log's intervals (`fixed`). A comparison (`compare.py`, devices in one vehicle) judges each log over the time all share (`store.part`). Both it and every log's own scorecard read one table, `scorecard.py`: a new measure is a `METRICS` row (group, label, unit, which side is better, how it is read from a report) and, if it has a limit, a `JUDGE` row. The page keeps one tab per open log or comparison (`tabs`, each log's page state stashed with it) and a back/forward list of places per tab — a new jump calls `remember()` before it moves. The right rail (`renderRail`: findings, scorecard, insights, the packet or gap picked on the map — `mapPoint` / `mapGap`) redraws from `renderHead`, so it follows every report or trip change; the maps run without Leaflet's keyboard handler (its focus scrolled a half-hidden map between press and release, losing the click). `store.py` keeps each analysis in `data/analysis/<id>/` (the upload gzipped, the
report, the flags) and caches the last two parsed logs. A new check = a function, a `LIMITS` row,
a finding; a new column = a `COLUMNS` row. Tests build drive logs in code — never commit a
device's real log (the repo is public).

**Pipeline ↔ API bridge** (`runway/storage/live.py`): two process-wide singletons connect the
parse loop to the web layer — `store` (ring buffer + per-device summaries; the `memory_store` sink
writes it, REST reads it) and `hub` (fan-out bus; the `ws_broadcast` sink publishes, each dashboard
WebSocket subscribes). The API (`runway/api/app.py`) runs replay/listen as background asyncio jobs
wired to the default sinks `[memory_store, ws_broadcast]`, and serves the static dashboard
(`runway/web/`) plus REST + `/ws`.

**One map for a track** (`web/trackmap.js` + `trackmap.css`, `window.RunwayMap.track`): Log
analysis's map as a component — one track or many (`{id, name, colour, series, driving, settings}`,
the analysis report's own `series` / `driving`). The Dashboard feeds it live: `/api/live/track`
reads each device's live-store records as drive-log rows (`analysis/live.py` `log_of`) and runs the one
`analyse` — never a second trip/gap logic in the page. A point's row `k` leads back to its record by
`seqs[k]`. Log analysis draws its log with it too: the page keeps its state (`mapTrip`, `mapPoint`,
`mapGap`) and its rail; the component hands clicks back (`onPoint`, `onGap`, `onTrip` — a trip rescopes
the page, which then sets it with `tm.trip`). A map feature is added to the component once, for every
page that shows a track. The device comparison keeps its own map on purpose (one vehicle: each device a
narrower stripe over the one beneath, so overlapping tracks all show). **Never thin what is shown or judged**
(the user: "we cannot deviate from the information received from the device"): the track carries
every packet; the map keeps quick by level of detail in drawing only — per zoom the points that move a
line by more than half a pixel (`importance` per run, `lodPts`, `smoothFactor: 0`), dots in the view
one per pixel — and `tm.accuracy()` measures the half-pixel promise. Where a chart must reduce, it keeps
each stretch's extremes (min and max, as the power chart and IMU minutes do), and a measure (a maximum,
a percentile) is taken over every value, never a sample.

## Conventions specific to this codebase

- **Keep `runway/core/` and `runway/codecs/` pure** — no FastAPI/httpx/socket imports. The core
  is deliberately framework-free so it stays unit-testable and portable (a future Rust port of hot
  codecs sits behind the same contract). I/O belongs in `sources/`, `sinks/`, `api/`.
- **Codecs are bidirectional**: `decode` may return an `ack` (sent back via `Source.reply`);
  downlink/commission commands are built by `encode`.
- **CRC helpers** (`runway/core/crc.py`): `crc_itu` (GT06) and `crc16_ibm` (Teltonika) are verified
  against standard check vectors (`0x906E` / `0xBB3D`). Reuse them; don't re-roll.
- **One version, declared only in `runway/__init__.py`.** `pyproject.toml` reads it (`dynamic`),
  the UI learns it from `/api/health` (`version` = running process, `code_version` = code on
  disk), and the release is named `Runway-v<version>` from it (`packaging/build_release.py`).
  Never write the version as a literal anywhere else — `tests/test_version.py` fails on one, and
  also requires the CHANGELOG's newest section and the `v<x.y.z>` branch name to match.
- **Tests build packets with the real crc helpers** rather than hardcoding captures, so codec tests
  validate framing + field parsing end-to-end. New codecs should ship sample-based tests the same way.
- **Usage reporting and self-update** (the `app-control` / `app-updates` skills, as in BluePulse).
  `runway/install.py` is the one place that knows how this copy runs: `CHANNEL` (release = frozen),
  the launcher layout (`<install>\Runway.exe`, `versions\Runway-v<x>\`, `data\` in the install root —
  `db.DATA_DIR` follows it). `runway/usage.py` + `usage_client.py` (the skill's client, unmodified)
  report to the shared Supabase server — counts only, never IMEIs/packets/endpoints/keys; add a
  counter with `usage.count("camelCaseName")`. `runway/update/` checks the GitHub releases of this (public)
  repo (`config.py`), installs beside, restarts through the launcher (`--wait-port`, a marker in
  `data/update.json`). Usage reporting is on in a run from source too (the user wants the name
  asked and the flow checked there), sent with build `dev`; `RUNWAY_USAGE=off` switches it off and
  `tests/conftest.py` does so — no test may reach the real server. Updates stay off from source
  unless pointed at a test channel (`RUNWAY_UPDATE_MANIFEST_URL`; `RUNWAY_USAGE_URL` likewise for
  usage). Page side: `web/appctl.js`, loaded by the shell. Packaging and publishing: `packaging/README.md`.
  **Features** (control, server v4): `runway/features.py` lists each page and its default (shown,
  `more`, or `data` — IMU once data exists); the owner's policy comes back in the usage answer
  (`usage.feature(name)`: True/False/None) and wins; `/api/features` gives the page the result and
  `/api/health`'s `features_key` tells it when to re-read; the shell applies owner → the person's
  own Settings choice → default. A new page needs a `FEATURES` row and a rail entry with the same id.
- **On the desktop** (`runway/desktop.py`, stdlib ctypes): `serve` opens Runway in Edge's app mode
  (its own window) in its own Edge profile (`data/window`), with Edge's dark mode while Runway's
  theme is dark (`ui_theme`, remembered by the page through `/api/desktop/titlebar`) — an app
  window ignores theme-color, and that is what darkens its title bar. It opens unless one of this
  server's pages is already open; a second start finds the
  running Runway (`/api/health`) and brings it to the front; release builds are windowed (no
  console — `packaging/runway_main.py` gives a terminal command its console back and writes
  `data/runway.crash.log` + a message box if start-up fails) and show a tray icon (Open · Logs ·
  Quit, Quit asks while listeners run; `RUNWAY_TRAY=1` shows it from source). It stays a server
  underneath on purpose: closing the window must not stop 24x7 relays.
- **UI: one frame and one kit, in the house style** (the `utility-ui` skill; BluePulse sets the
  layout and components, Supabase Studio the look — palette, Inter, density). `shell.js` builds
  the fixed banner (Runway · version / *Hello, Name* / page · the page's controls · chips) and the
  rail around each page's `#content` and exposes
  `window.RunwayUI` — the one icon map (`icons`, `icon()`, `<span data-icon>` painted on load;
  a `<template>`'s clones need `paint()`), `setLabel` (icon + word), `foldable` (side panes,
  `data-fold`; their inner edge resizes them, `data-fold-min/-max`), `resizable` (any edge,
  `data-resize` + `-edge -var -min -max -keep`: writes a CSS variable on the parent, which the
  page's CSS reads — a new split must read it), `confirm` / `prompt` (never the browser's), and 5 s
  hold-tips (`data-tip`). Table columns resize by their header edge, remembered per page.
  `style.css` holds the tokens (`--dim --frame --accent-soft`, `--primary-*` / `--btn-bg` for
  buttons, `--info` for "a second kind" beside green, the `--*-ink` tones) and the kit; Inter is
  bundled in `web/fonts/` (never a CDN — Runway runs offline); `ota.css` only Device OTA's own parts. Sentence case, no emoji on controls, no rule on a bare
  `main`/`aside`/`section`/`header`/`nav` — `tests/test_ui.py` fails otherwise. Verify UI changes
  driven and populated in headless Edge, never from an empty page.
