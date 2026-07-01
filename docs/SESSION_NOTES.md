# Runway — Session Notes

Running handoff log. Newest session on top. Product name: **Runway** ("where telematics
data lands & takes off"); internal Python package stays `proinfo`.

---

## 2026-06-30 — `v1.1.0`

Branch `v1.1.0` (version `1.1.0`). Tests: **41 passing** (`python -m pytest -q`). Since 1.0.0
this cycle added the **Protocols** repository, the **Converter** (CanPlus XML → JSON/DBC), the
standalone **`Runway.exe`** build, and the headline **Live TCP server**.

### Live TCP server (Capture → Live TCP)
- Start/stop/resume listeners (Protocol incl. **Auto** + **Generic (raw)**, Port, auto-detected
  **IP**); running cards show status/uptime/records/bytes and update live (2.5s poll + WS).
- **Per-client**: checkboxes filter the feed per client (hide ≠ stop) + All/None; per-client
  packet **count** and **idle** tracked **server-side** (`pipeline.per_peer` → `job.clients`),
  so they survive reloads. Downlink **Send** per client.
- **Live packets** feed: `Timestamp | Payload` header, complete packet (click a row → full
  HEX/ASCII), remembered HEX/ASCII view, feed persists across navigation.
- **Logging vs Download**: one-time **Download** (buffered, TXT/CSV, all/selected, ASCII/HEX)
  **and** continuous **Start/Stop log** — server-side, chosen folder+filename, flushed per
  packet + fsync'd (crash/power-safe), append-only; timestamps `DD-MMM-YYYY HH:MM:SS:zzz`.

### Working rules in force (see memory)
- **3 dev phases**: local edits (incl. version bumps) by default → push only when asked →
  build exe/zip only when asked.
- **Retention**: user config persists (`localStorage`) until changed; page data persists
  across nav (`sessionStorage`/server snapshot) until Clear/restart.
- `RUNWAY_DATA_DIR` overrides the data dir; tests use a temp dir (no pollution).

### Pending / notes
- Server per-client counts reset when a listener stops/resumes (live, not durable) — can move
  to the DB if cross-restart totals are wanted.
- Continuous log currently captures **all** packets; could honor the per-client selection.

---

## 2026-06-26 — `v0.0.3`

Branch `v0.0.3` (version `0.0.3`). All work pushed to `origin/v0.0.3`. Tests: **38 passing**
(`python -m pytest -q`). Run the app with **`python main.py --reload`** so code edits load
without a manual restart.

### Shipped this session
- **Signatures repository** (Configuration → Signatures): local `data/signatures/` folder +
  `GET/POST/DELETE /api/signatures`; UI to add (upload/paste), search, preview (field
  tables + raw JSON), download, delete; pro inline-SVG icons.
- **Signature-driven visualization** (Parser): pick a signature + Apply → matching AVL
  params expand into named sub-signal columns, decoded per startBit/size/multiplier/
  offset/signed/reverse (`sigValue()` in `parser.js`). *Bit convention assumed: reverse 0 =
  big-endian/MSB-first, reverse 1 = little-endian/LSB-first, signed = two's-complement,
  value = raw×mult+offset — confirm against ground truth.*
- **Transposed grid**: time instances as rows, signals as columns; numeric headers; click a
  Time cell pans+highlights the map.
- **Streaming decode** (`POST /api/parse-stream`, NDJSON): live "Decoding… X%" status.
- **Resilient parsing**: corrupt frames are skipped (resync to next `00000000`) instead of
  aborting; each skip's offset+reason is logged (see Logs).
- **Diagnostics → Logs**: rotating `data/runway.log` (all activity) + **kept**
  `data/runway.errors.log` (WARN+ERROR, 5 MB × 10 backups) for later analysis; viewer with
  source/level filter, search, auto-refresh, download, clear; global exception handler.
- **Fresh start**: live store no longer warm-starts persisted devices (Dashboard empty on
  restart). **Parser state** persists in `sessionStorage` — survives page navigation +
  refresh, resets on a new browser session (re-decodes retained input automatically).
- **Performance caps**: parser grid renders ≤1000 rows, map ≤2000 markers (Summary counts
  all; CSV exports all) — fixes the big-dump page freeze.
- **Selective CSV export**: choose which signals to include (Time always); per-tab "More"
  overflow dropdown (auto-folds after a pick); foldable map.
- **Summary panel** (replaces redundant records table): metric tiles (Packets, AVL records,
  Handshakes, GPRS, CRC bad, Corrupt skipped, GPS-valid, Speed, Span) + Priority and
  By-codec bars.
- **Dashboard polish**: header "N packets" = total across devices; table labeled "Recent
  packets · showing N" (it's a rolling window, by design); click a packet → map highlight;
  device source badge (parsed tab-name / `:port`) + live/idle; multi-select device
  visibility (all shown by default).

### Pending — pick up next session
1. **Map: plot ALL location points** (use a canvas renderer for performance) + **paginate
   the packet preview at 200/page** with page controls — instead of the current 1000-row /
   2000-marker caps. *(Refactor `parser.js` grid+map pipeline; started reading, not yet
   coded.)*
2. **Map point/line toggle** (Points / Line / Both) and **click a map point → open that
   packet in the grid** (navigate to its page + highlight the row).
3. Confirm the signature **bit/endianness convention** against a known-good decoded value.
### Also shipped (added after the main session)
- **Converter** (Tools → Converter): upload a **CanPlus `*.Config.XML`** vehicle signature →
  view/edit the signals in a table → export **JSON** and **DBC**. Parser/exporters in
  `proinfo/convert/canplus.py` (+ root `canplus_signature.py` CLI shim); endpoints
  `POST /api/convert/parse` and `/api/convert/export`. Descriptor decode is verified
  against the file's own mult/div; CAN-id low byte = descriptor byte 1 (SOC `0xAC` ↔ PID
  `0x3AC`). **Open:** field **start-bit/length** isn't in the XML — user edits it in the
  table before DBC export (defaults: start 0, len 16, big-endian). Need real raw/value
  pairs to auto-derive bit positions.
- **Protocols repository** (Configuration → Protocols): `GET/POST/DELETE /api/protocols`
  over `data/protocols/` — **same mechanism as signatures** (JSON files). UI mirrors the
  Signatures page (`protocols.html` + `protocols.js`, shared CSS): list / search / add
  (upload) / **edit** (in-place JSON editor) / download / delete + generic JSON preview.
  Intent: protocols are droppable repository files, **not** baked into core. Codecs also
  carry a one-line `description`, surfaced via `/api/codecs`.

### Notes / gotchas
- `data/` (DB, `signatures/*.json`, `runway*.log`, uploads) is git-ignored — **commit code
  only**.
- Branch-per-version (`vX.Y.Z`), summary in commit body, no same-named tag.
- The parser is the only place all decoded packets live client-side (per decode); the
  Dashboard is a bounded live window.
