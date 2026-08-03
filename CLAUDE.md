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
- **Tests build packets with the real crc helpers** rather than hardcoding captures, so codec tests
  validate framing + field parsing end-to-end. New codecs should ship sample-based tests the same way.
