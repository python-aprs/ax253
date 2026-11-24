"""asyncio Protocol for extracting individual frames from CRLF-delimited TNC2."""

import logging
from collections.abc import Iterable
from typing import SupportsBytes

from attrs import Factory, define, field

from .decode import FrameDecodeProtocol, GenericDecoder
from .frame import TNC2_ENCODING, Frame

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2022 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"


log = logging.getLogger(__name__)


@define
class TNC2Decode(GenericDecoder[Frame]):
    """
    Decode packets in CR-LF delimited TNC2 monitor format.

    Example: SENDER>DEST,PATH:data for the packet

    Lines may be split across any number of ``update()`` calls (TCP delivers
    a byte stream, not lines); an incomplete trailing line is buffered until
    its terminator arrives, or until ``flush()``.

    Lines are ``encoding`` text, UTF-8 by default. Bytes that aren't valid in
    it are kept as they are in the frame's info field.
    """

    encoding: str = field(default=TNC2_ENCODING, kw_only=True)
    _buffer: bytes = field(default=b"", init=False, repr=False)

    def decode_frames(self, frame: bytes) -> Iterable[Frame]:
        try:
            yield Frame.from_str(
                frame.decode(self.encoding, "surrogateescape"), encoding=self.encoding
            )
        except Exception:
            log.debug("Ignore frame decode error %r", frame, exc_info=True)

    def _decode_line(self, packet: bytes) -> Iterable[Frame]:
        if packet.strip().startswith(b"#"):
            log.debug(packet)  # log comment
            return
        if packet:
            yield from self.decode_frames(packet)

    def update(self, new_data: bytes) -> Iterable[Frame]:
        data = self._buffer + new_data
        lines = data.splitlines(keepends=True)
        self._buffer = b""
        if lines and not lines[-1].endswith((b"\n", b"\r")):
            self._buffer = lines.pop()
        elif data.endswith(b"\r"):
            # CR may be the first half of a CRLF split across reads; the
            # LF will arrive as an empty line and be ignored
            pass
        for line in lines:
            yield from self._decode_line(line.rstrip(b"\r\n"))

    def flush(self) -> Iterable[Frame]:
        buffered, self._buffer = self._buffer, b""
        yield from self._decode_line(buffered)


@define
class TNC2Protocol(FrameDecodeProtocol[Frame]):
    """
    Protocol for decoding a stream of TNC2 format packets.

    ``encoding`` is the text encoding of the lines (UTF-8 by default); the
    default decoder uses it too.
    """

    encoding: str = field(default=TNC2_ENCODING, kw_only=True)
    decoder: GenericDecoder[Frame] = field(
        default=Factory(lambda self: TNC2Decode(encoding=self.encoding), takes_self=True)
    )

    def write(self, frame: SupportsBytes) -> None:
        """Write the frame to the transport as a TNC2 line."""
        if not isinstance(frame, Frame):
            frame = Frame.from_bytes(bytes(frame))
        line = frame.to_str(self.encoding).encode(self.encoding, "surrogateescape")
        self._connected_transport().write(line + b"\r\n")
