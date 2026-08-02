# Contributing

Thanks for helping out. This project drives IR-remote devices (flameless candles,
projectors, etc.) from an Art-Net lighting desk via a Raspberry Pi.

## Dev setup

```bash
pip install pyyaml            # only runtime dep for the tests
python selftest.py            # offline end-to-end test — no Pi, no pigpio needed
python -m py_compile ir_artnet/*.py selftest.py
```

CI (`.github/workflows/selftest.yml`) runs the same on every push/PR.

## Ground rules

- **Keep `selftest.py` green.** It covers parsing, protocol encoders, the ArtDMX
  round-trip, all trigger modes (threshold / index / rate / selector), the
  coalescing + priority queue, and `--gen-config`. Add a test with any behaviour change.
- **No hard Pi dependency at import time.** `pigpio` and `ir-ctl` are optional; the
  code must import and run in "dry mode" on a laptop so the tests work anywhere.
- **One code transmits at a time.** All sends funnel through `TransmitQueue`
  (single worker, min-gap, coalescing, priority). Don't call the transmitter directly.
- Prefer config-driven behaviour over new code paths where practical.

## Adding remote codes

Capture on a Flipper Zero (or any tool that writes Flipper `.ir` / raw format), drop
the file in `remotes/`, then either reference `file:signal` in a config or regenerate
a selector map:

```bash
python -m ir_artnet --gen-config --ir remotes/yourremote.ir --key yourremote > config.yaml
```

## Using this inside `ansible-raspi-dmx`

The Ansible role (`ansible/roles/ir_artnet_tower`) ships in this repo. To manage towers
from the fleet repo, add this project as a submodule and point at the role:

```bash
# in your ansible-raspi-dmx checkout
git submodule add git@github.com:sandinak/ir-artnet-blaster.git vendor/ir-artnet-blaster
# then reference vendor/ir-artnet-blaster/ansible/roles in roles_path (ansible.cfg),
# or import vendor/ir-artnet-blaster/ansible/playbook-tower-ir.yml
```

This keeps the daemon versioned here while the fleet repo pins a known-good commit.

## Layout

See `UNIFIED-DESIGN.md` for the architecture and `README.md` / `DMX-CHART.md` for
operation. Hardware is in `hardware.md` and the `*-layout.svg` / `schematic.svg` files.
