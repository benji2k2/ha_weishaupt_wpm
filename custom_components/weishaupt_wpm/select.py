"""Operating mode.

Only summer, winter, holiday and party are offered. "2nd heat generator" (heating
rod only) and "cooling" can be set at the controller; the sensor "active operating
mode" shows them, and this select then has no current option.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WpmConfigEntry, WpmCoordinator
from .entity import WpmRegisterEntity
from .registers import OPERATING_MODES, SELECTABLE_OPERATING_MODES

OPTIONS = {OPERATING_MODES[code]: code for code in SELECTABLE_OPERATING_MODES}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the operating mode select."""
    async_add_entities([WpmOperatingModeSelect(entry.runtime_data)])


class WpmOperatingModeSelect(WpmRegisterEntity, SelectEntity):
    """Summer, winter, holiday or party."""

    _attr_options = list(OPTIONS)

    def __init__(self, coordinator: WpmCoordinator) -> None:
        super().__init__(coordinator, "operating_mode", "operating_mode")

    @property
    def current_option(self) -> str | None:
        raw = self.raw
        if raw is None or raw not in SELECTABLE_OPERATING_MODES:
            return None
        return OPERATING_MODES[raw]

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_write(self.register.key, OPTIONS[option])
