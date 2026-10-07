"""Diagnostics: raw register values, what is missing, and the write history."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import WpmConfigEntry
from .registers import COUNTERS, REGISTERS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WpmConfigEntry
) -> dict[str, Any]:
    """Return everything needed to compare the integration with the display."""
    coordinator = entry.runtime_data
    data = coordinator.data
    raw = data.raw if data else {}
    now = time.monotonic()
    return {
        "entry_data": dict(entry.data),
        "entry_options": dict(entry.options),
        "address_offset": coordinator.offset,
        "intervals": {group.value: seconds for group, seconds in coordinator.intervals.items()},
        "seconds_since_read": {
            group.value: round(now - read, 1) for group, read in coordinator._last_read.items()
        },
        "registers": [
            {
                "address": register.address,
                "key": register.key,
                "raw": raw.get(register.address),
                "value": register.decode(raw[register.address])
                if register.address in raw
                else None,
                "verified": register.verified,
            }
            for register in REGISTERS
        ],
        "counters": [
            {
                "key": counter.key,
                "parts": [raw.get(address) for address in counter.addresses],
                "value": data.counters.get(counter.key) if data else None,
            }
            for counter in COUNTERS
        ],
        "unsupported": sorted(coordinator.unsupported),
        "client": dict(coordinator.client.stats),
        "writes": list(coordinator.write_log),
        "write_count_since_setup": coordinator.write_count,
        "clock_deviation_minutes": coordinator.clock_deviation,
    }
