"""Fault and lock as problem sensors, for alerts."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WpmConfigEntry, WpmCoordinator
from .entity import WpmRegisterEntity

# Entity key -> register whose non-zero value means "problem".
PROBLEMS = {"fault_active": "fault", "lock_active": "lock"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the problem sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        WpmProblemSensor(coordinator, key, register_key) for key, register_key in PROBLEMS.items()
    )


class WpmProblemSensor(WpmRegisterEntity, BinarySensorEntity):
    """On while the controller reports a fault or a lock."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: WpmCoordinator, key: str, register_key: str) -> None:
        super().__init__(coordinator, key, register_key)

    @property
    def is_on(self) -> bool | None:
        raw = self.raw
        return None if raw is None else raw != 0
