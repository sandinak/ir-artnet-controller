"""Service entry point:  python3 -m ir_artnet --config config.yaml

Also supports a couple of bench utilities that need no ArtNet source:

    python3 -m ir_artnet --config config.yaml --list
        Print every command the config can fire (file:signal names).

    python3 -m ir_artnet --config config.yaml --send projector:Power
        Transmit one command immediately (cabling / aim test).

    python3 -m ir_artnet --config config.yaml --dump projector:Power
        Print the decoded mark/space timings without transmitting.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

import yaml

from .artnet import ArtNetReceiver
from .controller import Controller, TxJob


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg["_base_dir"] = os.path.dirname(os.path.abspath(path))
    return cfg


def _watch_loop(ctrl, rx, log):
    """Live troubleshooting view: incoming DMX, computed per-channel action, tx
    stats. Refreshes twice a second. Ctrl-C to quit."""
    print(f"Watching Art-Net universe {ctrl.universe}. Ctrl-C to stop.\n"
          f"(If every channel shows v=0, the desk isn't reaching this box:"
          f" check universe, IP/unicast, and WiFi.)\n")
    try:
        while True:
            time.sleep(0.5)
            dmx = getattr(ctrl.cmap, "_latest", b"")
            s = ctrl.txq.stats
            hdr = (f"[tx {s['sent']}  drop {s['dropped']}  "
                   f"coal {s['coalesced']}  preempt {s['preempted']}]  "
                   f"frame={len(dmx)}B")
            print(hdr)
            for ch, desc in ctrl.cmap.snapshot(dmx):
                print(f"   ch{ch:<3} {desc}")
            print("-" * 60)
    except KeyboardInterrupt:
        pass
    finally:
        rx.stop()
        ctrl.close()
    return 0


def _gen_selector_config(ir_path, key=None, base=1, universe=0):
    """Build a selector-mode config from a Flipper .ir file: DMX value N -> the
    Nth captured code (file order). Returns (config_dict, value_map)."""
    from .flipper import parse_file
    sigs = parse_file(ir_path)               # ordered dict, file order preserved
    key = key or os.path.splitext(os.path.basename(ir_path))[0]
    value_map = {i: f"{key}:{name}" for i, name in enumerate(sigs.keys(), start=1)}
    cfg = {
        "artnet": {"universe": universe, "bind_ip": "0.0.0.0",
                   "short_name": "Tower-IR", "long_name": "Tower IR blaster"},
        "transmitter": {"backend": "ir-ctl", "lirc_device": "/dev/lirc0",
                        "gpio_pin": 18, "min_gap_ms": 60},
        "ir_files": {key: ir_path},
        "channels": [{
            "mode": "selector",
            "name": "IR",
            "select_channel": base,          # value 0=none, 1..N = code
            "rate_channel": base + 1,        # how often (Hz curve)
            "count_channel": base + 2,       # 0=continuous, N=send N times
            "go_channel": base + 3,          # rising edge / hold = execute
            "min_hz": 1.0, "max_hz": 8.0, "floor": 1, "repeats": 1,
            "table": value_map,
        }],
    }
    return cfg, value_map


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ir_artnet")
    ap.add_argument("--config", help="config YAML (not needed for --gen-config)")
    ap.add_argument("--list", action="store_true", help="list all loadable commands")
    ap.add_argument("--send", metavar="file:signal", help="transmit one command and exit")
    ap.add_argument("--dump", metavar="file:signal", help="print decoded timings and exit")
    ap.add_argument("--watch", action="store_true",
                    help="live monitor: show incoming DMX + per-channel action + tx stats")
    ap.add_argument("--gen-config", action="store_true",
                    help="generate a selector config from an .ir file (with --ir) and print it")
    ap.add_argument("--ir", metavar="PATH", help="the .ir file to read for --gen-config")
    ap.add_argument("--key", help="ir_files key for --gen-config (default: .ir filename)")
    ap.add_argument("--base-channel", type=int, default=1,
                    help="first DMX channel of the selector block (--gen-config)")
    ap.add_argument("--universe", type=int, default=0, help="Art-Net universe (--gen-config)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("ir_artnet")

    # --- generate a selector config straight from a Flipper capture ---------
    if args.gen_config:
        if not args.ir:
            ap.error("--gen-config requires --ir PATH")
        cfg, vmap = _gen_selector_config(
            args.ir, key=args.key, base=args.base_channel, universe=args.universe)
        b = args.base_channel
        print(f"# Generated from {args.ir} — selector (fixture) personality.")
        print(f"# DMX ch{b}=Select(0=none 1..{len(vmap)}=code)  "
              f"ch{b+1}=Rate  ch{b+2}=Count(0=cont)  ch{b+3}=GO")
        print("# Value -> code map:")
        for v, cmd in vmap.items():
            print(f"#   {v:>3} = {cmd}")
        print(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))
        return 0

    if not args.config:
        ap.error("--config is required")

    cfg = load_config(args.config)
    ctrl = Controller(cfg)

    if args.list:
        for fkey in sorted(ctrl.libs):
            for name in sorted(ctrl.libs[fkey]):
                print(f"{fkey}:{name}")
        return 0

    if args.dump:
        fkey, name = args.dump.split(":", 1)
        sig = ctrl.libs[fkey][name]
        print(f"# {args.dump}: {sig.frequency} Hz, duty {sig.duty_cycle}, "
              f"{len(sig.timings)} edges ({sig.source_type})")
        print(" ".join(str(t) for t in sig.timings))
        return 0

    if args.send:
        ctrl.txq.start()
        fkey, name = args.send.split(":", 1)
        ctrl.txq.submit(TxJob(ctrl.libs[fkey][name], 1, args.send))
        time.sleep(1.0)
        ctrl.close()
        return 0

    # --- normal service mode -------------------------------------------------
    ctrl.start()
    rx = ArtNetReceiver(
        on_dmx=ctrl.on_dmx,
        bind_ip=cfg.get("artnet", {}).get("bind_ip", "0.0.0.0"),
        short_name=cfg.get("artnet", {}).get("short_name", "IR-Blaster"),
        long_name=cfg.get("artnet", {}).get("long_name", "ArtNet IR Blaster"),
    )
    rx.start()
    log.info("listening for Art-Net on universe %d", ctrl.universe)

    if args.watch:
        return _watch_loop(ctrl, rx, log)

    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    try:
        while not stop["flag"]:
            time.sleep(0.25)
    finally:
        log.info("shutting down")
        rx.stop()
        ctrl.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
