# Runway

*Where telematics data lands & takes off* — a unified utility to **parse, visualize and relay**
telematics packets from any IoT tracking device, regardless of OEM or protocol.

Point it at a device, or drop in a captured log, and Runway decodes the raw bytes into
one readable, normalized format — on a live map, in a packet inspector, and (optionally)
forwarded on to your platform.

```
        ┌─────────┐     ┌──────────┐     ┌─────────────┐     ┌──────────────────┐
device ─▶│ SOURCE  │────▶│  CODEC   │────▶│ UnifiedRecord│────▶│      SINKS       │
.log   ─▶│ tcp/udp │     │ gt06 /   │     │ (normalized) │     │ dashboard · store │
mqtt   ─▶│ mqtt /  │     │ teltonika│     │              │     │ webhook · tcp     │
         │ file    │     │ nmea ... │     │              │     │ relay → platform  │
         └─────────┘     └──────────┘     └─────────────┘     └──────────────────┘
            plugin           plugin                                  plugin
```

Everything that varies between devices lives in **plugins**. Adding a protocol is a
single new file in `runway/codecs/` — nothing downstream changes.

---

## What it does (the four pillars)

1. **Parse anything** — pluggable codecs per protocol. Ships with **GT06/Concox**,
   **Teltonika (Codec 8/8E/16 + GPRS 12/13/14)**, **AIS-140 (Locate 140B)**,
   **Zenithra Ax1 sCAN** (AIS-140 + inline CAN), **NMEA-0183**, a **generic hex** fallback,
   and a **template** to add your own OEM protocol.
2. **Visualize** — a web dashboard with a live map, per-device trails, a streaming
   packet table, and a hex + decoded-tree inspector. Single device or many at once. The
   **IMU** page charts a device's stored 6-axis samples over time (seconds to 30 days).
3. **Ingest two ways** — upload/replay a `.log`/`.loc`/dump file, **or** have devices
   stream live to the built-in **Live TCP** server (start/stop listeners, see connected
   clients, filter/log per client).
4. **Relay** — devices can point at Runway instead of the platform: each device's raw stream
   goes on to its server (one rule for all devices, or per device) through an on-disk outbox,
   so a platform outage loses nothing, while Runway decodes and keeps a copy. Every packet is
   archived as received, one file per device per day, and a day can be **replayed** to a
   platform. Also **webhook/HTTP** and **SQLite** sinks (Postgres/Timescale on the roadmap).

Plus a **Converter** (CanPlus vehicle `*.Config.XML` → JSON/DBC), a **Protocols** repository
for shareable/transferable parser files, and a standalone **`Runway-v<version>.exe`** build
(see `packaging/`). Runs **locally first**, packaged in **Docker** so your team can push it to the cloud.

---

## Quickstart

```bash
# 1. Install (editable, with dev extras for tests)
cd Runway
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e ".[dev]"

# 2. Parse a capture from the command line
runway replay tests/fixtures/nmea_sample.log --codec nmea
runway codecs                      # list available protocol codecs

# 3. Launch the dashboard (then open http://127.0.0.1:8080)
runway serve
```

In the dashboard you can upload a log, pick a codec (or *auto-detect*), watch packets
stream onto the map, and click any packet to inspect its raw bytes and decoded fields.

### Live device

```bash
runway listen --codec gt06 --port 5050        # then point the device at this host:5050
```
or use **Capture → Live TCP** in the dashboard: start a listener (protocol/port/IP), watch
connected clients and a live packet feed, filter or **log** per client (continuous server-side
capture to TXT/CSV), and send downlink commands.

### Relay onward

In the dashboard: **Capture → New session**, set *Relay to* `host:port` and tick *Store*; change
the rules on a running session with **⇄ Relay & store** (all devices, none, or a server per
device). Such a listener restarts with the server. Data lands in `data/`: `telemetry.db`
(decoded packets and IMU samples, kept 7 days by default), `archive/` (every packet as received,
kept until a limit is set) and `relay_outbox.db` (what the platform has not received yet).
Sizes and retention are on **Capture → Archive → Storage**.

From the command line (no outbox; for a bench):

```bash
runway listen --codec gt06 --port 5050 --db runway.db --relay platform.example.com:6000
```

---

## Project layout

```
runway/
  core/        models · plugin interfaces · registry · pipeline engine · crc
  sources/     file_replay · tcp_server            (how bytes get IN)
  codecs/      gt06 · teltonika · ais140 · zenithra · nmea · generic_hex · custom_template
  sinks/       console · memory_store · ws_broadcast · webhook · tcp_relay · telemetry_store · archive_store
  storage/     live (in-mem store + WS hub) · sqlite_store · telemetry · archive · outbox
  api/         app.py  (FastAPI: REST + WebSocket + upload + dashboard host)
  web/         one HTML + JS per page (dashboard, parser, capture, imu, archive, …) · shell.js · style.css
  cli.py       runway serve | replay | listen | codecs
docs/          ARCHITECTURE · ADDING_A_PROTOCOL · ROADMAP
config/        pipeline.example.yaml
tests/         codec + pipeline tests with fixtures
```

## Add a new protocol

Copy `runway/codecs/custom_template.py`, implement `detect()` and `decode()`, drop a
sample in `samples/`. It auto-registers everywhere. Full guide:
[docs/ADDING_A_PROTOCOL.md](docs/ADDING_A_PROTOCOL.md).

## Architecture & roadmap

- **Full design (start here):** [docs/DESIGN.md](docs/DESIGN.md) — topology, run-modes, interfaces,
  device I/O (serial/USB/RS232/BLE/BT), data model, edge↔hub, phased plan. Draft for discussion.
- **Phase 1 build brief (for implementers):** [docs/PHASE1_KICKOFF.md](docs/PHASE1_KICKOFF.md) —
  paste into a fresh Claude Code workspace to start the clean implementation.
- Spike notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Milestones and what's next: [docs/ROADMAP.md](docs/ROADMAP.md)

## Run in Docker / cloud

```bash
docker compose up --build      # dashboard on :8080, device listener on :5050
```

---

> Status: **v0.1** — log-replay → parse → dashboard path is the first working milestone,
> with the full pluggable Source/Codec/Sink framework in place. See the roadmap.
