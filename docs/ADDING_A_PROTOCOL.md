# Adding a new protocol (codec)

Adding support for a new OEM/device is one file. Nothing else in Runway changes.

## 1. Copy the template

```bash
cp runway/codecs/custom_template.py runway/codecs/acme.py
```

## 2. Name it

```python
@register_codec
class AcmeCodec(Codec):
    name = "acme"                       # unique slug (used in CLI/API/dashboard)
    display_name = "ACME Tracker v2"
```

## 3. Implement `detect()` (optional but recommended)

Lets `--codec auto` and the dashboard auto-pick your codec. Return confidence 0..1.

```python
def detect(self, sample: bytes) -> float:
    return 0.9 if sample[:2] == b"\xAB\xCD" else 0.0
```

## 4. Implement `decode()`

This is the only required method. Parse the **front** of `buffer` and return a
`DecodeOutput`.

```python
def decode(self, buffer: bytes, session: Session) -> DecodeOutput:
    if len(buffer) < 4:
        return DecodeOutput(consumed=0)               # need more bytes
    if buffer[:2] != b"\xAB\xCD":
        return DecodeOutput(consumed=1)               # resync: drop one byte
    length = buffer[2]
    total = 3 + length + 1                             # header + payload + crc
    if len(buffer) < total:
        return DecodeOutput(consumed=0)               # need the whole packet
    payload = buffer[3:3 + length]

    rec = UnifiedRecord(
        device_id=session.device_id or "acme-unknown",
        protocol=self.name,
        message_type="location",
        position=Position(latitude=lat, longitude=lon),
        speed_kph=speed,
        raw_hex=buffer[:total].hex(),
    )
    return DecodeOutput(records=[rec], consumed=total, ack=self._ack(...))
```

### The `decode` contract

| You return…                       | Pipeline does…                                  |
|-----------------------------------|-------------------------------------------------|
| `consumed = 0`                    | keeps the buffer, calls you again on more data  |
| `consumed = N`                    | drops N bytes from the front                    |
| `records = [...]`                 | stamps + fans them out to all sinks             |
| `ack = b"..."`                    | writes those bytes back to the device           |

### State rules

- A codec instance is reused across packets **of one session** — keep per-connection
  state (learned IMEI, counters) in `session.state`, not on `self`.
- Set `session.device_id` once you learn it (e.g. from a login packet); later records
  that omit the id inherit it automatically.

## 5. Map fields well

Put recognized telemetry on first-class `UnifiedRecord` fields (`speed_kph`, `ignition`,
`satellites`, `battery_v`, …). Everything else goes in `io` (keep the original codes) so
nothing is lost and the inspector can show it. Always set `raw_hex`.

## 6. Add a sample + test

Drop a capture in `samples/acme_capture.hex` and a test in `tests/` (see
`tests/test_codecs.py` for the pattern). Then:

```bash
runway codecs                                  # your codec should be listed
runway replay samples/acme_capture.hex --codec acme --hex
pytest
```

That's it — your protocol now works in the CLI, the REST API and the dashboard.
