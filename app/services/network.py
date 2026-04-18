from __future__ import annotations

import time
from pathlib import Path


def read_network_rx_bytes() -> dict:
    proc_net_dev = Path("/proc/net/dev")
    if not proc_net_dev.exists():
        return {
            "supported": False,
            "rx_bytes": 0,
            "timestamp": time.time(),
            "interfaces": [],
        }

    rx_bytes = 0
    interfaces = []
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
        rx_bytes += received
        interfaces.append({"name": interface, "rx_bytes": received})

    return {
        "supported": True,
        "rx_bytes": rx_bytes,
        "timestamp": time.time(),
        "interfaces": interfaces,
    }
