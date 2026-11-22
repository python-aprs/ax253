import struct

import pytest

from ax253 import Address, AX25BytestreamDecoder, Control, Frame
from ax253.fcs import FCS

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2022 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"


@pytest.mark.parametrize(
    "ax25_str, exp_frame",
    (
        (
            "FOO>APRS:!4605.21N/12327.31W#RNG0125Foo comment",
            Frame(
                destination=Address(callsign=b"APRS", ssid=0, digi=False, a7_hldc=False),
                source=Address(callsign=b"FOO", ssid=0, digi=False, a7_hldc=True),
                path=[],
                control=Control(b"\x03"),
                pid=b"\xf0",
                info=b"!4605.21N/12327.31W#RNG0125Foo comment",
            ),
        ),
    ),
)
def test_Frame_from_str(ax25_str, exp_frame):
    assert Frame.from_str(ax25_str) == exp_frame


def x25_fcs(data: bytes) -> bytes:
    """Reference CRC-16/X.25 (the AX.25 FCS), independent of ax253.fcs."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return struct.pack("<H", crc ^ 0xFFFF)


def hdlc(frame: Frame, fcs: bytes | None = None) -> bytes:
    raw = bytes(frame)
    return b"~" + raw + (x25_fcs(raw) if fcs is None else fcs) + b"~"


F1 = Frame.from_str("N0CALL>APRS,WIDE1-1:foo bar baz")
F2 = Frame.from_str("N0CALL>APZ069,WIDE1-1,FOODP*:digi'd 1")
F3 = Frame.from_str("KF7HVM-9>APDR16:>third")


def test_fcs_check_value():
    """CRC catalogue check value for CRC-16/X.25 is 0x906E over b"123456789"."""
    assert FCS.from_bytes(b"123456789").digest() == b"\x6e\x90"
    assert bytes(FCS.from_bytes(b"123456789")) == b"\x6e\x90"


@pytest.mark.parametrize("frame", [F1, F2, F3], ids=str)
def test_fcs_matches_reference(frame):
    assert FCS.from_bytes(bytes(frame)).digest() == x25_fcs(bytes(frame))


def test_fcs_known_vector():
    """Literal vector so a change in both implementations is still caught."""
    assert hdlc(F1) == (
        b"~\x82\xa0\xa4\xa6@@`\x9c`\x86\x82\x98\x98`\xae\x92\x88\x8ab@c"
        b"\x03\xf0foo bar baz" + x25_fcs(bytes(F1)) + b"~"
    )
    assert x25_fcs(bytes(F1)) == FCS.from_bytes(bytes(F1)).digest()


def _decode(chunks):
    d = AX25BytestreamDecoder()
    frames = []
    for c in chunks:
        frames.extend(d.update(c))
    frames.extend(d.flush())
    return frames


STREAM = hdlc(F1) + hdlc(F2) + hdlc(F3)


@pytest.mark.parametrize(
    "chunks, exp_frames",
    (
        ([hdlc(F1)], [F1]),
        ([hdlc(F1)[:10], hdlc(F1)[10:]], [F1]),
        ([hdlc(F1)[1:]], [F1]),
        ([hdlc(F1) + hdlc(F2)], [F1, F2]),
        ([hdlc(F1) + b"~~~~~~~"], [F1]),
        ([b"~~~~" + hdlc(F2)], [F2]),
        ([STREAM[i : i + 5] for i in range(0, len(STREAM), 5)], [F1, F2, F3]),
    ),
    ids=[
        "single frame",
        "split frame",
        "no leading flag",
        "2 frames",
        "extra trailing flags",
        "extra leading flags",
        "5-byte chunks",
    ],
)
def test_AX25BytestreamDecoder(chunks, exp_frames):
    assert _decode(chunks) == exp_frames


def test_AX25BytestreamDecoder_bad_fcs_dropped_stream_continues():
    corrupt = hdlc(F1, fcs=b"\x00\x00")
    assert _decode([corrupt + hdlc(F2)]) == [F2]


def test_AX25BytestreamDecoder_corrupt_payload_dropped():
    raw = bytearray(hdlc(F1))
    raw[20] ^= 0x01  # flip a bit; FCS no longer matches
    assert _decode([bytes(raw) + hdlc(F3)]) == [F3]


def test_AX25BytestreamDecoder_fcs_over_received_bytes():
    """
    Dire Wolf sets the destination/source C bits, ax253 re-encodes them
    cleared. The FCS must be checked over the bytes received.
    """
    raw = bytearray(bytes(F3))
    raw[6] |= 0x80
    raw[13] |= 0x80
    stream = b"~" + bytes(raw) + x25_fcs(bytes(raw)) + b"~"
    assert _decode([stream]) == [F3]
