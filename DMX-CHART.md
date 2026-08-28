# DMX chart — tower IR blaster (candles)

The desk patch for the tower IR controller, and how a button on the physical candle
remote becomes a DMX channel. Implemented by `config.candles.yaml` driving the
`ir_artnet` daemon.

## How a Flipper capture becomes a DMX channel

Four hops, remote → desk:

```
  1. CAPTURE   Flipper Zero learns a remote button   →  a named signal in a .ir file
                 (e.g. the "On" button              →  name: On  in remotes/candles.ir)

  2. NAME      You keep that signal name             →  "candles:On"
                 (file key "candles" + signal "On")     (file key set in ir_files:)

  3. MAP       config.candles.yaml ties a DMX         →  channel 1, mode threshold,
                 channel + trigger to that command        command: "candles:On"

  4. FIRE      Desk pushes channel 1 past 50%         →  daemon transmits ON via
                                                          ir-ctl / gpio-ir-tx on GPIO18
```

So the **only thing that must match** is the signal **name**: whatever you call a
button when you capture it on the Flipper must be the same name after the `:` in the
config. Everything else (DMX channel, trigger style, burst count) is just config.

### Capture procedure (do this once, with the real remote)

1. Flipper → **Infrared → Learn New Remote**; press a candle-remote button.
2. Name it clearly (`On`, `Off`, `Candle`, `Light`, `Dim`, `Brighten` — the names the
   shipped capture uses). The chart's *Command* column follows the capture, so if you
   name a button differently, change it in `config.candles.yaml` to match.
3. Save; repeat for every button you want on the desk.
4. Copy the Flipper's `.ir` into `remotes/candles.ir`, keeping the button names. If a
   button won't decode, capture it as **RAW** — it still transmits.
5. Verify: `python3 -m ir_artnet --config config.candles.yaml --list` should print
   `candles:On`, `candles:Off`, … Then bench-test one: `--send candles:On`.

## The patch chart

Universe **0** (set `artnet.universe` to the tower's universe). Channels are 1-based.

| DMX ch | Function | Mode | Held level does… | Command |
|:------:|----------|------|------------------|---------|
| **1** | All candles **On** | rate (held look) | value scales resend rate: ~3% = 0.5×/s … full = 6×/s | `candles:On` |
| **2** | All candles **Off** | rate (held look) | hold during blackout so late-turning candles still go dark | `candles:Off` |
| **3** | **Candle** (flicker mode) | rate (held look) | hold the flicker look; value = resend rate | `candles:Candle` |
| **4** | **Light** (steady mode) | rate (held look) | hold the steady look; value = resend rate | `candles:Light` |
| **5** | **Brighten** | rate | fader = presses/s from 0 (up to 5) while raised | `candles:Brighten` |
| **6** | **Dim** | rate | fader = presses/s from 0 (up to 5) while raised | `candles:Dim` |

Channels 7+ are free for expansion.

> The six functions above are **every button on the physical remote**. It has no timer
> buttons, so there are no timer cues — an earlier draft of this chart patched
> `TIMER_4H`/`TIMER_8H` on ch 10/11, but those codes were placeholders and never
> existed on this remote.
>
> **Verified from the desk (2026-08-27):** this patch has been driven with **QLC+ over
> Art-Net** into the daemon against the real candles — all six codes trigger the units,
> `Candle` is the flickering flame effect and `Light` is a constant glow, and the
> held-look model behaves as described below. What is *not* yet proven is range and aim
> from the tower (see the IR hit-rate test in `UNIFIED-DESIGN.md`), and the `max_hz`
> values below have not been tuned against the real choreography.

### The held-look model (what the desk should send)

The state cues (On / Off / Candle / Light) are **held looks**: park the channel at a
level for the whole look and the daemon keeps **re-transmitting** that command while
it's held, so cast turning in place catch it as they come around. The DMX **value sets
the resend rate**, not just on/off:

```
   value < floor (≈3%) → off
   value at floor      → min_hz   (slow "reminder" pulse, e.g. every 2 s)
   value at 255        → max_hz   (rapid, for fast choreography)
```

Fader feel is shaped by `curve` (2.0 = finer control low down), and `jitter_ms`
randomises timing slightly so two towers don't fire in perfect lockstep. Because these
re-send continuously, use **discrete** candle commands (separate ON and OFF), never a
single toggle button — a repeated toggle would flip-flop. This remote is well behaved
here: `On` and `Off` are discrete buttons, and `Candle`/`Light` select a mode rather
than toggling one.

The three modes in one line each:

- **rate / held look** — level → resend rate; hold to keep candles in a state.
- **rate / dimming** — `min_hz: 0`, so the fader is a speed control from zero.
- **index + GO** — one channel's value selects a command, a GO channel fires it once.
  (Not used by the shipped candle patch, which has few enough buttons to give each its
  own channel; it stays available for gear with more commands, like the projector.)

### One code at a time, and who wins

The daemon transmits **one IR code at a time** (single serial worker + minimum gap),
so simultaneous channel fires are queued, not overlapped. Two behaviours shape that
queue:

- **Priority** — a channel with `priority: true` (the blackout **Off**, ch 2) jumps
  ahead of any queued ON/flicker pulses, so a blackout takes hold fast. It preempts the
  *queue*, not a code already mid-transmit (≤ ~130 ms).
- **Coalescing** — held-look channels keep at most **one** pending shot of themselves
  (latest wins), so a fast-held look can't flood the queue with stale repeats.

Avoid holding contradictory looks at once (e.g. On and Off together) — the daemon will
faithfully alternate them and the candles will flip-flop. Use the cues as mutually
exclusive states.

### Tuning for the hit-rate test

Use **`--watch`** (see README) during the hit-rate test — it shows each channel's live
value, the computed resend rate, and the `tx / drop / coal / preempt` counters, so you
can see exactly what the desk is sending and what the daemon does with it.


If candles occasionally miss during a turn, raise that channel's **`max_hz`** (more
resends/s) or **`repeats`** (more IR shots per resend) in `config.candles.yaml`. If IR
feels "busy" or candles double-trigger, lower them. Start around `max_hz: 6`,
`repeats: 1` and adjust against the actual choreography.

## Two-channel personality (field-tested — start here)

This is the patch that was driven from QLC+ against the real candles on 2026-08-27.
It is the `selector` mode with **Rate and GO left out**, which the daemon reads as
"fixed rate, armed whenever a code is selected" — so the whole remote fits on two
channels. Config: [`config.candles-2ch.yaml`](config.candles-2ch.yaml).

| DMX ch | Function | Values |
|:------:|----------|--------|
| **1** | **Function** | `0` = idle · `1` On · `2` Off · `3` Candle (flicker) · `4` Light (steady) · `5` Dim · `6` Brighten |
| **2** | **Repeat** | `0` = continuous while selected · `N` = send exactly N times |

In testing **one shot was usually enough**, so `Repeat: 1` is a sensible default; raise
it only if the hit-rate test at tower distance shows misses.

> ⚠️ **Re-firing the same function.** With no GO channel a shot is armed by the
> selection *changing*. Parking ch 1 at the same value will **not** fire again — drop it
> to `0` (or another function) and back. If a cue needs a dedicated re-trigger, use the
> four-channel personality below, which has a real GO edge.

Like the four-channel version, this cannot hold two contradictory cues at once: exactly
one code is ever latched, so a desk mistake sends the wrong code, never two.

## Selector / fixture mode — one code at a time, with Rate and GO

The per-channel chart further up is easy to busk but lets you *hold two contradictory
cues at once* (On and Off), which flip-flops the candles. The **selector** personality
removes that failure mode entirely, and adds live Rate and a GO edge on top of the
two-channel patch above: a single **Select** channel points at exactly one
code, so a desk mistake can send the wrong code but never two at once. Control
channels decide how it's sent. This is a normal 4-channel DMX fixture.

### Fixture patch (4 channels)

| Offset | Channel | What it does |
|:------:|---------|--------------|
| +0 | **Select** | `0` = none (idle). `1..N` = the Nth captured code (one value per code). |
| +1 | **Rate** | How often to send while executing — value → Hz (floor + min/max curve). |
| +2 | **Count** | `0` = continuous (send while GO held). `N` = send exactly N times. |
| +3 | **GO** | Rising edge executes the selected code; hold for continuous. Release stops. |

Operate it like any fixture: set **Select** to the code, set **Rate** and **Count**,
then push **GO** (hold GO for continuous when Count = 0). Because Select carries **one
value per code**, a remote with up to 255 buttons fits on a single channel.

### Generate the value→code map from the Flipper file

You don't hand-write the table — the daemon builds it from the capture, in file order:

```bash
python3 -m ir_artnet --gen-config --ir remotes/candles.ir --key candles \
    --universe 0 > config.candles-selector.yaml
```

That prints the fixture config plus a value map — for the shipped candle capture:

```
# DMX ch1=Select(0=none 1..6=code)  ch2=Rate  ch3=Count(0=cont)  ch4=GO
# Value -> code map:
#     1 = candles:On       4 = candles:Light
#     2 = candles:Off      5 = candles:Dim
#     3 = candles:Candle   6 = candles:Brighten
```

Re-capture the remote → re-run `--gen-config` → the map updates itself. Keep a printed
copy of the value map at the desk so the operator knows which Select value is which code.
Run it with `python3 -m ir_artnet --config config.candles-selector.yaml` (add `--watch`
to see Select→code, Rate, Count and GO live).

> **Future (not today):** `--gen-config` reads Flipper `.ir` files. For non-Flipper
> users we can add capture via a cheap IR receiver on a Pi (`ir-ctl --receive` / LIRC
> `mode2`) that writes the same `.ir`/raw format, so the whole toolchain works without a
> Flipper. The daemon side needs no change — it already transmits from those timings.

## QLC+ patching (fleet desk)

Patch an **Art-Net output universe** to the tower's universe, then drive these as
**generic dimmer** channels (six channels, 1–6). For the momentary cues, a **button → scene** that sets the
channel to 255 (with a short flash/back-to-0) is the cleanest; for dim up/down use a
fader. Keep the tower on the same universe number in QLC+ and in `artnet.universe`.

## Wireless (unicast) notes

Towers run over WiFi with **unicast** Art-Net (broadcast is unreliable on WiFi).

- **Reserve an IP per tower** (DHCP reservation/static). In the QLC+ Art-Net plugin,
  set the node to **unicast** to that IP (the tower answers ArtPoll, so it also shows
  up for discovery). One universe per tower IP.
- **Hold momentary cues high for ~100 ms**, not a single-frame flash. Art-Net is UDP
  with no retransmit, so a one-frame bump can drop over WiFi; holding the value high
  guarantees a packet lands. Edge detection compares to the last *received* frame, so a
  dropped transition frame is fine as long as the value stays high — and `repeats: 3`
  adds margin.
- **Universe must match** between the QLC+ output and each tower's `artnet.universe`.

## Changing the patch

Everything is in `config.candles.yaml` — no code changes:

- **Move a function to another channel:** edit its `channel:`.
- **Add a button:** capture it on the Flipper into `remotes/candles.ir`, then add a
  `channels:` entry referencing `candles:<name>`.
- **Change burst count / dim speed:** edit `repeats:` or `max_hz:`.
- **Second tower:** identical config; just set each tower's `artnet.universe` (and, if
  you want independent control, give it its own universe/channels).
