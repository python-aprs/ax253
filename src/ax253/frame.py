"""AX.25 frame encode/decode"""

import enum
import logging
import re
from collections.abc import Iterable, Sequence
from typing import Any, Self, SupportsBytes

from attr import converters, validators
from attrs import define, field
from bitarray import bitarray

from . import util
from .address import Address
from .decode import GenericDecoder
from .fcs import FCS

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2022 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"

_logger = logging.getLogger(__name__)

AX25_FLAG = 0x7E
AX25_FLAG_B = bytes([AX25_FLAG])

# AX.25 Protocol ID — This field is set to 0xf0 (no layer 3 protocol).
NO_PROTOCOL_ID = b"\xf0"
# AX.25 Control Field — This field is set to 0x03 (UI-frame).
UI_CONTROL_FIELD = b"\x03"
# AX.25 2.2: at most 8 digipeater addresses in the address field
MAX_DIGIPEATERS = 8


class FrameType(enum.Enum):
    """
    Determines the type of the packet based on the control field.
    """

    U_TEST = 0xE3
    U_XID = 0xAF
    U_FRMR = 0x87
    U_SABME = 0x6F
    U_UA = 0x63
    U_DISC = 0x43
    U_SABM = 0x2F
    U_DM = 0xF
    S_SREJ = 0xD
    S_REJ = 0x9
    S_RNR = 0x5
    U_UI = 0x3
    S_RR = 0x1
    I = 0x0  # noqa: E741

    @classmethod
    def from_control_byte(cls, control: int) -> "FrameType":
        """
        Classify a (modulo-8) control byte, ignoring the P/F, N(S) and N(R) bits.

        :raises ValueError: for a U-frame control byte that isn't a defined
            AX.25 command or response.
        """
        if control & 0x01 == 0:
            return cls.I
        if control & 0x03 == 0x01:
            return cls(control & 0x0F)  # RR, RNR, REJ, SREJ
        try:
            return cls(control & ~0x10 & 0xFF)  # U frame without the P/F bit
        except ValueError:
            raise ValueError(
                f"Cannot interpret control byte {control:#04x} as a valid AX.25 frame type."
            ) from None


def bytes_from_int(b_or_i) -> bytes:
    if isinstance(b_or_i, int):
        return bytes([b_or_i])
    return bytes(b_or_i)


@define(frozen=True, slots=True)
class Control:
    """The Frame control byte, indicates FrameType and PID"""

    v: bytes = field(
        validator=util.valid_length(1, 1, validators.instance_of(bytes)),
        converter=bytes_from_int,
    )

    @property
    def bv(self) -> bitarray:
        bv = bitarray()
        bv.frombytes(self.v)
        return bv

    @property
    def ftype(self) -> FrameType:
        """The FrameType associated with this Control byte."""
        return FrameType.from_control_byte(self.v[0])

    @classmethod
    def from_any(cls, obj: Any) -> "Control":
        if isinstance(obj, cls):
            return obj
        return Control(obj)

    @property
    def n_r(self) -> int:
        return self.v[0] >> 5

    @property
    def n_s(self) -> int:
        return (self.v[0] & 0x0F) >> 1

    @property
    def p_f(self) -> bool:
        """The Poll/Final bit (0x10 in modulo-8 control fields)."""
        return bool(self.v[0] & 0x10)

    def __bytes__(self) -> bytes:
        return self.v


TNC2_ENCODING = "utf-8"
"""The default text encoding of TNC2 lines (and of APRS-IS)."""


def _with_flags(address: Address, digi: bool, a7_hldc: bool) -> Address:
    """``address`` with these H and end-of-address bits (unchanged if equal)."""
    if address.digi == digi and address.a7_hldc == a7_hldc:
        return address
    return address.evolve(digi=digi, a7_hldc=a7_hldc)


def _first_line(info: bytes) -> bytes:
    """``info`` up to its first CR or LF."""
    return re.split(rb"[\r\n]", info, maxsplit=1)[0]


def bytes_or_encode_utf8(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return str(v).encode("utf-8")


@define(frozen=True, slots=True)
class Frame:
    """
    An AX.25 Frame.
    """

    # how large the `control` field is: 1 byte for UN frames
    CONTROL_SIZE = 1

    destination: Address
    source: Address
    path: Sequence[Address] = field(converter=tuple)
    """Digipeater addresses, kept as a tuple so frames are hashable."""
    control: Control = field(
        default=Control(UI_CONTROL_FIELD), converter=Control.from_any
    )
    pid: bytes | None = field(
        default=NO_PROTOCOL_ID,
        validator=validators.optional(
            util.valid_length(1, 1, validators.instance_of(bytes)),
        ),
        converter=converters.optional(bytes_from_int),
    )
    info: bytes = field(default=b"", converter=bytes_or_encode_utf8)
    command_response: tuple[bool, bool] = field(
        default=(False, False),
        converter=lambda v: (bool(v[0]), bool(v[1])),
        kw_only=True,
    )
    """
    The AX.25 v2 command/response (C) bits of the destination and source
    addresses, kept so a decoded frame re-encodes to the same bytes.
    ``(True, False)`` is a command, ``(False, True)`` a response; both equal
    means a v1 frame. Not shown in TNC2 text.
    """

    @classmethod
    def ui(
        cls,
        *,
        destination: Address | str,
        source: Address | str,
        path: Sequence[Address | str] | None = None,
        info: bytes | SupportsBytes | str = b"",
    ) -> Self:
        """
        Create a UI frame with the given information.

        The arguments are keyword-only: TNC2 text reads ``source>destination``,
        so positional ``ui(destination, source)`` was easy to get backwards.

        Path entries follow the TNC2 monitor convention: a ``*`` marks the
        last digipeater that has repeated the frame, and every digipeater
        before it is implicitly repeated too. Their H bits are all set.
        """
        path_addresses = [Address.from_any(p) for p in path or []]
        last_repeated = max(
            (ix for ix, p in enumerate(path_addresses) if p.digi), default=-1
        )
        last_ix = len(path_addresses) - 1
        return cls(
            destination=Address.from_any(destination, digi=False, a7_hldc=False),
            source=Address.from_any(source, digi=False, a7_hldc=not path_addresses),
            path=[
                _with_flags(p, digi=ix <= last_repeated, a7_hldc=ix == last_ix)
                for ix, p in enumerate(path_addresses)
            ],
            info=info,
        )

    @classmethod
    def from_bytes(cls, ax25_bytes: bytes) -> Self:
        """
        Decode the frame from AX.25 bytes.
        """
        # bit 7 of the destination/source SSID byte is the C (command/response)
        # bit, not a has-been-repeated flag. Dire Wolf sets it on both.
        destination = Address.from_bytes(ax25_bytes[:7], digi=False)
        source = last_address = Address.from_bytes(ax25_bytes[7:14], digi=False)
        path = []
        path_start = 14
        while not last_address.a7_hldc:
            if len(path) == MAX_DIGIPEATERS:
                raise ValueError(
                    f"AX.25 allows at most {MAX_DIGIPEATERS} digipeater addresses"
                )
            last_address = Address.from_bytes(ax25_bytes[path_start : path_start + 7])
            path.append(last_address)
            path_start += 7
        info_start = control_end = path_start + cls.CONTROL_SIZE
        control = Control(ax25_bytes[path_start:control_end])
        if control.ftype in (FrameType.I, FrameType.U_UI):
            info_start += 1
            pid = ax25_bytes[control_end:info_start]
        else:
            pid = None
        return cls(
            destination=destination,
            source=source,
            path=path,
            control=control,
            pid=pid,
            info=ax25_bytes[info_start:],
            command_response=(bool(ax25_bytes[6] & 0x80), bool(ax25_bytes[13] & 0x80)),
        )

    def __bytes__(self) -> bytes:
        """Encode the frame as AX.25."""
        if len(self.path) > MAX_DIGIPEATERS:
            raise ValueError(
                f"Cannot encode {len(self.path)} digipeaters as AX.25 (at most "
                f"{MAX_DIGIPEATERS}); TNC2 text has no such limit"
            )
        dest_c, src_c = self.command_response
        encoded_frame = [
            self.destination._to_bytes(dest_c or self.destination.digi),
            self.source._to_bytes(src_c or self.source.digi),
            *(bytes(p) for p in self.path),
            bytes(self.control),
        ]
        if self.control.ftype in (FrameType.I, FrameType.U_UI) and self.pid is not None:
            encoded_frame.append(self.pid)
        encoded_frame.append(bytes(self.info))
        return b"".join(encoded_frame)

    @classmethod
    def from_str(cls, ax25_text: str, encoding: str = TNC2_ENCODING) -> Self:
        """
        Decode the frame from TNC2 monitor format.

        The info field is ``encoding`` text (UTF-8 by default). Bytes that
        aren't valid in it travel as surrogate escapes (as ``to_str``
        produces them), so any info field round-trips unchanged.
        """
        source_text, gt, rem = ax25_text.partition(">")
        address_field, colon, info_text = rem.partition(":")
        destination_text, *paths_text = address_field.split(",")
        return cls.ui(
            destination=destination_text,
            source=source_text,
            path=paths_text,
            info=info_text.encode(encoding, "surrogateescape"),
        )

    def __str__(self) -> str:
        """The frame in TNC2 monitor format, with UTF-8 text: ``to_str()``."""
        return self.to_str()

    def to_str(self, encoding: str = TNC2_ENCODING) -> str:
        """
        Serialize the frame as TNC2 monitor format.

        The info field is decoded as ``encoding`` (UTF-8 by default). Bytes
        that aren't valid in it become surrogate escapes, which
        ``from_str`` and ``.encode(encoding, "surrogateescape")`` turn back
        into the same bytes.

        Only the last digipeater with its H bit set is marked with ``*``.

        The info field ends at its first CR or LF, as APRS-IS igates cut it,
        so the text is always one line: a CR or LF heard on RF can't start a
        second, forged packet when the text is sent on to APRS-IS.
        """
        path = self.path or ()
        last_repeated = max((ix for ix, p in enumerate(path) if p.digi), default=-1)
        full_path = [
            self.destination._to_str(False),
            *(p._to_str(ix == last_repeated) for ix, p in enumerate(path)),
        ]
        return "%s>%s:%s" % (
            self.source._to_str(False),
            ",".join(full_path),
            _first_line(bytes(self.info)).decode(encoding, "surrogateescape"),
        )


# Shortest frame: destination + source (14) + control (1) + FCS (2)
MIN_FRAME_WITH_FCS = 17
# How far past a failed frame to look for a later flag that completes it
# (AX.25 2.2 maximum is ~330 bytes; leave headroom for long info fields)
MAX_FRAME_WITH_FCS = 1024


@define
class AX25BytestreamDecoder(GenericDecoder[Frame]):
    """
    Decode a byte stream of AX25_FLAG (0x7E) delimited frames, each followed
    by its 2-byte FCS.

    The stream carries no escaping, so 0x7E may also occur inside a frame
    (e.g. "~" in the info field, or in the FCS). When the bytes up to a flag
    fail the FCS check, later flags are tried as the end of the same frame
    before the bytes are dropped as corrupt.
    """

    max_buffered_bytes: int = field(default=4 * MAX_FRAME_WITH_FCS, kw_only=True)
    """
    Most bytes kept while waiting for a flag. Past this, the buffered bytes
    are dropped up to the next flag, so a stream with no flags (the wrong
    baud rate, the wrong service on the port) can't grow without limit.
    """
    _last_pframe: bytes = field(default=b"", init=False)
    _discarding: bool = field(default=False, init=False, repr=False)

    @staticmethod
    def decode_frames(frame: bytes) -> Iterable[Frame]:
        """
        Decode a single deframed byte chunk.

        :param frame: should represent a single higher level frame to
            decode in some way.
        """
        if frame:
            yield Frame.from_bytes(frame)

    @staticmethod
    def _check(segment: bytes) -> bytes | None:
        """The frame bytes of ``segment`` (frame + FCS) if its FCS matches."""
        if len(segment) < MIN_FRAME_WITH_FCS:
            return None
        payload, fcs = segment[:-2], segment[-2:]
        # check the FCS over the bytes as received
        if FCS.from_bytes(payload).digest() != fcs:
            return None
        return payload

    def _decode_payload(self, payload: bytes) -> Iterable[Frame]:
        try:
            yield from self.decode_frames(payload)
        except ValueError:
            _logger.debug("Dropping undecodable frame %r", payload, exc_info=True)

    @staticmethod
    def _skip_flags(data: bytes, ix: int) -> int:
        while ix < len(data) and data[ix] == AX25_FLAG:
            ix += 1
        return ix

    def update(self, new_data: bytes) -> Iterable[Frame]:
        """
        Decode the next sequence of bytes from the stream.

        :param new_data: the next bytes from the stream
        :return: an iterable of decoded frames
        """
        data, self._last_pframe = self._last_pframe + new_data, b""
        if self._discarding:
            # skip the rest of an overlong run of bytes, up to the next flag
            flag = data.find(AX25_FLAG)
            if flag < 0:
                return
            data, self._discarding = data[flag:], False
        start = self._skip_flags(data, 0)
        while start < len(data):
            end = data.find(AX25_FLAG, start)
            if end < 0:
                break  # frame not finished yet
            payload = self._check(data[start:end])
            if payload is not None:
                yield from self._decode_payload(payload)
                start = self._skip_flags(data, end)
                continue
            # Not a valid frame up to this flag. Either the flag byte belongs
            # to the frame, or the frame is corrupt.
            next_start = self._skip_flags(data, end)
            next_end = data.find(AX25_FLAG, next_start)
            later_end = end
            resolved = False
            while True:
                later_end = data.find(AX25_FLAG, later_end + 1)
                if later_end < 0 or later_end - start > MAX_FRAME_WITH_FCS:
                    break
                payload = self._check(data[start:later_end])
                if payload is not None:
                    yield from self._decode_payload(payload)
                    start = self._skip_flags(data, later_end)
                    resolved = True
                    break
                if later_end == next_end and self._check(data[next_start:next_end]):
                    break  # the next frame is whole: this one was corrupt
            if resolved:
                continue
            if (
                later_end < 0
                and len(data) - start <= MAX_FRAME_WITH_FCS
                and (next_end < 0 or not self._check(data[next_start:next_end]))
            ):
                break  # a later flag may still complete this frame
            _logger.debug("Dropping frame with bad FCS: %r", data[start:end])
            start = next_start
        self._last_pframe = data[start:]
        if len(self._last_pframe) > self.max_buffered_bytes:
            _logger.debug("Dropping %d bytes without a flag", len(self._last_pframe))
            self._last_pframe, self._discarding = b"", True

    def flush(self) -> Iterable[Frame]:
        """
        Call when the stream is closing to decode any final buffered bytes.

        Buffered bytes are decoded only if they end in a valid FCS.
        """
        buffered, self._last_pframe = self._last_pframe, b""
        self._discarding = False
        payload = self._check(buffered)
        if payload is not None:
            yield from self._decode_payload(payload)
        elif buffered:
            _logger.debug("Dropping unterminated frame %r", buffered)
