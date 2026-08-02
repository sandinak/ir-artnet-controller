# Ansible — tower IR blaster deploy (standalone)

> **For the show fleet, use `sandinak/ansible-raspi-dmx` instead.** This repo is
> vendored there as the submodule `external/ir-artnet-controller`, and the
> `ir_artnet_tower` role deploys it alongside the steps, sceptres and routers:
>
> ```bash
> ansible-playbook playbooks/build/ir_towers.yml -e ir_artnet_allow_reboot=true
> ```
>
> That path is the canonical one. It carries the fleet's inventory (universes,
> addressing, WiFi), validates the whole cue map before touching a host, and
> installs into a venv — required on Raspberry Pi OS Bookworm/Trixie, which are
> PEP 668 externally-managed.
>
> What follows is the **standalone** deploy, kept for developing against a
> single Pi without the fleet inventory. It installs to the system Python and
> hardcodes the candle cue map in its template, so the two will drift; treat the
> fleet role as the source of truth for anything show-facing.

Provisions the tower Pis end-to-end: installs `ir-ctl`, deploys the `ir_artnet`
service + captured `.ir` codes, enables the `gpio-ir-tx` overlay in `config.txt`,
templates the per-tower config, installs/enables the systemd unit, and (optionally)
joins the show WiFi with a reserved IP for unicast Art-Net.

## Layout

```
ansible/
  playbook-tower-ir.yml         run this
  inventory.ini.example         → copy to inventory.ini
  requirements.yml              collections (community.general)
  group_vars/tower_ir.yml       shared vars (WiFi, tuning)
  host_vars/tower1.yml.example  → per-tower universe + IP
  roles/ir_artnet_tower/        the role
```

Paths assume this `ansible/` dir sits next to the `ir_artnet/` package and `remotes/`
(as in this repo). The role copies `../ir_artnet` and `../remotes` to the Pi.

## Use

```bash
ansible-galaxy collection install -r requirements.yml
cp inventory.ini.example inventory.ini            # set tower IPs
cp host_vars/tower1.yml.example host_vars/tower1.yml
# edit group_vars/tower_ir.yml for WiFi (put the PSK in a vault)

# First run enables the IR overlay, which needs a reboot:
ansible-playbook -i inventory.ini playbook-tower-ir.yml -e allow_reboot=true

# Subsequent config/code pushes (no reboot):
ansible-playbook -i inventory.ini playbook-tower-ir.yml
```

## What each var controls

| Var | Where | Purpose |
|-----|-------|---------|
| `artnet_universe` | host_vars | must match the QLC+ output universe for that tower |
| `tower_static_ip` / `tower_gateway` | host_vars | reserved IP so QLC+ unicasts reliably |
| `wifi_ssid` / `wifi_psk` | group_vars (vault the PSK) | join the show AP |
| `ir_gpio_pin` | group_vars | gpio-ir-tx pin → MOSFET gate (default 18) |
| `transmit_backend` | group_vars | `ir-ctl` (default) or `pigpio` |
| `candle_on_max_hz`, `candle_jitter_ms` | group_vars | held-look tuning (see `../DMX-CHART.md`) |
| `allow_reboot` | group_vars / `-e` | let Ansible reboot after enabling the overlay |

## Updating the candle codes

After capturing the real remote on the Flipper, replace `../remotes/candles.ir` in the
repo and re-run the playbook — it redeploys the `.ir` file and restarts the service.

## Notes

- The WiFi task uses `community.general.nmcli` (Raspberry Pi OS Bookworm uses
  NetworkManager). For an older wpa_supplicant image, swap in a `wpa_supplicant.conf`
  template instead.
- `boot_config_path` defaults to `/boot/firmware/config.txt` (Bookworm). Override to
  `/boot/config.txt` for older images.
- Bluetooth is **not** disabled here — towers use `gpio-ir-tx`, not the PL011 UART
  (that constraint is only for the Pixelblaze step units).
