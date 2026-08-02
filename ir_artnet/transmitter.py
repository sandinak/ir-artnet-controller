"""IR transmission via pigpio hardware-timed waveforms.

Why pigpio and not the kernel gpio-ir-tx driver: pigpio generates the 38 kHz
carrier and the mark/space envelope in the DMA hardware with microsecond
accuracy and, crucially, lets us emit *arbitrary* raw timings at *any* carrier
frequency.  That means a Flipper RAW capture replays byte-for-byte, and every
parsed protocol (which we've already reduced to raw timings) works through the
exact same path.

Wiring assumption: one GPIO pin drives a logic-level MOSFET gate; the MOSFET
switches a high-power IR LED array (the tower blasters).  Set `active_low` if
your driver stage inverts.  See wiring.md.

The transmitter is designed to fail safe: if pigpio or the daemon is missing it
still constructs (so the rest of the service can run and log), and transmit
calls raise a clear RuntimeError instead of crashing the ArtNet loop.
"""

from __future__ import annotations

import threading
from typing import List, Optional

try:
    import pigpio  # type: ignore
    _HAVE_PIGPIO = True
except ImportError:            # allows dev/test on a non-Pi host
    pigpio = None
    _HAVE_PIGPIO = False

# pigpio waves are capped (~12000 pulses).  Carrier modulation turns every
# microsecond of "mark" into many pulses, so we chunk long signals into several
# waves and fire them back-to-back with wave_chain.
_MAX_PULSES_PER_WAVE = 5000


class IRTransmitter:
    def __init__(self, gpio_pin: int, active_low: bool = False,
                 pi_host: Optional[str] = None):
        self.gpio_pin = gpio_pin
        self.active_low = active_low
        self._lock = threading.Lock()
        self._pi = None

        if not _HAVE_PIGPIO:
            return
        # pi_host=None -> local daemon; or "hostname"/"ip" for a remote pigpiod.
        self._pi = pigpio.pi() if pi_host is None else pigpio.pi(pi_host)
        if not self._pi.connected:
            self._pi = None
            return
        self._pi.set_mode(self.gpio_pin, pigpio.OUTPUT)
        self._pi.write(self.gpio_pin, 1 if self.active_low else 0)  # idle

    @property
    def available(self) -> bool:
        return self._pi is not None

    # -- carrier waveform construction --------------------------------------

    def _carrier_pulses(self, marks_spaces: List[int], freq: int, duty: float):
        """Turn a mark/space list into pigpio pulses with a modulated carrier.

        Even indices are marks (carrier on), odd indices are spaces (carrier off).
        """
        on_mask = (1 << self.gpio_pin)
        # "on" means LED emitting.  With active_low, emitting = pin LOW.
        if self.active_low:
            emit_set, emit_clear = 0, on_mask       # set=gpio_on bits, clear bits
        else:
            emit_set, emit_clear = on_mask, 0

        period = 1_000_000.0 / freq                 # us per carrier cycle
        on_us = period * duty
        off_us = period - on_us

        pulses = []
        for i, dur in enumerate(marks_spaces):
            if dur <= 0:
                continue
            if i % 2 == 0:
                # MARK: emit modulated carrier for `dur` microseconds.
                cycles = int(round(dur / period))
                for _ in range(max(1, cycles)):
                    pulses.append(pigpio.pulse(emit_set, emit_clear, int(round(on_us))))
                    pulses.append(pigpio.pulse(emit_clear, emit_set, int(round(off_us))))
            else:
                # SPACE: LED off for the whole duration.
                pulses.append(pigpio.pulse(emit_clear, emit_set, int(dur)))
        return pulses

    # -- public API ---------------------------------------------------------

    def transmit(self, timings: List[int], freq: int, duty: float, repeats: int = 1):
        """Send `timings` (mark/space us list) `repeats` times.  Thread-safe."""
        if self._pi is None:
            raise RuntimeError(
                "pigpio unavailable: is the pigpiod daemon running "
                "(sudo systemctl start pigpiod)?"
            )

        pulses = self._carrier_pulses(timings, freq, duty)
        if not pulses:
            return

        with self._lock:
            self._pi.wave_clear()
            wave_ids = []
            for i in range(0, len(pulses), _MAX_PULSES_PER_WAVE):
                chunk = pulses[i:i + _MAX_PULSES_PER_WAVE]
                self._pi.wave_add_generic(chunk)
                wid = self._pi.wave_create()
                if wid < 0:
                    self._cleanup_waves(wave_ids)
                    raise RuntimeError(f"wave_create failed (code {wid})")
                wave_ids.append(wid)

            # Build a chain that plays all chunks, repeated `repeats` times.
            # pigpio wave_chain loop syntax: 255,0 ... 255,1 count_lo count_hi
            chain = []
            if repeats <= 1:
                chain = list(wave_ids)
            else:
                chain = [255, 0] + list(wave_ids) + [255, 1,
                                                     repeats & 0xFF,
                                                     (repeats >> 8) & 0xFF]
            self._pi.wave_chain(chain)
            while self._pi.wave_tx_busy():
                pass
            self._cleanup_waves(wave_ids)

    def _cleanup_waves(self, wave_ids):
        for wid in wave_ids:
            try:
                self._pi.wave_delete(wid)
            except Exception:
                pass

    def close(self):
        if self._pi is not None:
            try:
                self._pi.wave_tx_stop()
                self._pi.write(self.gpio_pin, 1 if self.active_low else 0)
            finally:
                self._pi.stop()
                self._pi = None


# ===========================================================================
#  ir-ctl / gpio-ir-tx backend  (the canonical, Ansible-managed fleet path)
# ===========================================================================
#
# This is the default backend.  It drives the kernel's gpio-ir-tx device
# (enabled by `dtoverlay=gpio-ir-tx,gpio_pin=N` in config.txt, managed by
# Ansible across the fleet) by shelling out to `ir-ctl`.  We feed ir-ctl the
# same mark/space timing list every signal is normalised to -- so a Flipper
# RAW capture and a decoded protocol both transmit through the identical path.

import os
import shutil
import subprocess
import tempfile


class IrCtlTransmitter:
    """Send IR via `ir-ctl` on a gpio-ir-tx LIRC device (e.g. /dev/lirc0)."""

    def __init__(self, lirc_device: str = "/dev/lirc0",
                 ir_ctl_path: Optional[str] = None, **_ignored):
        self.device = lirc_device
        self.ir_ctl = ir_ctl_path or shutil.which("ir-ctl") or "ir-ctl"
        self._lock = threading.Lock()
        self._ok = (shutil.which(self.ir_ctl) is not None
                    or os.path.exists(self.ir_ctl)) and os.path.exists(self.device)

    @property
    def available(self) -> bool:
        return self._ok

    @staticmethod
    def _build_file(timings: List[int], freq: int) -> str:
        """ir-ctl text format: carrier + alternating pulse/space (microseconds)."""
        lines = [f"carrier {int(freq)}"]
        for i, dur in enumerate(timings):
            if dur <= 0:
                continue
            lines.append(f"{'pulse' if i % 2 == 0 else 'space'} {int(dur)}")
        return "\n".join(lines) + "\n"

    def transmit(self, timings: List[int], freq: int, duty: float, repeats: int = 1):
        if not self._ok:
            raise RuntimeError(
                f"ir-ctl backend unavailable: need `ir-ctl` installed and "
                f"{self.device} present (dtoverlay=gpio-ir-tx in config.txt)."
            )
        payload = self._build_file(timings, freq)
        with self._lock:
            with tempfile.NamedTemporaryFile("w", suffix=".ir", delete=False) as fh:
                fh.write(payload)
                path = fh.name
            try:
                for _ in range(max(1, repeats)):
                    subprocess.run(
                        [self.ir_ctl, "-d", self.device, f"--send={path}"],
                        check=True, capture_output=True, timeout=5,
                    )
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def close(self):
        pass


def make_transmitter(tcfg: dict):
    """Factory: pick the transmit backend from config.

    transmitter.backend: "ir-ctl" (default, canonical) | "pigpio" (fallback)
    """
    backend = str(tcfg.get("backend", "ir-ctl")).lower()
    if backend in ("pigpio", "pi", "wave"):
        return IRTransmitter(
            gpio_pin=int(tcfg.get("gpio_pin", 18)),
            active_low=bool(tcfg.get("active_low", False)),
            pi_host=tcfg.get("pi_host"),
        )
    # default: canonical gpio-ir-tx / ir-ctl
    return IrCtlTransmitter(
        lirc_device=tcfg.get("lirc_device", "/dev/lirc0"),
        ir_ctl_path=tcfg.get("ir_ctl_path"),
    )
