"""The public value types are frozen stdlib dataclasses that validate on init."""

import dataclasses

import pytest

from ax253 import Address, Frame
from ax253.frame import Control


def test_address_is_frozen_dataclass():
    a = Address(b"N0CALL", 7)
    assert dataclasses.is_dataclass(a)
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.ssid = 3  # type: ignore[misc]
    assert hash(a) == hash(Address(b"N0CALL", 7))


@pytest.mark.parametrize(
    "kwargs, exc",
    [
        ({"callsign": "N0CALL"}, TypeError),
        ({"callsign": b"N0 CALL"}, ValueError),
        ({"callsign": b"N0CALL", "ssid": 16}, ValueError),
        ({"callsign": b"N0CALL", "ssid": -1}, ValueError),
        ({"callsign": b"N0CALL", "ssid": "x"}, ValueError),
    ],
)
def test_address_validation(kwargs, exc):
    with pytest.raises(exc):
        Address(**kwargs)


def test_address_conversion():
    a = Address(b"N0CALL", "7", digi=1, a7_hldc=0)
    assert (a.ssid, a.digi, a.a7_hldc) == (7, True, False)


def test_evolve_revalidates():
    a = Address(b"N0CALL")
    assert a.evolve(ssid="3").ssid == 3
    with pytest.raises(ValueError):
        a.evolve(ssid=99)


@pytest.mark.parametrize("value", [b"", b"\x03\x03"])
def test_control_must_be_one_byte(value):
    with pytest.raises(ValueError, match="exactly 1 byte"):
        Control(value)


def test_frame_conversion_and_validation():
    f = Frame(
        destination=Address(b"APZ069"),
        source=Address(b"N0CALL"),
        path=[],
        control=0x03,
        pid=0xF0,
        info="hello",
    )
    assert f.control == Control(b"\x03")
    assert f.pid == b"\xf0"
    assert f.info == b"hello"
    assert dataclasses.replace(f, pid=None).pid is None
    with pytest.raises(ValueError, match="pid must be exactly 1 byte"):
        dataclasses.replace(f, pid=b"\xf0\xf0")
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.info = b"x"  # type: ignore[misc]


def test_frame_subclass_can_convert_info():
    @dataclasses.dataclass(frozen=True)
    class UpperFrame(Frame):
        @staticmethod
        def _convert_info(info):
            return bytes(info).upper()

    f = UpperFrame.from_str("N0CALL>APZ069:hello")
    assert isinstance(f, UpperFrame)
    assert f.info == b"HELLO"


def test_value_types_are_slotted():
    # these are created per frame and per address; no per-instance __dict__
    frame = Frame.from_str("N0CALL>APZ069,WIDE1-1:hello")
    for obj in (frame, frame.source, frame.path[0], frame.control):
        assert not hasattr(obj, "__dict__"), type(obj)


def test_decoders_are_slotted():
    from ax253.fcs import FCS
    from ax253.frame import AX25BytestreamDecoder
    from ax253.tnc2 import TNC2Decode

    for obj in (TNC2Decode(), AX25BytestreamDecoder(), FCS()):
        assert not hasattr(obj, "__dict__"), type(obj)
