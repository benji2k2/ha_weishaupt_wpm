"""Base entity for the Weishaupt WPM integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import WpmCoordinator
from .registers import REGISTERS_BY_KEY, Register


class WpmEntity(CoordinatorEntity[WpmCoordinator]):
    """An entity of the heat pump device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WpmCoordinator, key: str) -> None:
        super().__init__(coordinator)
        identifier = coordinator.device_identifier
        self._attr_unique_id = f"{identifier}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=coordinator.config_entry.title,
        )


class WpmRegisterEntity(WpmEntity):
    """An entity that shows one register."""

    def __init__(self, coordinator: WpmCoordinator, key: str, register_key: str) -> None:
        super().__init__(coordinator, key)
        self.register: Register = REGISTERS_BY_KEY[register_key]

    @property
    def raw(self) -> int | None:
        return self.coordinator.data.raw.get(self.register.address)

    @property
    def available(self) -> bool:
        return super().available and self.raw is not None
