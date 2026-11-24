"""Test decode CRLF delimited UI packets"""

import asyncio

import pytest

from ax253 import Address, Frame, TNC2Decode
from ax253.tnc2 import TNC2Protocol

idiotv = dict(
    frames=b"""
IDIOTV>BEACON,HEBOWX,N7QXO-9,qAR,KG7ZZA-10:T#890,174,193,087,046,080,00010011
IDIOTV>BEACON,HEBOWX,N7QXO-9,qAR,KG7ZZA-10:T#891,173,192,086,046,079,00010011
IDIOTV>APN391,HEBOWX,qAR,KG7ZZA-10:!4539.11NF12324.96W#PHG27651/KB7WUK OR Idiotville fill in
IDIOTV>BEACON,HEBOWX,N7QXO-9,qAR,KG7ZZA-10:T#892,172,191,088,046,079,00010011
IDIOTV>BEACON,HEBOWX,N7QXO-9,qAR,KG7ZZA-10:;145.270- T107.2 Timber OR see www.nwaprs.info for settings KB7WUK-11
IDIOTV>BEACON,HEBOWX,N7QXO-9,qAR,NPLNS:T#893,173,192,087,046,079,00010011
IDIOTV>BEACON,HEBOWX,N7QXO-9,qAR,SHERWD:T#894,173,191,088,047,079,00010011
IDIOTV>APN391,HEBOWX,qAR,NPLNS:!4539.11NF12324.96W#PHG27651/KB7WUK OR Idiotville fill in
    """,
    exp=[
        Frame(
            destination=Address(callsign=b"BEACON", ssid=0, digi=False, a7_hldc=False),
            source=Address(callsign=b"IDIOTV", ssid=0, digi=False, a7_hldc=False),
            path=[
                Address(callsign=b"HEBOWX", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"N7QXO", ssid=9, digi=False, a7_hldc=False),
                Address(callsign=b"qAR", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"KG7ZZA", ssid=10, digi=False, a7_hldc=True),
            ],
            control=b"\x03",
            pid=b"\xf0",
            info=b"T#890,174,193,087,046,080,00010011",
        ),
        Frame(
            destination=Address(callsign=b"BEACON", ssid=0, digi=False, a7_hldc=False),
            source=Address(callsign=b"IDIOTV", ssid=0, digi=False, a7_hldc=False),
            path=[
                Address(callsign=b"HEBOWX", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"N7QXO", ssid=9, digi=False, a7_hldc=False),
                Address(callsign=b"qAR", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"KG7ZZA", ssid=10, digi=False, a7_hldc=True),
            ],
            control=b"\x03",
            pid=b"\xf0",
            info=b"T#891,173,192,086,046,079,00010011",
        ),
        Frame(
            destination=Address(callsign=b"APN391", ssid=0, digi=False, a7_hldc=False),
            source=Address(callsign=b"IDIOTV", ssid=0, digi=False, a7_hldc=False),
            path=[
                Address(callsign=b"HEBOWX", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"qAR", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"KG7ZZA", ssid=10, digi=False, a7_hldc=True),
            ],
            control=b"\x03",
            pid=b"\xf0",
            info=b"!4539.11NF12324.96W#PHG27651/KB7WUK OR Idiotville fill in",
        ),
        Frame(
            destination=Address(callsign=b"BEACON", ssid=0, digi=False, a7_hldc=False),
            source=Address(callsign=b"IDIOTV", ssid=0, digi=False, a7_hldc=False),
            path=[
                Address(callsign=b"HEBOWX", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"N7QXO", ssid=9, digi=False, a7_hldc=False),
                Address(callsign=b"qAR", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"KG7ZZA", ssid=10, digi=False, a7_hldc=True),
            ],
            control=b"\x03",
            pid=b"\xf0",
            info=b"T#892,172,191,088,046,079,00010011",
        ),
        Frame(
            destination=Address(callsign=b"BEACON", ssid=0, digi=False, a7_hldc=False),
            source=Address(callsign=b"IDIOTV", ssid=0, digi=False, a7_hldc=False),
            path=[
                Address(callsign=b"HEBOWX", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"N7QXO", ssid=9, digi=False, a7_hldc=False),
                Address(callsign=b"qAR", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"KG7ZZA", ssid=10, digi=False, a7_hldc=True),
            ],
            control=b"\x03",
            pid=b"\xf0",
            info=b";145.270- T107.2 Timber OR see www.nwaprs.info for settings KB7WUK-11",
        ),
        Frame(
            destination=Address(callsign=b"BEACON", ssid=0, digi=False, a7_hldc=False),
            source=Address(callsign=b"IDIOTV", ssid=0, digi=False, a7_hldc=False),
            path=[
                Address(callsign=b"HEBOWX", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"N7QXO", ssid=9, digi=False, a7_hldc=False),
                Address(callsign=b"qAR", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"NPLNS", ssid=0, digi=False, a7_hldc=True),
            ],
            control=b"\x03",
            pid=b"\xf0",
            info=b"T#893,173,192,087,046,079,00010011",
        ),
        Frame(
            destination=Address(callsign=b"BEACON", ssid=0, digi=False, a7_hldc=False),
            source=Address(callsign=b"IDIOTV", ssid=0, digi=False, a7_hldc=False),
            path=[
                Address(callsign=b"HEBOWX", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"N7QXO", ssid=9, digi=False, a7_hldc=False),
                Address(callsign=b"qAR", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"SHERWD", ssid=0, digi=False, a7_hldc=True),
            ],
            control=b"\x03",
            pid=b"\xf0",
            info=b"T#894,173,191,088,047,079,00010011",
        ),
        Frame(
            destination=Address(callsign=b"APN391", ssid=0, digi=False, a7_hldc=False),
            source=Address(callsign=b"IDIOTV", ssid=0, digi=False, a7_hldc=False),
            path=[
                Address(callsign=b"HEBOWX", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"qAR", ssid=0, digi=False, a7_hldc=False),
                Address(callsign=b"NPLNS", ssid=0, digi=False, a7_hldc=True),
            ],
            control=b"\x03",
            pid=b"\xf0",
            info=b"!4539.11NF12324.96W#PHG27651/KB7WUK OR Idiotville fill in",
        ),
    ],
)


@pytest.mark.parametrize(
    "packet_data,exp_frames,exp_exception",
    (
        (
            idiotv["frames"],
            idiotv["exp"],
            None,
        ),
    ),
)
def test_tnc2_decode(packet_data, exp_frames, exp_exception):
    try:
        assert list(TNC2Decode().update(packet_data)) == exp_frames
    except Exception as exc:
        if exp_exception:
            assert exp_exception in str(exc)
        raise


@pytest.mark.parametrize("eol", ["\r", "\n", "\r\n"])
def test_str_ends_info_at_line_break(eol):
    """
    Issue #26: a CR or LF in the info field (e.g. heard on RF) must not
    start a second, forged packet in TNC2 text sent to APRS-IS.
    """
    frame = Frame.ui(
        destination="APRS", source="N0CALL", info=f">hi{eol}EVIL>APRS::N0CALL   :pwned{{1"
    )
    assert str(frame) == "N0CALL>APRS:>hi"
    assert frame.info.endswith(b"pwned{1")  # the frame itself is unchanged


def test_tnc2_protocol_writes_one_line_per_frame():
    written = []

    class Transport(asyncio.Transport):
        def write(self, data):
            written.append(data)

    async def write_frame():
        protocol = TNC2Protocol(transport=Transport())
        protocol.write(
            Frame.ui(destination="APRS", source="N0CALL", info=b">x\rEVIL>APRS:>y")
        )

    asyncio.run(write_frame())
    decoded = list(TNC2Decode().update(b"".join(written)))
    assert [str(f) for f in decoded] == ["N0CALL>APRS:>x"]
