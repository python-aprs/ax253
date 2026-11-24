"""
Interop with Dire Wolf, the most widely deployed software TNC.

tests/fixtures/direwolf/direwolf_frames.json holds the exact AX.25 bytes
Dire Wolf produced for each packet in packets.txt (see generate.py), plus
Dire Wolf's own monitor-format rendering of those bytes.
"""

import json
import pathlib
import re

import pytest

from ax253 import Address, Frame, FrameType

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "direwolf"
CORPUS = json.loads((FIXTURES / "direwolf_frames.json").read_text())
FRAMES = CORPUS["frames"]
DW_HEX_ESCAPE = re.compile(r"<0x([0-9a-f]{2})>")


def _id(f):
    return f["input"][:60]


def direwolf_monitor_to_bytes(text: str) -> bytes:
    """Dire Wolf renders unprintable info bytes as <0xNN>."""
    return DW_HEX_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text).encode("latin1")


def ax25(f) -> bytes:
    return bytes.fromhex(f["ax25"])


def address_fields(raw: bytes):
    """Split raw AX.25 bytes into 7-byte address fields using the extension bit."""
    fields = []
    for offset in range(0, len(raw), 7):
        fields.append(raw[offset : offset + 7])
        if raw[offset + 6] & 0x01:
            return fields
    raise AssertionError("no address extension bit found")


def mask_command_bits(raw: bytes) -> bytes:
    """Clear the C bits of destination and source (bit 7 of each SSID byte)."""
    masked = bytearray(raw)
    masked[6] &= 0x7F
    masked[13] &= 0x7F
    return bytes(masked)


def test_corpus_is_nonempty():
    assert len(FRAMES) >= 30
    assert CORPUS["generator"].startswith("Dire Wolf")


@pytest.mark.parametrize("f", FRAMES, ids=_id)
def test_decode_matches_direwolf_monitor(f):
    """Decoding Dire Wolf's bytes yields the text Dire Wolf itself prints."""
    frame = Frame.from_bytes(ax25(f))
    assert str(frame).encode("utf-8", "surrogateescape") == direwolf_monitor_to_bytes(
        f["direwolf_monitor"]
    )


@pytest.mark.parametrize("f", FRAMES, ids=_id)
def test_decode_structure(f):
    raw = ax25(f)
    fields = address_fields(raw)
    frame = Frame.from_bytes(raw)
    assert len(frame.path) == len(fields) - 2
    assert frame.control.ftype is FrameType.U_UI
    assert frame.pid == b"\xf0"
    assert frame.info == raw[len(fields) * 7 + 2 :]
    for address, field in zip(
        [frame.destination, frame.source, *frame.path], fields, strict=True
    ):
        assert address.callsign == bytes(b >> 1 for b in field[:6]).rstrip(b" ")
        assert address.ssid == (field[6] >> 1) & 0x0F


@pytest.mark.parametrize("f", FRAMES, ids=_id)
def test_decode_has_been_repeated_bits(f):
    """Every digipeater's H bit is decoded, not only the last address's."""
    raw = ax25(f)
    fields = address_fields(raw)
    frame = Frame.from_bytes(raw)
    h_bits = [bool(field[6] & 0x80) for field in fields[2:]]
    assert [p.digi for p in frame.path] == h_bits


@pytest.mark.parametrize("f", FRAMES, ids=_id)
def test_decode_source_command_bit_is_not_digipeated(f):
    """Dire Wolf sets the source C bit; that must not render as 'N0CALL*'."""
    frame = Frame.from_bytes(ax25(f))
    assert not frame.source.digi
    assert not frame.destination.digi


@pytest.mark.parametrize("f", FRAMES, ids=_id)
def test_encode_matches_direwolf(f):
    """
    Encoding the monitor text produces Dire Wolf's bytes.

    The destination/source C bits are masked: Dire Wolf 1.7 sets both, while
    ax253 clears both; either is accepted on the air.
    """
    frame = Frame.from_str(
        direwolf_monitor_to_bytes(f["input"]).decode("utf-8", "surrogateescape")
    )
    assert mask_command_bits(bytes(frame)) == mask_command_bits(ax25(f))


@pytest.mark.parametrize("f", FRAMES, ids=_id)
def test_text_round_trip(f):
    text = direwolf_monitor_to_bytes(f["direwolf_monitor"]).decode(
        "utf-8", "surrogateescape"
    )
    assert str(Frame.from_str(text)) == text


@pytest.mark.parametrize("f", FRAMES, ids=_id)
def test_bytes_round_trip(f):
    frame = Frame.from_bytes(ax25(f))
    assert Frame.from_bytes(bytes(frame)) == frame


def test_address_decode_reserved_bits_ignored():
    """Some TNCs clear the reserved bits; decoding must not depend on them."""
    a = Address.from_bytes(bytes([0x9C, 0x60, 0x86, 0x82, 0x98, 0x98, 0x01 | (7 << 1)]))
    assert (a.callsign, a.ssid, a.a7_hldc) == (b"N0CALL", 7, True)
