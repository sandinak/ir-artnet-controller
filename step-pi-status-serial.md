# Step Pi — status LEDs + serial (student build)

> **Unified with the step redesign — read `UNIFIED-DESIGN.md` first.** This has been
> corrected to match the canonical design. Two important changes from an earlier draft:
> (1) the status LEDs are **red / yellow / green** on the **swappable universal pod**
> (connector **J_STAT**, JST-XH), not loose on-board LEDs; and (2) **GPIO14 belongs to
> the Pixelblaze** (PL011 UART @ 2 Mbaud), so the login console does **not** live on
> GPIO14/15. The primary console is an **off-HAT USB-TTL adapter / SSH**; an *optional*
> on-HAT debug port uses the spare **UART3** pins instead. See **steppi-layout.svg**.

This is still a good first solder project — short, straight runs and only the LEDs
are polarity-sensitive (we call that out clearly).

## What goes where

| Function | Pi GPIO | Header pin | Colour | Resistor | Lands on |
|----------|---------|-----------|--------|----------|----------|
| **Power / ready** | GPIO17 | 11 | green | 220 Ω | pod via **J_STAT** |
| **ArtNet activity** | GPIO27 | 13 | yellow | 220 Ω | pod via **J_STAT** |
| **Network / fault** | GPIO22 | 15 | red | 220 Ω | pod via **J_STAT** |
| Pod ground | GND | 20 | — | — | **J_STAT** pin 4 |
| Pixelblaze data (do **not** use for console) | GPIO14 | 8 | — | — | **J_PB** |
| *Optional* debug **TXD3** | GPIO4 | 7 | — | — | debug header |
| *Optional* debug **RXD3** | GPIO5 | 29 | — | — | debug header |
| Debug / pod ground | GND | 6 or 9 | — | — | — |

All three LEDs are now low-forward-voltage colours (red ~1.8 V, yellow ~2.0 V,
green ~2.1 V), so **one uniform 220 Ω** works for all of them — no special-case
resistor. (That's why the old "blue LED" note is gone; blue's high Vf was the only
reason a different value was ever needed.)

### LED meanings

- **Green — Power / ready:** solid once the Pi is booted and the service is running.
- **Yellow — ArtNet activity:** blinks on each received DMX frame for this unit.
- **Red — Network / fault:** lit when there is **no** network link (dark = healthy).
  Logic is set in software, so you can invert it if you prefer "red = connected."

## The status pod (J_STAT) — universal pod PCB

Per the fleet design, the LEDs live on the **swappable universal pod** — the same
bare PCB used for the IR emitter pods, just a different BOM. On the HAT you solder a
**4-pin JST-XH (J_STAT)**; the four conductors are the three LED GPIOs plus a shared
ground:

```
   J_STAT (JST-XH, 4-pin)      →  status pod
   1  GPIO17  (green / power)
   2  GPIO27  (yellow / activity)
   3  GPIO22  (red / network)
   4  GND
```

Each LED chain on the pod is `GPIO → 220 Ω → LED (long leg = +) → GND`. Keep the pin
order identical to the pod so any pod plugs into any HAT. For a quick bench test you
can solder the LEDs straight onto the Perma-Proto instead, but the shipping design
uses the connector so a pod can be swapped without touching the HAT.

## Parts (per step Pi)

| Part | Value / type | Qty | Notes |
|------|--------------|-----|-------|
| LED, 3 mm | red, yellow, green | 1 each | Standard-brightness through-hole |
| Resistor, ¼ W | 220 Ω | 3 | One value for all three |
| JST-XH header, 4-pin | J_STAT | 1 | To the status pod |
| JST-XH header, 3-pin | J_PB | 1 | Pixelblaze data (GPIO14 / 5V / GND) |
| Pin header, 3-pin (optional) | debug UART3 | 1 | Only if you want an on-HAT console port |
| Hook-up wire | 22–24 AWG solid | — | Point-to-point runs |

## Serial / console — the corrected story

**GPIO14/15 (PL011, `serial0`) is the Pixelblaze data line at 2 Mbaud** (Bluetooth
disabled to free it). The login console therefore cannot share it. Two ways to get a
console:

1. **Primary (canonical): off-HAT.** For day-to-day work, use **SSH over the network**.
   For low-level/no-network debugging, plug a **USB-TTL adapter into the Pi** (off the
   HAT) — nothing to solder on the HAT for this path. Set the adapter to **3.3 V**.

2. **Optional on-HAT debug port:** if you want a physical "port on the Perma-Proto,"
   solder a 3-pin header wired to the **spare UART3** — `GPIO4 (TXD3, pin 7)`,
   `GPIO5 (RXD3, pin 29)`, `GND`. This keeps Pixelblaze's GPIO14 untouched. Enable it
   with `dtoverlay=uart3` in `/boot/firmware/config.txt` (Ansible-managed), then use
   `/dev/ttyAMA1` at 115200 baud. Remember **TX crosses to RX** on the adapter.

> Do **not** enable the login console on `serial0` / GPIO14 — it will corrupt the
> Pixelblaze data stream. The fleet's `config.txt` (via Ansible) keeps `enable_uart`
> and the Pixelblaze overlay coordinated; don't hand-edit it on one unit.

## Driving the status LEDs from software

`gpiozero` is the friendliest option (`sudo apt install python3-gpiozero`). A minimal
demo lives at **examples/status_leds.py**:

```python
from gpiozero import LED
power   = LED(17)   # green  — pin 11 — solid when up
artnet  = LED(27)   # yellow — pin 13 — blink on data
network = LED(22)   # red    — pin 15 — ON when link is DOWN
```

Once wiring is proven, the same three `LED()` objects wire into the ArtNet service:
`power.on()` at start, `artnet.blink()` per received frame, `network` driven from the
default-route check.

## Quick build checklist

1. Solder the 2×20 header onto the HAT first (everything references it).
2. Solder **J_STAT** (4-pin JST-XH) and **J_PB** (3-pin JST-XH).
3. Build the status pod: three `GPIO → 220 Ω → LED (long leg to resistor) → GND`
   chains; wire the pod's 4-pin plug to match J_STAT.
4. Leave GPIO14 for Pixelblaze — do **not** wire a console there.
5. (Optional) solder the 3-pin UART3 debug header on GPIO4 / GPIO5 / GND.
6. Power up, run `status_leds.py`: green solid, yellow blinking, red off when the
   network is up (pull the Ethernet to confirm red comes on).
7. Console when needed: SSH, or a 3.3 V USB-TTL adapter (off-HAT, or the UART3 port).
