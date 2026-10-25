"""LAN discovery of Tuya devices (no local keys required)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveredDevice:
    ip: str
    id: str
    version: str


def scan(timeout: int = 18) -> list[DiscoveredDevice]:
    """Broadcast-scan the local network for Tuya devices."""
    import tinytuya

    found = tinytuya.deviceScan(False, timeout) or {}
    out = []
    for ip, info in found.items():
        out.append(DiscoveredDevice(
            ip=ip,
            id=str(info.get("gwId") or info.get("id") or "?"),
            version=str(info.get("version") or "?"),
        ))
    return out
