"""Generic deframing decoder."""

import abc
import asyncio
import logging
from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Callable,
    Iterable,
    Iterator,
    Sequence,
)
from types import TracebackType
from typing import (
    Any,
    Generic,
    Self,
    SupportsBytes,
    TypeVar,
    cast,
)

from attrs import define, field

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2022 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"


log = logging.getLogger(__name__)

# indicates that no more frames will appear on this protocol
EOF = object()

# default bound on decoded frames waiting to be read
DEFAULT_MAX_QUEUED_FRAMES = 10000

# Used throughout this module to represent the type of the decoded frame
_T = TypeVar("_T")


@define
class GenericDecoder(Generic[_T]):
    """Generic stateful decoder."""

    @staticmethod
    def decode_frames(frame: bytes) -> Iterable[_T]:
        """
        Decode a single deframed byte chunk.

        :param frame: should represent a single higher level frame to
            decode in some way.
        """
        yield cast(_T, frame)

    def update(self, new_data: bytes) -> Iterable[_T]:
        """
        Decode the next sequence of bytes from the stream.

        :param new_data: the next bytes from the stream
        :return: an iterable of decoded frames
        """
        yield cast(_T, new_data)

    def flush(self) -> Iterable[_T]:
        """Call when the stream is closing to decode any final buffered bytes."""
        if None:
            yield
        return


@define
class FrameDecodeProtocol(asyncio.Protocol, Generic[_T]):
    """Protocol which uses a GenericDecoder to split the stream into frames."""

    transport: asyncio.Transport | None = field(default=None)
    decoder: GenericDecoder[_T] = field(factory=GenericDecoder)
    frames: asyncio.Queue = field(factory=asyncio.Queue, init=False)
    connection_future: asyncio.Future = field(
        factory=asyncio.Future,
        init=False,
    )
    max_queued_frames: int | None = field(
        default=DEFAULT_MAX_QUEUED_FRAMES,
        kw_only=True,
    )
    """
    Most decoded frames kept waiting to be read. When full, the oldest frame
    is dropped to make room (and counted in `dropped_frames`). None means
    no limit.
    """
    dropped_frames: int = field(default=0, init=False)
    """Number of frames dropped because nobody read them in time."""
    _frame_handlers: list[Callable[[_T], bool]] = field(factory=list, init=False)
    _close_handlers: list[Callable[[Exception | None], None]] = field(
        factory=list, init=False
    )

    def add_frame_handler(
        self,
        handler: Callable[[_T], bool],
        on_close: Callable[[Exception | None], None] | None = None,
    ) -> None:
        """
        Offer each decoded frame to ``handler`` before it is queued for ``read()``.

        A handler returns True when it has consumed the frame, which then
        isn't queued or offered to later handlers. The AX.25 link layer
        (``ax253.link``) uses this to take the connected-mode frames
        addressed to it while UI frames still reach ``read()``.

        ``on_close`` is called with the exception (or None) when the
        connection is lost.
        """
        self._frame_handlers.append(handler)
        if on_close is not None:
            self._close_handlers.append(on_close)

    def remove_frame_handler(
        self,
        handler: Callable[[_T], bool],
        on_close: Callable[[Exception | None], None] | None = None,
    ) -> None:
        """Stop offering frames to ``handler`` (added by ``add_frame_handler``)."""
        if handler in self._frame_handlers:
            self._frame_handlers.remove(handler)
        if on_close is not None and on_close in self._close_handlers:
            self._close_handlers.remove(on_close)

    def _queue_frame(self, frame: _T) -> None:
        self.frame_decoded(frame)
        for handler in list(self._frame_handlers):
            try:
                if handler(frame):
                    return
            except Exception:  # pylint: disable=broad-except
                log.exception("Frame handler %r failed on %r", handler, frame)
        limit = self.max_queued_frames
        if limit is not None:
            while self.frames.qsize() >= max(limit, 1):
                try:
                    self.frames.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover
                    break
                self.dropped_frames += 1
                if self.dropped_frames == 1 or self.dropped_frames % 1000 == 0:
                    log.warning(
                        "Receive queue full (%d frames): dropped %d oldest "
                        "frame(s) so far. Read frames more often or raise "
                        "max_queued_frames.",
                        limit,
                        self.dropped_frames,
                    )
        self.frames.put_nowait(frame)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """
        asyncio callback when connection is established.

        Because this protocol exposes higher-level read/write operations that
        require the transport, an awaitable `connection_future` is completed for consumers
        who depend on an active connection.
        """
        # the stream transports this protocol is used with are full Transports
        self.transport = cast(asyncio.Transport, transport)
        self.connection_future.set_result(self.transport)

    def frame_decoded(self, frame: _T) -> None:
        """Subclasses may override this function to handle new frame."""
        pass

    def data_received(self, data: bytes) -> None:
        """Pass data off to decoder instance and put frames on the queue."""
        for frame in self.decoder.update(data):
            self._queue_frame(frame)

    def connection_lost(self, exc: Exception | None) -> None:
        """asyncio callback when connection is lost."""
        for frame in self.decoder.flush():
            self._queue_frame(frame)
        self.frames.put_nowait(EOF)
        for on_close in list(self._close_handlers):
            try:
                on_close(exc)
            except Exception:  # pylint: disable=broad-except
                log.exception("Close handler %r failed", on_close)

    async def read(
        self,
        n_frames: int | None = None,
        callback: Callable[[_T], None] | None = None,
    ) -> AsyncIterable[_T]:
        """
        Iterate through decoded frames.

        If n_frames is specified, exit after yielding that number of frames.
        """
        if n_frames is None:
            n_frames = -1
        transport = await self.connection_future
        while (not transport.is_closing() or not self.frames.empty()) and n_frames:
            frame = await self.frames.get()
            if frame is EOF:
                break
            if callback is not None:
                callback(frame)
            yield frame
            n_frames -= 1

    def __aiter__(self) -> AsyncIterator[_T]:
        """``async for frame in protocol`` reads frames until the connection closes."""
        return aiter(self.read())

    def read_frames(
        self,
        n_frames: int | None = -1,
        callback: Callable[[_T], None] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> Sequence[_T]:
        """Blocking read of the given number of frames."""
        if loop is None:
            loop = asyncio.get_event_loop()

        if n_frames is not None and n_frames < 0:
            n_frames = self.frames.qsize()

        async def _():
            return [f async for f in self.read(n_frames=n_frames, callback=callback)]

        return loop.run_until_complete(_())

    def write(self, frame: SupportsBytes) -> None:
        """
        Write serialized frame to the underlying transport.

        :param frame: Frame to write.
        """
        self._connected_transport().write(bytes(frame))

    def _connected_transport(self) -> asyncio.Transport:
        if self.transport is None:
            raise ConnectionError("not connected: the transport isn't open yet")
        return self.transport


@define
class SyncFrameDecode(abc.ABC, Generic[_T]):
    """
    Synchronous wrapper over FrameDecodeProtocol.

    It is recommended to use this class as a contextmanager for automatic
    start/stop functionality.
    """

    _protocol: FrameDecodeProtocol[_T] | None = field(default=None)
    """The connected protocol (access via .protocol property)."""

    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    """This instance's event loop (access via .loop property)."""

    _owns_loop: bool = field(default=False, init=False, repr=False)
    """True when the loop was created here and should be closed with us."""

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """
        Get the event loop that this instance runs its connection on.

        Each instance gets its own loop, so instances can be used from
        different threads at the same time. Inside a running loop, that
        loop is used instead. A loop created here is closed when the
        instance is garbage collected.
        """
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_running_loop()
                self._owns_loop = False
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                self._owns_loop = True
                # as before: code in this thread that calls get_event_loop()
                # (e.g. constructing a protocol outside a coroutine) finds it
                asyncio.set_event_loop(self._loop)
        return self._loop

    def _close_loop(self) -> None:
        """Close the event loop if this instance created it."""
        loop, self._loop = self._loop, None
        if self._owns_loop and loop is not None and not loop.is_running():
            loop.close()
        self._owns_loop = False

    @property
    def protocol(self) -> FrameDecodeProtocol[_T]:
        if self._protocol is None:
            raise OSError(
                "Underlying connection is not active. Hint: did you call start()?",
            )
        return self._protocol

    @property
    def connected(self) -> bool:
        """
        True while the connection is open.

        Becomes False after stop(), or once the peer closes the connection
        or the device goes away (a blocking read() then returns early with
        whatever frames were left). Frames already received can still be
        read after that. A TCP peer that vanishes without closing (cable
        pulled, host crashed) is only noticed once a write fails or TCP
        keepalive gives up.
        """
        protocol = self._protocol
        if protocol is None or protocol.transport is None:
            return False
        return not protocol.transport.is_closing()

    @protocol.setter
    def protocol(self, p) -> None:
        if not isinstance(p, FrameDecodeProtocol):
            raise ValueError(
                f"Protocol must be a subclass of {FrameDecodeProtocol!r}, got {p!r}",
            )
        self._protocol = p

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return self.stop()

    def __del__(self) -> None:
        # a destructor must never raise, even on an object whose __init__
        # or start() failed part way (or during interpreter shutdown)
        try:
            self.stop()
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            self._close_loop()
        except Exception:  # pylint: disable=broad-except
            pass

    @abc.abstractmethod
    def start(self, **kwargs: Any) -> None:
        """
        Method is responsible for establishing the connection and assigning self.protocol.

        If self.protocol isn't assigned, the SyncFrameDecode is considered closed.
        """

    @abc.abstractmethod
    def stop(self) -> None:
        """
        Method is responsible for closing the transport.
        """

    def read(
        self,
        *,
        min_frames: int | None = -1,
        callback: Callable[[_T], None] | None = None,
    ) -> Sequence[_T]:
        """
        Read frames from underlying protocol.

        :param callback: optional function is called after each frame is decoded.
            The callback will be fired for already-decoded frames immediately,
            and then for any frames that are decoded while blocked.
        :param min_frames: block until this minimum number of frames are available.
            if -1 (default), return all buffered frames without blocking
            if None, read until EOF is seen (device closed)
        :return: List of frames
        """
        return list(
            self.protocol.read_frames(
                n_frames=min_frames,
                callback=callback,
                loop=self.loop,
            )
        )

    def __iter__(self) -> Iterator[_T]:
        """
        Block for each frame in turn, until the connection closes.

        ``for frame in tnc:`` is ``read(min_frames=1)`` in a loop.
        """
        while True:
            frames = self.read(min_frames=1)
            if not frames:
                return
            yield from frames

    def write(self, frame: SupportsBytes) -> None:
        """
        Writes frame to KISS interface.

        :param frame: Frame to write.
        """
        self.protocol.write(frame)
