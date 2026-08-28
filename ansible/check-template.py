#!/usr/bin/env python3
"""Render the role's config template and load it with the real Controller.

Catches the failure that matters most: a cue map that references a signal which
isn't in the .ir files, or a mode/key the daemon doesn't understand. Without this
the mistake surfaces on a tower, at a load-in, in the dark.

    python3 ansible/check-template.py

Needs jinja2 + pyyaml (dev-only; not a runtime dependency of the daemon).
"""
import os
import sys

import jinja2
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLE = os.path.join(ROOT, "ansible", "roles", "ir_artnet_tower")
VAR_FILES = [
    os.path.join(ROLE, "defaults", "main.yml"),
    os.path.join(ROOT, "ansible", "group_vars", "tower_ir.yml"),
    os.path.join(ROOT, "ansible", "host_vars", "tower1.yml.example"),
]
TEMPLATE = os.path.join(ROLE, "templates", "config.candles.yaml.j2")


def main() -> int:
    variables = {}
    for path in VAR_FILES:
        variables.update(yaml.safe_load(open(path, encoding="utf-8")) or {})

    with open(TEMPLATE, encoding="utf-8") as fh:
        rendered = jinja2.Template(fh.read()).render(**variables)

    cfg = yaml.safe_load(rendered)
    print(f"rendered {os.path.relpath(TEMPLATE, ROOT)}: "
          f"{len(cfg['channels'])} channels, universe {cfg['artnet']['universe']}")

    # Resolve command references against the real .ir files, from the repo root
    # so the config's relative remotes/ paths work exactly as they do on a tower.
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    from ir_artnet.controller import Controller  # noqa: E402

    Controller(cfg)
    print("OK: every command reference resolves and every mode is valid")

    # The deployed cue map and the checked-in one must not drift apart.
    checked_in = yaml.safe_load(open(os.path.join(ROOT, "config.candles.yaml"),
                                     encoding="utf-8"))

    def shape(c):
        return [(e.get("channel"), e.get("mode"),
                 e.get("command") or e.get("table")) for e in c["channels"]]

    if shape(cfg) != shape(checked_in):
        print("FAIL: ansible template and config.candles.yaml have drifted apart",
              file=sys.stderr)
        return 1
    print("OK: ansible template matches config.candles.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
