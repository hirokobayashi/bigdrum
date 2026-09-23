"""50000/udp transport layer for the Hitachi Big Drum integration.

洗濯機のサービスは「送信者の 50000/udp」にレスポンスを返すため、
クライアント側も 50000/udp にバインドして送受信する。
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass

from . import protocol
from .const import UDP_TIMEOUT

PORT = 50000


class UdpExchangeError(Exception):
    """No usable UDP response was received."""


def _make_socket(timeout: float) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if sys.platform == "win32":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    else:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("", PORT))
    s.settimeout(timeout)
    return s


@dataclass
class UdpResult:
    resp: protocol.DecodedResponse
    from_host: str


def udp_exchange(
    key: bytes,
    iv: bytes,
    src: str,
    dst: str,
    seq: int,
    cmd: str,
    data: str | None,
    addr: str,
    timeout: float = UDP_TIMEOUT,
) -> UdpResult:
    """One encrypted request/response round-trip. Raises UdpExchangeError."""
    req = protocol.build_request(key, iv, seq, src, dst, cmd, data)
    s = _make_socket(timeout)
    try:
        s.sendto(req, (addr, PORT))
        packet, from_addr = s.recvfrom(65535)
    except socket.timeout as err:
        raise UdpExchangeError(f"{cmd} no response from {addr}:{PORT}") from err
    finally:
        s.close()
    resp = protocol.parse_packet(key, iv, packet)
    if resp.src != dst or resp.dst != src:
        raise UdpExchangeError(
            f"unexpected response SRC={resp.src} DST={resp.dst} "
            f"(expected SRC={dst} DST={src})"
        )
    return UdpResult(resp=resp, from_host=from_addr[0])
