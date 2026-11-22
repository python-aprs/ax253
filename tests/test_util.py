import pytest

from ax253 import util


@pytest.mark.parametrize(
    "value, expected",
    [(None, None), (True, True), (False, False), (b"x", b"x"), (bytearray(b"y"), b"y")],
)
def test_optional_bool_or_bytes(value, expected):
    assert util.optional_bool_or_bytes(value) == expected
