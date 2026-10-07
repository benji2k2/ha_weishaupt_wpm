"""Temperatures, status, runtimes and heat amounts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WpmConfigEntry, WpmCoordinator
from .entity import WpmEntity, WpmRegisterEntity
from .registers import (
    COUNTERS_BY_KEY,
    FAULT_CODES,
    LOCK_CODES,
    OPERATING_MODES,
    REGISTERS_BY_KEY,
    SENSOR_FAULT_CODES,
    STATUS_CODES,
    UNKNOWN_CODE,
)


@dataclass(frozen=True, kw_only=True)
class WpmSensorDescription(SensorEntityDescription):
    """A sensor for one register; ``codes`` turns it into a text sensor."""

    codes: dict[int, str] | None = None


def _temperature(key: str, *, measured: bool = True, **kwargs: Any) -> WpmSensorDescription:
    return WpmSensorDescription(
        key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT if measured else None,
        suggested_display_precision=1,
        entity_registry_enabled_default=not REGISTERS_BY_KEY[key].optional,
        **kwargs,
    )


def _runtime(key: str, **kwargs: Any) -> WpmSensorDescription:
    return WpmSensorDescription(
        key=key,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=not REGISTERS_BY_KEY[key].optional,
        **kwargs,
    )


def _codes(key: str, codes: dict[int, str]) -> WpmSensorDescription:
    return WpmSensorDescription(
        key=key,
        device_class=SensorDeviceClass.ENUM,
        options=sorted({*codes.values(), UNKNOWN_CODE}),
        codes=codes,
    )


REGISTER_SENSORS: tuple[WpmSensorDescription, ...] = (
    _temperature("outdoor_temperature"),
    _temperature("flow_temperature"),
    _temperature("return_temperature"),
    _temperature("hot_water_temperature"),
    _temperature("heat_source_inlet_temperature"),
    _temperature("heat_source_outlet_temperature"),
    _temperature("return_setpoint", measured=False),
    _temperature("hot_water_setpoint_active", measured=False),
    _temperature(
        "hot_water_setpoint_max", measured=False, entity_category=EntityCategory.DIAGNOSTIC
    ),
    _temperature(
        "hot_water_setpoint_min", measured=False, entity_category=EntityCategory.DIAGNOSTIC
    ),
    _temperature("room_temperature_setpoint", measured=False),
    _temperature("heating_curve_fixed_setpoint", measured=False),
    _temperature("heating_curve_end_point", measured=False),
    # A temperature difference, like the hot water hysteresis: no temperature device class.
    WpmSensorDescription(
        key="heating_hysteresis",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        suggested_display_precision=1,
    ),
    WpmSensorDescription(
        key="pressure_8",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement="bar",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    WpmSensorDescription(
        key="pressure_101",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement="bar",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    _codes("status", STATUS_CODES),
    _codes("lock", LOCK_CODES),
    _codes("fault", FAULT_CODES),
    _codes("sensor_fault", SENSOR_FAULT_CODES),
    _codes("operating_mode", OPERATING_MODES),
    _runtime("runtime_compressor_1"),
    _runtime("runtime_compressor_2"),
    _runtime("runtime_primary_pump", entity_category=EntityCategory.DIAGNOSTIC),
    _runtime("runtime_second_heat_generator"),
    _runtime("runtime_heating_pump", entity_category=EntityCategory.DIAGNOSTIC),
    _runtime("runtime_hot_water_pump", entity_category=EntityCategory.DIAGNOSTIC),
    _runtime("runtime_flange_heater", entity_category=EntityCategory.DIAGNOSTIC),
    _runtime("runtime_aux_pump", entity_category=EntityCategory.DIAGNOSTIC),
    _runtime("runtime_pool_pump", entity_category=EntityCategory.DIAGNOSTIC),
)

COUNTER_SENSORS: tuple[WpmSensorDescription, ...] = tuple(
    WpmSensorDescription(
        key=key,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=not COUNTERS_BY_KEY[key].optional,
    )
    for key in ("heat_heating", "heat_hot_water", "environmental_energy")
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add all sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(WpmRegisterSensor(coordinator, description) for description in REGISTER_SENSORS),
            *(WpmCounterSensor(coordinator, description) for description in COUNTER_SENSORS),
        ]
    )


class WpmRegisterSensor(WpmRegisterEntity, SensorEntity):
    """The value of one register, or its text for code registers."""

    entity_description: WpmSensorDescription

    def __init__(self, coordinator: WpmCoordinator, description: WpmSensorDescription) -> None:
        super().__init__(coordinator, description.key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | str | None:
        raw = self.raw
        if raw is None:
            return None
        codes = self.entity_description.codes
        if codes is not None:
            return codes.get(raw, UNKNOWN_CODE)
        return self.register.decode(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.codes is None:
            return None
        return {"code": self.raw}


class WpmCounterSensor(WpmEntity, SensorEntity):
    """A heat amount combined from three registers."""

    entity_description: WpmSensorDescription

    def __init__(self, coordinator: WpmCoordinator, description: WpmSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.counters.get(self.entity_description.key)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None
