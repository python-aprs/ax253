"""FCS (CRC-16/X.25) checks."""

import os

import pytest

from ax253.fcs import FCS


def bitwise_fcs(data: bytes) -> bytes:
    """Reference: feed every bit, least significant first, through update_bit."""
    fcs = FCS()
    for byte in data:
        for i in range(8):
            fcs.update_bit((byte >> i) & 0x01 == 1)
    return fcs.digest()


def test_check_value():
    # CRC-16/X.25 catalogue check value for "123456789" is 0x906E
    assert FCS.from_bytes(b"123456789").digest() == b"\x6e\x90"


@pytest.mark.parametrize("size", [0, 1, 2, 7, 16, 17, 100, 330])
def test_table_matches_bitwise(size):
    data = os.urandom(size)
    assert FCS.from_bytes(data).digest() == bitwise_fcs(data)


def test_incremental_update():
    data = os.urandom(64)
    fcs = FCS()
    fcs.update(data[:20]).update(data[20:])
    assert fcs.digest() == FCS.from_bytes(data).digest() == bytes(fcs)
