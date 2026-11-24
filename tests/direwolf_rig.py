"""
Two Dire Wolf software TNCs joined by a fake radio, for connected-mode tests.

Each Dire Wolf sends its transmit audio through an ALSA ``file`` plugin into
a relay, which forwards it to the other's ``UDP:`` audio input (padding with
silence while idle, so carrier detect drops between transmissions). Station
A offers KISS over TCP to the code under test; station B runs Dire Wolf's
own connected-mode engine, driven through its AGW port.
"""

import asyncio
import os
import pathlib
import random
import shutil
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Iterator

from ax253 import Frame, FrameDecodeProtocol, GenericDecoder

RELAY = """
import os, select, socket, sys, time
port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
fd = sys.stdin.fileno()
RATE = 96000  # bytes per second: 48 kHz, 16-bit mono
SILENCE = bytes(960)  # 10 ms
due = time.monotonic()
while True:
    # pace the audio in real time, as a radio would: carrier detect and
    # transmit timing in both Dire Wolfs then behave as on the air
    due = max(due, time.monotonic() - 0.05)
    time.sleep(max(0.0, due - time.monotonic()))
    readable, _, _ = select.select([fd], [], [], 0)
    data = os.read(fd, 960) if readable else SILENCE  # <= 1024: Dire Wolf's datagram size
    if not data:
        break
    sock.sendto(data, ("127.0.0.1", port))
    due += len(data) / RATE
"""

CONFIG = """\
ADEVICE UDP:{audio_in} to_{peer}
ARATE 48000
CHANNEL 0
MYCALL {mycall}
MODEM {baud}
KISSPORT {kiss}
AGWPORT {agw}
FRACK 2
"""

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


def direwolf_available() -> bool:
    return shutil.which("direwolf") is not None


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    """A free port that Dire Wolf accepts (it wants 1024-49151)."""
    while True:
        port = random.randint(20000, 49151)
        with socket.socket(socket.AF_INET, kind) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port


def kiss_encode(frame: Frame) -> bytes:
    escaped = (
        bytes(frame)
        .replace(bytes([FESC]), bytes([FESC, TFESC]))
        .replace(bytes([FEND]), bytes([FESC, TFEND]))
    )
    return bytes([FEND, 0x00]) + escaped + bytes([FEND])


class KISSDecoder(GenericDecoder[Frame]):
    """Just enough KISS to read Dire Wolf's data frames (kiss3 does this for real)."""

    def __init__(self) -> None:
        self.buffer = b""

    def update(self, new_data: bytes):
        self.buffer += new_data
        *frames, self.buffer = self.buffer.split(bytes([FEND]))
        for raw in frames:
            raw = raw.replace(bytes([FESC, TFEND]), bytes([FEND])).replace(
                bytes([FESC, TFESC]), bytes([FESC])
            )
            if len(raw) > 1 and raw[0] == 0x00:
                yield Frame.from_bytes(raw[1:])


class Station:
    def __init__(self, name: str, mycall: str, workdir: pathlib.Path) -> None:
        self.name, self.mycall = name, mycall
        self.audio_in = free_port(socket.SOCK_DGRAM)
        self.kiss = free_port()
        self.agw = free_port()
        self.log = workdir / f"{name}.log"
        self.process: subprocess.Popen | None = None


class DireWolfPair:
    """Station ``a`` (KISS for the test) and ``b`` (Dire Wolf's own AX.25)."""

    def __init__(
        self, workdir: pathlib.Path, baud: int = 9600, bit_error_rate: float = 0.0
    ) -> None:
        self.workdir = workdir
        self.baud = baud
        self.bit_error_rate = bit_error_rate
        self.a = Station("a", "N0AAA", workdir)
        self.b = Station("b", "N0BBB", workdir)

    def start(self) -> None:
        relay = self.workdir / "relay.py"
        relay.write_text(RELAY)
        pcms = []
        for peer in (self.a, self.b):
            pcms.append(
                f'pcm.to_{peer.name} {{ type file slave.pcm "null" format "raw" '
                f'file "|{sys.executable} {relay} {peer.audio_in}" }}'
            )
        (self.workdir / ".asoundrc").write_text("\n".join(pcms) + "\n")
        env = {**os.environ, "HOME": str(self.workdir)}
        for station, peer in ((self.a, self.b), (self.b, self.a)):
            conf = self.workdir / f"{station.name}.conf"
            conf.write_text(
                CONFIG.format(
                    audio_in=station.audio_in,
                    peer=peer.name,
                    mycall=station.mycall,
                    baud=self.baud,
                    kiss=station.kiss,
                    agw=station.agw,
                )
            )
            args = ["direwolf", "-c", str(conf), "-t", "0", "-a", "0"]
            if self.bit_error_rate:
                args += ["-e", str(self.bit_error_rate)]  # corrupt received bits
            station.process = subprocess.Popen(
                args,
                stdout=station.log.open("wb"),
                stderr=subprocess.STDOUT,
                cwd=self.workdir,
                env=env,
            )
        for station in (self.a, self.b):
            self._wait_for_port(station.kiss)
            self._wait_for_port(station.agw)

    def _wait_for_port(self, port: int, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"Dire Wolf didn't open port {port}:\n{self.logs()}")

    def stop(self) -> None:
        for station in (self.a, self.b):
            if station.process is not None:
                station.process.terminate()
                try:
                    station.process.wait(5)
                except subprocess.TimeoutExpired:
                    station.process.kill()

    def logs(self) -> str:
        return "\n".join(
            f"--- {s.name}\n{s.log.read_text(errors='replace')[-4000:]}"
            for s in (self.a, self.b)
            if s.log.exists()
        )

    def heard(self, station: Station) -> list[str]:
        """The frames this Dire Wolf decoded or sent, as it printed them."""
        lines = station.log.read_text(errors="replace").splitlines()
        return [line for line in lines if line.startswith("[0")]

    async def open_kiss(self) -> tuple[asyncio.Transport, FrameDecodeProtocol[Frame]]:
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_connection(
            lambda: FrameDecodeProtocol(decoder=KISSDecoder()), "127.0.0.1", self.a.kiss
        )
        return transport, protocol


def agw_header(
    kind: bytes, call_from: str = "", call_to: str = "", data: bytes = b""
) -> bytes:
    return (
        struct.pack(
            "<B3xcxBx10s10sI4x",
            0,
            kind,
            0xF0,
            call_from.encode(),
            call_to.encode(),
            len(data),
        )
        + data
    )


class AGWClient:
    """Dire Wolf's connected-mode engine, through its AGW (AGWPE) port."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader, self.writer = reader, writer
        self.events: asyncio.Queue[tuple[bytes, str, str, bytes]] = asyncio.Queue()
        self.task = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, port: int) -> "AGWClient":
        return cls(*await asyncio.open_connection("127.0.0.1", port))

    async def _read(self) -> None:
        try:
            while True:
                header = await self.reader.readexactly(36)
                length = struct.unpack("<I", header[28:32])[0]
                data = await self.reader.readexactly(length)
                call_from = header[8:18].split(b"\0")[0].decode()
                call_to = header[18:28].split(b"\0")[0].decode()
                await self.events.put((header[4:5], call_from, call_to, data))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    def send(
        self, kind: bytes, call_from: str = "", call_to: str = "", data: bytes = b""
    ):
        self.writer.write(agw_header(kind, call_from, call_to, data))

    async def expect(self, kind: bytes, timeout: float = 30.0) -> tuple[str, str, bytes]:
        """The next event of ``kind`` (skipping others)."""

        async def wait():
            while True:
                event = await self.events.get()
                if event[0] == kind:
                    return event[1:]

        return await asyncio.wait_for(wait(), timeout)

    async def receive_data(self, size: int, timeout: float = 60.0) -> bytes:
        """``size`` bytes of connected-mode data ('D' events)."""
        data = b""

        async def collect():
            nonlocal data
            while len(data) < size:
                _, _, payload = await self.expect(b"D", timeout)
                data += payload

        await asyncio.wait_for(collect(), timeout)
        return data

    async def close(self) -> None:
        self.task.cancel()
        self.writer.close()


def make_pair(tmp_path_factory, **kwargs) -> Iterator[DireWolfPair]:
    pair = DireWolfPair(tmp_path_factory.mktemp("direwolf"), **kwargs)
    pair.start()
    try:
        yield pair
    finally:
        pair.stop()
