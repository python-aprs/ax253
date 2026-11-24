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


# --- ax253#30: text accessors, callsign bytes in from_any ---


@pytest.mark.parametrize(
    ("text", "call", "base"),
    [
        ("KF7HVM-9", "KF7HVM-9", "KF7HVM"),
        ("WIDE1-1*", "WIDE1-1", "WIDE1"),
        ("N0CALL", "N0CALL", "N0CALL"),
        ("KJ4ABC-B", "KJ4ABC-B", "KJ4ABC"),
        ("qAR", "qAR", "qAR"),
    ],
)
def test_call_and_base(text, call, base):
    address = Address.from_str(text)
    assert address.call == call
    assert address.base == base


def test_from_any_callsign_bytes():
    assert Address.from_any(b"N0CALL") == Address.from_str("N0CALL")
    assert Address.from_any(b"N0CALL-7") == Address.from_str("N0CALL-7")
    assert Address.from_any(b"N0CALL1") == Address.from_str("N0CALL1")  # 7 bytes of text


def test_from_any_ax25_bytes_still_decode():
    encoded = bytes(Address.from_str("N0CALL-7"))
    assert Address.from_any(encoded).call == "N0CALL-7"


@pytest.mark.parametrize("ssid_octet", range(256))
def test_ssid_octet_bits(ssid_octet):
    """C/H is bit 7, the SSID bits 1-4, end-of-address bit 0; bits 5-6 are reserved."""
    a = Address.from_bytes(b"\x9c`\x86\x82\x98\x98" + bytes([ssid_octet]))
    assert a.digi == bool(ssid_octet & 0x80)
    assert a.ssid == (ssid_octet >> 1) & 0x0F
    assert a.a7_hldc == bool(ssid_octet & 0x01)
    # the reserved bits are always sent as 1
    assert bytes(a)[6] == ssid_octet | 0x60
