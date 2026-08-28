#!/usr/bin/env python3
"""End-to-end self test that needs no Pi and no pigpio.

Verifies:
  1. Flipper .ir parsing (raw + parsed).
  2. Protocol encoders produce sane timings (NEC frame length, header, etc.).
  3. ArtDMX packet build/parse round-trips.
  4. The full ArtNet -> ChannelMap -> TransmitQueue pipeline fires the right
     commands for threshold / index / rate modes (using a capturing fake TX).

Run:  python3 selftest.py
"""
import struct
import time

from ir_artnet import flipper, protocols
from ir_artnet.artnet import port_address
from ir_artnet.controller import Controller, TxJob

CFG = {
    "_base_dir": ".",
    "artnet": {"universe": 0},
    "transmitter": {"gpio_pin": 18, "min_gap_ms": 0},
    "ir_files": {"projector": "remotes/projector.ir", "fog": "remotes/fogger.ir"},
    "channels": [
        {"channel": 1, "mode": "threshold", "threshold": 128, "command": "projector:Power"},
        {"channel": 10, "mode": "index", "go_channel": 11, "go_threshold": 128,
         "table": {"1-20": "fog:Low", "21-255": "fog:High"}},
        {"channel": 20, "mode": "rate", "command": "projector:Volume_Up", "max_hz": 50},
    ],
}


def build_artdmx(universe, values):
    dmx = bytes(values)
    pkt = bytearray()
    pkt += b"Art-Net\x00"
    pkt += struct.pack("<H", 0x5000)
    pkt += bytes([0, 14, 0, 0])
    pkt += bytes([universe & 0xFF, (universe >> 8) & 0x7F])
    pkt += struct.pack(">H", len(dmx))
    pkt += dmx
    return bytes(pkt)


def test_parsing():
    sigs = flipper.parse_file("remotes/projector.ir")
    assert "Power" in sigs and "Mute" in sigs
    # NEC frame: 2 (header) + 32 bits * 2 + trailing mark + gap = 68 edges
    assert len(sigs["Power"].timings) == 68, len(sigs["Power"].timings)
    assert sigs["Power"].timings[0] == 9000  # NEC leading mark
    assert sigs["Mute"].source_type == "raw"
    print("  parsing OK")


def test_protocols():
    _, _, nec = protocols.encode("NEC", [0x04], [0x08])
    assert nec[0] == 9000 and nec[1] == 4500
    _, _, sam = protocols.encode("Samsung32", [0x07], [0x12])
    assert sam[0] == 4500 and sam[1] == 4500  # Samsung header is 4500/4500
    _, _, sirc = protocols.encode("SIRC", [0x01], [0x15])
    assert sirc[0] == 2400 and sirc[1] == 600
    try:
        protocols.encode("BOGUS", [0], [0])
        assert False, "should have raised"
    except protocols.UnsupportedProtocol:
        pass
    print("  protocols OK")


def test_artdmx_roundtrip():
    pkt = build_artdmx(0, [0] * 512)
    assert pkt.startswith(b"Art-Net\x00")
    assert struct.unpack_from("<H", pkt, 8)[0] == 0x5000
    assert port_address(pkt[15], pkt[14]) == 0
    print("  artdmx round-trip OK")


def test_pipeline():
    fired = []

    ctrl = Controller(CFG)
    # swap in a capturing transmitter -- no pigpio required
    ctrl.tx.transmit = lambda timings, freq, duty, repeats=1: fired.append(
        (len(timings), freq, repeats))
    ctrl.start()

    def frame(values):
        buf = bytearray(512)
        for ch, v in values.items():
            buf[ch - 1] = v
        ctrl.on_dmx(0, bytes(buf))

    # threshold: rising across 128 fires once, staying high does NOT re-fire
    frame({1: 0}); frame({1: 200}); frame({1: 255}); frame({1: 0})
    time.sleep(0.1)
    n_threshold = len(fired)
    assert n_threshold == 1, f"threshold fired {n_threshold} times"

    # index: selector=100 (->fog:High), go rises across 128 => one fire
    frame({10: 100, 11: 0}); frame({10: 100, 11: 255})
    time.sleep(0.1)
    assert len(fired) == 2, fired

    # rate: hold channel 20 high; expect several fires over 0.25 s at 50 Hz
    before = len(fired)
    frame({20: 255})
    time.sleep(0.25)
    frame({20: 0})
    time.sleep(0.05)
    rate_fires = len(fired) - before
    assert rate_fires >= 5, f"rate produced only {rate_fires} fires"

    ctrl.close()
    print(f"  pipeline OK (threshold=1, index=1, rate={rate_fires} fires/0.25s)")


def test_queue_coalesce_priority():
    import time as _t
    from ir_artnet.controller import TransmitQueue, TxJob
    from ir_artnet.flipper import IRSignal

    sig = IRSignal(name="X", frequency=38000, duty_cycle=0.33, timings=[560, 560])

    class SlowTx:
        available = True
        def transmit(self, t, f, d, repeats=1): _t.sleep(0.03)
        def close(self): pass

    # coalescing: flood one key while the worker is slow -> most collapse
    q = TransmitQueue(SlowTx(), min_gap_s=0.0)
    q.start()
    for i in range(20):
        q.submit(TxJob(sig, 1, f"ON#{i}", key="rate:1"), coalesce=True)
        _t.sleep(0.004)
    _t.sleep(0.3)
    assert q.stats["coalesced"] > 0, "nothing coalesced"
    assert q.stats["sent"] < 20, "coalescing did not reduce sends"
    q.stop()

    # priority: a priority job drains before queued normal jobs
    q2 = TransmitQueue(SlowTx(), min_gap_s=0.0)
    q2.submit(TxJob(sig, 1, "norm-A"), coalesce=False)
    q2.submit(TxJob(sig, 1, "norm-B"), coalesce=False)
    q2.submit(TxJob(sig, 1, "BLACKOUT"), coalesce=False, priority=True)
    picks = []
    while q2._prio or q2._norm:
        j = q2._prio.popleft() if q2._prio else q2._norm.popleft()
        picks.append(j.label)
    assert picks[0] == "BLACKOUT", picks
    assert q2.stats["preempted"] == 1
    print(f"  queue OK (coalesced={q.stats['coalesced']}, priority preempts)")


CFG_SEL = {
    "_base_dir": ".",
    "artnet": {"universe": 0},
    "transmitter": {"min_gap_ms": 0},
    "ir_files": {"candles": "remotes/candles.ir"},
    "channels": [{
        "mode": "selector", "name": "IR",
        "select_channel": 1, "rate_channel": 2, "count_channel": 3, "go_channel": 4,
        "min_hz": 5, "max_hz": 40, "floor": 1,
        "table": {1: "candles:On", 2: "candles:Off", 3: "candles:Candle"},
    }],
}


def test_selector():
    ctrl = Controller(CFG_SEL)
    ctrl.tx.transmit = lambda *a, **k: None
    labels = []
    orig = ctrl.txq.submit
    ctrl.txq.submit = lambda job, **kw: (labels.append(job.label), orig(job, **kw))[1]
    ctrl.start()

    def frame(sel=0, rate=0, count=0, go=0):
        b = bytearray(512)
        b[0], b[1], b[2], b[3] = sel, rate, count, go
        ctrl.on_dmx(0, bytes(b))

    # one-shot: select Off (code 2), count=1, GO rising -> exactly one send
    frame(2, 255, 1, 0)
    frame(2, 255, 1, 255)
    time.sleep(0.15)
    off = [x for x in labels if ":Off@" in x]
    assert len(off) == 1, off

    # continuous: select On (code 1), count=0, hold GO -> repeated sends
    labels.clear()
    frame(1, 255, 0, 0)        # GO low first
    frame(1, 255, 0, 255)      # GO rises -> continuous
    time.sleep(0.25)
    frame(1, 255, 0, 0)        # release -> stop
    on = [x for x in labels if ":On@" in x]
    assert len(on) >= 3, on
    # exclusivity: never an Off while On is selected
    assert not any(":Off@" in x for x in labels)
    ctrl.close()
    print(f"  selector OK (one-shot=1, continuous={len(on)}, exclusive)")


CFG_2CH = {
    "_base_dir": ".",
    "artnet": {"universe": 0},
    "transmitter": {"min_gap_ms": 0},
    "ir_files": {"candles": "remotes/candles.ir"},
    "channels": [{
        "mode": "selector", "name": "IR",
        "select_channel": 1,        # function
        "count_channel": 2,         # repeat (0 = continuous)
        # no rate_channel -> fixed max_hz; no go_channel -> selection arms it
        "max_hz": 20, "floor": 1,
        "table": {1: "candles:On", 2: "candles:Off", 3: "candles:Candle"},
    }],
}


def test_selector_2ch():
    """The field-tested patch: ch1 = function, ch2 = repeat, no Rate, no GO.

    The daemon treats a missing go_channel as "armed whenever a code is
    selected", so a shot is triggered by the selection *changing*. That is the
    behaviour the desk relies on, and the re-fire quirk operators need to know
    about, so both are pinned here.
    """
    ctrl = Controller(CFG_2CH)
    ctrl.tx.transmit = lambda *a, **k: None
    labels = []
    orig = ctrl.txq.submit
    ctrl.txq.submit = lambda job, **kw: (labels.append(job.label), orig(job, **kw))[1]
    ctrl.start()

    def frame(sel=0, count=0):
        b = bytearray(512)
        b[0], b[1] = sel, count
        ctrl.on_dmx(0, bytes(b))
        time.sleep(0.20)

    def shots():
        got = list(labels)
        labels.clear()
        return got

    frame(1, 1)                      # select On, repeat=1 -> exactly one shot
    one = shots()
    assert len(one) == 1, one
    assert ":On@" in one[0], one

    frame(1, 1)                      # same selection held -> does NOT re-fire
    assert shots() == []

    frame(0, 1)                      # back to idle...
    shots()
    frame(1, 1)                      # ...and reselect -> fires again
    assert len(shots()) == 1

    frame(3, 3)                      # repeat=3 -> exactly three shots
    three = shots()
    assert len(three) == 3, three
    assert all(":Candle@" in x for x in three), three

    frame(0, 0)
    shots()
    frame(2, 0)                      # repeat=0 -> continuous while selected
    cont = shots()
    # ~4 expected at 20 Hz over 0.2 s; assert only that it repeats, so a loaded
    # CI runner can't turn a timing wobble into a red build.
    assert len(cont) >= 2, cont

    frame(0, 0)                      # deselect -> stops
    assert shots() == []

    ctrl.close()
    print(f"  selector 2ch OK (one-shot=1, no re-fire on hold, "
          f"count=3, continuous={len(cont)}, deselect stops)")


def test_gen_config():
    from ir_artnet.__main__ import _gen_selector_config
    cfg, vmap = _gen_selector_config("remotes/candles.ir", key="candles")
    assert vmap[1] == "candles:On" and vmap[2] == "candles:Off", vmap
    assert cfg["channels"][0]["mode"] == "selector"
    assert cfg["channels"][0]["table"][1] == "candles:On"
    print(f"  gen-config OK ({len(vmap)} codes -> DMX values 1..{len(vmap)})")


def test_snapshot():
    ctrl = Controller(CFG)
    ctrl.tx.transmit = lambda *a, **k: None
    buf = bytearray(512)
    buf[0] = 200      # ch1 threshold -> ARMED
    rows = dict(ctrl.cmap.snapshot(bytes(buf)))
    assert "ARMED" in rows[1], rows
    ctrl.close()
    print("  snapshot OK")


if __name__ == "__main__":
    print("Running self-test (no Pi / pigpio required)...")
    test_parsing()
    test_protocols()
    test_artdmx_roundtrip()
    test_pipeline()
    test_queue_coalesce_priority()
    test_selector()
    test_selector_2ch()
    test_gen_config()
    test_snapshot()
    print("ALL TESTS PASSED")
