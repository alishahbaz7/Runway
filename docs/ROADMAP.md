# ProInfo — Roadmap

Mapped to the four pillars from the original brief. ✅ done · 🔨 in progress · ⬜ planned.

## M1 — Foundation & first milestone (v0.1) ✅
- ✅ Pluggable **Source / Codec / Sink** framework + registry (auto-discovery)
- ✅ Normalized `UnifiedRecord` data model
- ✅ Pipeline engine (per-session buffering, framing, ACK, fan-out)
- ✅ **Log replay → parse → dashboard** end to end (the chosen first milestone)
- ✅ Codecs: GT06/Concox, Teltonika Codec 8/8E, NMEA-0183, generic hex, template
- ✅ Dashboard: live map, device list, packet table, hex + decoded inspector
- ✅ CLI: `serve` / `replay` / `listen` / `codecs`

## M2 — Ingest everywhere (Pillar 1 & 3)
- 🔨 Live **TCP listener** (built; harden: reconnect, per-port codec, auth)
- ⬜ **UDP** source
- ⬜ **MQTT** source (`aiomqtt`) — subscribe to device topics
- ⬜ **HTTP** source — devices that POST
- ⬜ Auto-detect across mixed streams; per-connection codec override
- ⬜ More codecs: Queclink, Ruptela, Meitrack, Sinotrack, + your proprietary OEMs

## M3 — Visualization (Pillar 2)
- ✅ Single & multi-device live map + trails
- ⬜ Time-range scrubber / history playback from storage
- ⬜ Per-device detail page (IO charts: speed, voltage, ignition timeline)
- ⬜ Geofences, alarm feed, filtering/search across packets
- ⬜ Export selection (CSV / JSON / GPX)

## M4 — Relay & integrate (Pillar 4)
- ✅ Webhook (HTTP POST) sink
- ✅ TCP relay sink (raw pass-through or JSON)
- ⬜ Transform/mapping layer before relay (rename/units per platform)
- ⬜ MQTT publish sink; Kafka sink
- ⬜ Store-and-forward with retry/queue when the platform is down

## M5 — Config-driven runner
- ⬜ Declarative `config/pipeline.yaml` runner (`proinfo run config.yaml`) to wire
      multiple Source→Codec→Sinks pipelines without code (format drafted in
      `config/pipeline.example.yaml`)
- ⬜ Hot-reload pipelines

## M6 — Cloud & team (scale-out)
- 🔨 Docker / docker-compose packaging
- ⬜ Postgres/TimescaleDB persistence sink (drop-in for SQLite)
- ⬜ Redis-backed `store`/`hub` so multiple workers share live state
- ⬜ AuthN/AuthZ (team accounts, per-device ACLs), API keys
- ⬜ Horizontal scale behind a load balancer; health/metrics endpoints

## M7 — Quality
- 🔨 Unit tests per codec with real captures (golden fixtures)
- ⬜ Fuzz/robustness tests for framing (truncated/garbled streams)
- ⬜ CI (lint + tests), versioned releases
