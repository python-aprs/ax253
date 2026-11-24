"""AX.25 connections as asyncio streams, over any frame protocol (e.g. KISS)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from ..address import Address
from ..decode import FrameDecodeProtocol
from ..frame import Frame, FrameType
from .state import (
    Connected,
    DataLink,
    DataReceived,
    Disconnected,
    DisconnectReason,
    LinkConfig,
    LinkEvent,
    SendFrame,
)

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2026 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"

log = logging.getLogger(__name__)

_P = TypeVar("_P", bound=asyncio.BaseProtocol)

ClientConnected = Callable[
    [asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None] | None
]


def _error(reason: DisconnectReason) -> Exception | None:
    """The exception a disconnect reason raises, or None for a clean close."""
    if reason is DisconnectReason.REFUSED:
        return ConnectionRefusedError(reason.value)
    if reason is DisconnectReason.TIMEOUT:
        return TimeoutError(reason.value)
    if reason is DisconnectReason.RESET:
        return ConnectionResetError(reason.value)
    return None


class LinkTransport(asyncio.Transport):
    """
    The asyncio transport of one AX.25 connection.

    ``write()`` queues data for I frames; ``close()`` sends DISC once
    everything written has been acknowledged. ``get_extra_info("peername")``
    is the remote callsign.
    """

    def __init__(self, endpoint: "LinkEndpoint", link: DataLink) -> None:
        super().__init__(
            extra={
                "peername": link.remote.call,
                "sockname": link.local.call,
                "path": tuple(p.call for p in link.path),
            }
        )
        self._endpoint = endpoint
        self._link = link
        self._protocol: asyncio.BaseProtocol | None = None
        self._closing = False
        self._reading = True
        self._writing_paused = False
        # flow control: drain() waits while more than a window is queued
        self._high = link.config.k * link.config.n1
        self._low = self._high // 4

    @property
    def link(self) -> DataLink:
        """The state machine of this connection."""
        return self._link

    def set_protocol(self, protocol: asyncio.BaseProtocol) -> None:
        self._protocol = protocol

    def get_protocol(self) -> asyncio.BaseProtocol:
        assert self._protocol is not None
        return self._protocol

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        """Disconnect after all written data has been acknowledged."""
        if not self._closing:
            self._closing = True
            self._endpoint._call(self._link, self._link.disconnect)

    def abort(self) -> None:
        """Disconnect now, dropping data not yet acknowledged."""
        self._closing = True
        self._endpoint._call(self._link, self._link.abort)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        if not data:
            return
        if self._closing or not self._link.connected:
            log.debug("%s: write after close ignored", self._link.remote)
            return
        payload = bytes(data)
        self._endpoint._call(self._link, lambda now: self._link.send(payload, now))

    def can_write_eof(self) -> bool:
        return False

    def get_write_buffer_size(self) -> int:
        return self._link.queued_bytes

    def get_write_buffer_limits(self) -> tuple[int, int]:
        return self._low, self._high

    def set_write_buffer_limits(
        self, high: int | None = None, low: int | None = None
    ) -> None:
        if high is None:
            high = self._link.config.k * self._link.config.n1 if low is None else 4 * low
        if low is None:
            low = high // 4
        if not high >= low >= 0:
            raise ValueError(f"high ({high!r}) must be >= low ({low!r}) must be >= 0")
        self._high, self._low = high, low
        self._update_write_flow()

    def _update_write_flow(self) -> None:
        protocol = self._protocol
        if not isinstance(protocol, asyncio.Protocol):
            return
        size = self.get_write_buffer_size()
        if not self._writing_paused and size > self._high:
            self._writing_paused = True
            protocol.pause_writing()
        elif self._writing_paused and size <= self._low:
            self._writing_paused = False
            protocol.resume_writing()

    def is_reading(self) -> bool:
        return self._reading

    def pause_reading(self) -> None:
        """Tell the remote station we're busy (RNR) until resume_reading()."""
        if self._reading:
            self._reading = False
            self._endpoint._call(self._link, lambda now: self._link.set_busy(True, now))

    def resume_reading(self) -> None:
        if not self._reading:
            self._reading = True
            self._endpoint._call(self._link, lambda now: self._link.set_busy(False, now))


class _Connection:
    def __init__(self, link: DataLink, transport: LinkTransport) -> None:
        self.link = link
        self.transport = transport
        self.protocol: asyncio.BaseProtocol | None = None
        self.protocol_factory: Callable[[], asyncio.BaseProtocol] | None = None
        self.waiter: asyncio.Future[None] | None = None
        self.timer: asyncio.TimerHandle | None = None
        self.made = False
        """True between the protocol's connection_made and connection_lost."""


class LinkEndpoint:
    """
    AX.25 connected mode for station ``mycall`` over a frame protocol, such
    as kiss3's ``KISSProtocol``.

    ``connect()`` opens a connection to another station and returns asyncio
    streams, like ``asyncio.open_connection``; ``serve()`` accepts incoming
    connections, like ``asyncio.start_server``::

        _, kiss_protocol = await kiss.create_tcp_connection("localhost", 8001)
        endpoint = LinkEndpoint(kiss_protocol, "N0CALL-1")
        reader, writer = await endpoint.connect("N0BBB")
        writer.write(b"hello\\r")
        print(await reader.readline())
        writer.close()
        await writer.wait_closed()

    The endpoint takes the link frames addressed to ``mycall`` from the
    protocol; everything else (APRS UI frames, frames for other stations)
    still reaches ``protocol.read()``.
    """

    def __init__(
        self,
        protocol: FrameDecodeProtocol[Frame],
        mycall: Address | str,
        *,
        config: LinkConfig | None = None,
        write: Callable[[Frame], None] | None = None,
    ) -> None:
        """
        :param protocol: a connected frame protocol (decodes ``Frame``)
        :param mycall: this station's callsign and SSID
        :param config: link parameters (window, timers, packet length)
        :param write: how to send a frame, default ``protocol.write`` (pass
            e.g. a function that picks the KISS port)
        """
        self.protocol = protocol
        self.mycall = Address.from_any(mycall)
        self.config = config or LinkConfig()
        self._write = write or protocol.write
        self._connections: dict[Address, _Connection] = {}
        self._client_connected: ClientConnected | None = None
        self._stream_limit = 2**16
        self._closed = False
        protocol.add_frame_handler(self._handle_frame, on_close=self._protocol_lost)

    @property
    def connections(self) -> tuple[LinkTransport, ...]:
        """The transports of the connections that are open or opening."""
        return tuple(c.transport for c in self._connections.values())

    # -- opening connections ------------------------------------------------

    async def create_connection(
        self,
        protocol_factory: Callable[[], _P],
        remote: Address | str,
        via: Sequence[Address | str] = (),
    ) -> tuple[LinkTransport, _P]:
        """
        Connect to ``remote`` (through digipeaters ``via``), like
        ``loop.create_connection``.

        :raises ConnectionRefusedError: the station answered DM
        :raises TimeoutError: no answer after ``config.n2`` retries
        """
        if self._closed:
            raise ConnectionError("the endpoint is closed")
        remote = Address.from_any(remote)
        if remote in self._connections:
            raise ConnectionError(f"already connected to {remote.call}")
        loop = asyncio.get_running_loop()
        link = DataLink(local=self.mycall, remote=remote, path=via, config=self.config)
        conn = _Connection(link, LinkTransport(self, link))
        protocol = protocol_factory()
        conn.protocol = protocol
        conn.transport.set_protocol(protocol)
        conn.waiter = loop.create_future()
        self._connections[link.remote] = conn
        self._call(link, link.connect)
        try:
            await conn.waiter
        except asyncio.CancelledError:
            conn.transport.abort()
            raise
        return conn.transport, protocol

    async def connect(
        self,
        remote: Address | str,
        via: Sequence[Address | str] = (),
        *,
        limit: int = 2**16,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """
        Connect to ``remote`` and return its streams, like
        ``asyncio.open_connection``.
        """
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=limit, loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        transport, _ = await self.create_connection(lambda: protocol, remote, via)
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        return reader, writer

    def serve(
        self, client_connected: ClientConnected | None, *, limit: int = 2**16
    ) -> None:
        """
        Accept connections to ``mycall``, calling
        ``client_connected(reader, writer)`` for each (a coroutine function
        runs as a task), like ``asyncio.start_server``. ``None`` stops
        accepting; open connections stay up.
        """
        self._client_connected = client_connected
        self._stream_limit = limit

    def close(self) -> None:
        """Stop accepting, drop every connection, and let go of the protocol."""
        self._closed = True
        self._client_connected = None
        for conn in list(self._connections.values()):
            conn.transport.abort()  # send DISC; nobody waits for the UA
            self._disconnected(conn, None)
        self.protocol.remove_frame_handler(
            self._handle_frame, on_close=self._protocol_lost
        )

    async def __aenter__(self) -> "LinkEndpoint":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.close()

    # -- frames and events --------------------------------------------------

    def _handle_frame(self, frame: Frame) -> bool:
        """Take link frames addressed to us; leave everything else."""
        try:
            ftype = frame.control.ftype
        except ValueError:
            ftype = None
        if ftype is FrameType.U_UI or frame.destination != self.mycall:
            return False
        if frame.path and not frame.path[-1].digi:
            return False  # still on its way through the digipeaters
        if self._closed:
            return False
        log.debug("rx %s", frame.describe())
        conn = self._connections.get(frame.source)
        if conn is not None:
            self._call(conn.link, lambda now: conn.link.receive(frame, now))
            return True
        link = DataLink(
            local=self.mycall,
            remote=frame.source,
            path=frame.reply_path,
            config=self.config,
            accept_incoming=self._client_connected is not None,
        )
        conn = _Connection(link, LinkTransport(self, link))
        conn.protocol_factory = self._stream_protocol_factory()
        self._connections[link.remote] = conn
        self._call(link, lambda now: link.receive(frame, now))
        if not link.connected:
            self._forget(conn)  # answered DM/FRMR, or ignored
        return True

    def _stream_protocol_factory(self) -> Callable[[], asyncio.BaseProtocol]:
        client_connected, limit = self._client_connected, self._stream_limit

        def factory() -> asyncio.BaseProtocol:
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader(limit=limit, loop=loop)
            return asyncio.StreamReaderProtocol(reader, client_connected, loop=loop)

        return factory

    def _call(self, link: DataLink, step: Callable[[float], list[LinkEvent]]) -> None:
        """Run a state machine step now and act on its events."""
        conn = self._connections.get(link.remote)
        if conn is None or conn.link is not link:
            return  # already disconnected
        now = asyncio.get_running_loop().time()
        self._process(conn, step(now))

    def _process(self, conn: _Connection, events: list[LinkEvent]) -> None:
        for event in events:
            if isinstance(event, SendFrame):
                log.debug("tx %s", event.frame.describe())
                try:
                    self._write(event.frame)
                except ConnectionError as exc:
                    log.warning("Can't send %s: %s", event.frame.describe(), exc)
            elif isinstance(event, Connected):
                if conn.protocol is None and conn.protocol_factory is not None:
                    conn.protocol = conn.protocol_factory()
                    conn.transport.set_protocol(conn.protocol)
                if conn.protocol is not None and not conn.made:
                    conn.made = True
                    conn.protocol.connection_made(conn.transport)
                if conn.waiter is not None and not conn.waiter.done():
                    conn.waiter.set_result(None)
            elif isinstance(event, DataReceived):
                if isinstance(conn.protocol, asyncio.Protocol):
                    conn.protocol.data_received(event.data)
            elif isinstance(event, Disconnected):
                self._disconnected(conn, _error(event.reason))
                return
        if self._connections.get(conn.link.remote) is not conn:
            return  # a nested call (a write from data_received) closed it
        self._schedule(conn)
        conn.transport._update_write_flow()

    def _disconnected(self, conn: _Connection, exc: Exception | None) -> None:
        self._forget(conn)
        if conn.waiter is not None and not conn.waiter.done():
            conn.waiter.set_exception(exc or ConnectionAbortedError("connect cancelled"))
        if conn.made and conn.protocol is not None:
            conn.made = False
            conn.protocol.connection_lost(exc)

    def _forget(self, conn: _Connection) -> None:
        conn.transport._closing = True
        if conn.timer is not None:
            conn.timer.cancel()
            conn.timer = None
        if self._connections.get(conn.link.remote) is conn:
            del self._connections[conn.link.remote]

    def _schedule(self, conn: _Connection) -> None:
        deadline = conn.link.next_deadline
        if conn.timer is not None:
            if deadline is not None and conn.timer.when() == deadline:
                return
            conn.timer.cancel()
            conn.timer = None
        if deadline is not None:
            loop = asyncio.get_running_loop()
            conn.timer = loop.call_at(deadline, self._timer_fired, conn, deadline)

    def _timer_fired(self, conn: _Connection, deadline: float) -> None:
        conn.timer = None
        if self._connections.get(conn.link.remote) is not conn:
            return
        now = max(asyncio.get_running_loop().time(), deadline)
        self._process(conn, conn.link.poll(now))

    def _protocol_lost(self, exc: Exception | None) -> None:
        """The TNC went away: every connection is gone with it."""
        for conn in list(self._connections.values()):
            self._disconnected(conn, ConnectionResetError("the TNC connection was lost"))
