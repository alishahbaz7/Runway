# Runway — Architecture

## Goal

One utility that can parse the raw output of **any** telematics device, show it in a
readable form, and relay it onward — while staying trivial to extend with new
protocols and new transports, and easy to lift from a laptop to the cloud.

## The pipeline

Every flow through Runway is the same four stages:

```
 Source  ──bytes──▶  Codec  ──UnifiedRecord──▶  Pipeline  ──▶  Sinks
 (where)            (decode)     (normalize)      (route)      (do something)
```

- **Source** — produces `RawFrame`s (raw bytes + provenance) from a transport: a TCP
  socket, a replayed file, later UDP/MQTT/HTTP.
- **Codec** — turns bytes into one or more `UnifiedRecord`s. Owns message framing and
  protocol quirks. May emit an `ack` to send back to the device.
- **Pipeline** — the engine. Buffers bytes per session, drives the codec, stamps
  provenance, and fans each record out to every sink.
- **Sink** — consumes records: the in-memory store (for the dashboard), the WebSocket
  broadcaster, a webhook, a TCP relay, SQLite, the console.

The three plugin types are defined in [`runway/core/interfaces.py`](../runway/core/interfaces.py).

## Why plugins + a registry

Devices differ in *two* dimensions: how they connect (transport) and how they encode
(protocol). We isolate both behind small interfaces and a registry:

- Each plugin module decorates its class with `@register_codec` / `@register_source` /
  `@register_sink`.
- `registry.load_plugins()` imports every module under the three packages, so the maps
  populate themselves. **Dropping a file into `codecs/` is all it takes** — the CLI, the
  REST API (`/api/codecs`) and the dashboard dropdown pick it up automatically.

## The unified data model

[`runway/core/models.py`](../runway/core/models.py) defines `UnifiedRecord` — the
contract between "everything upstream" and "everything downstream". Only `device_id` and
`protocol` are required; codecs fill in whatever their message carried. Protocol-specific
telemetry that has no first-class field goes into `io` (e.g. Teltonika AVL IO elements),
and the original bytes are always preserved in `raw_hex` for the inspector.

Because everything downstream speaks `UnifiedRecord`, a new protocol never touches the
dashboard, storage or relays.

## Sessions, framing and back-pressure

TCP and files are byte **streams**, not message frames. The pipeline keeps a per-session
`buffer` (a `Session` object, keyed by transport+peer) and calls `codec.decode(buffer)`
in a loop:

- `decode` returns `consumed` = how many bytes it handled (advance the buffer), plus the
  records it produced and an optional `ack`.
- `consumed == 0` means *"need more bytes"* — the pipeline keeps the buffer and waits for
  the next frame. This is how a codec cleanly handles a packet split across two reads.

A codec instance is created **once per session**, so cross-packet state (the IMEI learned
from a GT06 login, a sequence counter) lives in `session.state` — never in module globals.

## Concurrency

Everything is `asyncio`. The TCP source accepts many device connections concurrently and
feeds one queue; sinks `await` their I/O (webhook POST, relay write) without blocking the
parse loop. Blocking work (SQLite) is pushed to a thread executor.

## Real-time to the browser

`storage/live.py` holds two process-wide singletons:

- `store` — a ring buffer of recent records + per-device summaries (REST reads this).
- `hub` — a fan-out bus; the `ws_broadcast` sink publishes to it and every dashboard
  WebSocket subscribes. Slow consumers drop messages rather than stall the pipeline.

## Local-first, cloud-ready

| Concern        | Local (today)              | Cloud (roadmap)                       |
|----------------|----------------------------|---------------------------------------|
| Run            | `runway serve`            | Docker / `docker compose`             |
| Live state     | in-process `store` + `hub` | Redis pub/sub + shared cache          |
| Persistence    | `sqlite_store` sink        | Postgres/TimescaleDB sink (same API)  |
| Scale-out      | single process             | N stateless workers behind a balancer |

Nothing in the parse path assumes a single machine — swapping the storage/hub
implementation is a sink/singleton change, not a rewrite. See [ROADMAP.md](ROADMAP.md).

## Request flow: file replay → dashboard (the v0.1 milestone)

1. Browser uploads a file to `POST /api/replay` (codec + format chosen).
2. API saves it and starts a `Pipeline(FileReplaySource → codec → [memory_store, ws_broadcast])`
   as a background task.
3. As records are produced they land in `store` and are pushed through `hub`.
4. The dashboard, subscribed over `WS /ws`, plots each on the map and appends it to the
   packet table in real time. Clicking a packet shows decoded fields + raw hex.
