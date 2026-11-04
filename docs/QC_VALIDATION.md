# Runway — QC Validation (implementation plan)

> Extends [QC_MODULE.md](QC_MODULE.md) (Target 1 intake/identify/record and Target 2 basic
> checks are built). This plan covers the six validation expectations for making QC actually
> *qualify* a device, and the order to build them in. Status: **plan — not yet implemented.**

## The six expectations

| # | Expectation | Short name |
|---|---|---|
| 1 | Which packet types were received / not received | **Packet coverage** |
| 2 | Compare a received packet against a known-good sample — similar or different? | **Sample match** |
| 3 | Packet length, firmware number, field positions correct? | **Parameter checks** |
| 4 | Alarm packet from the device correct or different? | **Alarm validation** |
| 5 | Data live on our server (iNTouch platform)? | **iNTouch live check** |
| 6 | Raw packets included in the QC report | **Raw reporting** |

## The key design decision — one validator, driven by the protocol JSON

`data/protocols/ais140.json` already declares, **per packet type** (LGN/NMP/HLP/EPB), every
field's `index`, `name`, `type` (ascii/decimal/uint/enum/date/time/bitfield/group/remainder),
`format`, `size`, allowed `values`, `range`, and a known-good `example` sentence.

That single file answers #2 and #3 with **one generic mechanism** — no per-protocol
validation code:

- **packet length** → expected field count from the `fields` list (AIS-140 is CSV; for
  binary protocols the same idea is byte length from offsets).
- **field positions** → walk each received packet's fields against the declared
  `type`/`format`/`values`/`range` at each index; report exactly which index broke and why
  (`field 9 "date": expected DDMMYYYY, got "2403201"`).
- **sample match** → the declared `example` sentences are the golden samples. Compare
  structure (header, field count, per-field format class), not raw bytes — two packets from
  different vehicles are "similar" if every field is the right *kind*, even though values
  differ.
- **firmware** → the field named `firmware_version` is already declared per packet type;
  compare its value against the expected value/pattern configured for the QC session.

**Rules:** protocol JSONs stay a clean, shared, declarative asset (they are used by the
Protocols page and other tools). Everything *QC-specific* — which packet types are
mandatory, which alarms must be exercised on the bench, expected firmware, thresholds,
iNTouch settings — lives in a separate **QC profile**, keyed by protocol:

```
data/qc/profiles/ais140.json        (auto-seeded with defaults from the protocol JSON;
                                     editable; per-session overrides at Start QC)
{
  "schema": "1",
  "protocol": "ais140",
  "expected_packets": {              // #1 — coverage checklist
    "login":    { "match": {"message_type": "login"},                 "required": true  },
    "live":     { "match": {"io.packet_status": "L"},                 "required": true  },
    "history":  { "match": {"io.packet_status": "H"},                 "required": false },
    "health":   { "match": {"message_type": "health"},                "required": false },
    "alarm":    { "match": {"has_alarms": true},                      "required": true  }
  },
  "firmware": { "expected": null, "pattern": "^\\d+\\.\\d+\\.\\d+$" },   // #3
  "alarm_tests": ["emergency_on", "ignition_on", "ignition_off",
                  "disconnect_main_battery"],                            // #4 bench list
  "thresholds": { "cadence_max_s": 30, "location_window_s": 60,
                  "voltage_min": null, "voltage_max": null },            // existing engine
  "intouch": { "max_lag_s": 120 }                                        // #5
}
```

## New / changed pieces

### Backend (all framework-free under `runway/qc/`, per the core-purity convention)

| Piece | What it does |
|---|---|
| `qc/validators.py` **(new)** | The generic packet validator. `validate(raw_bytes, ptype_def) -> FieldReport` — field count, per-field type/format/enum/range results; `similarity(raw, ptype_def)` — same/different vs the declared `example` (structural). Pure string/byte work; unit-testable with the fixtures already in `tests/fixtures/`. |
| `qc/profile.py` **(new)** | Load `data/qc/profiles/<protocol>.json`, auto-seed defaults from the protocol JSON when missing, merge per-session overrides. |
| `qc/engine.py` **(extend)** | New checks appended to `DEFAULT_CHECKS`: `packet_coverage` (#1, one chip per expected type: seen×N / missing), `template_valid` (#2+#3 aggregate: “214/214 packets match template” or first failure detail), `firmware_match` (#3), `alarm_valid` (#4). Same `(result, detail)` contract; `na` when no profile exists for the protocol. |
| `qc/session.py` **(extend)** | `QcCaptureSink` tallies per-`message_type` counts and runs `validators.validate()` per packet (cheap string ops), keeping a bounded list of failures with their raw packets — this is what makes per-packet field reports available live and in the report (#6). |
| `integrations/intouch.py` **(new)** | iNTouch client (httpx, lives OUTSIDE `qc/` — it does I/O). `status(imei) -> {found, last_data_at, lag_s}`. The API layer polls it while a session runs and injects an `intouch_live` check result into the view (engine stays pure/sync). Mockable for tests. |

### API

- `_qc_view` gains `coverage[]`, `validation{}` (per-type counts + failures), `firmware`,
  `intouch{}` — the UI reads everything from the one view it already polls.
- `GET/PUT /api/qc/profile/{protocol}` — read/edit the QC profile (PUT in a later phase;
  defaults work without it).
- `POST /api/qc/{id}/intouch` — on-demand re-check; also auto-polled while running.
- `GET /api/qc/{id}/report` — extended with coverage/validation/alarm/iNTouch sections and
  **raw packet samples per type** (first + last + every failing packet), plus the existing
  capture-file download for the complete raw log (#6).

### UI (on the reworked QC page — same tab layout as Live TCP)

- **Coverage chips** (#1): one per expected packet type — green `live ×214`, grey
  `history — not seen`, red when required & missing at Stop.
- **Validation panel** (#2+#3): per packet type, `OK n/n` or `FAIL` — clicking opens the
  field-level diff: `# | field | expected | got | ok/bad` rows (reuses the packet modal).
- **Alarm checklist** (#4): the profile's `alarm_tests` as a bench to-do list — the operator
  triggers each on the device; each ticks green when a *valid* alarm packet of that type
  arrives (invalid one shows red with the field report).
- **iNTouch chip** (#5): `live · lag 4s` / `not found` / `stale 300s`.
- **Report** (#6): existing report modal + the new sections; TXT/JSON now, printable HTML
  later.

## Build order (in parts, review after each)

| Phase | Delivers | Needs from you |
|---|---|---|
| **A** | QC page reworked onto the Live TCP tab layout (already agreed) **+ packet coverage (#1)** | nothing — ships from existing data |
| **B** | `validators.py` + template/parameter checks (#2, #3) + field-diff UI + firmware check | expected firmware value(s) per model (else pattern-only) |
| **C** | Alarm validation + operator checklist (#4) | which alarms are mandatory for PASS |
| **D** | iNTouch live check (#5) | iNTouch API: base URL, auth, "device status by IMEI" endpoint + sample response |
| **E** | Report with raw packets, per-type samples, failing packets (#6) | preferred formats (TXT/JSON now; PDF/HTML print?) |

Each phase bumps the version's last digit and lands with tests (validators get pure unit
tests against `tests/fixtures/ais140_sample.log` + live-capture samples).

## Target report format (from a real LOCATE 140B QC sheet, 13-07-2026)

A real exported report (`samples/LOCATE 140B[TS]-13-07-2026 (1).xlsx`) shows what QC must
ultimately **produce**. It's an Excel workbook, **per lot** (not per device):

- **`Devices Details`** — the summary matrix, one row per device, plus lot header
  (Date of Inspection, Item Name, Total Devices Tested, Lot Size) and a sign-off footer
  (Observation, Firmware Version, PCB Number, Corrective Action, **Final Result: Pass**,
  Quality tested by / approved by, a wire-colour harness pinout table, and
  `NOTE: tested on I.P 13.127.151.181 PORT 4048`). Its per-device columns:

  `GSM Fix · GPS Fix · Data on Static · Data on Intouch · All Input and Output (On Static) ·
  Harness · Battery Charging · Ignition On (Intouch) · Ignition Off (Intouch) ·
  Plugged/Unplugged Alarm on Intouch · COTA · FOTA · SMS Command · GPRS Command ·
  Serial Command · Power consumption without ignition (mA) · Power consumption with Ignition
  ON (mA) · Field Testing · Firmware(tested on) · Remarks`

- **`Raw Data-<IMEI>`** (one sheet per device) — firmware + **one captured raw sample per
  packet type / alert id**: LOGIN, Device Health (`$HEL`), Emergency (`$EPB` EMR + SEM),
  Location NR/L (alert 1), History NR/H (alert 2), and alert ids 3-16 (BD/BR/IN/IF/EA10/
  EA11/OT/HB/HA/DT…). Blank where that alert wasn't exercised (4,5,9,15 in this sample).

- **`Device status`, `Alarm-<IMEI>`** — pasted GPS/GPRS + alarm screenshots (manual).

**Key realisation:** the report is **auto-fillable columns + manual columns**:
- *Auto (packet-derived):* GSM/GPS fix, Data on Static, All I/O seen, Ignition On/Off,
  the alarm alert-ids, Firmware, and the whole `Raw Data` sheet (= coverage #1 + raw #6).
- *Auto (external):* Data on Intouch + the "(Intouch)" alarm columns (= #5).
- *Manual (operator/meter):* Power consumption mA, Harness, Battery charging, COTA/FOTA/
  SMS/GPRS/Serial command, Field testing, Remarks, and the sign-off block.

So the end goal isn't just live chips — it's: **auto-fill what the packet stream + iNTouch
prove, let the operator tick/enter the rest, then export this exact workbook per lot.**
That reshapes the phases: Phase A/E already produce the `Raw Data` sheet content; a new
**Phase F = lot report + Excel export** ties the matrix together with the manual fields.

### ⚠ Codec discrepancies found in the real capture (block accurate coverage)

The live firmware **2.2.5** deviates from `data/protocols/ais140.json` (doc v1.9) — the
codec must accept both or coverage/validation will mis-key:
- **Health packet header is `$HEL`, not `$HLP`** (`$HEL,LOCT14A,2.2.5,<imei>,68,30,0,10,600,0011,00*`).
  The codec/JSON only know `HLP`/`HLM` → health packets currently fall through to `unknown`.
- **Packet-type field carries `BD/BR/IN/IF/EA/OT/HB/HA/DT`** and the alert id in field 4;
  `DT` (tamper, id 16) isn't in the JSON's NMP enum.
- **Vendor tag is `LOCT14A`**; **LGN** on the wire is 7 fields with **no N/S/E/W direction
  letters** (`$LGN,0000000000,<imei>,2.2.5,AIS140,<lat>,<lon>*`) — the content-based `_lgn`
  parser already tolerates this, but it's worth a regression sample.

## Open questions

1. **Verdict policy** — does a missing *required* packet type fail the whole QC, or warn?
   (Plan assumes: required+missing = FAIL at Stop; optional+missing = grey `na`.)
2. **Golden samples** — are the `example` sentences in the protocol JSON authoritative for
   your devices, or should QC compare against a *captured* known-good packet per
   model/firmware (uploadable per profile)? Plan supports both: JSON examples by default,
   captured samples override.
3. **iNTouch** — exact API contract (auth, endpoint, response), and whether Runway should
   also *forward* the packets to iNTouch during QC (existing `tcp_relay` sink can) or the
   device sends to iNTouch in parallel on its own.
4. **History packets** — required on the bench? Testing them means forcing a GPRS outage;
   plan defaults them to optional.
