"""A simulated Locate 140 4G for the firmware-test tests: a day of packets from a correct device, and the
same day with known faults switched on — each fault one a real firmware has shown (V7.06.10) or one the
gate must not let through. Built in code; a made-up IMEI.

    dev = Device(faults={"kept_live", "lifo"})
    events = day(dev)                 # [(raw bytes, received epoch, connection, skipped)]
"""

from datetime import datetime, timezone
from functools import reduce

IMEI = "356938035643809"
OTHER = "490154203237518"
T0 = 1795000000.0
CONFIG = {"ign_on_s": 10, "ign_off_s": 60, "sleep_after_min": 10, "sleep_s": 600, "history_order": "fifo",
          "formats": {"PVT": "50"}}
FAULTS = {
    "kept_live": "history packets keep the checksum of their live version",
    "login_star": "the login's checksum is worked out over the '*' too",
    "hel_no_cs": "the health packet has no checksum",
    "fields51": "a 51st field before the OTA response",
    "short_date": "some history packets carry DDMMYY",
    "broken_time": "some history packets carry a broken time",
    "lifo": "history sent newest first",
    "no_login_on_reconnect": "no login on the second connection",
    "repeat_frame": "a live frame number used twice",
    "bd_power_1": "a main-power-disconnect packet says main power 1",
    "wrong_imei": "one packet carries another IMEI",
    "garbage": "stray bytes between packets",
    "cut": "a packet cut off mid-way",
    "unknown": "a packet type the sheet does not describe",
    "stale_live": "a packet made long ago sent marked live",
    "time_repeat": "the same history packet made twice: one time, one data, a new frame number",
    "stuck_clock": "two different packets at one time (the clock did not move)",
}


def xor(b: bytes) -> int:
    return reduce(lambda a, c: a ^ c, b, 0)


def _d(at):
    t = datetime.fromtimestamp(at, timezone.utc)
    return t.strftime("%d%m%Y"), t.strftime("%H%M%S")


class Device:
    def __init__(self, faults=(), fw="V7.6.0", reg="DL1CAB1234"):
        self.f = set(faults)
        unknown = self.f - set(FAULTS)
        assert not unknown, unknown
        self.fw, self.reg = fw, reg
        self.frame = 0
        self.events = []
        self.conns = 0
        self.conn = None
        self.online = True
        self.buffer = []
        self.ign, self.power, self.emerg, self.tamper = "1", "1", "0", "C"
        self.speed, self.heading, self.fix, self.sats = 0.0, 0, "1", 15
        self.di, self.do, self.ain = "1000", "00", 0.0
        self.lat, self.lon = 28.550455, 77.268884
        self.sent_frames = 0

    # ── the wire ──────────────────────────────────────────────────────────────────────────────
    def emit(self, raw: bytes, received: float, skipped=False):
        self.events.append((raw, received, self.conn, skipped))

    def frame_of(self, body: str, star: bool = False, cs: bool = True) -> bytes:
        raw = ("$" + body).encode()
        tail = f"{xor(raw + (b'*' if star else b'')):02X}" if cs else ""
        return raw + b"*" + tail.encode() + b"\r\n"

    def connect(self, at: float):
        self.conns += 1
        self.conn = f"10.0.0.{self.conns}:{5000 + self.conns}"
        self.online = True
        if not ("no_login_on_reconnect" in self.f and self.conns > 1):
            body = f"LGN,{self.reg},{IMEI},{self.fw},AIS140,{self.lat:.6f},N,{self.lon:.6f},E"
            self.emit(self.frame_of(body, star="login_star" in self.f), at)
        held = list(reversed(self.buffer)) if "lifo" in self.f else self.buffer
        for i, raw in enumerate(held):
            self.emit(raw, at + 1 + 0.2 * i)
        self.buffer = []

    # ── packets ───────────────────────────────────────────────────────────────────────────────
    def pvt(self, at: float, ptype="NR", alert=None, imei=IMEI):
        self.frame += 1
        frame = self.frame
        if "repeat_frame" in self.f and self.online and frame == 20:
            self.frame -= 1                                       # the counter does not move once
            frame = self.frame
            self.frame += 1
        live = self.online
        st = "L" if live else "H"
        if alert is None:
            alert = "01" if live else "02"
        date, time = _d(at)
        if not live and "short_date" in self.f and frame % 5 == 0:
            date = date[:4] + date[6:]
        if not live and "broken_time" in self.f and frame % 7 == 0:
            time = "07804.000"

        def body(status, al):
            fields = ["PVT", "GTRP", self.fw, ptype, al, status, imei, self.reg, self.fix, date, time,
                      f"{self.lat:.6f}", "N", f"{self.lon:.6f}", "E", f"{self.speed:.1f}", f"{self.heading:03d}",
                      str(self.sats), "221.0", "0.8", "0.4", "VI", self.ign, self.power, "14.0", "3.9", self.emerg,
                      self.tamper, "25", "404", "11", "CDF3", "642D633",
                      "20", "0CDF", "0430", "18", "0CDF", "0280", "15", "0CDF", "0045", "12", "0CDF", "0094",
                      self.di, self.do, f"{frame:06d}", f"{self.ain:.1f}", "0.0"]
            if "fields51" in self.f:
                fields.append("29.1")
            fields.append("()")
            return ",".join(fields)

        if not live and "kept_live" in self.f:
            live_raw = ("$" + body("L", "01")).encode()
            raw = ("$" + body(st, alert)).encode() + b"*" + f"{xor(live_raw):02X}".encode() + b"\r\n"
        else:
            raw = self.frame_of(body(st, alert))
        if live:
            self.emit(raw, at + (3600 if ("stale_live" in self.f and frame == 15) else 1))
        else:
            self.buffer.append(raw)

    def alert(self, at, ptype, alert, **state):
        for k, v in state.items():
            setattr(self, k, v)
        self.pvt(at, ptype, alert)

    def hel(self, at):
        body = f"HEL,GTRP,{self.fw},{IMEI},31,10,71.38,71.38,10,60,000111,00.00,00.00"
        self.emit(self.frame_of(body, cs="hel_no_cs" not in self.f), at)

    def hlm(self, at):
        self.emit(self.frame_of(f"HLM,GTRP,{self.fw},{IMEI},99,30,45.5,10,60"), at)

    def epb(self, at, typ, status="NM"):
        t = datetime.fromtimestamp(at, timezone.utc).strftime("%d%m%Y%H%M%S")
        body = (f"EPB,{typ},{IMEI},{status},{t},A,{self.lat:.6f},N,{self.lon:.6f},E,221.0,{self.speed:.1f},G,"
                f"0.0,{self.reg},0")
        self.emit(self.frame_of(body), at + 1)


def day(dev: Device) -> list:
    """A day on the bench and the road: every scenario of the Locate 140 4G, in order."""
    t = T0
    dev.connect(t)
    for i in range(120):                                          # ignition on, driving, every 10 s
        at = t + 10 + 10 * i
        dev.speed = 0.0 if i < 3 else 30.0 + (i % 20)
        dev.heading = (i * 7) % 360
        dev.lat += 0.0002 if i >= 3 else 0
        dev.di = "1000" if i % 40 < 20 else "1100"
        dev.ain = (i % 10) / 2
        if i == 60:
            dev.online = False                                    # out of coverage: buffered as history
        if i == 90:
            dev.connect(at - 5)                                   # back: login, then the history
            if "garbage" in dev.f:
                dev.emit(b"GARBAGE\r\n", at - 3, skipped=True)
        dev.pvt(at)
        if i == 30:
            dev.alert(at + 2, "HA", "14")
        if i == 40:
            dev.alert(at + 2, "HB", "13")
        if i == 50:
            dev.alert(at + 2, "RT", "15")
        if i == 100:
            dev.alert(at + 2, "EA", "10", emerg="1")
            dev.epb(at + 3, "EMR", "NM")
        if i == 104:
            dev.alert(at + 2, "EA", "11", emerg="0")
            dev.epb(at + 3, "SEM", "NM")
            dev.epb(at + 4, "EMR", "SP")
        if i == 108:
            dev.alert(at + 2, "TA", "16", tamper="O")
            dev.tamper = "C"
        if i == 110:
            dev.alert(at + 2, "HP", "00")
            dev.hel(at + 3)
            dev.hlm(at + 4)
        if i == 112:
            dev.alert(at + 2, "OT", "12")
        if i == 114:
            for k, typ in enumerate(("DT", "OA", "BC", "TL")):
                dev.alert(at + 2 + k, typ, {"DT": "16", "TL": "22"}.get(typ, "00"))
        if i == 115:                                              # moved without ignition (FSD-ALL-002): start, end
            dev.alert(at + 2, "MoveS", "21")
            dev.alert(at + 6, "MoveE", "22")
        if i == 116 and "wrong_imei" in dev.f:
            dev.pvt(at + 5, imei=OTHER)
        if i == 118:
            if "unknown" in dev.f:
                dev.emit(dev.frame_of("XYZ,1,2"), at + 6)
            if "cut" in dev.f:
                dev.emit(b"$PVT,GTRP,V7.6.0,NR,01,L,35693", at + 7, skipped=True)
    off = t + 1210                                                # ignition off: standby, then sleep
    dev.speed = 0.0
    dev.alert(off, "IF", "08", ign="0")
    for k in range(1, 11):
        if k == 4:
            dev.fix, dev.sats = "0", 0                            # a moment without a fix
        dev.pvt(off + 60 * k)
        dev.fix, dev.sats = "1", 15
        if k == 7:
            dev.alert(off + 60 * k + 5, "BD", "03", power="1" if "bd_power_1" in dev.f else "0")
        if k == 8:
            dev.alert(off + 60 * k + 5, "BR", "06", power="1")
        if k == 9:
            dev.alert(off + 60 * k + 5, "BL", "04")
    for k in range(1, 4):
        dev.pvt(off + 600 + 600 * k)                              # asleep, every 600 s
    on = off + 600 + 600 * 3 + 100
    dev.alert(on, "IN", "07", ign="1")
    for k in range(1, 13):
        dev.pvt(on + 10 * k)                                      # idling: ignition on, parked
    if "time_repeat" in dev.f:
        dev.online = False
        dev.pvt(on + 200)
        dev.pvt(on + 200)
        dev.connect(on + 300)
    if "stuck_clock" in dev.f:
        dev.pvt(on + 400)
        dev.speed = 12.0                                          # moving on, the clock not
        dev.pvt(on + 400)
        dev.speed = 0.0
    return dev.events
