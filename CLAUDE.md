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
and devices re-dial from a new source port every time). `store` adds the `STORE_SINKS` —
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

**Pipeline ↔ API bridge** (`runway/storage/live.py`): two process-wide singletons connect the
parse loop to the web layer — `store` (ring buffer + per-device summaries; the `memory_store` sink
writes it, REST reads it) and `hub` (fan-out bus; the `ws_broadcast` sink publishes, each dashboard
WebSocket subscribes). The API (`runway/api/app.py`) runs replay/listen as background asyncio jobs
wired to the default sinks `[memory_store, ws_broadcast]`, and serves the static dashboard
(`runway/web/`) plus REST + `/ws`.

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
