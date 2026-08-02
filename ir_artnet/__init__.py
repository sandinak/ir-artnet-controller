"""ArtNet-driven Raspberry Pi IR blaster.

Package layout:
    flipper.py      parse Flipper Zero .ir files -> IRSignal
    protocols.py    encode parsed protocols (NEC/Samsung/SIRC/RC5) -> raw timings
    transmitter.py  pigpio carrier-modulated IR output on a GPIO pin
    artnet.py       Art-Net (ArtDMX) UDP receiver
    controller.py   channel -> command mapping and trigger logic
    __main__.py     service entry point
"""

__version__ = "1.0.0"
