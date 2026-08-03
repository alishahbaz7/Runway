"""Teltonika AVL ID -> human parameter name (+ unit) lookup.

Teltonika devices report telemetry as numeric AVL IO IDs (e.g. ``239``); the packet
itself never carries a name. This table maps the common FMB-family IDs to readable
parameter names so the inspector can show "Ignition" instead of "AVL ID 239".

The list is the widely-shared permanent-I/O set (FMBxxx). It is intentionally a
*common* subset, not exhaustive or model-exact - some IDs differ per model/firmware.
Unknown IDs fall back to ``"AVL ID <n>"`` so nothing is ever dropped.

Each entry is ``(name, unit)``; ``unit`` is a display hint ("" when unitless). The
raw integer value is shown as-is - we do not apply scaling here, to keep the inspector
faithful to the bytes on the wire.
"""

from __future__ import annotations

# id -> (name, unit)
AVL_IDS: dict[int, tuple[str, str]] = {
    1: ("Digital Input 1", ""),
    2: ("Digital Input 2", ""),
    3: ("Digital Input 3", ""),
    4: ("Pulse Counter Din1", ""),
    5: ("Pulse Counter Din2", ""),
    6: ("Analog Input 2", "mV"),
    9: ("Analog Input 1", "mV"),
    10: ("SD Status", ""),
    11: ("ICCID1", ""),
    12: ("Fuel Used GPS", "ml"),
    13: ("Average Fuel Use", "L/100km"),
    15: ("Eco Score", ""),
    16: ("Total Odometer", "m"),
    17: ("Axis X", "mG"),
    18: ("Axis Y", "mG"),
    19: ("Axis Z", "mG"),
    21: ("GSM Signal", ""),
    24: ("Speed", "km/h"),
    66: ("External Voltage", "mV"),
    67: ("Battery Voltage", "mV"),
    68: ("Battery Current", "mA"),
    69: ("GNSS Status", ""),
    72: ("Dallas Temperature 1", "0.1 C"),
    73: ("Dallas Temperature 2", "0.1 C"),
    74: ("Dallas Temperature 3", "0.1 C"),
    75: ("Dallas Temperature 4", "0.1 C"),
    78: ("iButton", ""),
    80: ("Data Mode", ""),
    81: ("Vehicle Speed", "km/h"),
    82: ("Accelerator Pedal Position", "%"),
    83: ("Fuel Consumed", "L"),
    84: ("Fuel Level", "L"),
    85: ("Engine RPM", "rpm"),
    87: ("Total Mileage (CAN)", "m"),
    89: ("Fuel Level", "%"),
    90: ("Door Status", ""),
    100: ("Program Number", ""),
    101: ("Module ID 8B", ""),
    102: ("Engine Worktime", ""),
    104: ("Total Mileage Counted", "m"),
    105: ("Fuel Consumed Counted", "L"),
    107: ("Fuel Rate", "L/h"),
    113: ("Battery Level", "%"),
    115: ("Engine Temperature", "0.1 C"),
    179: ("Digital Output 1", ""),
    180: ("Digital Output 2", ""),
    181: ("GNSS PDOP", "0.1"),
    182: ("GNSS HDOP", "0.1"),
    199: ("Trip Odometer", "m"),
    200: ("Sleep Mode", ""),
    205: ("GSM Cell ID", ""),
    206: ("GSM Area Code", ""),
    239: ("Ignition", ""),
    240: ("Movement", ""),
    241: ("Active GSM Operator", ""),
    246: ("Towing", ""),
    247: ("Crash Detection", ""),
    248: ("Immobilizer", ""),
    249: ("Jamming", ""),
    250: ("Trip", ""),
    251: ("Idling", ""),
    252: ("Unplug", ""),
    253: ("Green Driving Type", ""),
    254: ("Green Driving Value", ""),
    255: ("Overspeeding", "km/h"),
    256: ("VIN", ""),
    257: ("Crash Trace Data", ""),
    263: ("BT Status", ""),
    264: ("Barcode ID", ""),
    303: ("Instant Movement", ""),
    636: ("UMTS/LTE Cell ID", ""),
}


def avl_param(io_id: int) -> dict[str, str]:
    """Return ``{"name", "unit"}`` for an AVL IO id, with a safe fallback."""
    name, unit = AVL_IDS.get(io_id, (f"AVL ID {io_id}", ""))
    return {"name": name, "unit": unit}
