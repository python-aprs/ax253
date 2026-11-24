"""TNC2 / APRS-IS text: stream framing and address fidelity."""

import asyncio
import pathlib

import pytest

from ax253 import Address, Frame, TNC2Decode

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "aprsis"
APRSIS_LINES = [
    line
    for line in (FIXTURES / "representative.txt").read_text("latin1").splitlines()
    if line and not line.startswith("#")
]

SERVER_STREAM = (
    b"# aprsc 2.1.14-g5e22b37 22 Nov 2026 07:00:00 GMT T2TEST 192.0.2.1:14580\r\n"
    b"# logresp N0CALL unverified, server T2TEST\r\n"
    + b"".join(line.encode("latin1") + b"\r\n" for line in APRSIS_LINES)
    + b"# aprsc 2.1.14-g5e22b37 22 Nov 2026 07:00:20 GMT T2TEST 192.0.2.1:14580\r\n"
)


@pytest.mark.parametrize("line", APRSIS_LINES, ids=lambda s: s[:50])
def test_aprsis_text_round_trip(line):
    """APRS-IS lines must come back byte-for-byte: case, long calls, q-constructs."""
    assert str(Frame.from_str(line)) == line


@pytest.mark.parametrize("line", APRSIS_LINES, ids=lambda s: s[:50])
def test_aprsis_structure(line):
    header, _, info = line.partition(":")
    source, _, dest_path = header.partition(">")
    dest, *path = dest_path.split(",")
    frame = Frame.from_str(line)
    assert str(frame.source) == source
    assert str(frame.destination) == dest
    assert len(frame.path) == len(path)
    assert frame.info == info.encode("latin1")
    assert frame.path[-1].a7_hldc
    assert not any(p.a7_hldc for p in frame.path[:-1])


def test_q_construct_case_preserved():
    frame = Frame.from_str("N0CALL>APRS,TCPIP*,qAC,T2TEXAS:>x")
    assert frame.path[1].callsign == b"qAC"
    assert [p.digi for p in frame.path] == [True, False, False]


def test_stream_all_frames_decoded():
    frames = list(TNC2Decode().update(SERVER_STREAM))
    assert [str(f) for f in frames] == APRSIS_LINES


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 64, 500])
def test_stream_split_at_any_point(chunk_size):
    """TCP delivers arbitrary chunks; lines split across reads must survive."""
    decoder = TNC2Decode()
    frames = []
    for start in range(0, len(SERVER_STREAM), chunk_size):
        frames.extend(decoder.update(SERVER_STREAM[start : start + chunk_size]))
    frames.extend(decoder.flush())
    assert [str(f) for f in frames] == APRSIS_LINES


def test_stream_crlf_split_between_reads():
    decoder = TNC2Decode()
    assert list(decoder.update(b"N0CALL>APRS:>one\r")) == [
        Frame.from_str("N0CALL>APRS:>one")
    ]
    assert list(decoder.update(b"\nN0CALL>APRS:>two\r\n")) == [
        Frame.from_str("N0CALL>APRS:>two")
    ]


@pytest.mark.parametrize("eol", [b"\n", b"\r", b"\r\n"])
def test_stream_line_endings(eol):
    data = b"N0CALL>APRS:>one" + eol + b"N0CALL>APRS:>two" + eol
    assert [f.info for f in TNC2Decode().update(data)] == [b">one", b">two"]


def test_flush_returns_unterminated_line():
    decoder = TNC2Decode()
    assert list(decoder.update(b"N0CALL>APRS:>no terminator")) == []
    assert [f.info for f in decoder.flush()] == [b">no terminator"]
    assert list(decoder.flush()) == []


def test_undecodable_line_is_skipped():
    data = b"garbage without header\r\nN0CALL>APRS:>ok\r\nN0 CALL>APRS:>bad\r\n"
    assert [f.info for f in TNC2Decode().update(data)] == [b">ok"]


@pytest.mark.parametrize(
    "text,callsign,ssid",
    [
        ("N0CALL", b"N0CALL", 0),
        ("N0CALL-15", b"N0CALL", 15),
        ("qAR", b"qAR", 0),
        ("T2SWEDEN", b"T2SWEDEN", 0),
        ("KJ4ABC-B", b"KJ4ABC-B", 0),
        ("N0CALL-16", b"N0CALL-16", 0),
        ("N0CALL-01", b"N0CALL-01", 0),
    ],
)
def test_address_text_forms(text, callsign, ssid):
    a = Address.from_str(text)
    assert (a.callsign, a.ssid) == (callsign, ssid)
    assert str(a) == text


def test_address_ssid_zero_text_normalized():
    assert str(Address.from_str("N0CALL-0")) == "N0CALL"


@pytest.mark.parametrize("text", ["T2SWEDEN", "KJ4ABC-B", "N0CALL-16", "A-B"])
def test_address_not_encodable_as_ax25(text):
    with pytest.raises(ValueError):
        bytes(Address.from_str(text))


def test_lowercase_encodes_as_uppercase_ax25():
    assert bytes(Address.from_str("n0call-1")) == bytes(Address.from_str("N0CALL-1"))


@pytest.mark.parametrize("bad", ["", "N0 CALL", "N0CALL-", "N0CALL-ABC", "N0CALL*X"])
def test_address_invalid_text(bad):
    with pytest.raises(ValueError):
        Address.from_str(bad)


def test_address_ssid_range_enforced():
    with pytest.raises(ValueError):
        Address(callsign=b"N0CALL", ssid=16)


# --- TNC2 text is UTF-8 by default; the encoding is configurable ---


def test_decoder_reads_utf8_and_keeps_other_bytes():

    lines = ">café ☕".encode() + b"\r\n"
    lines += b"N0CALL>APRS:>caf\xe9\r\n"
    frames = list(TNC2Decode().update(b"N0CALL>APRS:" + lines))
    assert [f.info for f in frames] == [">café ☕".encode(), b">caf\xe9"]
    assert str(frames[0]) == "N0CALL>APRS:>café ☕"


def test_decoder_encoding():

    (frame,) = TNC2Decode(encoding="latin-1").update(b"N0CALL>APRS:>caf\xe9\r\n")
    assert frame.info == b">caf\xe9"
    assert frame.to_str("latin-1") == "N0CALL>APRS:>café"


class _Transport:
    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data


@pytest.mark.parametrize("encoding", ["utf-8", "latin-1"])
def test_protocol_encoding(encoding):
    asyncio.run(_protocol_encoding(encoding))


async def _protocol_encoding(encoding):
    from ax253 import TNC2Protocol

    protocol = TNC2Protocol(encoding=encoding)
    assert protocol.decoder.encoding == encoding  # ty: ignore[missing-attribute]
    protocol.transport = _Transport()  # ty: ignore[invalid-assignment]
    info = b">caf\xe9" if encoding == "latin-1" else ">café".encode()
    frame = Frame.ui(destination="APRS", source="N0CALL", info=info)
    protocol.write(frame)
    protocol.write(bytes(frame))  # any bytes-able frame
    line = b"N0CALL>APRS:" + info + b"\r\n"
    assert protocol.transport.written == line * 2  # ty: ignore[missing-attribute]


# --- issue #27: a stream with no line ends can't grow the buffer forever ---


def test_buffer_is_bounded_without_line_ends():
    decoder = TNC2Decode()
    for _ in range(100):
        assert list(decoder.update(b"A" * 1000)) == []
        assert len(decoder._buffer) <= decoder.max_line_length


def test_overlong_line_is_dropped_whole():
    decoder = TNC2Decode(max_line_length=64)
    assert list(decoder.update(b"X" * 100)) == []
    # the tail of the dropped line must not decode as a packet of its own
    frames = list(decoder.update(b"N0CALL>APRS:>tail\r\nN0CALL>APRS:>ok\r\n"))
    assert [str(f) for f in frames] == ["N0CALL>APRS:>ok"]


def test_overlong_line_dropped_across_reads():
    decoder = TNC2Decode(max_line_length=64)
    assert list(decoder.update(b"X" * 100)) == []
    assert list(decoder.update(b"Y" * 100)) == []
    assert [str(f) for f in decoder.update(b"\nN0CALL>APRS:>ok\n")] == ["N0CALL>APRS:>ok"]
    assert list(decoder.flush()) == []
