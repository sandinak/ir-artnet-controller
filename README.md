# ArtNet IR Blaster (Raspberry Pi)

> **Part of a larger design — see `UNIFIED-DESIGN.md`.** This is the **tower blaster**
> role. It transmits via the fleet-standard **`gpio-ir-tx` + `ir-ctl`** path by default
> (pigpio is a selectable fallback). Step-unit electronics are in
> `step-pi-status-serial.md`.

Consolidate the IR capability from 12 step fixtures into one or two high-power
blaster heads in the front towers, driven straight from the lighting desk over
**Art-Net**. Each DMX channel is mapped to a remote-control command you captured
on a **Flipper Zero**; the Pi replays it through a GPIO-driven IR LED array.

```
Lighting desk ──Art-Net/DMX──▶  Raspberry Pi  ──GPIO 38 kHz──▶  MOSFET ──▶ IR LED array
 (channel N = a         (this software)                                    (tower blaster)
  remote button)
```

## How it works

1. **ArtNet receiver** (`artnet.py`) listens on UDP 6454, decodes ArtDMX frames
   for your configured universe, and hands the 512 channel values to the controller.
2. **Channel map** (`controller.py`) watches the channels you care about and, on the
   right kind of change, fires a mapped IR command. Three trigger modes:
   - **threshold** — fire once when the channel rises past a level (a desk "button").
   - **index** — one channel's value selects a command from a table; a second `go`
     channel fires it. Dozens of commands on two channels.
   - **rate** — while the channel is up, repeat the command at a value-scaled rate
     (hold-to-repeat, e.g. volume/dimmer).
3. **IR library** (`flipper.py` + `protocols.py`) loads your Flipper `.ir` files.
   Both `raw` captures and `parsed` protocols (NEC, NECext, Samsung32, Sony SIRC,
   RC5) are normalised to raw mark/space timings.
4. **Transmitter** (`transmitter.py`) uses **pigpio** to generate the carrier and
   envelope in DMA hardware — microsecond-accurate, any carrier frequency — so a
   captured signal replays verbatim.

A single worker thread serialises all IR shots so the shared LED bus is never
double-driven, with a configurable minimum gap between shots.

See **hardware.md** for the schematic, parts list, and recommended HAT.

## Install (on the Pi)

> **Fleet deploy:** this repo is vendored into `sandinak/ansible-raspi-dmx` as
> `external/ir-artnet-controller`, and the `ir_artnet_tower` role there does all
> of the below (in a venv, with the cue map driven from inventory). Use
> `ansible-playbook playbooks/build/ir_towers.yml` for show machines. The manual
> steps here are for a bench Pi.

Tested on Raspberry Pi OS Bookworm and Trixie.

```bash
sudo apt update
sudo apt install -y v4l-utils python3-yaml     # v4l-utils provides ir-ctl

# enable the IR TX device (Ansible manages this in the fleet):
echo 'dtoverlay=gpio-ir-tx,gpio_pin=18' | sudo tee -a /boot/firmware/config.txt
sudo reboot                                    # creates /dev/lirc0

sudo mkdir -p /opt/ir-artnet
sudo cp -r ir_artnet config.yaml remotes /opt/ir-artnet/
```

> Fallback backend: to use pigpio instead of `ir-ctl`, set
> `transmitter.backend: pigpio` in `config.yaml`, `sudo apt install pigpio
> python3-pigpio`, and `sudo systemctl enable --now pigpiod`.

Wire the driver stage per **hardware.md** (GPIO18 → MOSFET → LED array).

## Capture your remotes on the Flipper

For each button you need on stage:

1. Flipper → **Infrared → Learn New Remote**, capture the button.
2. Give it a clear name (`Power`, `Input_HDMI1`, `Fog_High`, …).
3. Save. This appends a block to a `.ir` file under `infrared/` on the SD card.

Copy the `.ir` files into `remotes/` and point `config.yaml` at them.

> **Tip:** if a button uses a protocol not in the native list, just capture it as
> **RAW** on the Flipper (hold the button during capture). RAW always replays
> verbatim — no protocol support needed.

## Configure

Edit `config.yaml`. DMX channel numbers are 1-based, like your desk. Example:

```yaml
artnet:
  universe: 0
transmitter:
  gpio_pin: 18
ir_files:
  projector: "remotes/projector.ir"
channels:
  - channel: 1
    mode: threshold
    threshold: 128
    command: "projector:Power"
```

Commands are referenced as `<file-key>:<signal-name>`.

## Run

```bash
# list every command your config can fire
python3 -m ir_artnet --config config.yaml --list

# transmit one command now (aim / cabling test)
python3 -m ir_artnet --config config.yaml --send projector:Power

# print decoded timings without transmitting
python3 -m ir_artnet --config config.yaml --dump projector:Power

# GENERATE a selector/fixture config straight from a Flipper capture
python3 -m ir_artnet --gen-config --ir remotes/candles.ir --key candles > config.candles-selector.yaml

# LIVE DEBUG MONITOR — incoming DMX, per-channel action, tx stats (great for testing)
python3 -m ir_artnet --config config.yaml --watch

# run the service in the foreground (add -v for per-shot DEBUG logging)
python3 -m ir_artnet --config config.yaml -v
```

### Troubleshooting with `--watch`

`--watch` prints a refreshing table of every configured channel with its current
DMX value and the action the daemon computes from it, plus transmit counters:

```
[tx 12  drop 0  coal 34  preempt 1]  frame=512B
   ch1   rate candles:ON v=200 @3.80Hz
   ch2   rate candles:OFF v=0 off PRIO
   ch10  idx sel=0->— GO[11]=0
```

- **All channels `v=0`** → the desk isn't reaching this box: check universe number,
  the tower's IP / unicast target, and WiFi.
- **`v=` moves but no `TX` in `-v` logs** → threshold not crossed or below `floor`.
- **`drop` climbing** → the queue is saturating; lower `max_hz`/`repeats` or raise
  `min_gap_ms`.
- **`coal` climbing** is normal for held looks — stale repeats collapsing (good).
- **`TX failed … /dev/lirc0`** → the `gpio-ir-tx` overlay isn't active; reboot after
  enabling it (or the Ansible role hasn't rebooted yet).

### Run as a service

```bash
sudo cp ir-artnet.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ir-artnet
journalctl -u ir-artnet -f
```

## Test without a Pi

The decoding, mapping, and trigger logic all run on any machine (no pigpio needed):

```bash
pip install pyyaml
python3 selftest.py
```

This parses the sample remotes, checks the protocol encoders, round-trips an
ArtDMX packet, and drives the full pipeline through a fake transmitter to confirm
threshold / index / rate all fire correctly.

## Files

```
ir_artnet/
  __main__.py     service entry point + bench utilities (--send/--dump/--list)
  artnet.py       Art-Net (ArtDMX) UDP receiver + ArtPollReply
  controller.py   channel→command mapping, trigger modes, transmit queue
  flipper.py      parse Flipper .ir files
  protocols.py    NEC / NECext / Samsung32 / SIRC / RC5 → raw timings
  transmitter.py  pigpio carrier-modulated IR output
config.yaml       your mappings
remotes/*.ir      captured remotes
hardware.md       schematic, BOM, HAT recommendation
ir-artnet.service systemd unit
selftest.py       offline end-to-end test
```

## Notes & limits

- **One universe per instance.** Run multiple instances (different config) if you
  need several universes.
- **pigpio waves** are chunked/chained automatically for long raw captures.
- The Pi must share the lighting network (wired Cat5e recommended for timing).
- IR is line-of-sight-ish; aim the tower heads at the fixtures and expect bounce
  off the deck to help fill in.
