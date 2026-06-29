# Runway — Session Notes

Running handoff log. Newest session on top. Product name: **Runway** ("where telematics
data lands & takes off"); internal Python package stays `proinfo`.

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
4. **Protocols repository UI** (Configuration → Protocols): backend is **done** —
   `GET/POST/DELETE /api/protocols` over `data/protocols/` (same mechanism as signatures,
   JSON files). Still needed: a page **identical to the Signatures page** (list / add /
   edit / delete + search + preview + download), a rail item under Configuration, and CSS.
   Intent: protocols are droppable repository files, **not** baked into core. Codecs also
   now carry a one-line `description`, surfaced via `/api/codecs`.

### Notes / gotchas
- `data/` (DB, `signatures/*.json`, `runway*.log`, uploads) is git-ignored — **commit code
  only**.
- Branch-per-version (`vX.Y.Z`), summary in commit body, no same-named tag.
- The parser is the only place all decoded packets live client-side (per decode); the
  Dashboard is a bounded live window.
