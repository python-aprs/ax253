"""AX.25 address encode/decode"""

import re
from typing import Any, Self, Union

import attr.validators
from attr import validators
from attrs import define, field

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2022 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"


# APRS-IS (TNC2 text) is more permissive than AX.25: q-constructs are
# lowercase (qAR, qAC), and callsigns may be up to 9 characters with an
# alphanumeric SSID (e.g. D-STAR "KJ4ABC-B"). Accept those in text, and only
# enforce AX.25 rules when encoding to bytes.
VALID_CALLSIGN_REX = re.compile(rb"^[A-Za-z0-9]+(-[A-Za-z0-9]{1,2})?$")
AX25_CALLSIGN_REX = re.compile(rb"^[A-Z0-9]{1,6}$")
AX25_SSIDS = frozenset(str(n) for n in range(16))

# AX.25 address octets hold each callsign character shifted left one bit
_SHIFT_LEFT = bytes((b << 1) & 0xFF for b in range(256))
_SHIFT_RIGHT = bytes(b >> 1 for b in range(256))
# SSID octet: C/H bit, two reserved bits (sent as 1), SSID, end-of-address
_SSID_C_OR_H = 0x80
_SSID_RESERVED = 0x60
_SSID_HLDC = 0x01


_instance_of_bytes = validators.instance_of(bytes)


def valid_callsign(instance, attribute, value):
    _instance_of_bytes(instance, attribute, value)
    if not VALID_CALLSIGN_REX.match(value):
        raise ValueError(f"{value!r} does not match {VALID_CALLSIGN_REX!r}")


def valid_ssid(instance, attribute, value):
    if not 0 <= value <= 15:
        raise ValueError(f"ssid must be 0-15, got {value!r}")


@define(slots=True, frozen=True, eq=False)
class Address:
    """
    A source, destination, or path entry in an AX.25 frame.

    Addresses are equal when they name the same station: callsign (ignoring
    case) and SSID. The ``digi`` and ``a7_hldc`` flags describe where the
    address sits in a frame, not the station, so they don't count. An
    address also equals its text: ``frame.source == "N0CALL-7"``.
    """

    callsign: bytes = field(validator=valid_callsign)
    """
    The callsign associated with the address, case preserved.

    For APRS-IS addresses whose SSID is not a number 0-15 (e.g. "KJ4ABC-B"),
    the whole text is kept here and ``ssid`` is 0.
    """
    ssid: int = field(default=0, converter=int, validator=valid_ssid)
    """The ssid (0-15) extension of the callsign."""
    digi: bool = field(default=False, converter=bool)
    """If true, indicates that this address has digipeated the packet."""
    # The high order bit of HLDC; True indicates end of address information.
    a7_hldc: bool = field(default=False, converter=bool, repr=False, kw_only=True)
    starred: bool | None = field(default=None, repr=False, kw_only=True)
    """
    Whether the TNC2 text this address came from had a ``*``; None for an
    address not read from text. A frame's ``str()`` keeps those marks, so
    an APRS-IS line with a ``*`` on every used hop round-trips.
    """

    @classmethod
    def from_bytes(cls, ax25_address: bytes, **kwargs: Any) -> Self:
        """Create an address from ax25 bytes."""
        if len(ax25_address) != 7:
            raise ValueError(f"ax25 address must be 7 bytes, got {len(ax25_address)}")
        callsign = ax25_address[:6].translate(_SHIFT_RIGHT).rstrip()
        ssid_octet = ax25_address[6]
        ssid = (ssid_octet >> 1) & 0x0F
        c_or_h = bool(ssid_octet & _SSID_C_OR_H)
        hldc = bool(ssid_octet & _SSID_HLDC)
        init_kwargs: dict[str, Any] = dict(
            callsign=callsign,
            ssid=ssid,
            # For digipeater addresses bit 7 is the H ("has been repeated")
            # bit, set on *every* digipeater that has relayed the frame, not
            # only the last address. For destination/source it is the C
            # (command/response) bit instead; Frame.from_bytes overrides it.
            digi=c_or_h,
            a7_hldc=hldc,
        )
        if kwargs:
            init_kwargs.update(kwargs)
        return cls(**init_kwargs)

    @classmethod
    def from_str(
        cls,
        address_spec: str,
        a7_hldc: bool = False,
        **kwargs: Any,
    ) -> Self:
        """Create an address from a string (TNC2 format)."""
        digi = "*" in address_spec
        address = address_spec.strip("*")
        callsign_str, found, ssid_str = address.partition("-")
        if ssid_str in AX25_SSIDS:
            callsign, ssid = callsign_str.encode("latin1"), int(ssid_str)
        elif not found:
            callsign, ssid = callsign_str.encode("latin1"), 0
        else:
            # not representable in AX.25 (e.g. "KJ4ABC-B", "N0CALL-16",
            # "N0CALL-01"): keep the text verbatim
            callsign, ssid = address.encode("latin1"), 0
        init_kwargs: dict[str, Any] = dict(
            callsign=callsign,
            ssid=ssid,
            digi=digi,
            a7_hldc=a7_hldc,
            starred=digi,
        )
        if kwargs:
            init_kwargs.update(**kwargs)
        return cls(**init_kwargs)

    @classmethod
    def from_any(
        cls,
        address: Union["Address", bytes, str],
        **kwargs: Any,
    ) -> "Address":
        """Create an address from any supported value."""
        if isinstance(address, cls):
            if kwargs:
                address = address.evolve(**kwargs)
            return address
        elif isinstance(address, bytes):
            if len(address) == 7 and not VALID_CALLSIGN_REX.match(address):
                return cls.from_bytes(address, **kwargs)
            # anything else is callsign text, like ``Address.callsign``
            return cls.from_str(address.decode("latin1"), **kwargs)
        return cls.from_str(str(address), **kwargs)

    @property
    def call(self) -> str:
        """
        The station as text, with its SSID: ``"KF7HVM-9"``.

        Unlike ``str()``, never has the ``*`` that marks a repeated digipeater.
        """
        return str(self.evolve(digi=False))

    @property
    def base(self) -> str:
        """The callsign without its SSID: ``"KF7HVM"`` for ``KF7HVM-9``."""
        return self.call.partition("-")[0]

    def _station(self) -> tuple[bytes, int]:
        return self.callsign.upper(), self.ssid

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            try:
                other = Address.from_str(other)
            except ValueError:
                return False
        if not isinstance(other, Address):
            return NotImplemented
        return self._station() == other._station()

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self) -> int:
        return hash(self._station())

    def __str__(self) -> str:
        """Encode address as TNC2 string."""
        return self._to_str(self.digi)

    def _to_str(self, digi: bool) -> str:
        """TNC2 text, with a ``*`` if ``digi`` (instead of ``self.digi``)."""
        text = self.callsign.decode("latin1")
        if self.ssid:  # Append SSID if non-zero
            text = f"{text}-{self.ssid}"
        # If callsign was digipeated, append '*'.
        return f"{text}*" if digi else text

    def __bytes__(self) -> bytes:
        """Encode address as ax25 bytes."""
        return self._to_bytes(self.digi)

    def _to_bytes(self, c_or_h: bool) -> bytes:
        """AX.25 bytes, with the C/H bit set to ``c_or_h`` (not ``self.digi``)."""
        callsign = self.callsign.upper()
        if not AX25_CALLSIGN_REX.match(callsign):
            raise ValueError(
                f"Cannot encode {self.callsign!r} as an AX.25 address: callsign must be 1-6 "
                "letters or digits"
            )
        ssid_octet = (self.ssid << 1) | _SSID_RESERVED
        if c_or_h:
            ssid_octet |= _SSID_C_OR_H
        if self.a7_hldc:
            ssid_octet |= _SSID_HLDC
        return callsign.ljust(6).translate(_SHIFT_LEFT) + bytes((ssid_octet,))

    def evolve(self, **kwargs) -> "Address":
        """Create a new Address by applying kwargs to this Address."""
        return attr.evolve(self, **kwargs)
