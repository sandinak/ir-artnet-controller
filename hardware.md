# Hardware — ArtNet IR Blaster driver stage (tower role)

> **Unified with the step redesign — read `UNIFIED-DESIGN.md` first.** This is the
> **tower blaster** role. It reuses the project's custom Pi HAT PCB with a
> *different population*: the IR driver channels (**AO3400A** low-side FETs on
> **J_IR0 / J_IR1**, JST-XH) are populated here and drive the tower emitter head,
> while the Pixelblaze/pixel parts are left unpopulated (the reverse of a step
> unit). IR is transmitted through the fleet-standard **`gpio-ir-tx` + `ir-ctl`**
> path (`dtoverlay=gpio-ir-tx`, Ansible-managed). The discrete Perma-Proto build
> below is the **hand-soldered prototype stage** of that HAT — same circuit, before
> the JLCPCB run.


This is the electronics that sits between the Pi's GPIO pin and the high-power
IR LED array in each front tower. It's a textbook **low-side N-channel MOSFET
switch**: the Pi's 3.3 V GPIO carrier turns a logic-level FET on and off at 38 kHz,
and the FET switches the LED string current from a separate 12 V supply. No PCB
fabrication is required — everything below builds on a proto/terminal HAT with
point-to-point soldering or screw terminals.

## What we're controlling

The targets are **commercial IR-remote flameless candles** (the Amazon board is
the candles' own driver, not something we wire into). The Pi's job is simply to
**broadcast the candle remote's captured commands** across the stage so the candles
on all 12 steps react together. The "load" in this document is therefore *our own*
IR emitter array on the tower head — the thing that shines 830–850 nm IR at the
candles. We build one self-contained blaster (Pi + driver + emitter head) **per
tower** so a single failure never dark the whole rig.

> **Candle-receiver reality check:** flameless-candle IR receivers are cheap and
> tuned for short indoor range, so they're *less* sensitive than AV gear. Favour
> more emitters per head, aim the head down at the steps from the top of the tower
> (~8 ft is fine — height helps throw and bounce), and confirm range with the
> `--send` bench test before the show. 830 nm and 850 nm both fall well inside a
> candle receiver's response curve, so either wavelength works; 850 nm parts are
> just easier to source at high power.

## Recommended HAT (no fabrication)

You asked for a HAT you can build the circuit on without etching a board. Two good
routes, pick based on how much you want to solder:

**Option A — Proto HAT with a soldered perfboard area (recommended).**
The circuit is only ~5 components, so a proto HAT gives you a rock-solid, vibration-proof
build for touring.

- **Adafruit Perma-Proto HAT for Pi Mini Kit – No EEPROM** (Adafruit #2310, ~$5). Plated
  perfboard laid out as a breadboard, brings up all 40 GPIO. Solder Q1/R1/R2 here and
  run the LED-array feed out to a screw terminal.
- Add a **2- or 3-position 5 mm screw terminal block** (e.g. Phoenix/generic, ~$1) soldered
  at the board edge for the LED-array + and – wires so tower cabling is tool-less.

**Option B — Screw-terminal breakout HAT (minimal soldering).**
If you'd rather not solder to a perfboard at all, use a GPIO screw-terminal HAT and mount
Q1 on a small MOSFET carrier.

- **GPIO screw-terminal breakout HAT** (52Pi "GPIO Terminal Block" / Electronics-Salon RPi
  T-block HAT, ~$10). Every GPIO and power pin lands on a labeled screw terminal.
- Drive a **logic-level MOSFET module** from GPIO18 (see note on modules below). Wire
  GPIO18 → module signal, GND → module GND, and the 12 V + LED array to the module's
  power screw terminals. Zero soldering.

> ⚠️ **MOSFET-module caveat:** the ubiquitous blue "IRF520 MOSFET module" is **not**
> truly logic-level — the IRF520 needs ~4.5 V on the gate to fully turn on and will run
> hot and dim when driven from 3.3 V, especially switching at 38 kHz. If you go the module
> route, use one built on a genuine logic-level FET (AO3400, IRLZ44N, IRL540N) or just
> solder a discrete IRLB8721 per Option A. This one part choice is what makes or breaks
> range and reliability.

## Bill of materials (per tower / per Pi)

You'll build **one complete blaster per tower** (Pi + driver + emitter head) for
redundancy — two towers = two identical kits. Quantities below are for **one** tower:
one driver stage + one blaster head of ~6–8 LEDs.

### Control / driver stage

| Ref | Part | Value / P/N | Qty | ~Price | Notes |
|-----|------|-------------|-----|--------|-------|
| — | Raspberry Pi | Pi 4 / Pi 3B+ / Pi Zero 2 W | 1 | $15–55 | Any Pi with a 40-pin header; needs wired or Wi-Fi Ethernet for ArtNet |
| — | Proto/terminal HAT | Adafruit #2310 **or** GPIO screw-terminal HAT | 1 | $5–10 | See "Recommended HAT" |
| Q1 | Logic-level N-MOSFET | **AO3400A** (SOT-23) — fleet-standard part | 1–2 | $0.15 | Same FET as the step HAT's IR channels. ~1–1.5 A practical per SOT-23; split a big tower array across **both** IR channels (J_IR0+J_IR1), or sub **IRLB8721** (TO-220) if one channel must carry >2 A |
| R1 | Resistor | 220 Ω, ¼ W | 1 | $0.10 | Gate series (limits GPIO inrush) |
| R2 | Resistor | 10 kΩ, ¼ W | 1 | $0.10 | Gate pulldown (LED off during boot/reset) |
| — | Screw terminal | 5 mm pitch, **4-position** | 1 | $1 | Four wires leave the HAT: `12V+`, `12V–`, `LED+`, `LED–` (matches the solder table below). Two 2-pos blocks side by side also work |
| — | Flyback/decoupling | 100 µF electrolytic + 0.1 µF across 12 V | 1 ea | $0.50 | Stiffens the rail against 38 kHz switching |

### IR LED blaster head (per tower)

Aim for **6–8 high-power 850 nm emitters** per head, wired as parallel strings of 2 in
series (see math below). 850 nm is what almost all consumer/AV IR receivers expect; use
940 nm only if your target gear specifically wants it.

| Ref | Part | Value / P/N | Qty | ~Price | Notes |
|-----|------|-------------|-----|--------|-------|
| D1–Dn | High-power IR LED | **Osram SFH 4715AS** (850 nm, 1 A, ~½ W) — or any 830–850 nm high-power emitter | 6–8 | $1–2 ea | Very high radiant intensity; great throw to the candles. 830/850 nm both fine |
| R3… | Current-limit resistor | 8.2 Ω, **5 W** (one per 2-LED string) | 3–4 | $0.30 ea | Or replace all with one CC driver (below) |
| — | *(optional)* Constant-current LED driver | e.g. Recom RCD-24 or Mean Well LDD-700H (700 mA) | 1 | $8–12 | Cleaner than resistors, no wasted heat, steady output as Vf drifts |
| — | Heatsink / aluminum bar | small, for the LED cluster | 1 | $2–5 | SFH 4715AS runs warm at 1 A; mount on metal |
| — | 12 V PSU | ≥ 3 A per tower (LED load + margin) | 1 | $10 | Separate from the Pi's 5 V supply; **common ground** |

### Wiring

| Item | Spec | Notes |
|------|------|-------|
| Pi → gate | any hookup wire | GPIO18 → R1 → Q1 gate |
| LED feed to tower | 18 AWG 2-conductor | carries the array current from Q1 drain / 12 V |
| Ethernet | Cat5e | ArtNet from the lighting network to the Pi |

### Pi host kit (easy to forget — one per tower)

| Item | Spec | Qty | ~Price | Notes |
|------|------|-----|--------|-------|
| microSD card | 16–32 GB, **A1/A2 rated** | 1 | $6–10 | Raspberry Pi OS Lite (Bookworm or Trixie). A1/A2 endurance matters for a box that gets power-cut at strike |
| Pi power supply | official 5 V, 3 A USB-C (Pi 4/5) or 2.5 A micro-USB (3B+) | 1 | $8–10 | **Separate from the 12 V LED supply.** Undervolt warnings = dropped Art-Net frames |
| Case / enclosure | vented, with header cutout for the HAT | 1 | $8–15 | Must clear the HAT and the screw terminal |
| HAT standoffs | M2.5 × 11 mm, nylon or brass | 4 | $2 | Stops the HAT flexing on the header under road vibration |
| 40-pin header | stacking, 2×20, **only if** the HAT doesn't ship with one | 0–1 | $2 | Adafruit #2310 includes one |

### Connectors & protection

| Item | Spec | Qty | ~Price | Notes |
|------|------|-----|--------|-------|
| JST-XH 4-pin | header + crimp housing (**J_IR0** / **J_IR1**) | 1–2 | $0.50 | **Fleet standard** — matches the custom HAT and the step units. Use these instead of the screw terminal if you are building toward the production board |
| JST-XH 4-pin | header + crimp housing (**J_STAT**) | 0–1 | $0.50 | Optional status pod (see [step-pi-status-serial.md](step-pi-status-serial.md)) |
| Inline fuse + holder | 3 A slow-blow, 5×20 mm or blade | 1 | $2 | On the **12 V+** feed, before the HAT. A shorted emitter head otherwise draws whatever the PSU can deliver |
| Ferrules / heatshrink | 18 AWG ferrules, assorted heatshrink | — | $3 | Stranded wire into screw terminals wants ferrules; every solder joint at the head wants shrink |
| Strain relief | cable gland or P-clip at the head | 1 | $1 | The head cable is the thing that will get snagged |

### Mechanical (emitter head)

| Item | Spec | Qty | ~Price | Notes |
|------|------|-----|--------|-------|
| Head substrate | small aluminium bar / PCB blank, ~50 × 25 mm | 1 | $2–5 | Doubles as the heatsink for the LED cluster |
| Mount | ball-head or Manfredi-style clamp to the tower truss | 1 | $8–15 | **Aim is everything** — you want to re-aim it after the hit-rate test without rebuilding |
| Thermal pad / epoxy | thermally conductive | — | $3 | Bonds the SFH 4715AS pads to the bar |

### Cost roll-up (one tower, excluding the Pi)

| Group | ~Cost |
|-------|-------|
| Driver stage (HAT, Q1, R1/R2, terminal, caps) | $8–13 |
| Emitter head (8 × SFH 4715AS, resistors or CC driver, heatsink) | $20–30 |
| 12 V PSU + fuse + cable | $14–18 |
| Pi host kit (SD, 5 V PSU, case, standoffs) | $24–37 |
| Mount + mechanical | $13–23 |
| **Total per tower, without the Pi** | **≈ $80–120** |
| Raspberry Pi | $15–55 |

Two towers = two identical kits. Build both; the redundancy is the whole point of
one-blaster-per-tower.

### Bench-build checklist

Everything you need on the table before you start soldering:

- [ ] Pi flashed with Raspberry Pi OS Lite, SSH enabled, on the show network
- [ ] Proto HAT, Q1 (AO3400A or IRLB8721), R1 220 Ω, R2 10 kΩ, 100 µF + 0.1 µF
- [ ] 4-position screw terminal (or JST-XH headers if building to the fleet standard)
- [ ] 6–8 × SFH 4715AS on the heatsink bar, 3–4 × 8.2 Ω 5 W (or one LDD-700H)
- [ ] 12 V PSU ≥ 3 A, 3 A inline fuse, 18 AWG 2-conductor to the head
- [ ] Soldering iron, multimeter, **and a phone camera** (to see the IR firing)
- [ ] The candle remote captured on the Flipper as `remotes/candles.ir`


## Pi-HAT solder layout

See **hat-layout.svg** for the top-down placement/soldering diagram. Only three
components live on the HAT — Q1, R1, R2 — plus a 4-position screw terminal at the
board edge for the tower cabling. The per-string current-limit resistors (R3) live
out at the emitter head, not on the HAT.

Solder connections (all point-to-point on the proto field):

| # | From | To | Wire |
|---|------|-----|------|
| 1 | Header **pin 12 (GPIO18)** | R1 pad A | signal |
| 2 | R1 pad B | Q1 **gate** | signal |
| 3 | Q1 **gate** | R2 pad A | — |
| 4 | R2 pad B | GND rail (blue) | — |
| 5 | Header **pin 14 (GND)** | GND rail (blue) | black |
| 6 | Q1 **source** | GND rail (blue) | black |
| 7 | Q1 **drain** | screw terminal **LED–** | — |
| 8 | Screw **12V+** | +12 V rail (red) | red |
| 9 | Screw **12V–** | GND rail (blue) | black |
| 10 | Screw **LED+** | +12 V rail (red) | red |

Screw-terminal outward cabling: **12V+/12V–** → the 12 V PSU; **LED+/LED–** → the
emitter head (2-conductor to the tower top). Keep the Pi on its own 5 V USB supply;
the 12 V PSU only feeds the emitters. **One common ground** ties Pi GND, Q1 source,
and the PSU return together — this is mandatory or the FET won't switch.

## LED array design (the numbers)

Low-side switching means Q1 sits between the LED string and ground; the string runs from
+12 V through a current-limit resistor down into Q1's drain.

Using the **SFH 4715AS** (Vf ≈ 3.5 V at 1 A) on a **12 V** rail, put **2 LEDs in series**
per string (2 × 3.5 V = 7 V), leaving ~5 V across the resistor:

```
R = (Vsupply − 2·Vf) / I = (12 − 7) / 0.7 A ≈ 7.1 Ω  → use 8.2 Ω
P = I²·R = 0.7² × 8.2 ≈ 4 W  → use a 5 W resistor
```

Run each string at ~0.7 A for a comfortable thermal margin (the part is rated 1 A
continuous, more when pulsed). Parallel **3–4 of these strings** per tower head for
6–8 emitters and a wide, bright cone. Because the IR carrier is only on during "marks"
(and only ~33 % duty even then), average power is well below the continuous worst case —
but size R3 and the PSU for the continuous case to be safe.

**Prefer a constant-current driver** (the optional LDD-700H/RCD line) if you want to skip
the hot 5 W resistors and keep output rock-steady as the LEDs warm up — feed the whole
series string from it and drop the per-string resistor.

## Assembly steps

1. Solder Q1, R1, R2 and the decoupling caps onto the proto HAT (Option A), or land them
   on the screw-terminal HAT + FET module (Option B).
2. Wire **GPIO18 → R1 → Q1 gate**, and **R2 from gate to GND**.
3. **Q1 source → Pi GND** (and to the 12 V return — one common ground; this is essential).
4. Build the LED head: 2-in-series strings, each with its R3 (or one CC driver), all
   strings in parallel. Cathode end of each string ties together → **Q1 drain**. Anode
   end → **+12 V**.
5. Run the 12 V PSU's + and – to the head and to the common ground. Keep the Pi on its own
   5 V supply.
6. Mount the LED head on a heatsink in the tower, aimed back at the step fixtures.

## Bench test before hanging it

```bash
# Default backend (ir-ctl): needs the gpio-ir-tx overlay enabled and a reboot,
# so that /dev/lirc0 exists.  Confirm first:
ls -l /dev/lirc0

python3 -m ir_artnet --config config.candles.yaml --send candles:On

# If you switched transmitter.backend to pigpio, start pigpiod instead:
#   sudo systemctl enable --now pigpiod
```

Point a phone camera at the LEDs — most phone cameras see 850 nm as a faint purple/white
flicker, so you can confirm the array is firing before you ever touch the lighting desk.

## Why this topology (quick rationale)

Coverage across a stage is a **power/optics** problem, not a protocol one — a USB blaster
or a single-LED HAT can't throw far enough. Driving a MOSFET from one GPIO lets you hang
as many high-power emitters as your 12 V supply can feed and aim them from the towers, so
one or two heads replace IR hardware on all 12 steps. The carrier itself comes from the
kernel's **`gpio-ir-tx`** driver via `ir-ctl` — the fleet-standard path, managed by
Ansible like every other overlay. Flipper **raw** captures replay verbatim through it,
and the **pigpio** backend stays available as a fallback for the rare capture that needs
a non-38 kHz carrier or sub-microsecond envelope control (`transmitter.backend: pigpio`).
