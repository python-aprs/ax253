"""A simulated radio channel between DataLinks, with a fake clock."""

import random

from ax253 import Frame, FrameType
from ax253.link import (
    Connected,
    DataLink,
    DataReceived,
    Disconnected,
    LinkEvent,
    SendFrame,
)


class Station:
    def __init__(self, link: DataLink) -> None:
        self.link = link
        self.data = bytearray()
        self.connected = 0
        self.disconnected: list = []
        self.sent: list[Frame] = []


class Channel:
    """
    Two links joined by a channel that delays every frame by ``delay``
    seconds, and drops (``loss``) or duplicates (``dup``) frames at random.
    ``drop`` decides about individual frames: return True to lose it.
    """

    def __init__(
        self,
        a: DataLink,
        b: DataLink,
        *,
        delay: float = 0.1,
        loss: float = 0.0,
        dup: float = 0.0,
        seed: int = 0,
        drop=None,
    ) -> None:
        self.now = 0.0
        self.a, self.b = Station(a), Station(b)
        self.delay, self.loss, self.dup = delay, loss, dup
        self.rng = random.Random(seed)
        self.drop = drop
        self.air: list[tuple[float, int, Station, Frame]] = []
        self._seq = 0
        self.log: list[str] = []

    def other(self, station: Station) -> Station:
        return self.b if station is self.a else self.a

    def station(self, link: DataLink) -> Station:
        return self.a if link is self.a.link else self.b

    def feed(self, link: DataLink, events: list[LinkEvent]) -> None:
        station = self.station(link)
        for event in events:
            if isinstance(event, SendFrame):
                frame = event.frame
                station.sent.append(frame)
                lost = self.rng.random() < self.loss or (
                    self.drop is not None and self.drop(frame)
                )
                self.log.append(
                    f"{self.now:8.2f} {'LOST ' if lost else ''}{frame.describe()}"
                )
                if lost:
                    continue
                copies = 2 if self.rng.random() < self.dup else 1
                for _ in range(copies):
                    self._seq += 1
                    self.air.append(
                        (self.now + self.delay, self._seq, self.other(station), frame)
                    )
            elif isinstance(event, DataReceived):
                station.data += event.data
            elif isinstance(event, Connected):
                station.connected += 1
            elif isinstance(event, Disconnected):
                station.disconnected.append(event.reason)

    def call(self, link: DataLink, method: str, *args) -> None:
        self.feed(link, getattr(link, method)(*args, now=self.now))

    def step(self) -> bool:
        """Advance to the next delivery or timer; False when nothing is left."""
        times = [t for t, *_ in self.air]
        times += [
            s.link.next_deadline
            for s in (self.a, self.b)
            if s.link.next_deadline is not None
        ]
        if not times:
            return False
        self.now = max(self.now, min(times))
        self.air.sort()
        while self.air and self.air[0][0] <= self.now:
            _, _, station, frame = self.air.pop(0)
            self.feed(station.link, station.link.receive(frame, now=self.now))
        for s in (self.a, self.b):
            deadline = s.link.next_deadline
            if deadline is not None and deadline <= self.now:
                self.feed(s.link, s.link.poll(now=self.now))
        return True

    def run(self, until: float = 1e9, idle=None) -> None:
        """Run until nothing is on the air and no timer runs, or ``until``."""
        while self.now < until:
            if idle is not None and idle():
                return
            if not self.step():
                return

    def run_until(self, condition, limit: float = 10_000.0) -> None:
        while not condition():
            assert self.step(), "nothing left to happen"
            assert self.now < limit, "\n".join(self.log[-40:])


def frames_of(station: Station, ftype: FrameType) -> list[Frame]:
    return [f for f in station.sent if f.control.ftype is ftype]
