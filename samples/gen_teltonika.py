"""Generate a synthetic Teltonika capture you can replay into the dashboard.

No real OEM capture is available yet (kickoff brief, open question F), so this builds
valid TCP frames with the real CRC helper: an IMEI handshake followed by a moving track
of Codec-8 AVL records, for one or more devices. The output is ``hexlines`` (one packet
per line), which the file-replay source auto-detects.

Usage:
    python samples/gen_teltonika.py            # writes samples/teltonika_demo.hex (2 devices)
    proinfo replay samples/teltonika_demo.hex --codec teltonika
    # or upload it in the dashboard (Codec = teltonika, Format = auto)
"""

from __future__ import annotations

from pathlib import Path

from proinfo.core.crc import crc16_ibm

# Two devices, each starting at a point near Delhi and drifting east.
DEVICES = [
    {"imei": "356307042441013", "start": (28.6139, 77.2090)},
    {"imei": "352093081452367", "start": (28.7041, 77.1025)},
]
POINTS_PER_DEVICE = 12
BASE_TS_MS = 1_719_318_600_000


def avl_record(ts_ms: int, lat: float, lon: float, speed: int, angle: int,
               ignition: int, movement: int) -> bytes:
    gps = (int(lon * 1e7).to_bytes(4, "big", signed=True)
           + int(lat * 1e7).to_bytes(4, "big", signed=True)
           + (80).to_bytes(2, "big", signed=True)   # altitude
           + (angle).to_bytes(2, "big")
           + bytes([10])                             # satellites
           + (speed).to_bytes(2, "big"))
    # IO: ignition(239)=1B, movement(240)=1B, GSM(21)=1B, ext-voltage(66)=2B mV.
    io = (bytes([0])                                 # event io id
          + bytes([4])                               # total io count
          + bytes([3]) + bytes([239, ignition, 240, movement, 21, 4])  # three 1-byte
          + bytes([1]) + bytes([66]) + (12500).to_bytes(2, "big")      # one 2-byte
          + bytes([0])                               # four-byte group
          + bytes([0]))                              # eight-byte group
    return ts_ms.to_bytes(8, "big") + bytes([1]) + gps + io


def data_packet(records: list[bytes]) -> bytes:
    data = bytes([0x08, len(records)]) + b"".join(records) + bytes([len(records)])
    return (b"\x00\x00\x00\x00" + len(data).to_bytes(4, "big")
            + data + crc16_ibm(data).to_bytes(4, "big"))


def handshake(imei: str) -> bytes:
    return len(imei).to_bytes(2, "big") + imei.encode("ascii")


def build() -> list[str]:
    lines: list[str] = []
    for dev in DEVICES:
        lines.append(handshake(dev["imei"]).hex())
        lat, lon = dev["start"]
        for i in range(POINTS_PER_DEVICE):
            lat += 0.0008 * i
            lon += 0.0012 * i
            speed = 30 + (i * 7) % 60
            rec = avl_record(BASE_TS_MS + i * 15_000, lat, lon, speed,
                             angle=(i * 30) % 360, ignition=1, movement=1 if speed else 0)
            lines.append(data_packet([rec]).hex())
    return lines


if __name__ == "__main__":
    out = Path(__file__).parent / "teltonika_demo.hex"
    out.write_text("\n".join(build()) + "\n", encoding="ascii")
    print(f"wrote {out} ({len(DEVICES)} devices, {POINTS_PER_DEVICE} points each)")
