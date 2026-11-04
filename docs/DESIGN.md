# Runway — Architecture & Design

**Draft v0.1 · 2026-06-25 · for discussion (nothing here is final — mark it up).**

*Pro*(tocol) + *Info*(rmation): a unified utility to **parse, visualize & relay** telematics
packets from any device, any OEM, basic or advanced.

> A rendered/shareable version of this document is also published as an Artifact for the team.

## Locked decisions

| Topic | Decision |
|---|---|
| Primary use | **Bench tool now → cloud gateway later** |
| Topology | **One codebase, three run-modes** |
| Stack | **Python-first core + TypeScript UI** |
| Onboarding | **Hybrid: code + declarative + sniff** |

---

## 1. Goals & scope

One tool that takes the raw output of **any** tracking device and turns it into a readable,
normalized stream you can see, store and forward. Four pillars:

1. **Parse anything** — a scalable way to add protocols (binary/ASCII over TCP, UDP, MQTT,
   HTTP, serial, BLE…) without touching the rest of the system.
2. **Visualize** — one device or many, on a live map + a hex/decoded packet inspector.
3. **Two ways in** — replay an uploaded log/`.loc` file, *or* take live data off a device.
4. **Relay** — forward device → Runway → platform via TCP, API or webhook.

Non-negotiables shaping every decision: runs **locally first**, handles **physically-attached
devices** (serial/BLE), scales to a **shared cloud instance** — all from **one codebase**.

---

## 2. Topology & run-modes

USB, TTL, RS232, Bluetooth and BLE devices are **physically attached to a machine**. A cloud
server has no COM ports and no radio. So the same binary runs in three modes, chosen by config:

```
ALL-IN-ONE   edge + hub in one process    → laptop, single user, SQLite
HUB          headless server              → cloud, many feeds, Postgres+Timescale, auth
EDGE AGENT   device I/O only, forwards up  → near the devices, streams to a HUB
```

```
┌──────────────── EDGE (laptop / Pi physically near devices) ────────────────┐
  USB-TTL ─┐
  RS232  ──┼─▶ SerialSource ─┐
  BT-SPP ──┘                 │
  BLE ────────▶ BleSource ───┼─▶ decode or pass-raw ─▶ local UI ─┐ forward (TLS)
  local TCP ──▶ TcpSource ───┘                                   │
└──────────────────────────────────────────────────────────────┼────────────┘
                                                                 ▼  WebSocket / gRPC / MQTT
┌──────────────────────── HUB (cloud / shared) ──────────────────┼────────────┐
  network devices ─▶ TCP/UDP/MQTT/HTTP ─┐                         │
  edge agents ──────────────────────────┼─▶ decode ─▶ normalize ─▶ TSDB ◀┘
                                         └─▶ team UI (multi-user) ─▶ relay → platform
└─────────────────────────────────────────────────────────────────────────────┘
```

**Keystone:** "local vs cloud" isn't a deploy flag — it's *where the physical I/O lives*. The
edge agent owns serial/BLE/BT and forwards raw or parsed data to a hub. Run both in one process
= the local utility; split them = the cloud platform. Same code, codecs and UI.

---

## 3. The pipeline

```
discover ─▶ Source ─▶ frame ─▶ Codec.decode ─▶ UnifiedRecord ─▶ Router ─┬─▶ Store
  ▲          (in)            (normalize)                                 ├─▶ WS → UI
  │                                                                      └─▶ Relay → platform
  └────────── Codec.encode ◀─ command ◀─ UI / Hub   (downlink: ACK, AT-config, commission)
```

Two corrections vs the early spike: the chain is **bidirectional** (you commission devices, not
just listen), and a **device id learned mid-session** (e.g. a GT06 login) propagates to later
packets via session state.

---

## 4. Core interfaces (`runway-core`, pure — no framework, no I/O)

```python
class Source:
    name: str
    modes: set[RunMode]                       # {EDGE} for serial/ble · {HUB} for mqtt …
    async def discover() -> list[DeviceRef]   # list COM ports / scan BLE / none
    async def open(params: dict) -> None       # baud, parity, UUID, host:port …
    def      frames() -> AsyncIterator[RawFrame]
    async def send(cmd: bytes) -> None         # downlink (config / AT)
    async def request(cmd: bytes, timeout) -> bytes   # req/resp dialog
    async def close() -> None

class Codec:
    profile: Profile                           # vendor·model·transport·framing·fw
    def detect(sample: bytes) -> float
    def decode(buf: bytes, s: Session) -> DecodeOutput     # consumed · records · ack
    def encode(cmd: Command, s: Session) -> bytes          # build downlink / ACK

class Sink:
    name: str
    async def handle(rec: UnifiedRecord) -> None

# Normalized shape everything downstream speaks (only id+protocol required):
UnifiedRecord = { device_id, protocol, msg_type, event_time, position{lat,lon,alt},
    speed_kph, heading, sats, ignition, battery_v, gsm, alarms[], io{…}, raw_hex, … }
```

A new protocol never touches UI/storage/relays because they all consume `UnifiedRecord`. The
pure core means codecs are unit-tested against captures — and could later be ported to Rust
behind the same contract.

---

## 5. Protocol onboarding (hybrid)

| Path | Use when | Who | Examples |
|---|---|---|---|
| **Code Codec** | stateful, binary, CRC, variable-length, login→data | developer | GT06, Teltonika, proprietary |
| **Declarative field-map** | fixed-layout / delimited ASCII or binary | engineer, no code | simple ASCII trackers |
| **Sniff & scaffold** | unknown device — capture, guess framing, generate stub | anyone → validate | new OEM, no spec |

A non-developer onboards a simple supplier by writing a profile, not Python:

```yaml
# profiles/acme_t2.yaml — declarative codec, parsed by a generic engine
profile:  { vendor: ACME, model: T2, transport: serial, fw: "2.x" }
framing:  { type: delimited, delimiter: "\r\n" }   # or {type:length, header:"AB CD", len_at:2}
fields:
  - { name: device_id, at: 0,  type: ascii,    len: 15 }
  - { name: lat,       at: 15, type: int32_be, scale: 1e-7, to: position.lat }
  - { name: lon,       at: 19, type: int32_be, scale: 1e-7, to: position.lon }
  - { name: speed_kph, at: 23, type: uint16_be,            to: speed_kph }
checksum: { type: crc16_ibm, over: "0:-2" }
```

Every protocol is a versioned **Profile** (vendor·model·transport·framing·firmware), shown in the
UI, shipping with **golden sample captures** + tests so a firmware quirk can't silently break
parsing. Flow: *capture → identify framing → write/declare → validate vs goldens → register → live.*

---

## 6. Device I/O matrix (edge-only)

| Transport | OS sees it as | Library | Abstraction | Notes |
|---|---|---|---|---|
| USB-serial / USB-**TTL** | COM port / `/dev/ttyUSB*` | pyserial-asyncio | `SerialSource` | TTL↔RS232 voltage handled by the adapter; software identical |
| Native **RS232** | COM port | pyserial-asyncio | `SerialSource` | baud · parity · stopbits · flow control |
| **Bluetooth Classic** (SPP) | COM port after pairing | pyserial / PyBluez | `SerialSource` | once paired, just a serial port |
| **BLE** | GATT characteristics | **bleak** (Win/Lin/mac) | `BleSource` | scan→connect→notify; write char for commands |
| TCP / UDP (local) | socket | asyncio | `TcpSource` | also runs in HUB mode |

So **two** new source types (`SerialSource` covers USB/TTL/RS232/BT-SPP; `BleSource` for BLE),
plus `discover()` and `send()/request()` on the Source interface for **port enumeration** and
**commissioning** (AT/config dialogs). All edge-only.

```
# commission a bench device over serial — the bidirectional path
Engineer →  UI: open COM5 @115200, profile=ACME_T2
UI       →  Agent.open({port:COM5, baud:115200})
Engineer →  UI: "read GPS config"
UI       →  Codec.encode(READ_CFG) → Agent.send(bytes) → Device
Device   →  Agent.frames() → Codec.decode() → UnifiedRecord(config) → UI shows it
```

---

## 7. Languages & why (Python-first, not polyglot yet)

| Layer | Choice | Why | Swap later? |
|---|---|---|---|
| Codec core | **Python** (construct/bitstruct) | fastest to write/test binary parsers; kept pure | → Rust lib if CPU-bound |
| Edge agent | **Python** | pyserial-asyncio + bleak best-in-class, cross-platform | — |
| Hub API + realtime | **Python / FastAPI** | async + WebSocket, same language as codecs | → Go if 10k+ sockets/node |
| High-fan-in gateway | *(later)* **Go** | goroutines + single static binary | only if needed |
| Frontend | **TypeScript** + MapLibre/Leaflet | rich live map + inspector | — |
| Desktop (optional) | **Tauri** or local-web | tiny native shell vs Electron | if engineers want a `.exe` |

**The rule that keeps it sane:** the codec core stays a pure package with zero framework/I/O deps.
That discipline lets us re-implement the hot path (gateway, or codecs in Rust) later *without
touching parsers, storage or UI*. Polyglot now buys performance we don't need yet.

---

## 8. Data & storage (split by shape; engine swaps by run-mode behind a repository)

| Data | Shape | Local (edge) | Server (hub) |
|---|---|---|---|
| Devices, profiles, users, relay targets, agents | relational | SQLite | PostgreSQL |
| Raw frames + records + events/alarms | time-series (append) | SQLite / Parquet | TimescaleDB hypertables |
| Large raw logs, uploaded captures | blobs | local disk | S3 / MinIO |
| Live state (last-seen, subscribers) | ephemeral | in-process | Redis (multi-worker) |

Core tables: `devices`, `profiles`, `records(ts,device_id,…,io,raw_hex)`, `raw_frames`, `events`,
`relay_targets`, plus `users` and `edge_agents` (hub only). `Repository` abstraction keeps the
engine storage-agnostic; Alembic migrations; retention + downsampling on the hub.

---

## 9. Edge ↔ Hub link

- **Enrollment** — agent registers with the hub, gets a rotatable API token; all traffic over
  TLS. Agent dials *out*, so it works behind NAT/firewalls.
- **Transport** — persistent stream (WebSocket or gRPC) edge→hub; MQTT optional for fan-in.
- **Payload** — per device/profile, forward **raw frames** (hub owns parsing truth) *or*
  **normalized records** (lighter link). Default raw; normalized when bandwidth-constrained.
- **Reliability** — store-and-forward queue on the edge (local disk), at-least-once with an
  idempotency key (`device_id+seq+ts`), retry with backoff when the hub is unreachable.
- **Backpressure** — bounded queues; the live view may drop under load, the durable archive never does.

---

## 10. UI / visualization

Live **map** (multi-device, trails, clustering) · **packet inspector** (hex + decoded tree +
per-field docs) · **device registry** · **commission panel** (COM/BLE config, edge-only) ·
**session timeline + telemetry charts** (speed/voltage/ignition/IO) · **replay scrubber** ·
**onboarding wizard** (declarative profiles) · **relay config + alarms feed**.

Realtime over WebSocket/SSE; state encoded as form (pills, severity stripes), not just text.
Auth & roles exist only in hub mode; local is single-user.

---

## 11. Relay & integration

Relays are Sinks: `TcpRelay` (raw pass-through or JSON), `Webhook/HTTP`, `MQTT publish`, later
Kafka. A small **transform/mapping** step (rename fields, convert units) adapts the unified
record to each platform's schema. Outbound reuses the store-and-forward + retry, so a platform
outage queues rather than drops.

---

## 12. Security

- **Hub auth** — team accounts + roles (viewer / operator / admin); API keys for programmatic + edge access.
- **Transport** — TLS everywhere off-box; rotatable agent enrollment tokens.
- **Device ACLs** — who can see / command which devices (commissioning is privileged).
- **Secrets** — relay credentials & tokens in a secret store / env, never plaintext in the DB.
- **Local mode** — single-user, no auth by default; bind to localhost.

---

## 13. Observability & testing

- **Golden captures** — every codec ships real sample packets + expected output; CI fails on regressions.
- **Framing fuzz** — truncated/garbled streams must resync, never crash.
- **Metrics & health** — per-source bytes/records, decode errors, relay lag, queue depth; `/health`, `/metrics`.
- **Structured logs** with session + device context.

---

## 14. Repo structure (monorepo)

```
runway/
  packages/
    runway-core/      # PURE: models · Codec interface · registry · framing · crc · declarative engine
      codecs/          # one folder per protocol: parser + samples/ + tests/ colocated
        gt06/  teltonika/  nmea/  acme_t2.yaml  …
    runway-io/        # sources & sinks: serial, ble, tcp, udp, mqtt, http, file; relays
    runway-hub/       # FastAPI: REST/WS · repos · auth · job runner
    runway-agent/     # edge: device discovery + I/O + local UI + forward-to-hub
    runway-web/       # TypeScript dashboard
  deploy/              # Dockerfiles · compose · k8s · config/{local,server,agent}.yaml
  docs/  samples/  tests/
```

> Note: the current repo is the **v0.1 spike** in a flat `runway/` package (proves the
> Source/Codec/Sink idea, working CLI + dashboard + tests). It migrates into the package layout
> above as we enter P2.

---

## 15. Phased roadmap

| Phase | Run-mode | Delivers |
|---|---|---|
| **P1 · Bench** | all-in-one (local) | serial/BLE/TCP/file sources w/ discovery + commission · code & declarative codecs (GT06, Teltonika, NMEA) · map + inspector + device registry · SQLite · webhook/TCP relay |
| **P2 · Hub & team** | hub + edge | edge↔hub stream w/ enrollment + store-forward · Postgres + Timescale + object storage · auth/roles · multi-device team UI · relay mapping |
| **P3 · Scale & polish** | hub (scaled) | Redis live state · optional Go gateway · geofences/alarms/analytics · onboarding wizard in UI · CI + fuzz |

---

## 16. Open questions — decide next

| # | Question | Lean |
|---|---|---|
| A | Edge↔hub wire: WebSocket / gRPC / MQTT? | WebSocket for P2 |
| B | Forward raw vs normalized by default? | raw, per-profile override |
| C | Time-series DB: Timescale / Influx / ClickHouse? | Timescale (it's just Postgres) |
| D | Frontend: React / Svelte / vanilla to start? | vanilla in P1, React in P2 |
| E | Desktop: browser-local vs Tauri `.exe`? | browser-local first |
| F | **First real protocols + sample captures?** | **need from you** — unblocks real codecs |
| G | First commissioning (AT/config) flows? | which devices, what command sets? |
