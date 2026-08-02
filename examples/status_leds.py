#!/usr/bin/env python3
"""Status-LED demo for the step Pi HAT (student-friendly, uses gpiozero).

Wiring (see steppi-layout.svg) -- status pod via J_STAT, all 220 ohm:
    GPIO17 (pin 11) -> 220 ohm -> GREEN  LED -> GND   "Power / ready"
    GPIO27 (pin 13) -> 220 ohm -> YELLOW LED -> GND   "ArtNet activity"
    GPIO22 (pin 15) -> 220 ohm -> RED    LED -> GND   "Network / fault" (ON = link down)

Run:  python3 status_leds.py
Stop: Ctrl-C  (all LEDs turn off cleanly)

Once the wiring is proven, import these same LED objects into the ArtNet service:
    power.on() at startup, artnet.blink() on each received frame,
    network reflecting link state.
"""

from time import sleep

try:
    from gpiozero import LED
except ImportError:
    raise SystemExit("gpiozero not found — run: sudo apt install python3-gpiozero")

POWER_PIN = 17     # green,  header pin 11
ARTNET_PIN = 27    # yellow, header pin 13
NETWORK_PIN = 22   # red,    header pin 15 (lit when link is DOWN)


def network_is_up() -> bool:
    """True if the Pi has a default route (a real network connection)."""
    try:
        with open("/proc/net/route") as fh:
            next(fh)  # skip header
            for line in fh:
                fields = line.split()
                # destination 00000000 == default route
                if len(fields) > 1 and fields[1] == "00000000":
                    return True
    except OSError:
        pass
    return False


def main():
    power = LED(POWER_PIN)
    artnet = LED(ARTNET_PIN)
    network = LED(NETWORK_PIN)

    power.on()  # solid = booted and this program is running
    print("Power LED on. Ctrl-C to quit.")
    try:
        while True:
            # red LED = fault: ON when the link is DOWN, dark when healthy
            network.off() if network_is_up() else network.on()
            # activity LED blinks to show the loop is alive
            artnet.toggle()
            sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        power.off()
        artnet.off()
        network.off()
        print("\nLEDs off. Bye.")


if __name__ == "__main__":
    main()
