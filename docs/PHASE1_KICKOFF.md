# Phase 1 — Clean implementation kickoff

**How to use:** open the repo in a fresh Claude Code workspace and paste the prompt
below as your first message. It carries the locked architecture decisions and points
the session at [DESIGN.md](DESIGN.md) + [../CLAUDE.md](../CLAUDE.md) so the build starts
clean and on-spec. It targets **Phase 1 (the local bench tool)** — including the
serial/USB/RS232/BLE/BT device I/O the v0.1 spike did not cover.

> If you want a brand-new empty repo instead of evolving this one, change the first
> "START HERE" step in the prompt accordingly.

---

```text
You are implementing ProInfo — a unified utility to parse, visualize and relay
telematics packets from any IoT tracking device (any OEM, basic or advanced).

START HERE
1. Clone and open the repo: https://github.com/alishahbaz7/ProInfo
2. Read these in full BEFORE writing any code — they are the source of truth:
   - docs/DESIGN.md  (architecture, run-modes, interfaces, device I/O, roadmap)
   - CLAUDE.md       (codebase conventions)
   - docs/ROADMAP.md
The current code is a working v0.1 "spike" in a flat proinfo/ package. Your job is a
CLEAN implementation of Phase 1 in the target package layout (DESIGN.md §14),
PORTING the proven logic from the spike (codecs, CRC, pipeline) — do not rewrite
working parsers from scratch; move and tidy them.

LOCKED DECISIONS (do not relitigate)
- Primary use: bench/diagnostic tool now → cloud gateway later.
- Topology: ONE codebase, three run-modes (all-in-one / hub / edge-agent).
  Phase 1 = all-in-one (local).
- Stack: Python-first core + TypeScript UI. The codec core stays PURE — no
  FastAPI/httpx/socket imports in proinfo-core or codecs.
- Protocol onboarding: hybrid — code Codec classes + a declarative field-map
  engine + a sniff-and-scaffold helper.

PHASE 1 SCOPE — build in this order
1. Restructure into packages per DESIGN.md §14: proinfo-core, proinfo-io,
   proinfo-hub, proinfo-agent, proinfo-web. Add run-mode config (config/local.yaml).
   Confirm the file-move plan with me before mass-moving.
2. Core contracts (DESIGN.md §4), pure + typed + tested: Source (with discover(),
   open(), frames(), send(), request()), Codec (detect/decode/encode), Sink,
   UnifiedRecord, Session, Profile, DecodeOutput.
3. Sources: file_replay, tcp_server, and the NEW ones the spike lacked —
   SerialSource (pyserial-asyncio; covers USB / USB-TTL / RS232 / Bluetooth-SPP)
   and BleSource (bleak). Both must support discovery (list COM ports / scan BLE)
   and the bidirectional command channel (send/request) for commissioning.
4. Codecs: port GT06, Teltonika (Codec 8/8E), NMEA, generic_hex into
   proinfo-core/codecs/<name>/ each with golden sample captures + tests. Implement
   the declarative field-map engine + one sample YAML profile, and a sniff/scaffold
   helper for unknown devices.
5. Sinks: console, memory_store, ws_broadcast, sqlite_store, webhook, tcp_relay.
6. Hub (FastAPI): REST + WebSocket + file upload + background job runner, plus
   device-discovery and commission endpoints.
7. Dashboard (vanilla TypeScript to start): live map, device registry, packet
   inspector (hex + decoded tree), commission panel (pick a COM port / BLE device,
   send config, read back), replay scrubber.
8. CLI: serve / replay / listen / codecs, plus serial and ble subcommands.

CONVENTIONS (from CLAUDE.md)
- Keep proinfo-core framework-free and I/O-free (portability + future Rust port).
- Per-connection codec state lives in session.state — never on self or in globals.
- decode() contract: consumed==0 means "need more bytes"; codecs are bidirectional
  (return ack / implement encode).
- Reuse the crc helpers (verified against standard check vectors).
- Every codec ships sample-based tests built with the real crc helpers.

PHASE-1 CHOICES for the open design questions (DESIGN.md §16)
- Frontend: vanilla TS now (React in Phase 2). Desktop: browser-local. Storage: SQLite.
- Defer edge↔hub wire protocol, time-series DB, and any Go gateway to Phase 2.

PENDING INPUT (design for it, don't block on it)
- Real OEM device captures (open question F) aren't provided yet. Until they are,
  validate with the existing fixtures + synthetic packets. Flag where a real capture
  is needed to finish a codec.

DEFINITION OF DONE (this session)
- Clean package structure; `pip install -e ".[dev]"` works; `pytest` is green.
- `proinfo serve` launches the dashboard.
- You can BOTH replay a log file AND open a serial or BLE device, see decoded
  packets on the map + inspector, and commission a device from the UI.
- README and docs updated to match the new structure.

PROCESS
- Work on a feature branch; commit in logical chunks.
- Do not deviate from docs/DESIGN.md without flagging it and explaining why.
- If a decision is genuinely ambiguous, ask me — don't guess on architecture.
```

---

## Quick reference — locked decisions

| Topic | Decision |
|---|---|
| Primary use | Bench tool now → cloud gateway later |
| Topology | One codebase, three run-modes (Phase 1 = all-in-one) |
| Stack | Python-first core + TypeScript UI |
| Onboarding | Hybrid: code + declarative + sniff |

See [DESIGN.md §16](DESIGN.md) for the open questions still to decide (mostly Phase 2).
