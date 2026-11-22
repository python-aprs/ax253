import attr
import pytest

from ax253 import util


@attr.s
class _Holder:
    v = attr.ib()


def _attribute():
    return attr.fields(_Holder).v


def test_valid_length():
    validator = util.valid_length(1, 2, attr.validators.instance_of(bytes))
    validator(None, _attribute(), b"a")
    validator(None, _attribute(), b"ab")
    with pytest.raises(ValueError, match="at least 1"):
        validator(None, _attribute(), b"")
    with pytest.raises(ValueError, match="at most 2"):
        validator(None, _attribute(), b"abc")
    with pytest.raises(TypeError):
        validator(None, _attribute(), "ab")
    util.valid_length(0)(None, _attribute(), b"x" * 1000)


def test_instance_of_or():
    validator = util.instance_of_or(bool, attr.validators.instance_of(bytes))
    validator(None, _attribute(), True)
    validator(None, _attribute(), b"x")
    with pytest.raises(TypeError):
        validator(None, _attribute(), "x")


@pytest.mark.parametrize(
    "value, expected",
    [(None, None), (True, True), (False, False), (b"x", b"x"), (bytearray(b"y"), b"y")],
)
def test_optional_bool_or_bytes(value, expected):
    assert util.optional_bool_or_bytes(value) == expected
