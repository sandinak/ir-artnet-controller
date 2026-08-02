"""Parse Flipper Zero .ir files into transmittable IR signals.

A Flipper .ir file is a list of blocks separated by lines containing only '#'.
Each block is either:

  type: parsed          (protocol + address + command, e.g. NEC)
  type: raw             (frequency + duty_cycle + a list of on/off durations)

Both are normalised here into an `IRSignal` whose `.timings` is a flat list of
microsecond durations alternating mark, space, mark, space ...  starting with a
mark.  That representation is exactly what the pigpio transmitter consumes, so
parsed and raw signals are handled identically downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import protocols


@dataclass
class IRSignal:
    name: str
    frequency: int                 # carrier Hz (e.g. 38000)
    duty_cycle: float              # 0.0 - 1.0
    timings: List[int]             # microseconds, alternating mark/space, starts on a mark
    source_type: str = "raw"       # "raw" or "parsed"
    protocol: Optional[str] = None
    raw_fields: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timings:
            raise ValueError(f"signal {self.name!r} has no timing data")
        if len(self.timings) % 2 != 0:
            # A well-formed pulse train ends on a space; if a trailing mark was
            # captured without its space, pad a nominal gap so chaining is safe.
            self.timings = self.timings + [protocols.INTER_FRAME_GAP_US]


def _parse_block(fields: Dict[str, str]) -> Optional[IRSignal]:
    name = fields.get("name", "unnamed")
    sig_type = fields.get("type", "").strip().lower()

    if sig_type == "raw":
        freq = int(fields.get("frequency", "38000"))
        duty = float(fields.get("duty_cycle", "0.33"))
        data = fields.get("data", "").split()
        timings = [int(x) for x in data]
        return IRSignal(
            name=name, frequency=freq, duty_cycle=duty,
            timings=timings, source_type="raw",
        )

    if sig_type == "parsed":
        proto = fields.get("protocol", "").strip()
        address = _hexbytes(fields.get("address", ""))
        command = _hexbytes(fields.get("command", ""))
        freq, duty, timings = protocols.encode(proto, address, command)
        return IRSignal(
            name=name, frequency=freq, duty_cycle=duty, timings=timings,
            source_type="parsed", protocol=proto,
            raw_fields={"address": fields.get("address", ""),
                        "command": fields.get("command", "")},
        )

    return None


def _hexbytes(s: str) -> List[int]:
    """'04 00 00 00' -> [4, 0, 0, 0]  (Flipper stores little-endian byte lists)."""
    return [int(tok, 16) for tok in s.split()] if s.strip() else []


def parse_file(path: str) -> Dict[str, IRSignal]:
    """Parse a .ir file, returning {signal_name: IRSignal}.

    Later duplicates of a name win, matching Flipper's own last-wins behaviour.
    """
    signals: Dict[str, IRSignal] = {}
    block: Dict[str, str] = {}

    def flush():
        if block:
            try:
                sig = _parse_block(block)
                if sig is not None:
                    signals[sig.name] = sig
            except (protocols.UnsupportedProtocol, ValueError) as exc:
                raise ValueError(
                    f"{path}: signal {block.get('name','?')!r}: {exc}"
                ) from exc

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.strip() == "#":
                flush()
                block = {}
                continue
            if line.startswith("Filetype:") or line.startswith("Version:"):
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                block[key.strip()] = val.strip()
        flush()  # last block, if the file did not end on a '#'

    return signals
