# Contributing

Thanks for helping out. This project drives IR-remote devices (flameless candles,
projectors, etc.) from an Art-Net lighting desk via a Raspberry Pi.

## Dev setup

Everything runs on a laptop — no Pi, no GPIO, no `pigpio` required.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # runtime deps (PyYAML, pigpio)
pip install jinja2 ansible-core ansible-lint   # dev-only, for the Ansible checks

python selftest.py                       # offline end-to-end test
python -m py_compile ir_artnet/*.py selftest.py examples/*.py
python ansible/check-template.py         # renders the role's cue map, loads it
ansible-lint ansible/
```

Two workflows run on every push and PR:

| Workflow | What it guards |
|----------|----------------|
| `.github/workflows/selftest.yml` | `selftest.py` + the bench utilities, across Python 3.9–3.13 |
| `.github/workflows/ansible.yml` | `ansible-lint`, playbook syntax, and `check-template.py` |

## Ground rules

- **Keep `selftest.py` green.** It covers parsing, protocol encoders, the ArtDMX
  round-trip, all trigger modes (threshold / index / rate / selector — including the
  two-channel selector with no Rate or GO channel), the coalescing + priority queue,
  and `--gen-config`. Add a test with any behaviour change.
- **Cue maps are checked, not trusted.** `ansible/check-template.py` renders the role's
  config template and loads it with the real `Controller`, so a command referencing a
  signal that isn't in the `.ir` files fails CI rather than a show. If you change the
  template or `config.candles.yaml`, keep the two in step — the script asserts that too.
- **Timing assertions must tolerate a loaded CI runner.** Assert that something repeats,
  not that it repeated exactly N times, unless N is deterministic (a `count`).
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
python -m ir_artnet --gen-config --ir remotes/yourremote.ir --key yourremote \
    > config.yourremote.yaml
python -m ir_artnet --config config.yourremote.yaml --list   # verify it resolves
```

Keep the signal names the capture tool produced rather than renaming them — the configs
reference `file:signal` verbatim, so a re-capture should drop in without edits.

## Using this inside `ansible-raspi-dmx`

The Ansible role (`ansible/roles/ir_artnet_tower`) ships in this repo. To manage towers
from the fleet repo, add this project as a submodule and point at the role:

```bash
# in your ansible-raspi-dmx checkout
git submodule add git@github.com:sandinak/ir-artnet-controller.git \
    external/ir-artnet-controller
# then reference external/ir-artnet-controller/ansible/roles in roles_path
# (ansible.cfg), or import its playbook-tower-ir.yml
```

This keeps the daemon versioned here while the fleet repo pins a known-good commit.

The role in this repo is the **standalone** one: it installs into a venv exactly like
the fleet role, but hardcodes the candle cue map in its template instead of driving it
from inventory. For show machines use the fleet repo's `ir_artnet_tower` role, which is
the source of truth for anything show-facing.

## Layout

See `UNIFIED-DESIGN.md` for the architecture and the current **validation status** —
what has been proven against real hardware and what has not. `README.md` and
`DMX-CHART.md` cover operation, and hardware is in `hardware.md` with the
`*-layout.svg` / `schematic.svg` drawings.

Open hardware questions are tracked as GitHub issues rather than doc bullets; the
design doc links the decision each one gates.
