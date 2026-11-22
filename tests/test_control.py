"""AX.25 control field decoding (modulo-8), per AX.25 v2.2 section 4.3."""

import pytest

from ax253 import Control, Frame, FrameType


@pytest.mark.parametrize(
    "control, ftype, p_f",
    [
        # I frames: N(R) in bits 5-7, P in bit 4, N(S) in bits 1-3, bit 0 = 0
        (0x00, FrameType.I, False),
        (0x10, FrameType.I, True),
        (0xEE, FrameType.I, False),
        (0xFE, FrameType.I, True),
        # S frames
        (0x01, FrameType.S_RR, False),
        (0xF1, FrameType.S_RR, True),
        (0x05, FrameType.S_RNR, False),
        (0x09, FrameType.S_REJ, False),
        (0x69, FrameType.S_REJ, False),
        (0x0D, FrameType.S_SREJ, False),
        (0x1D, FrameType.S_SREJ, True),
        # U frames, with and without P/F
        (0x03, FrameType.U_UI, False),
        (0x13, FrameType.U_UI, True),
        (0x0F, FrameType.U_DM, False),
        (0x1F, FrameType.U_DM, True),
        (0x2F, FrameType.U_SABM, False),
        (0x3F, FrameType.U_SABM, True),
        (0x43, FrameType.U_DISC, False),
        (0x53, FrameType.U_DISC, True),
        (0x63, FrameType.U_UA, False),
        (0x73, FrameType.U_UA, True),
        (0x6F, FrameType.U_SABME, False),
        (0x7F, FrameType.U_SABME, True),
        (0x87, FrameType.U_FRMR, False),
        (0x97, FrameType.U_FRMR, True),
        (0xAF, FrameType.U_XID, False),
        (0xBF, FrameType.U_XID, True),
        (0xE3, FrameType.U_TEST, False),
        (0xF3, FrameType.U_TEST, True),
    ],
)
def test_control_decode(control, ftype, p_f):
    c = Control(control)
    assert c.ftype is ftype
    assert c.p_f is p_f
    assert bytes(c) == bytes([control])


@pytest.mark.parametrize("n_r,n_s", [(0, 0), (3, 5), (7, 7)])
def test_control_sequence_numbers(n_r, n_s):
    c = Control((n_r << 5) | (n_s << 1))
    assert (c.n_r, c.n_s) == (n_r, n_s)


def test_control_from_any():
    c = Control(0x03)
    assert Control.from_any(c) is c
    assert Control.from_any(b"\x03") == c
    assert Control.from_any(3) == c
    assert c.bv.tobytes() == b"\x03"


@pytest.mark.parametrize("bad", [b"", b"\x03\xf0"])
def test_control_length(bad):
    with pytest.raises(ValueError):
        Control(bad)


@pytest.mark.parametrize(
    "control, has_pid",
    [(0x03, True), (0x00, True), (0x3F, False), (0x01, False), (0x63, False)],
)
def test_frame_pid_only_for_i_and_ui(control, has_pid):
    """Only I and UI frames carry a PID byte."""
    base = Frame.from_str("N0CALL>APRS:")
    raw = bytes(base)[:14] + bytes([control]) + (b"\xf0" if has_pid else b"") + b"xy"
    frame = Frame.from_bytes(raw)
    assert frame.control == Control(control)
    assert frame.pid == (b"\xf0" if has_pid else None)
    assert frame.info == b"xy"
    assert bytes(frame) == raw
