"""Maps Art-Net DMX channel activity to IR command transmissions.

Three per-channel trigger modes (chosen in config.yaml):

  threshold  Fire the mapped command once each time the channel value rises
             across `threshold` (classic lighting-desk "button" behaviour).

  index      One channel's value selects a command from a table; a separate
             `go_channel` fires the selected command on its own rising edge.
             Compact: dozens of commands on two DMX channels.

  rate       While the channel is above zero, repeat the command continuously
             at a rate scaled by the value (0..255 -> 0..max_hz).  Good for
             hold-to-repeat remotes (volume, dimmer up/down).

All transmissions funnel through a single worker thread so the shared IR bus is
never double-driven and a global minimum gap is enforced between shots.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .flipper import IRSignal, parse_file
from .transmitter import make_transmitter

log = logging.getLogger("ir_artnet")


@dataclass
class TxJob:
    signal: IRSignal
    repeats: int
    label: str
    key: Optional[str] = None      # coalescing key (e.g. "rate:5"); None = never coalesce


class TransmitQueue:
    """Serialises IR shots on one worker thread. Exactly one code transmits at a
    time (the IR bus is never double-driven), spaced by at least `min_gap_s`.

    Two refinements over a plain FIFO:
      * COALESCING  — jobs sharing a `key` collapse to one pending entry
        (latest wins). A held look never stacks more than one pending shot of
        itself, so the queue can't fill with stale repeats.
      * PRIORITY    — priority jobs (e.g. a blackout OFF) jump ahead of every
        normal job still waiting. Preemption is of the QUEUE, not the shot in
        flight (a code already transmitting finishes — at most ~130 ms).
    """

    def __init__(self, tx, min_gap_s: float = 0.06, maxsize: int = 64):
        self._tx = tx
        self._min_gap = min_gap_s
        self._max = maxsize
        self._cv = threading.Condition()
        self._prio: "deque[TxJob]" = deque()
        self._norm: "deque[TxJob]" = deque()
        self._keys: Dict[str, TxJob] = {}   # key -> the currently-pending job
        self._stop = False
        self._last = 0.0
        self.stats = {"sent": 0, "dropped": 0, "coalesced": 0, "preempted": 0}
        self._worker = threading.Thread(target=self._run, name="ir-tx", daemon=True)

    def start(self):
        self._worker.start()

    def submit(self, job: TxJob, coalesce: bool = True, priority: bool = False):
        with self._cv:
            if self._stop:
                return
            key = job.key if coalesce else None
            if key is not None and key in self._keys:
                # latest wins: update the already-queued job in place
                pending = self._keys[key]
                pending.signal, pending.repeats, pending.label = (
                    job.signal, job.repeats, job.label)
                self.stats["coalesced"] += 1
                log.debug("coalesce %s (key=%s)", job.label, key)
                return
            if (len(self._prio) + len(self._norm)) >= self._max and not priority:
                self.stats["dropped"] += 1
                log.warning("IR queue full (%d), dropping %s",
                            self._max, job.label)
                return
            if priority:
                if self._norm:
                    self.stats["preempted"] += 1
                self._prio.append(job)
                log.debug("enqueue PRIORITY %s", job.label)
            else:
                self._norm.append(job)
            if key is not None:
                self._keys[key] = job
            self._cv.notify()

    def _run(self):
        while True:
            with self._cv:
                while not self._stop and not self._prio and not self._norm:
                    self._cv.wait()
                if self._stop and not self._prio and not self._norm:
                    return
                job = self._prio.popleft() if self._prio else self._norm.popleft()
                if job.key is not None and self._keys.get(job.key) is job:
                    del self._keys[job.key]
                depth = len(self._prio) + len(self._norm)
            gap = self._min_gap - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            try:
                self._tx.transmit(job.signal.timings, job.signal.frequency,
                                  job.signal.duty_cycle, repeats=job.repeats)
                self.stats["sent"] += 1
                log.info("TX %s (x%d)  [q=%d]", job.label, job.repeats, depth)
            except Exception as exc:  # never let a bad shot kill the worker
                log.error("TX failed for %s: %s", job.label, exc)
            self._last = time.monotonic()

    def stop(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()


class ChannelMap:
    def __init__(self, cfg: dict, ir_libs: Dict[str, Dict[str, IRSignal]],
                 txq: TransmitQueue):
        self._txq = txq
        self._libs = ir_libs
        self._threshold: List[dict] = []
        self._index: List[dict] = []
        self._rate: List[dict] = []
        self._selectors: List[dict] = []
        self._prev = bytearray(512)          # last DMX frame for edge detection
        self._latest = b""                   # most recent frame (for rate loop + --watch)
        self._rate_state: Dict[int, float] = {}  # channel -> next fire time
        self._sel_state: Dict[int, dict] = {}    # selector index -> runtime state
        self._lock = threading.Lock()

        for entry in cfg.get("channels", []):
            mode = entry.get("mode", "threshold")
            if mode == "threshold":
                self._threshold.append(self._prep_threshold(entry))
            elif mode == "index":
                self._index.append(self._prep_index(entry))
            elif mode == "rate":
                self._rate.append(self._prep_rate(entry))
            elif mode == "selector":
                self._selectors.append(self._prep_selector(entry))
            else:
                raise ValueError(f"unknown channel mode {mode!r}")

        # rate + selector modes need a clock independent of incoming ArtNet frames.
        self._clock_thread = None
        if self._rate or self._selectors:
            self._clock_thread = threading.Thread(
                target=self._clock_loop, name="ir-clock", daemon=True)

    # -- config preparation --------------------------------------------------

    def _resolve(self, ref: str) -> IRSignal:
        if ":" not in ref:
            raise ValueError(f"command {ref!r} must be 'file:signal'")
        fkey, signame = ref.split(":", 1)
        if fkey not in self._libs:
            raise ValueError(f"ir_files has no entry {fkey!r} (in {ref!r})")
        lib = self._libs[fkey]
        if signame not in lib:
            raise ValueError(
                f"signal {signame!r} not in {fkey!r}; have: {', '.join(sorted(lib))}")
        return lib[signame]

    def _prep_threshold(self, e: dict) -> dict:
        return {
            "channel": int(e["channel"]) - 1,
            "threshold": int(e.get("threshold", 128)),
            "signal": self._resolve(e["command"]),
            "repeats": int(e.get("repeats", 1)),
            "label": e["command"],
            "priority": bool(e.get("priority", False)),
        }

    def _prep_index(self, e: dict) -> dict:
        table = {}
        for k, v in e["table"].items():
            table[self._key_range(k)] = self._resolve(v)
        return {
            "channel": int(e["channel"]) - 1,
            "go_channel": int(e["go_channel"]) - 1,
            "go_threshold": int(e.get("go_threshold", 128)),
            "table": table,
            "repeats": int(e.get("repeats", 1)),
            "priority": bool(e.get("priority", False)),
        }

    def _prep_rate(self, e: dict) -> dict:
        return {
            "channel": int(e["channel"]) - 1,
            "signal": self._resolve(e["command"]),
            # Rate curve: DMX value -> retransmit rate (Hz).
            #   value < floor          -> off
            #   value == floor         -> min_hz   (the "held look" rate)
            #   value == 255           -> max_hz
            #   curve>1 gives finer control at the low end of the fader.
            "min_hz": float(e.get("min_hz", 0.0)),
            "max_hz": float(e.get("max_hz", 8.0)),
            "floor": int(e.get("floor", 1)),
            "curve": float(e.get("curve", 1.0)),
            "repeats": int(e.get("repeats", 1)),
            "jitter_ms": float(e.get("jitter_ms", 0.0)),
            "label": e["command"],
            "priority": bool(e.get("priority", False)),
        }

    def _prep_selector(self, e: dict) -> dict:
        # Direct value->command map: DMX value 0 = none, N = the Nth code.
        # (A bare int key like `5:` matches exactly; ranges still allowed.)
        table = {}
        for k, v in e["table"].items():
            table[self._key_range(k)] = self._resolve(v)

        def _opt_ch(key):
            return int(e[key]) - 1 if e.get(key) else None

        return {
            "name": e.get("name", "sel"),
            "select": int(e["select_channel"]) - 1,
            "rate": _opt_ch("rate_channel"),      # None -> fixed max_hz
            "count": _opt_ch("count_channel"),    # None -> continuous (0)
            "go": _opt_ch("go_channel"),          # None -> enabled whenever a code is selected
            "go_threshold": int(e.get("go_threshold", 128)),
            "table": table,
            "min_hz": float(e.get("min_hz", 1.0)),
            "max_hz": float(e.get("max_hz", 8.0)),
            "floor": int(e.get("floor", 1)),
            "curve": float(e.get("curve", 1.0)),
            "repeats": int(e.get("repeats", 1)),
            "priority": bool(e.get("priority", False)),
        }

    @staticmethod
    def _key_range(k) -> Tuple[int, int]:
        s = str(k)
        if "-" in s:
            lo, hi = s.split("-", 1)
            return (int(lo), int(hi))
        return (int(k), int(k))

    @staticmethod
    def _hz(val: int, floor: int, min_hz: float, max_hz: float, curve: float) -> float:
        """Map a DMX value to a rate (Hz) via floor + curved min..max."""
        if val < floor:
            return 0.0
        span = max(1, 255 - floor)
        t = (max(0.0, min(1.0, (val - floor) / span))) ** curve
        return min_hz + t * (max_hz - min_hz)

    # -- runtime -------------------------------------------------------------

    def start(self):
        if self._clock_thread:
            self._clock_thread.start()

    def on_frame(self, dmx: bytes):
        with self._lock:
            for m in self._threshold:
                ch = m["channel"]
                if ch >= len(dmx):
                    continue
                new = dmx[ch]
                old = self._prev[ch]
                if old < m["threshold"] <= new:  # rising edge across threshold
                    log.debug("edge ch%d %d->%d fires %s", ch + 1, old, new, m["label"])
                    self._txq.submit(TxJob(m["signal"], m["repeats"], m["label"]),
                                     coalesce=False, priority=m["priority"])

            for m in self._index:
                go = m["go_channel"]
                if go >= len(dmx):
                    continue
                if self._prev[go] < m["go_threshold"] <= dmx[go]:
                    val = dmx[m["channel"]] if m["channel"] < len(dmx) else 0
                    sig = self._lookup(m["table"], val)
                    if sig is not None:
                        self._txq.submit(TxJob(sig, m["repeats"], f"{sig.name}[idx={val}]"),
                                         coalesce=False, priority=m["priority"])
                    else:
                        log.warning("index value %d has no table entry", val)

            # selector GO edges are detected here (every frame) so a brief GO
            # pulse can't slip between the slower clock-loop ticks.
            for i, s in enumerate(self._selectors):
                self._arm_selector(i, s, dmx)

            # snapshot this frame for next-frame edge detection
            self._prev[:len(dmx)] = dmx
            self._latest = bytes(dmx)  # for rate loop

    @staticmethod
    def _lookup(table, val):
        for (lo, hi), sig in table.items():
            if lo <= val <= hi:
                return sig
        return None

    def snapshot(self, dmx: bytes) -> List[Tuple[int, str]]:
        """Human-readable per-channel state for the --watch debug monitor.

        Returns [(dmx_channel_1based, description), ...] sorted by channel.
        """
        rows: List[Tuple[int, str]] = []
        for m in self._threshold:
            ch = m["channel"]
            v = dmx[ch] if ch < len(dmx) else 0
            arm = "ARMED" if v >= m["threshold"] else "."
            pr = " PRIO" if m["priority"] else ""
            rows.append((ch + 1, f"thr {m['label']} v={v} {arm}{pr}"))
        for m in self._index:
            sel, go = m["channel"], m["go_channel"]
            sv = dmx[sel] if sel < len(dmx) else 0
            gv = dmx[go] if go < len(dmx) else 0
            pick = self._lookup(m["table"], sv)
            name = pick.name if pick else "—"
            rows.append((sel + 1, f"idx sel={sv}->{name} GO[{go + 1}]={gv}"))
        for m in self._rate:
            ch = m["channel"]
            v = dmx[ch] if ch < len(dmx) else 0
            pr = " PRIO" if m["priority"] else ""
            hz = self._hz(v, m["floor"], m["min_hz"], m["max_hz"], m["curve"])
            state = f"@{hz:.2f}Hz" if hz > 0 else "off"
            rows.append((ch + 1, f"rate {m['label']} v={v} {state}{pr}"))
        for s in self._selectors:
            def _v(off):
                return dmx[off] if (off is not None and off < len(dmx)) else 0
            sv = _v(s["select"])
            cmd = self._lookup(s["table"], sv) if sv > 0 else None
            name = cmd.name if cmd else "—"
            rate_val = _v(s["rate"]) if s["rate"] is not None else 255
            hz = self._hz(rate_val, s["floor"], s["min_hz"], s["max_hz"], s["curve"])
            count = _v(s["count"]) if s["count"] is not None else 0
            cnt = "cont" if count == 0 else f"x{count}"
            go = "" if s["go"] is None else f" GO[{s['go'] + 1}]={_v(s['go'])}"
            rows.append((s["select"] + 1,
                         f"SEL[{s['name']}] v={sv}->{name} {cnt} @{hz:.1f}Hz{go}"))
        rows.sort()
        return rows

    def _clock_loop(self):
        TICK = 0.02
        while True:
            time.sleep(TICK)
            frame = getattr(self, "_latest", b"")
            now = time.monotonic()
            self._tick_rate(frame, now)
            self._tick_selectors(frame, now)

    def _tick_rate(self, frame, now):
        for m in self._rate:
            ch = m["channel"]
            val = frame[ch] if ch < len(frame) else 0
            hz = self._hz(val, m["floor"], m["min_hz"], m["max_hz"], m["curve"])
            if hz <= 0:
                self._rate_state.pop(ch, None)
                continue
            period = 1.0 / hz
            if m["jitter_ms"]:                  # de-sync multiple towers
                period += random.uniform(-m["jitter_ms"], m["jitter_ms"]) / 1000.0
                period = max(0.02, period)
            if now >= self._rate_state.get(ch, 0.0):
                # coalesce by channel: a held look keeps at most one pending shot
                self._txq.submit(
                    TxJob(m["signal"], m["repeats"],
                          f"{m['label']}@{hz:.1f}Hz", key=f"rate:{ch}"),
                    coalesce=True, priority=m["priority"])
                self._rate_state[ch] = now + period

    def _arm_selector(self, i, s, dmx):
        """Runs in on_frame (under lock, every DMX frame). Latches WHAT to send —
        the selected code and how many — on the GO edge / selection change. The
        clock loop only paces the actual sends. Called with self._lock held.

        Exactly one code is ever latched, so a desk mistake can send the wrong
        code but never two conflicting codes at once.
        """
        def _v(off):
            return dmx[off] if (off is not None and off < len(dmx)) else 0
        sel_val = _v(s["select"])
        cmd = self._lookup(s["table"], sel_val) if sel_val > 0 else None
        count_val = _v(s["count"]) if s["count"] is not None else 0
        go_val = _v(s["go"]) if s["go"] is not None else 255
        st = self._sel_state.setdefault(
            i, {"prev_go": 0, "remaining": 0, "next": 0.0, "cmd": None})

        if s["go"] is not None:
            rising = st["prev_go"] < s["go_threshold"] <= go_val
            st["prev_go"] = go_val
            if rising:                          # GO edge (re)starts the shot/burst
                if cmd is None:
                    log.warning("%s: GO with no code selected (val=%d)",
                                s["name"], sel_val)
                    st["remaining"] = 0
                else:
                    st["cmd"] = cmd
                    st["remaining"] = -1 if count_val == 0 else count_val
                    st["next"] = 0.0            # fire on the next clock tick
            if go_val < s["go_threshold"]:      # GO released -> stop
                st["remaining"] = 0
        else:                                   # no GO channel: selection drives it
            if cmd is not None:
                if st["cmd"] is not cmd:         # selection changed -> (re)arm
                    st["cmd"] = cmd
                    st["remaining"] = -1 if count_val == 0 else count_val
                    st["next"] = 0.0
            else:
                st["remaining"] = 0
                st["cmd"] = None

    def _tick_selectors(self, frame, now):
        """Paces the sends for whatever _arm_selector has latched. Rate is read
        live so the operator can change speed while a burst runs."""
        with self._lock:
            for i, s in enumerate(self._selectors):
                st = self._sel_state.get(i)
                if not st or st["remaining"] == 0 or st["cmd"] is None:
                    continue
                rate_val = frame[s["rate"]] if (s["rate"] is not None
                                                and s["rate"] < len(frame)) else 255
                hz = self._hz(rate_val, s["floor"], s["min_hz"], s["max_hz"], s["curve"])
                if hz <= 0 or now < st["next"]:
                    continue
                self._txq.submit(
                    TxJob(st["cmd"], s["repeats"],
                          f"{s['name']}:{st['cmd'].name}@{hz:.1f}Hz", key=f"sel:{i}"),
                    coalesce=True, priority=s["priority"])
                if st["remaining"] > 0:
                    st["remaining"] -= 1
                st["next"] = now + 1.0 / hz


class Controller:
    """Ties config -> IR libraries -> transmitter -> channel map together."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        tcfg = cfg.get("transmitter", {})
        self.tx = make_transmitter(tcfg)
        self.txq = TransmitQueue(self.tx, min_gap_s=tcfg.get("min_gap_ms", 60) / 1000.0)

        self.libs = self._load_libraries(cfg.get("ir_files", {}), cfg.get("_base_dir", "."))
        self.universe = int(cfg.get("artnet", {}).get("universe", 0))
        self.cmap = ChannelMap(cfg, self.libs, self.txq)

        if not self.tx.available:
            log.warning("%s backend not ready -- running in DRY mode "
                        "(decoding + mapping work, no IR is emitted)",
                        type(self.tx).__name__)

    @staticmethod
    def _load_libraries(ir_files: dict, base_dir: str) -> Dict[str, Dict[str, IRSignal]]:
        import os
        libs = {}
        for key, path in ir_files.items():
            full = path if os.path.isabs(path) else os.path.join(base_dir, path)
            libs[key] = parse_file(full)
            log.info("loaded %d signals from %s (%s)", len(libs[key]), path, key)
        return libs

    def start(self):
        self.txq.start()
        self.cmap.start()

    def on_dmx(self, universe: int, dmx: bytes):
        if universe != self.universe:
            return
        self.cmap.on_frame(dmx)

    def close(self):
        self.txq.stop()
        self.tx.close()
