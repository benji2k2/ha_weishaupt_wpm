"""Writing settings: only on request, only in range, never more often than needed."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tools.simulator import WpmSimulator


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True
    )


async def test_hot_water_setpoint(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    await set_number(hass, "number.weishaupt_wpm_hot_water_setpoint", 55)
    assert simulator.get(254) == 55
    assert hass.states.get("number.weishaupt_wpm_hot_water_setpoint").state == "55"
    log = list(setup_entry.runtime_data.write_log)
    assert log[-1]["result"] == "ok" and log[-1]["before"] == 50


async def test_same_value_is_not_written(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    await set_number(hass, "number.weishaupt_wpm_hot_water_setpoint", 50)
    assert simulator.writes == []


async def test_outside_the_allowed_range(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    with pytest.raises(ServiceValidationError):
        await set_number(hass, "number.weishaupt_wpm_hot_water_setpoint", 65)
    with pytest.raises(ServiceValidationError):
        await setup_entry.runtime_data.async_write("hot_water_setpoint", 65)
    with pytest.raises(ServiceValidationError):
        await setup_entry.runtime_data.async_write("status", 1)
    assert simulator.writes == []


async def test_a_register_is_not_written_twice_in_a_row(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    await set_number(hass, "number.weishaupt_wpm_hot_water_setpoint", 55)
    with pytest.raises(HomeAssistantError) as err:
        await set_number(hass, "number.weishaupt_wpm_hot_water_setpoint", 56)
    assert err.value.translation_key == "write_too_soon"
    # Another register is not held up.
    await set_number(hass, "number.weishaupt_wpm_party_hours", 3)
    assert simulator.writes == [(254, 55), (223, 3)]


async def test_write_again_after_the_pause(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    simulator: WpmSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.weishaupt_wpm.coordinator.WRITE_MIN_INTERVAL", 0)
    await set_number(hass, "number.weishaupt_wpm_hot_water_setpoint", 55)
    await set_number(hass, "number.weishaupt_wpm_hot_water_setpoint", 56)
    assert simulator.get(254) == 56


async def test_operating_mode(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    select = hass.states.get("select.weishaupt_wpm_operating_mode")
    assert select.attributes["options"] == ["summer", "winter", "holiday", "party"]
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.weishaupt_wpm_operating_mode", "option": "summer"},
        blocking=True,
    )
    assert simulator.get(222) == 0
    assert hass.states.get("sensor.weishaupt_wpm_active_operating_mode").state == "summer"


async def test_mode_set_at_the_controller_is_shown(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    coordinator = setup_entry.runtime_data
    simulator.set(222, 4)
    coordinator._last_read.clear()
    await coordinator.async_refresh()
    assert hass.states.get("select.weishaupt_wpm_operating_mode").state == "unknown"
    assert (
        hass.states.get("sensor.weishaupt_wpm_active_operating_mode").state
        == "second_heat_generator"
    )


async def test_controller_rejects_the_value(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    simulator.write_ranges[254] = (40, 50)  # stricter at the controller
    with pytest.raises(HomeAssistantError) as err:
        await set_number(hass, "number.weishaupt_wpm_hot_water_setpoint", 55)
    assert err.value.translation_key == "write_rejected"
    assert simulator.get(254) == 50
    assert setup_entry.runtime_data.write_log[-1]["result"].startswith("rejected")
