import pytest

from ax253 import Address

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"  # NOQA pylint: disable=R0801
__copyright__ = "Copyright 2022 Masen Furer and Contributors"  # NOQA pylint: disable=R0801
__license__ = "Apache License, Version 2.0"  # NOQA pylint: disable=R0801


@pytest.mark.parametrize(
    "text,exp_address",
    (
        ("N0CALL", Address(b"N0CALL", 0, False, False)),
        # '*' sets the H bit only; it says nothing about the end of the path
        ("N0CALL*", Address(b"N0CALL", 0, True, False)),
        ("N0CALL-1", Address(b"N0CALL", 1, False, False)),
        ("N0CALL-1*", Address(b"N0CALL", 1, True, False)),
    ),
)
def test_address_from_text(text, exp_address):
    a = Address.from_str(text)
    assert a == exp_address
    assert str(a) == text
    print(repr(bytes(a)))


@pytest.mark.parametrize(
    "ax25_bytes,exp_address",
    (
        (b"\x9c`\x86\x82\x98\x98`", Address(b"N0CALL", 0, False, False)),
        (b"\x9c`\x86\x82\x98\x98\xe1", Address(b"N0CALL", 0, True, True)),
        (b"\x9c`\x86\x82\x98\x98b", Address(b"N0CALL", 1, False, False)),
        (b"\x9c`\x86\x82\x98\x98\xe3", Address(b"N0CALL", 1, True, True)),
        # H bit set on an address that is not the last one (repeated digi
        # followed by more path entries)
        (b"\x9c`\x86\x82\x98\x98\xe0", Address(b"N0CALL", 0, True, False)),
        (b"\x9c`\x86\x82\x98\x98\xe2", Address(b"N0CALL", 1, True, False)),
        # last address, not repeated
        (b"\x9c`\x86\x82\x98\x98a", Address(b"N0CALL", 0, False, True)),
    ),
)
def test_address_from_ax25(ax25_bytes, exp_address):
    a = Address.from_bytes(ax25_bytes)
    assert a == exp_address
    assert bytes(a) == ax25_bytes
    assert Address.from_str(str(a), a7_hldc=a.a7_hldc) == exp_address
