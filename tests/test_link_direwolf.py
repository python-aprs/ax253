"""
AX.25 connected mode against Dire Wolf's own implementation, over a fake
radio between two Dire Wolf instances (see direwolf_rig.py).

Skipped when ``direwolf`` isn't installed (``apt install direwolf``).
"""

import asyncio

import pytest
from direwolf_rig import AGWClient, direwolf_available, kiss_encode, make_pair

from ax253 import FrameType
from ax253.link import LinkConfig, LinkEndpoint

pytestmark = [
    pytest.mark.skipif(not direwolf_available(), reason="direwolf is not installed"),
    pytest.mark.timeout(180),
]

MYCALL = "N0CCC"
CONFIG = LinkConfig()  # Dire Wolf's defaults


@pytest.fixture(scope="module")
def direwolf(tmp_path_factory):
    yield from make_pair(tmp_path_factory)


def run(direwolf, test, sent=None):
    """
    Run ``test(endpoint, agw)`` with a KISS endpoint on A and AGW on B.
    The frames the endpoint sends are appended to ``sent``.
    """
    sent = [] if sent is None else sent

    def write(transport, frame):
        sent.append(frame)
        transport.write(kiss_encode(frame))

    async def main():
        transport, protocol = await direwolf.open_kiss()
        endpoint = LinkEndpoint(
            protocol,
            MYCALL,
            config=CONFIG,
            write=lambda frame: write(transport, frame),
        )
        agw = await AGWClient.open(direwolf.b.agw)
        agw.send(b"X", call_from="N0BBB")  # register: accept connections
        await agw.expect(b"X")
        try:
            await test(endpoint, agw)
        finally:
            endpoint.close()
            await agw.close()
            transport.close()

    try:
        asyncio.run(asyncio.wait_for(main(), 150))
    except BaseException:
        print(direwolf.logs())
        raise


def test_connect_to_direwolf(direwolf):
    async def test(endpoint, agw):
        reader, writer = await endpoint.connect("N0BBB")
        _, _, banner = await agw.expect(b"C")
        assert b"CONNECTED" in banner
        writer.write(b"hello from ax253\r")
        assert await agw.receive_data(17) == b"hello from ax253\r"
        agw.send(b"D", call_from="N0BBB", call_to=MYCALL, data=b"hello from Dire Wolf\r")
        assert await reader.readuntil(b"\r") == b"hello from Dire Wolf\r"
        writer.close()
        await writer.wait_closed()
        await agw.expect(b"d")

    run(direwolf, test)


def test_direwolf_connects_to_us(direwolf):
    async def test(endpoint, agw):
        lines = []

        async def echo(reader, writer):
            try:
                while line := await reader.readuntil(b"\r"):
                    lines.append(line)
                    writer.write(b"echo: " + line)
            except asyncio.IncompleteReadError:
                lines.append(b"EOF")

        endpoint.serve(echo)
        agw.send(b"C", call_from="N0BBB", call_to=MYCALL)
        await agw.expect(b"C")
        agw.send(b"D", call_from="N0BBB", call_to=MYCALL, data=b"ping\r")
        assert await agw.receive_data(11) == b"echo: ping\r"
        agw.send(b"d", call_from="N0BBB", call_to=MYCALL)
        await agw.expect(b"d")
        await asyncio.sleep(0.5)
        assert lines == [b"ping\r", b"EOF"]
        # Dire Wolf opened with SABME (AX.25 2.2); our FRMR made it use SABM
        heard = "\n".join(direwolf.heard(direwolf.b))
        assert "SABME" in heard and "FRMR" in heard and "(SABM cmd" in heard

    run(direwolf, test)


def test_bulk_transfer_both_ways(direwolf):
    send = bytes(range(256)) * 12  # 3 KB each way: many windows of I frames
    reply = bytes(reversed(range(256))) * 12

    async def test(endpoint, agw):
        reader, writer = await endpoint.connect("N0BBB")
        await agw.expect(b"C")
        writer.write(send)
        await writer.drain()
        assert await agw.receive_data(len(send)) == send
        agw.send(b"D", call_from="N0BBB", call_to=MYCALL, data=reply[:1500])
        agw.send(b"D", call_from="N0BBB", call_to=MYCALL, data=reply[1500:])
        assert await reader.readexactly(len(reply)) == reply
        writer.close()
        await writer.wait_closed()

    run(direwolf, test)


@pytest.fixture(scope="module")
def noisy_direwolf(tmp_path_factory):
    """Both Dire Wolfs flip received bits: about 1 frame in 10 is lost."""
    yield from make_pair(tmp_path_factory, bit_error_rate=1e-4)


def test_bulk_transfer_over_a_noisy_channel(noisy_direwolf):
    send = bytes(range(256)) * 6
    reply = bytes(reversed(range(256))) * 6

    async def test(endpoint, agw):
        reader, writer = await endpoint.connect("N0BBB")
        await agw.expect(b"C")
        writer.write(send)
        agw.send(b"D", call_from="N0BBB", call_to=MYCALL, data=reply)
        assert await agw.receive_data(len(send), timeout=120) == send
        assert await reader.readexactly(len(reply)) == reply
        writer.close()
        await writer.wait_closed()

    sent = []
    run(noisy_direwolf, test, sent)
    # some frames were lost, and sent again
    iframes = [f for f in sent if f.control.ftype is FrameType.I]
    assert len(iframes) > len(send) // CONFIG.n1
