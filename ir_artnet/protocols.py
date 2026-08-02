"""Encode Flipper 'parsed' signals into raw mark/space timings (microseconds).

Only the protocols commonly found on stage / AV gear are implemented natively:
NEC, NECext, Samsung32, the Sony SIRC family, and RC5.  Anything else raises
UnsupportedProtocol -- in that case just re-capture the button on the Flipper as
RAW (Flipper: hold the button while capturing), which always transmits verbatim.

Every encoder returns (frequency_hz, duty_cycle, timings) where `timings` is a
flat list alternating mark, space, mark, space ... beginning with a mark.
"""

from __future__ import annotations

from typing import List, Tuple

INTER_FRAME_GAP_US = 40000  # nominal trailing gap appended after a final mark


class UnsupportedProtocol(Exception):
    pass


# --- little helpers ---------------------------------------------------------

def _le(byte_list: List[int]) -> int:
    """Little-endian byte list -> int (Flipper stores fields this way)."""
    v = 0
    for i, b in enumerate(byte_list):
        v |= (b & 0xFF) << (8 * i)
    return v


def _byte_lsb_first(value: int, nbits: int) -> List[int]:
    return [(value >> i) & 1 for i in range(nbits)]


# --- pulse-distance protocols (NEC, Samsung, Sony) --------------------------

def _pulse_distance(bits, mark, zero_space, one_space,
                    lead_mark, lead_space, trail_mark) -> List[int]:
    t = [lead_mark, lead_space]
    for bit in bits:
        t.append(mark)
        t.append(one_space if bit else zero_space)
    t.append(trail_mark)
    t.append(INTER_FRAME_GAP_US)
    return t


def _nec(address: List[int], command: List[int], ext: bool) -> Tuple[int, float, List[int]]:
    if ext:
        # 16-bit address + 16-bit command, no inversion.
        addr = _le(address[:2]) & 0xFFFF
        cmd = _le(command[:2]) & 0xFFFF
        bits = _byte_lsb_first(addr, 16) + _byte_lsb_first(cmd, 16)
    else:
        a = (address[0] if address else 0) & 0xFF
        c = (command[0] if command else 0) & 0xFF
        bits = (_byte_lsb_first(a, 8) + _byte_lsb_first(a ^ 0xFF, 8)
                + _byte_lsb_first(c, 8) + _byte_lsb_first(c ^ 0xFF, 8))
    timings = _pulse_distance(
        bits, mark=560, zero_space=560, one_space=1690,
        lead_mark=9000, lead_space=4500, trail_mark=560)
    return 38000, 0.33, timings


def _samsung32(address: List[int], command: List[int]) -> Tuple[int, float, List[int]]:
    a = (address[0] if address else 0) & 0xFF
    c = (command[0] if command else 0) & 0xFF
    bits = (_byte_lsb_first(a, 8) + _byte_lsb_first(a, 8)
            + _byte_lsb_first(c, 8) + _byte_lsb_first(c ^ 0xFF, 8))
    timings = _pulse_distance(
        bits, mark=560, zero_space=560, one_space=1690,
        lead_mark=4500, lead_space=4500, trail_mark=560)
    return 38000, 0.33, timings


def _sirc(address: List[int], command: List[int], nbits: int) -> Tuple[int, float, List[int]]:
    # Sony SIRC: 7-bit command + (5/8/13)-bit address, LSB first, carrier 40kHz.
    cmd = (command[0] if command else 0) & 0x7F
    addr_bits = nbits - 7
    addr = _le(address) & ((1 << addr_bits) - 1)
    bits = _byte_lsb_first(cmd, 7) + _byte_lsb_first(addr, addr_bits)

    t = [2400, 600]  # header
    for bit in bits:
        t.append(1200 if bit else 600)  # bit mark
        t.append(600)                   # bit space
    # SIRC frames are meant to be sent on a 45ms period; append gap.
    t.append(INTER_FRAME_GAP_US)
    return 40000, 0.33, t


def _rc5(address: List[int], command: List[int]) -> Tuple[int, float, List[int]]:
    # RC5: 14 Manchester bits, half-bit 889us, carrier 36kHz.
    # Bit order: S1, S2/field, toggle, 5 addr (MSB first), 6 cmd (MSB first).
    HALF = 889
    a = (address[0] if address else 0) & 0x1F
    c = (command[0] if command else 0) & 0x3F
    field_bit = 0 if (c & 0x40) else 1  # RC5X 7th command bit lives in S2 (inverted)
    frame = [1, field_bit, 0]  # S1=1, S2/field, toggle=0
    frame += [(a >> i) & 1 for i in range(4, -1, -1)]
    frame += [(c >> i) & 1 for i in range(5, -1, -1)]

    # Manchester (IEEE): logical 1 = space(low) then mark(high); 0 = mark then space.
    # Build a level sequence then collapse to mark/space durations.
    levels: List[int] = []
    for bit in frame:
        if bit:
            levels += [0, 1]   # low half, high half
        else:
            levels += [1, 0]
    # Collapse consecutive equal half-bits into durations; ensure we start on a mark.
    if levels[0] == 0:
        # A leading space cannot start an IR frame cleanly; RC5 S1 is always 1,
        # so this path should not occur, but guard anyway.
        levels = levels[1:]
    timings: List[int] = []
    run = 0
    cur = levels[0]
    for lv in levels:
        if lv == cur:
            run += HALF
        else:
            timings.append(run)
            cur = lv
            run = HALF
    timings.append(run)
    if len(timings) % 2 == 1:
        timings.append(INTER_FRAME_GAP_US)
    else:
        timings[-1] += 0
        timings.append(INTER_FRAME_GAP_US)
    return 36000, 0.33, timings


# --- dispatch ---------------------------------------------------------------

def encode(protocol: str, address: List[int], command: List[int]) -> Tuple[int, float, List[int]]:
    p = protocol.strip().upper()
    if p == "NEC":
        return _nec(address, command, ext=False)
    if p in ("NECEXT", "NEC-EXT", "NECEXTENDED"):
        return _nec(address, command, ext=True)
    if p in ("SAMSUNG32", "SAMSUNG"):
        return _samsung32(address, command)
    if p in ("SIRC", "SONY", "SIRC12"):
        return _sirc(address, command, 12)
    if p == "SIRC15":
        return _sirc(address, command, 15)
    if p == "SIRC20":
        return _sirc(address, command, 20)
    if p in ("RC5", "RC5X"):
        return _rc5(address, command)
    raise UnsupportedProtocol(
        f"protocol {protocol!r} not implemented -- re-capture this button as RAW "
        f"on the Flipper (RAW transmits any protocol verbatim)."
    )
