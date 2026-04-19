from __future__ import annotations

import time
from pathlib import Path

from app.config import settings


def _default_route_interface() -> str | None:
    route_file = Path("/proc/net/route")
    if not route_file.exists():
        return None
    for line in route_file.read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "00000000":
            return fields[0]
    return None


def read_network_rx_bytes() -> dict:
    proc_net_dev = Path("/proc/net/dev")
    if not proc_net_dev.exists():
        return {
            "supported": False,
            "rx_bytes": 0,
            "tx_bytes": 0,
            "timestamp": time.time(),
            "interface": None,
            "interfaces": [],
            "reason": "/proc/net/dev not found",
        }

    configured_interface = settings.network_interface
    selected_interface = configured_interface or _default_route_interface()
    rx_bytes = 0
    tx_bytes = 0
    interfaces = []
    found_selected = False
    for line in proc_net_dev.read_text(encoding="utf-8").splitlines()[2:]:
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        interface = name.strip()
        if interface == "lo":
            continue
        fields = values.split()
        if not fields:
            continue
        received = int(fields[0])
        transmitted = int(fields[8]) if len(fields) > 8 else 0
        interfaces.append({"name": interface, "rx_bytes": received, "tx_bytes": transmitted})
        if selected_interface:
            if interface == selected_interface:
                rx_bytes = received
                tx_bytes = transmitted
                found_selected = True
        else:
            rx_bytes += received
            tx_bytes += transmitted

    if selected_interface and not found_selected:
        return {
            "supported": False,
            "rx_bytes": 0,
            "tx_bytes": 0,
            "timestamp": time.time(),
            "interface": selected_interface,
            "interfaces": interfaces,
            "reason": f"interface {selected_interface} not found",
        }

    return {
        "supported": True,
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "timestamp": time.time(),
        "interface": selected_interface,
        "interfaces": interfaces,
    }
