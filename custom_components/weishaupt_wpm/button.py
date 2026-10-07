"""Button to set the controller's clock."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WpmConfigEntry, WpmCoordinator
from .entity import WpmEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the clock button, if the options allow setting the clock."""
    if "clock" in entry.runtime_data.writable:
        async_add_entities([WpmSetClockButton(entry.runtime_data)])


class WpmSetClockButton(WpmEntity, ButtonEntity):
    """Set the controller's date and time to Home Assistant's.

    Nothing in the integration presses it; an automation may, e.g. once a month
    when the clock deviation sensor shows more than a few minutes.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: WpmCoordinator) -> None:
        super().__init__(coordinator, "set_clock")

    async def async_press(self) -> None:
        await self.coordinator.async_set_clock()
