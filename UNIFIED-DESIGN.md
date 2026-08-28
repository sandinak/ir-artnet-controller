# Unified design — Dance Hysteria IR + step electronics

This document reconciles the ArtNet IR controller built in this session with the
established **step redesign** (the custom Pi 3B+ HAT PCB, the universal pod PCB, and
the Ansible-managed fleet). It is the source of truth; where a diagram or an older
note disagrees, this document wins.

## The decision that drove the unification

The IR topology is now **consolidated to the front towers** rather than distributed
across all 12 steps. That is a deliberate reversal of the earlier "IR flood on every
unit" approach, so it's worth stating the trade-off plainly:

- **Why towers (chosen):** one or two high-power blasters cover the whole deck; far
  fewer emitter pods to build, wire, and maintain across the fleet; the IR problem is
  decoupled from the step electronics entirely.
- **What we give up:** the original rationale for distributed flood was that cast
  members hold the battery candles (Amazon B0FNCW6Y56) at unpredictable positions and
  angles, so wide-angle emitters *on every unit* maximised hit probability. Towers put
  all the IR energy at the front, 8 ft up, aimed down — coverage now depends on aim,
  candle-receiver sensitivity, and bounce off the deck.
- **The gate still applies:** the **IR hit-rate test remains the go/no-go**. Before
  committing tower placement, capture the candle remote on the Flipper and verify that
  a tower blaster reliably triggers candles at the worst-case step positions and
  orientations. If it can't, the fallback is the hybrid (towers + a few distributed
  pods), which the hardware below still supports at zero board-design cost.

## Two device roles, one HAT PCB

The custom HAT is a **universal board with two populations** — the same "one PCB,
different BOM" philosophy already used for the pods:

| | **Step unit** (×12) | **Tower blaster** (×1–2) |
|---|---|---|
| Pixelblaze passthrough (**J_PB**, GPIO14) | populated | not populated |
| Status pod (**J_STAT**, JST-XH 4) | populated | populated (optional) |
| IR driver ch. 0/1 (**J_IR0/J_IR1**, AO3400A) | **not populated** | **populated** → tower emitter head |
| QWIIC / expansion / proto area | as designed | as needed |
| Role of the Pi | drive step LED matrix via Pixelblaze; ArtNet fixture | ArtNet → IR transmit appliance |

The IR channels are simply left unpopulated (DNP) on the steps and populated on the
towers. Keeping the footprints on every board is free and preserves the hybrid option.

## Connector map (unchanged from canonical)

`J_HAT` (2×20 Pi header), `J_STAT` (JST-XH 4-pin, status pod), `J_IR0` / `J_IR1`
(JST-XH 4-pin, IR pods / tower head), `J_PB` (JST-XH 3-pin, Pixelblaze data). No HAT
ID EEPROM — device-tree overlays are Ansible-managed in `config.txt`.

## The serial / console correction

**GPIO14 (PL011 `serial0`) is the Pixelblaze data line at 2 Mbaud** (Bluetooth
disabled to free it). The login console therefore must **not** use GPIO14/15. An
earlier draft in this session put a console header there and told you to reserve a
second UART for Pixelblaze — that was backwards and has been corrected everywhere:

- **Console, primary:** SSH over the network, or an **off-HAT USB-TTL adapter** (3.3 V)
  when there's no network. Nothing soldered on the HAT for this.
- **Console, optional on-HAT port:** a 3-pin header on **UART3** (`GPIO4`/`GPIO5`),
  enabled with `dtoverlay=uart3`, `/dev/ttyAMA1` @ 115200. Fully independent of GPIO14.
- **Never** enable a login console on `serial0`/GPIO14 — it corrupts Pixelblaze data.

Details and the student build are in `step-pi-status-serial.md`; the corrected
connector map is `steppi-layout.svg`.

## IR software stack (tower blaster)

The software written this session is the tower appliance and layers cleanly on the
fleet's IR method:

```
   Lighting desk ──Art-Net──▶  ir_artnet service  ──▶  transmit backend  ──▶ IR head
                               (universe + channel      (ir-ctl default,
                                mapping + triggers)       pigpio fallback)
```

- **Capture:** candle remote → Flipper Zero → `.ir` file (raw or parsed). Both are
  normalised to mark/space timings (`flipper.py`, `protocols.py`).
- **Map:** DMX channels → commands via `config.yaml` (threshold / index / rate modes),
  serialised through one transmit queue with a minimum inter-shot gap.
- **Transmit backend (chosen):** default **`ir-ctl` on a `gpio-ir-tx` device**
  (`dtoverlay=gpio-ir-tx`, Ansible-managed) — identical management to the rest of the
  fleet. The **pigpio** engine is retained as a selectable fallback
  (`transmitter.backend: pigpio`) for arbitrary-carrier raw replay if `ir-ctl` ever
  struggles with an odd capture. Set in `config.yaml`.
- **Repeated bursts:** for candle reliability, use `repeats:` (and per-shot jitter can
  be added at the mapping layer) rather than relying on a single frame.

## Divergence resolution (this session → unified)

| Item | Built this session | Canonical / fleet | Resolution |
|------|--------------------|-------------------|------------|
| IR topology | towers | distributed flood | **Towers** (per your call); hybrid still supported by DNP IR channels; IR hit-rate test gates it |
| IR transmit | pigpio carrier | `gpio-ir-tx` + `ir-ctl` | **Default `ir-ctl`**, pigpio kept as fallback backend |
| Driver FET | IRLB8721 (TO-220) | AO3400A (SOT-23) | **AO3400A** to match fleet BOM; split big tower array across J_IR0+J_IR1, or sub IRLB8721 if >2 A on one channel |
| Off-board connectors | screw terminals / pin headers | JST-XH latching | **JST-XH** (J_STAT/J_IR0/J_IR1/J_PB) |
| Status LEDs | on-board green/blue/yellow | red/yellow/green on universal pod | **red/yellow/green on J_STAT pod**, uniform 220 Ω |
| Console | on-HAT GPIO14/15 header | off-HAT USB-TTL (GPIO14 = Pixelblaze) | **Off-HAT primary**; optional UART3 debug header |
| HAT type | Adafruit Perma-Proto | custom JLCPCB HAT | Perma-Proto = **hand-soldered prototype stage** of the custom HAT |
| Board ID | — | no EEPROM (Ansible config.txt) | unchanged |

## Development sequence (unchanged, with this work folded in)

1. **Breadboard validation** — Pi + AO3400A channel + emitter, `ir-ctl` via
   `gpio-ir-tx`. Prove the ArtNet service triggers a shot. **Codes validated
   2026-08-27** — see *Validation status* below.
2. **IR hit-rate test (explicit gate)** — tower-height blaster vs candles at worst-case
   step positions. **Decide towers-only vs hybrid here.** *Still open;* the emitter head
   goes up on the tower the weekend of 2026-08-29.
3. **KiCad layout proof** — the universal HAT (both populations) and the pod.
4. **Hand-soldered prototype** — Perma-Proto per `hardware.md` / `steppi-layout.svg`.
5. **Production run** — bare boards via JLCPCB; provision with Ansible
   (`sandinak/ansible-raspi-dmx`): `config.txt` overlays for `gpio-ir-tx` (towers) and
   `uart` for Pixelblaze/step units.

## File map

| File | Role |
|------|------|
| `UNIFIED-DESIGN.md` | this document — source of truth |
| `README.md` | tower IR service: install, capture, config, run |
| `hardware.md` | tower driver stage: schematic, BOM (AO3400A), emitter head |
| `schematic.svg` | tower driver schematic |
| `step-pi-status-serial.md` | step-unit status pod + serial (corrected) |
| `steppi-layout.svg` | step HAT connector map + universal pod |
| `hat-layout.svg` | tower driver Perma-Proto solder layout |
| `breadboard-ir.svg` | breadboard validation wiring (step 1 of the sequence) |
| `ansible/` | standalone deploy role (`ir_artnet_tower`) + playbook for a single Pi |
| `config.yaml` | ArtNet universe, transmit backend, channel→command map |
| `ir_artnet/` | the service (parse / protocols / transmit backends / ArtNet / controller) |
| `examples/status_leds.py` | status-LED demo (gpiozero) |
| `selftest.py` | offline end-to-end test (no Pi required) |

## Validation status (2026-08-27)

**Confirmed against real hardware:**

- The candle remote is captured and in the repo (`remotes/candles.ir`): NEC, address
  `0x00`, six buttons — `On` `0x45`, `Off` `0x47`, `Candle` `0x16`, `Light` `0x0D`,
  `Dim` `0x0C`, `Brighten` `0x5E`. All six re-encode to the captured bytes with valid
  NEC checksums.
- **The codes drive the real candles**, tested extensively at better than bench range.
  Capture → parse → encode → transmit is proven end to end; this is no longer a
  theoretical chain.
- **`Candle` = flicker mode, `Light` = steady.** Verified on the units, so the
  operator-facing descriptions in `DMX-CHART.md` are fact rather than inference.
- This remote has **no timer buttons**; the earlier `TIMER_4H`/`TIMER_8H` cues were
  placeholders and have been removed from every cue map.

**Not yet proven — the gate is still open:**

- The test was *not* at tower height with the real step geometry, so the **IR hit-rate
  test remains the go/no-go**. What it must still show: reliable triggering from ~8 ft
  up, aimed down, with candles at worst-case step positions and orientations, held at
  unpredictable angles by moving cast.
- The emitter head (6 emitters) is not mounted on the tower yet — planned for the
  weekend of **2026-08-29**.

## Open items to confirm

- **IR hit-rate result** — the gate for towers-only vs hybrid. Codes and candle response
  are proven; what is unproven is *range and aim from the tower*.
- **Emitter head current** — sets whether one AO3400A channel suffices or the array is
  split across J_IR0+J_IR1 (or an IRLB8721 substituted). Measure when the head is built.
- **Tower count** — one covers most stages; two adds redundancy and edge fill.
- **Held-look rates** — `max_hz` per channel in `config.candles.yaml` is still a guess;
  tune it against the choreography with `--watch` during the hit-rate test.
