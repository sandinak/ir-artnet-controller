"""Minimal Art-Net (ArtDMX) receiver.

Listens on UDP 6454, decodes ArtDMX packets, and calls a callback with the
15-bit port address (universe) and the DMX channel data.  It also answers
ArtPoll with a small ArtPollReply so lighting consoles can discover the node.

Only the receive side is implemented -- this device is a sink, never a source.
"""

from __future__ import annotations

import socket
import struct
import threading
from typing import Callable, Optional

ARTNET_PORT = 6454
_ID = b"Art-Net\x00"
OP_POLL = 0x2000
OP_POLLREPLY = 0x2100
OP_DMX = 0x5000


def port_address(net: int, sub_uni: int) -> int:
    """Combine Net (hi) and SubUni (subnet<<4|universe) into a 15-bit address."""
    return ((net & 0x7F) << 8) | (sub_uni & 0xFF)


class ArtNetReceiver:
    def __init__(self, on_dmx: Callable[[int, bytes], None],
                 bind_ip: str = "0.0.0.0",
                 short_name: str = "IR-Blaster",
                 long_name: str = "ArtNet IR Blaster (RasPi)"):
        self.on_dmx = on_dmx
        self.bind_ip = bind_ip
        self.short_name = short_name.encode()[:17]
        self.long_name = long_name.encode()[:63]
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind((self.bind_ip, ARTNET_PORT))
        self._sock.settimeout(0.5)
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="artnet-rx", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._sock:
            self._sock.close()
            self._sock = None

    def _loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self._handle(data, addr)

    def _handle(self, data: bytes, addr):
        if len(data) < 10 or not data.startswith(_ID):
            return
        opcode = struct.unpack_from("<H", data, 8)[0]  # opcode is little-endian
        if opcode == OP_DMX:
            self._handle_dmx(data)
        elif opcode == OP_POLL:
            self._send_pollreply(addr)

    def _handle_dmx(self, data: bytes):
        if len(data) < 18:
            return
        # 8 id, 2 op, ProtHi, ProtLo, Seq, Phys, SubUni, Net, LenHi, LenLo
        sub_uni = data[14]
        net = data[15]
        length = struct.unpack_from(">H", data, 16)[0]
        dmx = data[18:18 + length]
        self.on_dmx(port_address(net, sub_uni), dmx)

    def _send_pollreply(self, addr):
        # A compact but valid-enough ArtPollReply so desks list the node.
        ip = _local_ip()
        pkt = bytearray(239)
        pkt[0:8] = _ID
        struct.pack_into("<H", pkt, 8, OP_POLLREPLY)
        pkt[10:14] = bytes(int(x) for x in ip.split("."))  # IP address
        struct.pack_into("<H", pkt, 14, ARTNET_PORT)       # port (LE here)
        pkt[16] = 0                                        # VersInfoH
        pkt[17] = 1                                        # VersInfoL
        pkt[18] = 0                                        # NetSwitch
        pkt[19] = 0                                        # SubSwitch
        struct.pack_into(">H", pkt, 20, 0)                 # Oem
        pkt[23] = 0xD2                                     # Status1
        struct.pack_into("<H", pkt, 24, 0x7FF0)           # ESTA man.
        pkt[26:26 + len(self.short_name)] = self.short_name
        pkt[44:44 + len(self.long_name)] = self.long_name
        pkt[173] = 1                                       # NumPorts
        pkt[174] = 0xC0                                    # PortType: input
        try:
            self._sock.sendto(bytes(pkt), (addr[0], ARTNET_PORT))
        except OSError:
            pass


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        s.close()
