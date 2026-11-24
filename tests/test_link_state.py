"""The AX.25 data link state machine (ax253.link.DataLink), on a fake clock."""

import itertools

import pytest
from link_sim import Channel, frames_of

from ax253 import Control, Frame, FrameType
from ax253.link import (
    Connected,
    DataLink,
    DataReceived,
    Disconnected,
    DisconnectReason,
    LinkConfig,
    LinkState,
    SendFrame,
)

A, B = "N0AAA", "N0BBB-1"


def pair(config=None, accept=True, **channel_kwargs):
    config = config or LinkConfig()
    a = DataLink(local=A, remote=B, config=config)
    b = DataLink(local=B, remote=A, config=config, accept_incoming=accept)
    return Channel(a, b, **channel_kwargs)


def connected_pair(**kwargs):
    ch = pair(**kwargs)
    ch.call(ch.a.link, "connect")
    ch.run_until(lambda: ch.a.link.connected and ch.b.link.connected)
    return ch


def frames(events) -> list[Frame]:
    return [e.frame for e in events if isinstance(e, SendFrame)]


def sent(events):
    return [f.describe() for f in frames(events)]


def frame_from(src, dst, control, command, info=b""):
    return Frame.link(
        destination=dst, source=src, control=control, command=command, info=info
    )


def test_connect_handshake():
    a = DataLink(local=A, remote=B)
    b = DataLink(local=B, remote=A, accept_incoming=True)
    (sabm,) = frames(a.connect(now=0.0))
    assert sabm.describe() == "N0AAA>N0BBB-1:(SABM cmd, p=1)"
    events = b.receive(sabm, now=0.1)
    assert sent(events) == ["N0BBB-1>N0AAA:(UA res, f=1)"]
    assert Connected() in events
    assert b.state is LinkState.CONNECTED
    events = a.receive(frames(events)[0], now=0.2)
    assert events == [Connected()]
    assert a.state is LinkState.CONNECTED
    # idle links poll each other after T3
    assert a.next_deadline == pytest.approx(0.2 + LinkConfig().t3)


def test_connect_refused():
    ch = pair(accept=False)
    ch.call(ch.a.link, "connect")
    ch.run()
    assert [f.describe() for f in ch.b.sent] == ["N0BBB-1>N0AAA:(DM res, f=1)"]
    assert ch.a.disconnected == [DisconnectReason.REFUSED]
    assert ch.a.link.state is LinkState.DISCONNECTED


def test_connect_timeout_after_n2_retries():
    config = LinkConfig(n2=3, t1=2.0)
    a = DataLink(local=A, remote=B, config=config)
    events = a.connect(now=0.0)
    now = 0.0
    while a.next_deadline is not None:
        now = a.next_deadline
        events += a.poll(now=now)
    assert sent(events) == ["N0AAA>N0BBB-1:(SABM cmd, p=1)"] * 4
    assert events[-1] == Disconnected(DisconnectReason.TIMEOUT)
    assert now == pytest.approx(8.0)


def test_t1_scales_with_digipeaters():
    link = DataLink(
        local=A, remote=B, path=["WIDE1-1", "WIDE2-1"], config=LinkConfig(t1=3)
    )
    assert link.t1 == 15
    (sabm,) = frames(link.connect(now=0.0))
    assert sabm.describe() == "N0AAA>N0BBB-1,WIDE1-1,WIDE2-1:(SABM cmd, p=1)"


def test_sabme_gets_frmr_so_a_v22_station_falls_back():
    b = DataLink(local=B, remote=A, accept_incoming=True)
    sabme = frame_from(A, B, Control.u(FrameType.U_SABME, True), command=True)
    (frmr,) = frames(b.receive(sabme, now=0.0))
    assert frmr.describe().startswith("N0BBB-1>N0AAA:(FRMR res, f=1)")
    assert frmr.info == bytes([0x7F, 0x00, 0x01])
    assert b.state is LinkState.DISCONNECTED


@pytest.mark.parametrize(
    "control, command, expected",
    [
        (Control.u(FrameType.U_DISC, True), True, ["N0BBB-1>N0AAA:(DM res, f=1)"]),
        (Control.i(0, 0, poll=True), True, ["N0BBB-1>N0AAA:(DM res, f=1)"]),
        (Control.s(FrameType.S_RR, 0, True), True, ["N0BBB-1>N0AAA:(DM res, f=1)"]),
        (Control.i(0, 0), True, []),
        (Control.s(FrameType.S_RR, 0, True), False, []),
        (Control.u(FrameType.U_UA, True), False, []),
    ],
)
def test_frames_without_a_connection(control, command, expected):
    b = DataLink(local=B, remote=A)
    assert sent(b.receive(frame_from(A, B, control, command), now=0.0)) == expected
    assert b.state is LinkState.DISCONNECTED


def test_test_frame_is_echoed():
    b = DataLink(local=B, remote=A)
    test = frame_from(A, B, Control.u(FrameType.U_TEST, True), command=True, info=b"ping")
    (reply,) = frames(b.receive(test, now=0.0))
    assert reply.describe() == "N0BBB-1>N0AAA:(TEST res, f=1)ping"
    assert b.receive(reply, now=0.0) == []  # responses aren't echoed


def test_ui_and_xid_are_ignored():
    b = connected_pair().b.link
    for control in (Control.u(FrameType.U_UI), Control.u(FrameType.U_XID, True)):
        assert b.receive(frame_from(A, B, control, command=True), now=1.0) == []
    assert b.receive(frame_from(A, B, Control(0xFF), command=True), now=1.0) == []


def test_transfer_in_frames_of_n1_with_window_k():
    ch = connected_pair(config=LinkConfig(n1=10, k=3))
    data = bytes(range(256)) * 2
    ch.call(ch.a.link, "send", data)
    first_burst = frames_of(ch.a, FrameType.I)
    assert [f.control.n_s for f in first_burst] == [0, 1, 2]  # window full
    ch.run_until(lambda: ch.b.data == data and ch.a.link.queued_bytes == 0)
    iframes = frames_of(ch.a, FrameType.I)
    assert len(iframes) == 52  # 512 bytes in 10 byte frames, none resent
    assert max(len(f.info) for f in iframes) == 10
    assert all(f.pid == b"\xf0" and f.is_command for f in iframes)


def test_both_directions_piggyback_acks():
    ch = connected_pair(config=LinkConfig(n1=10))
    ch.call(ch.a.link, "send", b"a" * 300)
    ch.call(ch.b.link, "send", b"b" * 300)
    ch.run_until(lambda: ch.a.data == b"b" * 300 and ch.b.data == b"a" * 300)
    ch.run_until(lambda: not ch.a.link.queued_bytes and not ch.b.link.queued_bytes)
    # N(R) in the I frames acknowledged the other side's I frames
    assert any(f.control.n_r for f in frames_of(ch.b, FrameType.I))


def test_delayed_ack_covers_a_burst():
    ch = connected_pair(config=LinkConfig(n1=10, k=4, t2=0.5), delay=0.0)
    ch.call(ch.a.link, "send", b"x" * 40)  # four I frames back to back
    ch.run_until(lambda: ch.a.link.queued_bytes == 0)
    acks = frames_of(ch.b, FrameType.S_RR)
    assert [f.control.n_r for f in acks] == [4]


def test_lost_iframe_is_rejected_and_resent():
    lost = []

    def drop(frame):
        if frame.control.ftype is FrameType.I and frame.control.n_s == 1 and not lost:
            lost.append(frame)
            return True
        return False

    ch = connected_pair(config=LinkConfig(n1=10, k=4), drop=drop)
    data = bytes(range(40))
    ch.call(ch.a.link, "send", data)
    ch.run_until(lambda: ch.b.data == data and ch.a.link.queued_bytes == 0)
    (rej,) = frames_of(ch.b, FrameType.S_REJ)
    assert rej.control.n_r == 1 and rej.is_response
    assert [f.control.n_s for f in frames_of(ch.a, FrameType.I)] == [0, 1, 2, 3, 1, 2, 3]


def test_lost_ack_recovers_with_a_poll():
    dropped = []

    def drop(frame):
        if frame.control.ftype is FrameType.S_RR and not dropped:
            dropped.append(frame)
            return True
        return False

    ch = connected_pair(drop=drop)
    ch.call(ch.a.link, "send", b"hello")
    ch.run_until(lambda: ch.a.link.queued_bytes == 0)
    polls = [f for f in frames_of(ch.a, FrameType.S_RR) if f.is_command]
    assert polls and polls[0].control.p_f
    assert ch.b.data == b"hello"
    assert len(frames_of(ch.a, FrameType.I)) == 1  # the poll's answer acked it
    assert ch.a.link.state is LinkState.CONNECTED


def test_lost_last_iframe_is_resent_after_poll():
    sent_once = []

    def drop(frame):
        if frame.control.ftype is FrameType.I and not sent_once:
            sent_once.append(frame)
            return True
        return False

    ch = connected_pair(drop=drop)
    ch.call(ch.a.link, "send", b"hello")
    ch.run_until(lambda: ch.b.data == b"hello" and ch.a.link.queued_bytes == 0)
    assert len(frames_of(ch.a, FrameType.I)) == 2


def test_remote_busy_pauses_sending():
    ch = connected_pair(config=LinkConfig(n1=10, k=2))
    ch.call(ch.b.link, "set_busy", True)
    ch.run_until(lambda: ch.a.link.peer_busy)
    ch.call(ch.a.link, "send", b"x" * 50)
    assert frames_of(ch.a, FrameType.I) == []
    ch.run_until(lambda: ch.now > 30)  # polls and gets RNR back; still waits
    assert frames_of(ch.a, FrameType.I) == []
    assert ch.a.link.connected
    ch.call(ch.b.link, "set_busy", False)
    ch.run_until(lambda: ch.b.data == b"x" * 50)


def test_own_busy_refuses_iframes():
    ch = connected_pair()
    ch.b.link.own_busy = True  # without telling the remote
    ch.call(ch.a.link, "send", b"data")
    ch.run_until(lambda: ch.now > 4)
    assert ch.b.data == b""
    assert frames_of(ch.b, FrameType.S_RNR)
    ch.call(ch.b.link, "set_busy", False)
    ch.run_until(lambda: ch.b.data == b"data")


def test_idle_link_is_polled_after_t3():
    ch = connected_pair(config=LinkConfig(t3=60))
    ch.run_until(lambda: ch.now > 61)
    for station in (ch.a, ch.b):
        assert any(
            f.is_command and f.control.p_f for f in frames_of(station, FrameType.S_RR)
        )
    ch.run_until(lambda: ch.a.link.state is LinkState.CONNECTED and ch.now > 62)


def test_dead_remote_times_out():
    ch = connected_pair(config=LinkConfig(n2=2, t1=1, t3=10))
    ch.drop = lambda frame: True  # the remote went off the air
    ch.call(ch.a.link, "send", b"anyone?")
    ch.run_until(lambda: ch.a.disconnected)
    assert ch.a.disconnected == [DisconnectReason.TIMEOUT]
    assert frames_of(ch.a, FrameType.U_DM)


def test_graceful_disconnect_waits_for_acks():
    ch = connected_pair(config=LinkConfig(n1=10, k=1))
    ch.call(ch.a.link, "send", b"x" * 30)
    ch.call(ch.a.link, "disconnect")
    assert frames_of(ch.a, FrameType.U_DISC) == []
    with pytest.raises(BrokenPipeError):
        ch.a.link.send(b"more", now=ch.now)
    ch.run_until(lambda: ch.a.disconnected and ch.b.disconnected)
    assert ch.b.data == b"x" * 30
    assert ch.a.disconnected == [DisconnectReason.LOCAL]
    assert ch.b.disconnected == [DisconnectReason.REMOTE]
    assert ch.a.link.next_deadline is None and ch.b.link.next_deadline is None


def test_abort_drops_queued_data():
    ch = connected_pair(config=LinkConfig(n1=10, k=1))
    ch.call(ch.a.link, "send", b"x" * 30)
    ch.call(ch.a.link, "abort")
    ch.run_until(lambda: ch.a.disconnected)
    assert len(ch.b.data) < 30


def test_disconnect_while_connecting():
    a = DataLink(local=A, remote=B)
    a.connect(now=0.0)
    events = a.disconnect(now=1.0)
    assert sent(events) == ["N0AAA>N0BBB-1:(DISC cmd, p=1)"]
    assert a.state is LinkState.AWAITING_RELEASE
    ua = frame_from(B, A, Control.u(FrameType.U_DM, True), command=False)
    assert a.receive(ua, now=1.1) == [Disconnected(DisconnectReason.LOCAL)]


def test_release_retries_then_gives_up():
    ch = connected_pair(config=LinkConfig(n2=2, t1=1))
    ch.drop = lambda frame: True
    ch.call(ch.a.link, "disconnect")
    ch.run_until(lambda: ch.a.disconnected)
    assert len(frames_of(ch.a, FrameType.U_DISC)) == 3
    assert ch.a.disconnected == [DisconnectReason.LOCAL]


def test_frames_while_releasing():
    a = DataLink(local=A, remote=B)
    a.connect(now=0)
    a.abort(now=0)
    assert a.state is LinkState.AWAITING_RELEASE
    disc = frame_from(B, A, Control.u(FrameType.U_DISC, True), command=True)
    assert sent(a.receive(disc, now=0)) == ["N0AAA>N0BBB-1:(UA res, f=1)"]
    sabm = frame_from(B, A, Control.u(FrameType.U_SABM, True), command=True)
    assert sent(a.receive(sabm, now=0)) == ["N0AAA>N0BBB-1:(DM res, f=1)"]
    assert a.state is LinkState.AWAITING_RELEASE


def test_connect_collision():
    ch = pair()
    ch.call(ch.a.link, "connect")
    ch.b.link.accept_incoming = False
    ch.call(ch.b.link, "connect")
    ch.run_until(lambda: ch.a.link.connected and ch.b.link.connected)
    assert ch.a.connected == ch.b.connected == 1


def test_remote_dm_resets_the_link():
    ch = connected_pair()
    dm = frame_from(B, A, Control.u(FrameType.U_DM, False), command=False)
    ch.feed(ch.a.link, ch.a.link.receive(dm, now=ch.now))
    assert ch.a.disconnected == [DisconnectReason.RESET]


def test_frmr_releases_the_link():
    ch = connected_pair()
    frmr = frame_from(B, A, Control.u(FrameType.U_FRMR, False), False, b"\x00\x00\x08")
    ch.call(ch.a.link, "receive", frmr)
    ch.run_until(lambda: ch.a.disconnected)
    assert ch.a.disconnected == [DisconnectReason.RESET]


def test_nr_out_of_range_releases_the_link():
    ch = connected_pair()
    bogus = frame_from(B, A, Control.s(FrameType.S_RR, 5), command=False)
    ch.call(ch.a.link, "receive", bogus)
    assert frames_of(ch.a, FrameType.U_DISC)
    ch.run_until(lambda: ch.a.disconnected)
    assert ch.a.disconnected == [DisconnectReason.RESET]


def test_sabm_while_connected_restarts_sequence_numbers():
    ch = connected_pair()
    ch.call(ch.a.link, "send", b"one")
    ch.run_until(lambda: ch.b.data == b"one" and ch.a.link.queued_bytes == 0)
    sabm = frame_from(A, B, Control.u(FrameType.U_SABM, True), command=True)
    events = ch.b.link.receive(sabm, now=ch.now)
    assert sent(events) == ["N0BBB-1>N0AAA:(UA res, f=1)"]
    assert (ch.b.link.vs, ch.b.link.vr) == (0, 0)


def test_duplicate_iframe_is_not_delivered_twice():
    ch = connected_pair(dup=1.0)
    ch.call(ch.a.link, "send", b"once")
    ch.run_until(lambda: ch.a.link.queued_bytes == 0 and ch.now > 10)
    assert ch.b.data == b"once"


def test_api_misuse():
    a = DataLink(local=A, remote=B)
    with pytest.raises(BrokenPipeError):
        a.send(b"x", now=0)
    a.connect(now=0)
    with pytest.raises(RuntimeError):
        a.connect(now=0)
    with pytest.raises(ValueError):
        LinkConfig(k=8)


@pytest.mark.parametrize("seed", range(40))
def test_lossy_channel_delivers_everything_in_order(seed):
    """Whatever is lost or duplicated, data arrives once and in order."""
    ch = pair(
        config=LinkConfig(n1=16, k=4, n2=20, t1=1.0),
        loss=0.2,
        dup=0.1,
        seed=seed,
        delay=0.05 + seed % 5 * 0.1,
    )
    ch.call(ch.a.link, "connect")
    ch.run_until(lambda: ch.a.link.connected and ch.b.link.connected)
    a_data = bytes((seed + i) % 256 for i in range(600))
    b_data = bytes((seed * 7 + i) % 256 for i in range(250))
    ch.call(ch.a.link, "send", a_data)
    ch.call(ch.b.link, "send", b_data)
    ch.run_until(lambda: ch.b.data == a_data and ch.a.data == b_data)
    ch.run_until(lambda: not ch.a.link.queued_bytes and not ch.b.link.queued_bytes)
    ch.call(ch.a.link, "disconnect")
    ch.run_until(lambda: ch.a.disconnected and ch.b.disconnected)
    assert ch.b.data == a_data
    assert ch.a.data == b_data
    assert ch.a.disconnected == [DisconnectReason.LOCAL]
    assert ch.b.disconnected == [DisconnectReason.REMOTE]


def test_events_are_data_received():
    ch = connected_pair()
    events = ch.b.link.receive(
        frame_from(A, B, Control.i(0, 0, poll=True), command=True, info=b"hi"), now=ch.now
    )
    assert DataReceived(b"hi") in events
    assert sent(events) == ["N0BBB-1>N0AAA:(RR res, n(r)=1, f=1)"]


def test_t1_adapts_to_the_round_trip():
    ch = connected_pair(config=LinkConfig(t1=10, t2=0), delay=0.25)
    assert ch.a.link.round_trip > 4  # starts at half the configured T1
    for n in range(20):
        ch.call(ch.a.link, "send", bytes([n]))
        ch.run_until(lambda: ch.a.link.queued_bytes == 0)
    assert ch.a.link.round_trip == pytest.approx(0.5, abs=0.5)
    assert len(frames_of(ch.a, FrameType.I)) == 20  # never timed out


def test_t1_backs_off_while_the_remote_is_silent():
    ch = connected_pair(config=LinkConfig(t1=1, n2=4))
    polls = []

    def drop(frame):
        if frame.control.ftype is FrameType.S_RR and frame.is_command:
            polls.append(ch.now)
        return True

    ch.drop = drop
    ch.call(ch.a.link, "send", b"x")
    ch.run_until(lambda: ch.a.disconnected)
    gaps = [later - earlier for earlier, later in itertools.pairwise(polls)]
    assert gaps == sorted(gaps) and gaps[-1] > gaps[0]
    assert max(gaps) <= 8  # capped at 8 times the configured T1
