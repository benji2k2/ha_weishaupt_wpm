"""Hot water setpoint and hysteresis, party hours and holiday days.

Every change goes through WpmCoordinator.async_write: range check, no write for an
unchanged value, at most one write per register within WRITE_MIN_INTERVAL.
"""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WpmConfigEntry, WpmCoordinator
from .entity import WpmRegisterEntity

NUMBERS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key="hot_water_setpoint",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_step=1,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key="hot_water_hysteresis",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key="party_hours",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        native_step=1,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key="holiday_days",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        native_step=1,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the adjustable settings."""
    coordinator = entry.runtime_data
    async_add_entities(WpmNumber(coordinator, description) for description in NUMBERS)


class WpmNumber(WpmRegisterEntity, NumberEntity):
    """A setting stored in one register."""

    def __init__(self, coordinator: WpmCoordinator, description: NumberEntityDescription) -> None:
        super().__init__(coordinator, description.key, description.key)
        self.entity_description = description
        assert self.register.write_min is not None and self.register.write_max is not None
        self._attr_native_min_value = self.register.write_min
        self._attr_native_max_value = self.register.write_max

    @property
    def native_value(self) -> float | None:
        raw = self.raw
        return None if raw is None else self.register.decode(raw)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_write(self.register.key, value)
