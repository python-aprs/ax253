"""asyncio Protocol for extracting individual frames from CRLF-delimited TNC2."""

import logging
import re
from collections.abc import Iterable
from typing import SupportsBytes

from attrs import define, field

from .decode import FrameDecodeProtocol, GenericDecoder
from .frame import Frame

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2022 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"


log = logging.getLogger(__name__)

# APRS-IS lines are at most 512 bytes; leave plenty of headroom
DEFAULT_MAX_LINE_LENGTH = 8192
_LINE_END = re.compile(rb"[\r\n]")


@define
class TNC2Decode(GenericDecoder[Frame]):
    """
    Decode packets in CR-LF delimited TNC2 monitor format.

    Example: SENDER>DEST,PATH:data for the packet

    Lines may be split across any number of ``update()`` calls (TCP delivers
    a byte stream, not lines); an incomplete trailing line is buffered until
    its terminator arrives, or until ``flush()``.

    A partial line longer than ``max_line_length`` is dropped, along with
    the rest of that line, so a stream that never sends a line end (the
    wrong service on the port, a serial port at the wrong speed) can't
    grow the buffer without limit.
    """

    max_line_length: int = field(default=DEFAULT_MAX_LINE_LENGTH, kw_only=True)
    """Longest partial line kept while waiting for its line end, in bytes."""
    _buffer: bytes = field(default=b"", init=False, repr=False)
    _discarding: bool = field(default=False, init=False, repr=False)

    @staticmethod
    def decode_frames(frame: bytes) -> Iterable[Frame]:
        try:
            yield Frame.from_str(frame.decode("latin1"))
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
        self._buffer = b""
        if self._discarding:
            # skip the rest of an overlong line, up to its line end
            match = _LINE_END.search(data)
            if match is None:
                return
            data, self._discarding = data[match.start() :], False
        lines = data.splitlines(keepends=True)
        if lines and not lines[-1].endswith((b"\n", b"\r")):
            self._buffer = lines.pop()
        elif data.endswith(b"\r"):
            # CR may be the first half of a CRLF split across reads; the
            # LF will arrive as an empty line and be ignored
            pass
        if len(self._buffer) > self.max_line_length:
            log.debug("Dropping %d bytes without a line end", len(self._buffer))
            self._buffer, self._discarding = b"", True
        for line in lines:
            yield from self._decode_line(line.rstrip(b"\r\n"))

    def flush(self) -> Iterable[Frame]:
        buffered, self._buffer = self._buffer, b""
        self._discarding = False
        yield from self._decode_line(buffered)


@define
class TNC2Protocol(FrameDecodeProtocol[Frame]):
    """Protocol for decoding a stream of TNC2 format packets."""

    decoder: GenericDecoder[Frame] = field(factory=TNC2Decode)

    def write(self, frame: SupportsBytes) -> None:
        """Write the frame to the transport as a TNC2 line."""
        self._connected_transport().write(str(frame).encode("latin1") + b"\r\n")
