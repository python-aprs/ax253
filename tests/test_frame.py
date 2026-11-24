import struct

import attr
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
    Dire Wolf sets the destination/source C bits. The FCS is checked over
    the bytes received, and the bits are kept (issue #18).
    """
    raw = bytearray(bytes(F3))
    raw[6] |= 0x80
    raw[13] |= 0x80
    stream = b"~" + bytes(raw) + x25_fcs(bytes(raw)) + b"~"
    (frame,) = _decode([stream])
    assert frame == attr.evolve(F3, command_response=(True, True))
    assert bytes(frame) == bytes(raw)
    assert str(frame) == str(F3)


# --- issue #15: 0x7E inside a frame's bytes must not cut the frame short ---

TILDE = Frame.from_str("N0CALL>APRS:>hello~world~")


def _frame_with_flag_in_fcs() -> Frame:
    for i in range(100000):
        f = Frame.from_str(f"N0CALL>APRS:>fcs {i}")
        if b"~" in x25_fcs(bytes(f)):
            return f
    raise AssertionError("no frame found")  # pragma: no cover


FCS_TILDE = _frame_with_flag_in_fcs()


@pytest.mark.parametrize("frame", [TILDE, FCS_TILDE], ids=["info", "fcs"])
def test_AX25BytestreamDecoder_flag_byte_inside_frame(frame):
    stream = hdlc(frame) + hdlc(F2)
    d = AX25BytestreamDecoder()
    # decoded without waiting for flush
    assert list(d.update(stream)) == [frame, F2]
    assert list(d.flush()) == []


@pytest.mark.parametrize("frame", [TILDE, FCS_TILDE], ids=["info", "fcs"])
def test_AX25BytestreamDecoder_flag_byte_inside_frame_chunked(frame):
    stream = hdlc(F1) + hdlc(frame) + hdlc(F3)
    for size in (1, 2, 3, 7):
        chunks = [stream[i : i + size] for i in range(0, len(stream), size)]
        assert _decode(chunks) == [F1, frame, F3], size


def test_AX25BytestreamDecoder_corrupt_then_valid_without_flush():
    raw = bytearray(hdlc(F1))
    raw[20] ^= 0x01
    d = AX25BytestreamDecoder()
    assert list(d.update(bytes(raw) + hdlc(F2))) == [F2]
    assert list(d.update(hdlc(F3))) == [F3]


def test_AX25BytestreamDecoder_garbage_is_bounded():
    d = AX25BytestreamDecoder()
    assert list(d.update(b"~" + b"\x55" * 5000)) == []
    assert list(d.update(hdlc(F1))) == [F1]


# --- issue #16: flush() only decodes a complete, FCS-valid frame ---


def test_AX25BytestreamDecoder_flush_drops_bad_unterminated():
    d = AX25BytestreamDecoder()
    assert list(d.update(hdlc(F1)[:-5])) == []
    assert list(d.flush()) == []


def test_AX25BytestreamDecoder_flush_decodes_valid_unterminated():
    d = AX25BytestreamDecoder()
    assert list(d.update(hdlc(F1)[:-1])) == []
    assert list(d.flush()) == [F1]


# --- issue #18: command/response bits round-trip ---


@pytest.mark.parametrize(
    "cr", [(False, False), (True, False), (False, True), (True, True)]
)
def test_Frame_command_response_round_trip(cr):
    f = attr.evolve(F1, command_response=cr)
    raw = bytes(f)
    assert bool(raw[6] & 0x80) == cr[0]
    assert bool(raw[13] & 0x80) == cr[1]
    assert Frame.from_bytes(raw) == f
    assert Frame.from_bytes(bytes(F1)).command_response == (False, False)


# --- issue #19: from_str accepts text outside Latin-1 ---


def test_Frame_from_str_non_latin1():
    f = Frame.from_str("N0CALL>APRS:>price 5€")
    assert f.info == ">price 5€".encode()
    assert Frame.from_bytes(bytes(f)) == f


def test_Frame_from_str_latin1_preferred():
    f = Frame.from_str("N0CALL>APRS:>caf\xe9")
    assert f.info == b">caf\xe9"


# --- issue #20: at most 8 digipeaters in binary AX.25 ---


def _path(n):
    return ",".join(f"DIGI{i}" for i in range(n))


def test_Frame_eight_digipeaters_ok():
    f = Frame.from_str(f"N0CALL>APRS,{_path(8)}:>x")
    assert Frame.from_bytes(bytes(f)) == f


def test_Frame_too_many_digipeaters():
    f = Frame.from_str(f"N0CALL>APRS,{_path(9)}:>x")  # TNC2 text has no limit
    assert len(f.path) == 9
    with pytest.raises(ValueError, match="at most 8"):
        bytes(f)

    raw = bytearray(bytes(Frame.from_str(f"N0CALL>APRS,{_path(8)}:>x")))
    last = raw[63:70]
    raw[69] &= 0xFE  # last address no longer ends the address field
    raw[70:70] = last
    with pytest.raises(ValueError, match="at most 8"):
        Frame.from_bytes(bytes(raw))


# --- ax253#31: evolve and display text ---


def test_frame_evolve_path():
    frame = Frame.from_str("N0CALL>APRS,WIDE1-1*:>hi")
    gated = frame.evolve(path=[*frame.path, "qAR", "N0CALL-10"])
    assert str(gated) == "N0CALL>APRS,WIDE1-1*,qAR,N0CALL-10:>hi"
    assert str(frame) == "N0CALL>APRS,WIDE1-1*:>hi"  # unchanged
