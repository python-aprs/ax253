"""AX.25 connections as asyncio streams (ax253.link.LinkEndpoint)."""

import asyncio

import pytest

from ax253 import Control, Frame, FrameDecodeProtocol, FrameType
from ax253.link import LinkConfig, LinkEndpoint

FAST = LinkConfig(t1=0.1, t2=0.01, t3=5, n2=3, n1=32)


class Radio:
    """Two frame protocols joined by a perfect channel (AX.25 bytes and back)."""

    def __init__(self, delay: float = 0.001) -> None:
        self.a: FrameDecodeProtocol[Frame] = FrameDecodeProtocol()
        self.b: FrameDecodeProtocol[Frame] = FrameDecodeProtocol()
        self.delay = delay
        self.heard: list[str] = []
        self.mute = False

    def sender(self, to: FrameDecodeProtocol[Frame]):
        def write(frame: Frame) -> None:
            self.heard.append(frame.describe())
            if not self.mute:
                raw = bytes(frame)
                asyncio.get_running_loop().call_later(
                    self.delay, to._queue_frame, Frame.from_bytes(raw)
                )

        return write

    def endpoints(self, a_call="N0AAA", b_call="N0BBB-2", config=FAST):
        return (
            LinkEndpoint(self.a, a_call, config=config, write=self.sender(self.b)),
            LinkEndpoint(self.b, b_call, config=config, write=self.sender(self.a)),
        )


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while line := await reader.readuntil(b"\r"):
            writer.write(line.upper())
            await writer.drain()
    except asyncio.IncompleteReadError:
        pass  # the client hung up
    writer.close()


def test_connect_and_echo():
    async def main():
        radio = Radio()
        a, b = radio.endpoints()
        b.serve(echo)
        reader, writer = await a.connect("n0bbb-2")
        assert writer.get_extra_info("peername") == "N0BBB-2"
        assert writer.get_extra_info("sockname") == "N0AAA"
        assert len(a.connections) == len(b.connections) == 1
        writer.write(b"hello\r")
        assert await reader.readuntil(b"\r") == b"HELLO\r"
        writer.write(b"x" * 100 + b"\r")
        assert await reader.readuntil(b"\r") == b"X" * 100 + b"\r"
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.1)
        assert a.connections == b.connections == ()
        assert radio.heard[0] == "N0AAA>N0BBB-2:(SABM cmd, p=1)"
        assert "N0AAA>N0BBB-2:(DISC cmd, p=1)" in radio.heard

    run(main())


def test_remote_close_is_eof():
    async def main():
        radio = Radio()
        a, b = radio.endpoints()

        async def greet_and_hang_up(reader, writer):
            writer.write(b"bye")
            writer.close()

        b.serve(greet_and_hang_up)
        reader, writer = await a.connect("N0BBB-2")
        assert await reader.read() == b"bye"  # until EOF
        await writer.wait_closed()

    run(main())


def test_refused_without_serve():
    async def main():
        a, b = Radio().endpoints()
        with pytest.raises(ConnectionRefusedError):
            await a.connect("N0BBB-2")
        b.serve(echo)
        b.serve(None)  # stop accepting again
        with pytest.raises(ConnectionRefusedError):
            await a.connect("N0BBB-2")

    run(main())


def test_timeout_when_nobody_answers():
    async def main():
        radio = Radio()
        a, _ = radio.endpoints()
        radio.mute = True
        with pytest.raises(TimeoutError):
            await a.connect("N0BBB-2", via=["DIGI"])
        assert radio.heard == ["N0AAA>N0BBB-2,DIGI:(SABM cmd, p=1)"] * 4

    run(main())


def test_large_write_with_drain():
    async def main():
        radio = Radio()
        a, b = radio.endpoints()
        received = asyncio.get_running_loop().create_future()

        async def sink(reader, writer):
            received.set_result(await reader.read())

        b.serve(sink)
        _, writer = await a.connect("N0BBB-2")
        assert writer.transport.get_write_buffer_limits() == (32, 128)
        data = bytes(range(256)) * 20
        writer.write(data)
        assert writer.transport.get_write_buffer_size() > 128
        await writer.drain()  # waits until the window has room
        assert writer.transport.get_write_buffer_size() <= 32
        writer.close()
        await writer.wait_closed()
        assert await received == data
        assert max(len(line) for line in radio.heard) < 200  # 32 byte frames

    run(main())


def test_frames_not_for_the_link_still_reach_read():
    async def main():
        radio = Radio()
        a, _ = radio.endpoints()
        ui = Frame.ui(destination="N0AAA", source="N0BBB-2", info=b":hi")
        other = Frame.link(
            destination="N0ZZZ",
            source="N0BBB-2",
            control=Control.u(FrameType.U_SABM, True),
            command=True,
        )
        in_transit = Frame.link(
            destination="N0AAA",
            source="N0BBB-2",
            path=["DIGI"],
            control=Control.u(FrameType.U_SABM, True),
            command=True,
        )
        for frame in (ui, other, in_transit):
            radio.a._queue_frame(frame)
        assert [radio.a.frames.get_nowait() for _ in range(3)] == [ui, other, in_transit]
        assert radio.heard == []  # nothing answered

    run(main())


def test_digipeated_connection_replies_through_the_reverse_path():
    async def main():
        radio = Radio()
        a, b = radio.endpoints()
        b.serve(echo)
        sabm = Frame.link(
            destination="N0BBB-2",
            source="N0AAA",
            path=["DIGI1*", "DIGI2*"],
            control=Control.u(FrameType.U_SABM, True),
            command=True,
        )
        radio.b._queue_frame(sabm)
        assert radio.heard == ["N0BBB-2>N0AAA,DIGI2,DIGI1:(UA res, f=1)"]
        (transport,) = b.connections
        assert transport.get_extra_info("path") == ("DIGI2", "DIGI1")
        b.close()

    run(main())


def test_tnc_lost_resets_connections():
    async def main():
        radio = Radio()
        a, b = radio.endpoints()
        b.serve(echo)
        reader, writer = await a.connect("N0BBB-2")
        radio.a.connection_lost(None)
        with pytest.raises(ConnectionResetError):
            await reader.read()
        assert a.connections == ()

    run(main())


def test_close_endpoint_aborts_connections():
    async def main():
        radio = Radio()
        a, b = radio.endpoints()
        b.serve(echo)
        async with a:
            reader, writer = await a.connect("N0BBB-2")
        assert await reader.read() == b""
        with pytest.raises(ConnectionError):
            await a.connect("N0BBB-2")
        # the closed endpoint no longer takes frames: the UA goes to read()
        await asyncio.sleep(0.05)
        assert radio.a.frames.get_nowait().describe() == "N0BBB-2>N0AAA:(UA res, f=1)"

    run(main())


def test_one_connection_per_station():
    async def main():
        a, b = Radio().endpoints()
        b.serve(echo)
        await a.connect("N0BBB-2")
        with pytest.raises(ConnectionError, match="already connected"):
            await a.connect("n0bbb-2")

    run(main())


def test_cancelled_connect_sends_disc():
    async def main():
        radio = Radio()
        a, _ = radio.endpoints()
        radio.mute = True
        task = asyncio.create_task(a.connect("N0BBB-2"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert radio.heard[-1] == "N0AAA>N0BBB-2:(DISC cmd, p=1)"

    run(main())


def test_pause_reading_tells_the_remote_to_wait():
    async def main():
        radio = Radio()
        a, b = radio.endpoints()
        server_writer = asyncio.get_running_loop().create_future()

        async def hold(reader, writer):
            server_writer.set_result(writer)
            await reader.read()

        b.serve(hold)
        _, writer = await a.connect("N0BBB-2")
        transport = writer.transport
        transport.pause_reading()
        transport.pause_reading()
        assert not transport.is_reading()
        assert radio.heard[-1] == "N0AAA>N0BBB-2:(RNR res, n(r)=0, f=0)"
        (await server_writer).write(b"wait")
        await asyncio.sleep(0.3)
        assert transport.link.vr == 0  # refused while busy
        transport.resume_reading()
        assert transport.is_reading()
        await asyncio.sleep(0.5)
        assert transport.link.vr == 1

    run(main())


def test_write_buffer_limits_and_write_after_close():
    async def main():
        a, b = Radio().endpoints()
        b.serve(echo)
        _, writer = await a.connect("N0BBB-2")
        transport = writer.transport
        assert not transport.can_write_eof()
        transport.set_write_buffer_limits(high=1000)
        assert transport.get_write_buffer_limits() == (250, 1000)
        transport.set_write_buffer_limits(low=10)
        assert transport.get_write_buffer_limits() == (10, 40)
        with pytest.raises(ValueError):
            transport.set_write_buffer_limits(high=1, low=2)
        transport.write(b"")
        writer.close()
        transport.write(b"ignored")
        assert transport.is_closing()
        await writer.wait_closed()

    run(main())
