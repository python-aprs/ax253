import pytest

from ax253 import Address

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"  # NOQA pylint: disable=R0801
__copyright__ = "Copyright 2022 Masen Furer and Contributors"  # NOQA pylint: disable=R0801
__license__ = "Apache License, Version 2.0"  # NOQA pylint: disable=R0801


@pytest.mark.parametrize(
    "text,exp_address",
    (
        ("N0CALL", Address(b"N0CALL", 0, False, a7_hldc=False)),
        # '*' sets the H bit only; it says nothing about the end of the path
        ("N0CALL*", Address(b"N0CALL", 0, True, a7_hldc=False)),
        ("N0CALL-1", Address(b"N0CALL", 1, False, a7_hldc=False)),
        ("N0CALL-1*", Address(b"N0CALL", 1, True, a7_hldc=False)),
    ),
)
def test_address_from_text(text, exp_address):
    a = Address.from_str(text)
    assert a == exp_address
    assert (a.digi, a.a7_hldc) == (exp_address.digi, exp_address.a7_hldc)
    assert str(a) == text
    print(repr(bytes(a)))


@pytest.mark.parametrize(
    "ax25_bytes,exp_address",
    (
        (b"\x9c`\x86\x82\x98\x98`", Address(b"N0CALL", 0, False, a7_hldc=False)),
        (b"\x9c`\x86\x82\x98\x98\xe1", Address(b"N0CALL", 0, True, a7_hldc=True)),
        (b"\x9c`\x86\x82\x98\x98b", Address(b"N0CALL", 1, False, a7_hldc=False)),
        (b"\x9c`\x86\x82\x98\x98\xe3", Address(b"N0CALL", 1, True, a7_hldc=True)),
        # H bit set on an address that is not the last one (repeated digi
        # followed by more path entries)
        (b"\x9c`\x86\x82\x98\x98\xe0", Address(b"N0CALL", 0, True, a7_hldc=False)),
        (b"\x9c`\x86\x82\x98\x98\xe2", Address(b"N0CALL", 1, True, a7_hldc=False)),
        # last address, not repeated
        (b"\x9c`\x86\x82\x98\x98a", Address(b"N0CALL", 0, False, a7_hldc=True)),
    ),
)
def test_address_from_ax25(ax25_bytes, exp_address):
    a = Address.from_bytes(ax25_bytes)
    assert a == exp_address
    assert (a.digi, a.a7_hldc) == (exp_address.digi, exp_address.a7_hldc)
    assert bytes(a) == ax25_bytes
    assert Address.from_str(str(a), a7_hldc=a.a7_hldc) == exp_address


# --- ax253#33: station equality ---


def test_equal_ignores_case_and_flags():
    assert Address.from_str("n0call-1") == Address.from_str("N0CALL-1")
    assert Address.from_str("WIDE1-1*") == Address.from_str("WIDE1-1")
    assert Address.from_str("N0CALL", a7_hldc=True) == Address.from_str("N0CALL")
    assert Address.from_str("N0CALL-1") != Address.from_str("N0CALL-2")
    assert len({Address.from_str("n0call"), Address.from_str("N0CALL*")}) == 1


def test_equal_to_text():
    assert Address.from_str("N0CALL-7") == "N0CALL-7"
    assert Address.from_str("N0CALL-7") == "n0call-7*"
    assert Address.from_str("N0CALL-7") != "N0CALL"
    assert "N0CALL-7" == Address.from_str("N0CALL-7")
    assert Address.from_str("N0CALL") != "not a call!"
    assert Address.from_str("N0CALL") != 7


def test_a7_hldc_is_keyword_only():
    with pytest.raises(TypeError):
        Address(b"N0CALL", 0, False, True)  # ty: ignore[too-many-positional-arguments]
