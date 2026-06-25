# ProInfo

**Pro**(tocol) + **Info**(rmation) — a unified utility to **parse, visualize and relay**
telematics packets from any IoT tracking device, regardless of OEM or protocol.

Point it at a device, or drop in a captured log, and ProInfo decodes the raw bytes into
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
single new file in `proinfo/codecs/` — nothing downstream changes.

---

## What it does (the four pillars)

1. **Parse anything** — pluggable codecs per protocol. Ships with **GT06/Concox**,
   **Teltonika Codec 8/8E**, **NMEA-0183**, a **generic hex** fallback, and a
   **template** to add your own OEM protocol.
2. **Visualize** — a web dashboard with a live map, per-device trails, a streaming
   packet table, and a hex + decoded-tree inspector. Single device or many at once.
3. **Ingest two ways** — upload/replay a `.log`/`.loc`/dump file, **or** have the device
   stream live to a built-in TCP listener.
4. **Relay** — forward decoded (or raw) traffic onward to your platform via **TCP**,
   **webhook/HTTP**, or persist to **SQLite** (Postgres/Timescale on the roadmap).

Runs **locally first**, packaged in **Docker** so your team can push it to the cloud.

---

## Quickstart

```bash
# 1. Install (editable, with dev extras for tests)
cd ProInfo
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e ".[dev]"

# 2. Parse a capture from the command line
proinfo replay tests/fixtures/nmea_sample.log --codec nmea
proinfo codecs                      # list available protocol codecs

# 3. Launch the dashboard (then open http://127.0.0.1:8080)
proinfo serve
```

In the dashboard you can upload a log, pick a codec (or *auto-detect*), watch packets
stream onto the map, and click any packet to inspect its raw bytes and decoded fields.

### Live device

```bash
proinfo listen --codec gt06 --port 5050        # then point the device at this host:5050
```
or start a listener from the dashboard ("Live TCP listener" panel).

### Relay onward

```bash
# Parse a live GT06 stream, persist it, and forward raw bytes to your platform:
proinfo listen --codec gt06 --port 5050 --db proinfo.db --relay platform.example.com:6000
```

---

## Project layout

```
proinfo/
  core/        models · plugin interfaces · registry · pipeline engine · crc
  sources/     file_replay · tcp_server            (how bytes get IN)
  codecs/      gt06 · teltonika · nmea · generic_hex · custom_template   (decode)
  sinks/       console · memory_store · ws_broadcast · webhook · tcp_relay (out)
  storage/     live (in-mem store + WS hub) · sqlite_store
  api/         app.py  (FastAPI: REST + WebSocket + upload + dashboard host)
  web/         index.html · app.js · style.css     (the dashboard)
  cli.py       proinfo serve | replay | listen | codecs
docs/          ARCHITECTURE · ADDING_A_PROTOCOL · ROADMAP
config/        pipeline.example.yaml
tests/         codec + pipeline tests with fixtures
```

## Add a new protocol

Copy `proinfo/codecs/custom_template.py`, implement `detect()` and `decode()`, drop a
sample in `samples/`. It auto-registers everywhere. Full guide:
[docs/ADDING_A_PROTOCOL.md](docs/ADDING_A_PROTOCOL.md).

## Architecture & roadmap

- Design and scaling notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Milestones and what's next: [docs/ROADMAP.md](docs/ROADMAP.md)

## Run in Docker / cloud

```bash
docker compose up --build      # dashboard on :8080, device listener on :5050
```

---

> Status: **v0.1** — log-replay → parse → dashboard path is the first working milestone,
> with the full pluggable Source/Codec/Sink framework in place. See the roadmap.
