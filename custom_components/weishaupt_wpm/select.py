"""Operating mode.

Which modes are offered is an option (default: summer, winter, holiday, party). A
mode set at the controller that is not offered is shown by the sensor "active
operating mode"; this select then has no current option.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WpmConfigEntry, WpmCoordinator
from .entity import WpmRegisterEntity
from .registers import OPERATING_MODES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the operating mode select."""
    async_add_entities([WpmOperatingModeSelect(entry.runtime_data)])


class WpmOperatingModeSelect(WpmRegisterEntity, SelectEntity):
    """The operating modes chosen in the options."""

    def __init__(self, coordinator: WpmCoordinator) -> None:
        super().__init__(coordinator, "operating_mode", "operating_mode")
        self._codes = {OPERATING_MODES[code]: code for code in coordinator.operating_modes}
        self._attr_options = list(self._codes)

    @property
    def current_option(self) -> str | None:
        raw = self.raw
        if raw is None or raw not in self.coordinator.operating_modes:
            return None
        return OPERATING_MODES[raw]

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_write(self.register.key, self._codes[option])
