"""Platform message-log exports (``<time> | <imei> | ,"MMI,...*37 | null"``) read as streams.

The fixture is the first rows of a real export for IMEI 865577071515754 (Zenithra sCAN,
firmware 5.1.30I): newest first, the two 12:05:17 rows in the same second, no '$' on any
message. Frame numbers give the true arrival order: 1069 -> 1070 -> 1071.
"""

from pathlib import Path

from runway.convert.log_export import unwrap
from runway.core.models import Session
from runway.core.registry import get_codec, load_plugins
from runway.sources.file_replay import FileReplaySource

EXPORT = Path(__file__).parent / "fixtures" / "zenithra_log_export.csv"


def _frames(stream: bytes) -> list[str]:
    load_plugins()
    c, sess, out = get_codec("zenithra"), Session(id="t"), []
    while stream:
        o = c.decode(stream, sess)
        assert o.consumed > 0
        out += [r.io["frame_number"] for r in o.records]
        stream = stream[o.consumed:]
    return out


def test_unwrap_rebuilds_the_stream_in_arrival_order():
    stream = unwrap(EXPORT.read_text(encoding="ascii"))
    lines = stream.split(b"\r\n")[:-1]
    assert len(lines) == 3 and all(ln.startswith(b"$MMI,V2.2.0,5.1.30I,") for ln in lines)
    assert stream.endswith(b"*37\r\n")             # the NR row, which arrived last
    assert _frames(stream) == ["001069", "001070", "001071"]


def test_unwrap_leaves_other_input_alone():
    assert unwrap("") is None
    assert unwrap("$MMI,V2.2.0,5.1.29C,NR,1,L,868221073948609*A4\r\n") is None   # device text
    assert unwrap("78 78 0d 01 03 51 09") is None                                # hex dump


def test_unwrap_hex_messages_iso_times_and_a_cut_line():
    text = ('2026-11-04T12:00:02 | 1 | ,"0102 | null"\n'
            '2026-11-04T12:00:01 | 1 | ,"PVT,GTRP,1 | null"\n'
            '2026-11-04T11:59:5')                        # an excerpt cut mid-row
    assert unwrap(text) == b"$PVT,GTRP,1\r\n" + b"\x01\x02"


def test_file_replay_sniffs_and_replays_an_export():
    src = FileReplaySource(str(EXPORT))
    assert src._sniff(EXPORT.read_bytes()) == "log_export"
    assert _frames(src._load_bytes()) == ["001069", "001070", "001071"]


async def test_parser_reads_an_uploaded_export():
    """The Parser's Upload button posts the file's text as-is."""
    from httpx import ASGITransport, AsyncClient

    from runway.api.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        d = (await c.post("/api/parse", json={"hex": EXPORT.read_text(encoding="ascii")})).json()
    assert d["protocol"] == "zenithra"
    assert len(d["packets"]) == 3 and d["skipped"] == 0 and d["error"] is None
    imu = d["packets"][0]
    named = {f["name"]: f["value"] for f in imu["fields"]}
    assert (named["packet_type"], named["frame_number"]) == ("IMU", "001069")
    assert named["nmr1_cell_id"] == "E28130C"           # neighbour cells named, not field_33..44
    assert not any(f["group"].startswith("Fields not") for f in imu["fields"])
    assert sum(f["group"] == "imu (20)" for f in imu["fields"]) == 20
