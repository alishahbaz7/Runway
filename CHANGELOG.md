# Changelog

All notable changes to **Runway** (package `proinfo`) are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [Unreleased] — v1.0.1 (in progress)

_Work continues on branch `v1.0.1`._

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

[1.0.0]: https://github.com/alishahbaz7/ProInfo/releases/tag/v1.0.0
