"""
The AX.25 data link state machine, without I/O.

``DataLink`` follows the state diagrams of AX.25 2.2 (section 6 and the SDL
in appendix C) for modulo-8 connections, the subset that AX.25 2.0 stations
and Dire Wolf speak. It never reads a clock or touches a socket: every
method takes the current time, and returns the events it produced (frames to
send, data received, connected, disconnected). ``next_deadline`` says when
``poll()`` must be called next for the timers. ``LinkEndpoint`` runs it on
asyncio; tests drive it with a fake clock.
"""

import enum
import logging
from collections import deque
from collections.abc import Callable, Sequence

from attrs import define, field, validators

from ..address import Address
from ..frame import Control, Frame, FrameType

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2026 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"

log = logging.getLogger(__name__)

MODULUS = 8
# T1 adapts to the measured round trip, between half and this many times
# the configured T1
MAX_T1_FACTOR = 8


class LinkState(enum.Enum):
    """The states of AX.25 2.2 section 6.2 (modulo-8 only)."""

    DISCONNECTED = "disconnected"
    AWAITING_CONNECTION = "awaiting connection"
    AWAITING_RELEASE = "awaiting release"
    CONNECTED = "connected"
    TIMER_RECOVERY = "timer recovery"


class DisconnectReason(enum.Enum):
    """Why a link went down."""

    LOCAL = "closed locally"
    REMOTE = "closed by the remote station"
    REFUSED = "connection refused (DM)"
    TIMEOUT = "no response from the remote station"
    RESET = "link reset by a protocol error"


class Timer(enum.Enum):
    T1 = "T1"
    """Acknowledgement timer: a reply is overdue."""
    T2 = "T2"
    """Response delay: send an RR for received I frames."""
    T3 = "T3"
    """Idle timer: poll the remote station to check the link."""


@define(frozen=True, slots=True)
class LinkConfig:
    """Link parameters. The defaults match Dire Wolf's."""

    n1: int = field(default=128, validator=validators.ge(1))
    """Most bytes of data in one I frame (PACLEN)."""
    k: int = field(default=4, validator=[validators.ge(1), validators.le(7)])
    """Most I frames sent and not yet acknowledged (window size, MAXFRAME)."""
    n2: int = field(default=10, validator=validators.ge(1))
    """Retries before giving up on the remote station (RETRY)."""
    t1: float = field(default=3.0, validator=validators.gt(0))
    """
    Seconds to wait for an acknowledgement (FRACK), with no digipeaters.
    Each digipeater in the path adds twice this.
    """
    t2: float = field(default=0.5, validator=validators.ge(0))
    """Seconds to wait before acknowledging received I frames on their own."""
    t3: float = field(default=300.0, validator=validators.gt(0))
    """Seconds of silence before polling the remote station."""


@define(frozen=True, slots=True)
class SendFrame:
    """Transmit this frame."""

    frame: Frame


@define(frozen=True, slots=True)
class Connected:
    """The link is up."""


@define(frozen=True, slots=True)
class DataReceived:
    """In-sequence data from the remote station."""

    data: bytes


@define(frozen=True, slots=True)
class Disconnected:
    """The link is down."""

    reason: DisconnectReason


LinkEvent = SendFrame | Connected | DataReceived | Disconnected


def _station(address: Address | str) -> Address:
    """The address as AX.25 sends it: upper case, no H bit."""
    address = Address.from_any(address)
    return address.evolve(callsign=address.callsign.upper(), digi=False)


def _path(path: Sequence[Address | str]) -> tuple[Address, ...]:
    return tuple(_station(p) for p in path)


def _seq(n: int) -> int:
    return n % MODULUS


@define(slots=True)
class DataLink:
    """
    One AX.25 connection between ``local`` and ``remote``, as a state machine.

    >>> link = DataLink(local="N0CCC", remote="N0BBB")
    >>> [e.frame.describe() for e in link.connect(now=0.0)]
    ['N0CCC>N0BBB:(SABM cmd, p=1)']
    >>> link.state, link.next_deadline
    (<LinkState.AWAITING_CONNECTION: 'awaiting connection'>, 3.0)
    """

    local: Address = field(converter=_station)
    remote: Address = field(converter=_station)
    path: tuple[Address, ...] = field(default=(), converter=_path)
    """Digipeaters on the way to ``remote``."""
    config: LinkConfig = field(factory=LinkConfig)
    accept_incoming: bool = field(default=False, kw_only=True)
    """Answer a SABM from ``remote`` with UA (otherwise with DM)."""

    state: LinkState = field(default=LinkState.DISCONNECTED, init=False)
    vs: int = field(default=0, init=False)
    """V(S): the N(S) of the next I frame to send."""
    va: int = field(default=0, init=False)
    """V(A): the oldest I frame not acknowledged yet."""
    vr: int = field(default=0, init=False)
    """V(R): the N(S) expected in the next I frame received."""
    retries: int = field(default=0, init=False)
    peer_busy: bool = field(default=False, init=False)
    own_busy: bool = field(default=False, init=False)
    reject_exception: bool = field(default=False, init=False)
    ack_pending: bool = field(default=False, init=False)
    _closing: bool = field(default=False, init=False)
    _release_reason: DisconnectReason = field(default=DisconnectReason.LOCAL, init=False)
    _send_queue: deque[bytes] = field(factory=deque, init=False)
    """Data not sent yet, split into I frame sized pieces."""
    _unacked: list[bytes] = field(factory=list, init=False)
    """Data of the I frames sent since V(A), in N(S) order."""
    _timers: dict[Timer, float] = field(factory=dict, init=False)
    _srt: float = field(default=0.0, init=False)
    """Smoothed round trip time, measured from acknowledgements."""
    _t1v: float = field(default=0.0, init=False)
    """The current T1, adapted from ``_srt`` and backed off on retries."""
    _t1_started: float = field(default=0.0, init=False)
    _events: list[LinkEvent] = field(factory=list, init=False)
    _now: float = field(default=0.0, init=False)

    def __attrs_post_init__(self) -> None:
        self._srt = self.t1 / 2
        self._t1v = self.t1

    # -- public interface ---------------------------------------------------

    @property
    def t1(self) -> float:
        """The initial T1 for this link's path (T1 then adapts to the round trip)."""
        return self.config.t1 * (2 * len(self.path) + 1)

    @property
    def round_trip(self) -> float:
        """The smoothed round trip time measured so far (SRT), in seconds."""
        return self._srt

    @property
    def next_deadline(self) -> float | None:
        """When ``poll()`` must next be called, or None when no timer runs."""
        return min(self._timers.values(), default=None)

    @property
    def queued_bytes(self) -> int:
        """Data accepted by ``send()`` and not acknowledged yet."""
        return sum(map(len, self._send_queue)) + sum(map(len, self._unacked))

    @property
    def connected(self) -> bool:
        return self.state in (LinkState.CONNECTED, LinkState.TIMER_RECOVERY)

    def connect(self, now: float) -> list[LinkEvent]:
        """Start connecting to ``remote`` (send SABM)."""
        if self.state is not LinkState.DISCONNECTED:
            raise RuntimeError(f"can't connect: link is {self.state.value}")
        return self._run(now, self._establish)

    def send(self, data: bytes, now: float) -> list[LinkEvent]:
        """Queue data to send, in I frames of at most ``config.n1`` bytes."""
        if self._closing or self.state in (
            LinkState.DISCONNECTED,
            LinkState.AWAITING_RELEASE,
        ):
            raise BrokenPipeError(f"can't send: link is {self.state.value}")

        def queue() -> None:
            n1 = self.config.n1
            self._send_queue.extend(data[i : i + n1] for i in range(0, len(data), n1))

        return self._run(now, queue)

    def disconnect(self, now: float) -> list[LinkEvent]:
        """
        Close the link once all queued data is acknowledged (then send DISC).
        """

        def close() -> None:
            if self.state is LinkState.AWAITING_CONNECTION:
                self._release(DisconnectReason.LOCAL)
            elif self.connected:
                self._closing = True

        return self._run(now, close)

    def abort(self, now: float) -> list[LinkEvent]:
        """Close the link now, dropping data not yet acknowledged."""

        def abort() -> None:
            if self.state not in (LinkState.DISCONNECTED, LinkState.AWAITING_RELEASE):
                self._release(DisconnectReason.LOCAL)

        return self._run(now, abort)

    def set_busy(self, busy: bool, now: float) -> list[LinkEvent]:
        """
        Flow control: while busy, received I frames are refused (RNR) and the
        remote station waits.
        """

        def busy_changed() -> None:
            if busy == self.own_busy:
                return
            self.own_busy = busy
            if self.connected:
                self._send_s(FrameType.S_RNR if busy else FrameType.S_RR, command=False)

        return self._run(now, busy_changed)

    def receive(self, frame: Frame, now: float) -> list[LinkEvent]:
        """Handle a frame from ``remote`` addressed to ``local``."""
        return self._run(now, lambda: self._receive(frame))

    def poll(self, now: float) -> list[LinkEvent]:
        """Run the timers that have expired by ``now``."""

        def expire() -> None:
            for timer, handler in (
                (Timer.T2, self._t2_expired),
                (Timer.T1, self._t1_expired),
                (Timer.T3, self._t3_expired),
            ):
                deadline = self._timers.get(timer)
                if deadline is not None and deadline <= now:
                    del self._timers[timer]
                    handler()

        return self._run(now, expire)

    # -- plumbing -----------------------------------------------------------

    def _run(self, now: float, step: Callable[[], None]) -> list[LinkEvent]:
        self._now = now
        step()
        self._settle()
        events, self._events = self._events, []
        return events

    def _emit(self, event: LinkEvent) -> None:
        self._events.append(event)

    def _start(self, timer: Timer) -> None:
        delay = {Timer.T1: self._t1v, Timer.T2: self.config.t2, Timer.T3: self.config.t3}
        self._timers[timer] = self._now + delay[timer]
        if timer is Timer.T1:
            self._t1_started = self._now

    def _select_t1(self) -> None:
        """
        Everything outstanding was acknowledged: adapt T1 to the round trip
        (AX.25 2.2 "select T1 value"). Only a first try is measured; after a
        retry, the backoff is undone.
        """
        if self.retries == 0 and self._running(Timer.T1):
            elapsed = self._now - self._t1_started
            self._srt = self._srt * 7 / 8 + elapsed / 8
        self._t1v = min(max(2 * self._srt, self.t1 / 2), self.t1 * MAX_T1_FACTOR)

    def _back_off_t1(self) -> None:
        """T1 expired: wait longer for the next try."""
        self._t1v = min(2 * self._srt * 2**self.retries, self.t1 * MAX_T1_FACTOR)

    def _stop(self, timer: Timer) -> None:
        self._timers.pop(timer, None)

    def _running(self, timer: Timer) -> bool:
        return timer in self._timers

    def _frame(self, control: Control, command: bool, info: bytes = b"") -> Frame:
        return Frame.link(
            destination=self.remote,
            source=self.local,
            path=self.path,
            control=control,
            command=command,
            info=info,
        )

    def _send(self, control: Control, command: bool, info: bytes = b"") -> None:
        self._emit(SendFrame(self._frame(control, command, info)))

    def _send_u(self, ftype: FrameType, poll_final: bool, command: bool) -> None:
        self._send(Control.u(ftype, poll_final), command)

    def _send_s(self, ftype: FrameType, command: bool, poll_final: bool = False) -> None:
        self._send(Control.s(ftype, self.vr, poll_final), command)
        self.ack_pending = False

    def _send_frmr(self, rejected: Frame, poll_final: bool) -> None:
        """Frame reject: W (unknown command) for ``rejected``'s control byte."""
        status = (self.vr << 5) | (0x10 if rejected.is_response else 0) | (self.vs << 1)
        info = bytes([rejected.control.v[0], status, 0x01])
        self._send(Control.u(FrameType.U_FRMR, poll_final), False, info)

    def _set_state(self, state: LinkState) -> None:
        if state is not self.state:
            log.debug("%s>%s: %s -> %s", self.local, self.remote, self.state, state)
        self.state = state

    def _reset(self) -> None:
        """Clear the sequence variables and exception conditions."""
        self.vs = self.va = self.vr = 0
        self.retries = 0
        self.peer_busy = self.reject_exception = self.ack_pending = False
        self._unacked.clear()

    def _down(self, reason: DisconnectReason) -> None:
        self._timers.clear()
        self._send_queue.clear()
        self._unacked.clear()
        self._closing = False
        self._set_state(LinkState.DISCONNECTED)
        self._emit(Disconnected(reason))

    def _establish(self) -> None:
        self._reset()
        self._send_u(FrameType.U_SABM, True, command=True)
        self._stop(Timer.T3)
        self._start(Timer.T1)
        self._set_state(LinkState.AWAITING_CONNECTION)

    def _release(self, reason: DisconnectReason) -> None:
        """Send DISC and wait for the UA."""
        self._send_queue.clear()
        self._unacked.clear()
        self._closing = False
        self.retries = 0
        self._release_reason = reason
        self._send_u(FrameType.U_DISC, True, command=True)
        self._timers.clear()
        self._start(Timer.T1)
        self._set_state(LinkState.AWAITING_RELEASE)

    def _settle(self) -> None:
        """After every input: send what the window allows and set the timers."""
        if self.state is LinkState.CONNECTED:
            self._pump()
            if self._closing and not self._send_queue and not self._unacked:
                self._release(DisconnectReason.LOCAL)
                return
            if self._unacked or (self.peer_busy and self._send_queue):
                self._stop(Timer.T3)
                if not self._running(Timer.T1):
                    self._start(Timer.T1)
            else:
                self._stop(Timer.T1)
                if not self._running(Timer.T3):
                    self._start(Timer.T3)
        if self.connected and self.ack_pending:
            if not self._running(Timer.T2):
                self._start(Timer.T2)
        else:
            self._stop(Timer.T2)

    def _pump(self) -> None:
        """Send queued (or to be retransmitted) I frames while the window allows."""
        if self.peer_busy:
            return
        while (outstanding := _seq(self.vs - self.va)) < self.config.k:
            if outstanding < len(self._unacked):
                data = self._unacked[outstanding]  # retransmission
            elif self._send_queue:
                data = self._send_queue.popleft()
                self._unacked.append(data)
            else:
                return
            self._send(Control.i(self.vs, self.vr), command=True, info=data)
            self.vs = _seq(self.vs + 1)
            self.ack_pending = False

    def _nr_valid(self, nr: int) -> bool:
        """V(A) <= N(R) <= the highest N(S) sent + 1."""
        return _seq(nr - self.va) <= len(self._unacked)

    def _ack_to(self, nr: int) -> None:
        """The remote station has received every I frame before ``nr``."""
        acked = _seq(nr - self.va)
        if not acked:
            return
        if _seq(self.vs - self.va) < acked:
            self.vs = nr  # acked frames we were about to retransmit
        del self._unacked[:acked]
        self.va = nr

    def _check_iframe_acked(self, nr: int) -> None:
        if self.peer_busy:
            self._ack_to(nr)
            if not self._running(Timer.T1):
                self._start(Timer.T1)
        elif nr == self.vs:
            self._ack_to(nr)
            self._select_t1()
            self._stop(Timer.T1)
        elif nr != self.va:
            self._ack_to(nr)
            self._start(Timer.T1)  # restart: the remote is making progress

    def _transmit_enquiry(self) -> None:
        """Poll the remote station (RR/RNR command, P=1)."""
        self._send_s(
            FrameType.S_RNR if self.own_busy else FrameType.S_RR,
            command=True,
            poll_final=True,
        )
        self._stop(Timer.T3)
        self._start(Timer.T1)

    def _enquiry_response(self) -> None:
        """Answer a poll (RR/RNR response, F=1)."""
        self._send_s(
            FrameType.S_RNR if self.own_busy else FrameType.S_RR,
            command=False,
            poll_final=True,
        )

    # -- timers -------------------------------------------------------------

    def _t1_expired(self) -> None:
        if self.state is LinkState.AWAITING_CONNECTION:
            if self.retries >= self.config.n2:
                self._down(DisconnectReason.TIMEOUT)
                return
            self.retries += 1
            self._send_u(FrameType.U_SABM, True, command=True)
            self._start(Timer.T1)
        elif self.state is LinkState.AWAITING_RELEASE:
            if self.retries >= self.config.n2:
                self._down(self._release_reason)
                return
            self.retries += 1
            self._send_u(FrameType.U_DISC, True, command=True)
            self._start(Timer.T1)
        elif self.state is LinkState.CONNECTED:
            self.retries = 1
            self._back_off_t1()
            self._transmit_enquiry()
            self._set_state(LinkState.TIMER_RECOVERY)
        elif self.state is LinkState.TIMER_RECOVERY:
            if self.retries >= self.config.n2:
                self._send_u(FrameType.U_DM, False, command=False)
                self._down(DisconnectReason.TIMEOUT)
                return
            self.retries += 1
            self._back_off_t1()
            self._transmit_enquiry()

    def _t2_expired(self) -> None:
        if self.ack_pending and self.connected:
            self._send_s(
                FrameType.S_RNR if self.own_busy else FrameType.S_RR, command=False
            )

    def _t3_expired(self) -> None:
        if self.state is LinkState.CONNECTED:
            self.retries = 0
            self._transmit_enquiry()
            self._set_state(LinkState.TIMER_RECOVERY)

    # -- received frames ----------------------------------------------------

    def _receive(self, frame: Frame) -> None:
        try:
            ftype = frame.control.ftype
        except ValueError:
            log.debug("Ignoring frame with unknown control byte: %s", frame.describe())
            return
        pf = frame.control.p_f
        if ftype is FrameType.U_TEST:
            if frame.is_command:
                self._send(Control.u(FrameType.U_TEST, pf), False, frame.info)
            return
        if ftype in (FrameType.U_UI, FrameType.U_XID):
            return  # UI isn't part of the link; XID needs AX.25 2.2
        {
            LinkState.DISCONNECTED: self._rx_disconnected,
            LinkState.AWAITING_CONNECTION: self._rx_awaiting_connection,
            LinkState.AWAITING_RELEASE: self._rx_awaiting_release,
            LinkState.CONNECTED: self._rx_connected,
            LinkState.TIMER_RECOVERY: self._rx_connected,
        }[self.state](frame, ftype, pf)

    def _rx_disconnected(self, frame: Frame, ftype: FrameType, pf: bool) -> None:
        if ftype is FrameType.U_SABM:
            if not self.accept_incoming:
                self._send_u(FrameType.U_DM, pf, command=False)
                return
            self._send_u(FrameType.U_UA, pf, command=False)
            self._reset()
            self._set_state(LinkState.CONNECTED)
            self._emit(Connected())
        elif ftype is FrameType.U_SABME:
            # modulo-128 isn't supported: FRMR makes AX.25 2.2 stations
            # (Dire Wolf) retry with SABM, where DM would mean "refused"
            self._send_frmr(frame, pf)
        elif ftype is FrameType.U_DISC:
            self._send_u(FrameType.U_DM, pf, command=False)
        elif frame.is_command and pf:
            self._send_u(FrameType.U_DM, True, command=False)

    def _rx_awaiting_connection(self, frame: Frame, ftype: FrameType, pf: bool) -> None:
        if ftype is FrameType.U_UA:
            if pf:
                self._select_t1()
                self._stop(Timer.T1)
                self._reset()
                self._set_state(LinkState.CONNECTED)
                self._emit(Connected())
        elif ftype is FrameType.U_DM:
            if pf:
                self._down(DisconnectReason.REFUSED)
        elif ftype is FrameType.U_SABM:
            self._send_u(FrameType.U_UA, pf, command=False)  # both sides connecting
        elif ftype in (FrameType.U_SABME, FrameType.U_DISC):
            self._send_u(FrameType.U_DM, pf, command=False)

    def _rx_awaiting_release(self, frame: Frame, ftype: FrameType, pf: bool) -> None:
        if ftype in (FrameType.U_UA, FrameType.U_DM):
            if pf:
                self._down(self._release_reason)
        elif ftype is FrameType.U_DISC:
            self._send_u(FrameType.U_UA, pf, command=False)
        elif ftype in (FrameType.U_SABM, FrameType.U_SABME) or (frame.is_command and pf):
            self._send_u(FrameType.U_DM, pf, command=False)

    def _rx_connected(self, frame: Frame, ftype: FrameType, pf: bool) -> None:
        """CONNECTED and TIMER_RECOVERY."""
        if ftype is FrameType.U_SABM:
            # the remote station restarted the link
            log.warning(
                "%s>%s: link reset by SABM; %d bytes unacknowledged",
                self.local,
                self.remote,
                sum(map(len, self._unacked)),
            )
            self._send_u(FrameType.U_UA, pf, command=False)
            self._timers.clear()
            self._reset()
            self._set_state(LinkState.CONNECTED)
        elif ftype is FrameType.U_SABME:
            self._send_frmr(frame, pf)
        elif ftype is FrameType.U_DISC:
            self._send_u(FrameType.U_UA, pf, command=False)
            self._down(DisconnectReason.REMOTE)
        elif ftype is FrameType.U_DM:
            self._down(DisconnectReason.RESET)
        elif ftype is FrameType.U_FRMR:
            log.warning("%s>%s: frame reject %s", self.local, self.remote, frame.info)
            self._release(DisconnectReason.RESET)
        elif ftype is FrameType.I:
            self._rx_iframe(frame, pf)
        elif ftype in (
            FrameType.S_RR,
            FrameType.S_RNR,
            FrameType.S_REJ,
            FrameType.S_SREJ,
        ):
            self._rx_supervisory(frame, ftype, pf)
        # UA while connected: ignore

    def _nr_error(self, frame: Frame) -> None:
        log.warning(
            "%s>%s: N(R) %d outside V(A) %d .. V(S) %d: %s",
            self.local,
            self.remote,
            frame.control.n_r,
            self.va,
            self.vs,
            frame.describe(),
        )
        self._release(DisconnectReason.RESET)

    def _rx_supervisory(self, frame: Frame, ftype: FrameType, pf: bool) -> None:
        nr = frame.control.n_r
        self.peer_busy = ftype is FrameType.S_RNR
        if frame.is_command and pf:
            self._enquiry_response()
        if not self._nr_valid(nr):
            self._nr_error(frame)
            return
        reject = ftype in (FrameType.S_REJ, FrameType.S_SREJ)
        if self.state is LinkState.TIMER_RECOVERY:
            self._ack_to(nr)
            if frame.is_response and pf:
                # the answer to our poll: resend whatever it didn't get
                self._select_t1()
                self._stop(Timer.T1)
                self.vs = self.va
                self.retries = 0
                self._set_state(LinkState.CONNECTED)
                if self.peer_busy:
                    self._start(Timer.T1)
        elif reject:
            self._ack_to(nr)
            self._stop(Timer.T1)
            self.vs = self.va  # retransmit from N(R)
        else:
            self._check_iframe_acked(nr)

    def _rx_iframe(self, frame: Frame, pf: bool) -> None:
        if frame.is_response:
            return  # I frames are always commands
        nr, ns = frame.control.n_r, frame.control.n_s
        if not self._nr_valid(nr):
            self._nr_error(frame)
            return
        if self.state is LinkState.CONNECTED:
            self._check_iframe_acked(nr)
        else:
            self._ack_to(nr)
        if self.own_busy:
            if pf:
                self._send_s(FrameType.S_RNR, command=False, poll_final=True)
            return
        if ns == self.vr:
            self.vr = _seq(self.vr + 1)
            self.reject_exception = False
            self._emit(DataReceived(frame.info))
            if pf:
                self._send_s(FrameType.S_RR, command=False, poll_final=True)
            else:
                self.ack_pending = True
                self._start(Timer.T2)  # restart: wait for the rest of a burst
        elif self.reject_exception:
            if pf:
                self._send_s(FrameType.S_RR, command=False, poll_final=True)
        else:
            self.reject_exception = True
            self._send_s(FrameType.S_REJ, command=False, poll_final=pf)
