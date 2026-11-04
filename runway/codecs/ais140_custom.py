"""Custom packets on the AIS-140 sentence family — data carried in the payload field.

Our own firmware packets (not part of the AIS-140 spec) reuse the standard normal-packet
layout field for field, and put their data in the one parenthesized field — the slot a normal
packet uses for its OTA response, ``()`` when idle. They are told apart by **identity, not
shape**: packet type + alert id, e.g. ``IMU`` / ``200``.

So the two questions a decoder asks are answered in two places:

* *where does each field sit?* — by the sentence's shape, from the layout tables
  (``ais140.NMP_LAYOUTS``, ``zenithra.MMI_LAYOUTS``);
* *what does the payload field mean?* — by packet type + alert id, from :data:`CUSTOM_PACKETS`.

Every AIS-140-family codec (``$PVT``/``$NMP`` and Zenithra's ``$MMI``) runs the same lookup,
so **adding a custom packet = one function here under** ``@custom_packet(...)`` — no codec,
layout or dispatch change. The function gets the payload field as sent (parentheses
included) and returns the ``io`` keys to add.

Pure on purpose (no I/O, no framework imports), like the rest of ``codecs/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CustomPacket:
    packet_type: str
    alert_id: int
    message_type: str                       # UnifiedRecord.message_type for this packet
    parse: Callable[[str], dict[str, Any]]  # payload field -> io keys
    alarm: str | None = None                # appended to UnifiedRecord.alarms; None = data, not an alarm


CUSTOM_PACKETS: dict[tuple[str, int], CustomPacket] = {}


def custom_packet(packet_type: str, alert_id: int, *, message_type: str,
                  alarm: str | None = None):
    """Register the payload parser for one (packet type, alert id) pair."""
    def register(fn: Callable[[str], dict[str, Any]]):
        CUSTOM_PACKETS[(packet_type, alert_id)] = CustomPacket(
            packet_type, alert_id, message_type, fn, alarm)
        return fn
    return register


# --------------------------------------------------------------------------- #
# IMU — 6-axis sensor samples (packet type IMU, alert id 200)
# --------------------------------------------------------------------------- #
# Order as sent: x|y|z|p|r|y — one direct register read of an ICM-42670 per sample tick (a
# snapshot, not an average): acceleration in m/s² (±8 g, gravity included — ~9.81 on z at rest)
# and the gyro's pitch / roll / yaw rates in °/s (±250 dps). Sensor ODR 100 Hz; the packet keeps
# 20 readings spread over its interval (sCAN firmware, SET IMUPKT — see storage/telemetry.py).
IMU_AXES = ("ax", "ay", "az", "pitch", "roll", "yaw")


@custom_packet("IMU", 200, message_type="imu")
def imu(field: str) -> dict[str, Any]:
    """``(1:x|y|z|p|r|y;2:...;n:...)`` -> ``io["imu"]``, one dict per sample, in the order sent.

    The sample count is whatever the packet carries (20 today; firmware may change it), so
    nothing here assumes a fixed n. A sample that doesn't read as ``<n>:<6 numbers>`` is kept
    verbatim in ``io["imu_bad_samples"]`` instead of failing the whole packet.

    No per-sample time here: it depends on the packet interval for the ignition state, which a
    single packet does not carry — the telemetry store works it out when samples are read.
    """
    samples: list[dict[str, Any]] = []
    bad: list[str] = []
    for part in field.strip().strip("()").split(";"):
        part = part.strip()
        if not part:
            continue
        n, _, values = part.partition(":")
        try:
            samples.append({"n": int(n), **{axis: float(v) for axis, v in
                                            zip(IMU_AXES, values.split("|"), strict=True)}})
        except ValueError:
            bad.append(part)
    out: dict[str, Any] = {"imu_sample_count": len(samples)}
    if samples:
        out["imu"] = samples
    if bad:
        out["imu_bad_samples"] = bad
    return out
