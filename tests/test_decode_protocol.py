"""asyncio protocol and sync wrapper behaviour, over real local sockets."""

import asyncio
import contextlib

import pytest

from ax253 import (
    Frame,
    FrameDecodeProtocol,
    GenericDecoder,
    SyncFrameDecode,
    TNC2Decode,
    TNC2Protocol,
)

LINES = [b"N0CALL>APRS:>one", b"N0CALL>APRS:>two", b"N0CALL>APRS:>three"]


@contextlib.asynccontextmanager
async def tnc2_peer(send=b"", close_after_send=False):
    """A local TCP server that sends `send` on connect and records what it gets."""
    received = []
    done = asyncio.Event()

    async def handle(reader, writer):
        writer.write(send)
        await writer.drain()
        if close_after_send:
            writer.close()
            return
        while True:
            data = await reader.read(4096)
            if not data:
                break
            received.append(data)
        writer.close()
        done.set()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port, received, done
    finally:
        server.close()
        await server.wait_closed()


async def _connect(port, protocol_factory=TNC2Protocol):
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_connection(
        protocol_factory, "127.0.0.1", port
    )
    await protocol.connection_future
    return transport, protocol


def test_protocol_read_n_frames_and_callback():
    async def main():
        decoded, seen = [], []

        class Hooked(TNC2Protocol):
            def frame_decoded(self, frame):
                decoded.append(frame)

        async with tnc2_peer(b"\r\n".join(LINES) + b"\r\n") as (port, _, _):
            transport, protocol = await _connect(port, Hooked)
            frames = [f async for f in protocol.read(n_frames=2, callback=seen.append)]
            transport.close()
        return frames, seen, decoded

    frames, seen, decoded = asyncio.run(main())
    assert [f.info for f in frames] == [b">one", b">two"]
    assert seen == frames
    assert [f.info for f in decoded][:2] == [b">one", b">two"]


def test_protocol_eof_flushes_unterminated_line_and_ends_read():
    async def main():
        payload = LINES[0] + b"\r\n" + LINES[1]  # second line unterminated
        async with tnc2_peer(payload, close_after_send=True) as (port, _, _):
            transport, protocol = await _connect(port)
            return [f async for f in protocol.read()]

    assert [f.info for f in asyncio.run(main())] == [b">one", b">two"]


def test_protocol_write_tnc2_text():
    async def main():
        async with tnc2_peer() as (port, received, done):
            transport, protocol = await _connect(port)
            protocol.write(Frame.from_str("N0CALL>APRS,WIDE1-1:>hi"))
            transport.close()
            await asyncio.wait_for(done.wait(), 5)
            return b"".join(received)

    assert asyncio.run(main()) == b"N0CALL>APRS,WIDE1-1:>hi\r\n"


def test_generic_protocol_write_bytes():
    async def main():
        async with tnc2_peer() as (port, received, done):
            transport, protocol = await _connect(port, FrameDecodeProtocol)
            protocol.write(Frame.from_str("N0CALL>APRS:>hi"))
            transport.close()
            await asyncio.wait_for(done.wait(), 5)
            return b"".join(received)

    assert asyncio.run(main()) == bytes(Frame.from_str("N0CALL>APRS:>hi"))


def test_generic_decoder_passthrough():
    d = GenericDecoder()
    assert list(d.update(b"abc")) == [b"abc"]
    assert list(d.decode_frames(b"x")) == [b"x"]
    assert list(d.flush()) == []


class SyncTNC2(SyncFrameDecode[Frame]):
    """Minimal concrete SyncFrameDecode over TCP, like aprs.TCP."""

    def __init__(self, port):
        super().__init__()
        self.port = port

    def start(self, **kwargs):
        _, self.protocol = self.loop.run_until_complete(
            self.loop.create_connection(TNC2Protocol, "127.0.0.1", self.port)
        )
        self.loop.run_until_complete(self.protocol.connection_future)

    def stop(self):
        if self._protocol is not None:
            self._protocol.transport.close()
            self._protocol = None


@pytest.fixture
def sync_server():
    """Run a TNC2 peer in a background thread for the sync wrapper tests."""
    import socket
    import threading

    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    received = []
    conns = []

    def serve():
        conn, _ = listener.accept()
        conns.append(conn)
        conn.sendall(b"\r\n".join(LINES) + b"\r\n")
        while True:
            try:
                data = conn.recv(4096)
            except OSError:  # closed by the fixture teardown
                break
            if not data:
                break
            received.append(data)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield port, received, conns
    for c in conns:
        with contextlib.suppress(OSError):
            c.shutdown(socket.SHUT_RDWR)
        c.close()
    listener.close()
    t.join(5)


def test_sync_wrapper_read_write(sync_server):
    port, received, conns = sync_server
    seen = []
    with SyncTNC2(port) as tnc:
        frames = tnc.read(min_frames=3, callback=seen.append)
        assert [f.info for f in frames] == [b">one", b">two", b">three"]
        assert seen == frames
        assert tnc.read() == []  # nothing buffered: returns without blocking
        tnc.write(Frame.from_str("N0CALL>APRS:>back"))
    conns[0].settimeout(5)
    import time

    deadline = time.time() + 5
    while not received and time.time() < deadline:
        time.sleep(0.01)
    assert b"".join(received) == b"N0CALL>APRS:>back\r\n"


def test_sync_wrapper_not_started():
    tnc = SyncTNC2(1)
    with pytest.raises(IOError, match="did you call start"):
        _ = tnc.protocol
    with pytest.raises(IOError):
        tnc.read()


def test_sync_wrapper_protocol_setter_validates():
    tnc = SyncTNC2(1)
    with pytest.raises(ValueError):
        tnc.protocol = object()
    p = TNC2Protocol()
    tnc.protocol = p
    assert tnc.protocol is p
    tnc._protocol = None


def test_tnc2_decode_comment_and_blank_lines():
    frames = list(TNC2Decode().update(b"# comment\r\n\r\n   \r\nN0CALL>APRS:>x\r\n"))
    assert [f.info for f in frames] == [b">x"]
