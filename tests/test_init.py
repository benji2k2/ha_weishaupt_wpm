"""Setting up the heat pump and reading it."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_wpm.const import DOMAIN
from custom_components.weishaupt_wpm.diagnostics import async_get_config_entry_diagnostics
from custom_components.weishaupt_wpm.modbus import ModbusClient
from custom_components.weishaupt_wpm.registers import REGISTERS_BY_KEY, Group
from tools.simulator import WpmSimulator

from .conftest import entry_for


def state(hass: HomeAssistant, entity_id: str) -> str:
    current = hass.states.get(entity_id)
    assert current is not None, entity_id
    return current.state


async def test_entities_show_the_simulated_values(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED
    assert state(hass, "sensor.weishaupt_wpm_outdoor_temperature") == "-3.5"
    assert state(hass, "sensor.weishaupt_wpm_hot_water_temperature") == "47.8"
    assert state(hass, "sensor.weishaupt_wpm_status") == "heating"
    assert hass.states.get("sensor.weishaupt_wpm_status").attributes["code"] == 2
    assert state(hass, "sensor.weishaupt_wpm_fault") == "none"
    assert state(hass, "sensor.weishaupt_wpm_active_operating_mode") == "winter"
    assert state(hass, "sensor.weishaupt_wpm_runtime_compressor_1") == "18342"
    assert state(hass, "sensor.weishaupt_wpm_heat_for_heating") == "49998"
    assert state(hass, "binary_sensor.weishaupt_wpm_fault_active") == "off"
    assert state(hass, "select.weishaupt_wpm_operating_mode") == "winter"
    assert state(hass, "number.weishaupt_wpm_hot_water_setpoint") == "50"


async def test_one_device_with_all_entities(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    # Looked up within the config entry: async_get_device(identifiers=...) is deprecated
    # since 2026.9, and its replacement does not exist before that.
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), setup_entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert (DOMAIN, setup_entry.unique_id) in device.identifiers
    assert device.manufacturer == "Weishaupt"
    entities = er.async_entries_for_device(er.async_get(hass), device.id, True)
    # 35 sensors (30 registers, 3 heat amounts, write count, clock deviation),
    # 2 binary sensors, 5 numbers, 1 select, 1 button.
    assert len(entities) == 44


async def test_missing_registers_are_left_out(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    coordinator = setup_entry.runtime_data
    # The simulator lacks 6, 73 and 79; the block reads fall back and remember them.
    assert coordinator.unsupported == {6, 73, 79}
    registry = er.async_get(hass)
    inlet = registry.async_get("sensor.weishaupt_wpm_heat_source_inlet")
    assert inlet is not None and inlet.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    # Neighbours of a missing register are still read (7 is optional, so check the raw value).
    assert coordinator.data.raw[7] == 0x10000 - 71
    requests = simulator.requests
    await coordinator.async_refresh()
    # Status tier only, and no more probing of the missing register: 1-3, 5, 7-8, 53, 58, 101..
    assert simulator.requests - requests == 7


async def test_tiers_are_read_at_their_own_pace(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    coordinator = setup_entry.runtime_data
    simulator.set(72, 20000)
    simulator.set_value("outdoor_temperature", 1.5)
    await coordinator.async_refresh()
    assert state(hass, "sensor.weishaupt_wpm_outdoor_temperature") == "1.5"
    assert state(hass, "sensor.weishaupt_wpm_runtime_compressor_1") == "18342"
    del coordinator._last_read[Group.COUNTERS]
    await coordinator.async_refresh()
    assert state(hass, "sensor.weishaupt_wpm_runtime_compressor_1") == "20000"


async def test_implausible_temperature_has_no_value(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    simulator.set_value("return_temperature", -99.9)
    await setup_entry.runtime_data.async_refresh()
    assert state(hass, "sensor.weishaupt_wpm_return_temperature") == STATE_UNKNOWN


async def test_unknown_code_is_shown_as_other(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    simulator.set(105, 99)
    await setup_entry.runtime_data.async_refresh()
    assert state(hass, "sensor.weishaupt_wpm_fault") == "other"
    assert hass.states.get("sensor.weishaupt_wpm_fault").attributes["code"] == 99
    assert state(hass, "binary_sensor.weishaupt_wpm_fault_active") == "on"


async def test_counter_glitch_is_ignored_but_reset_is_not(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    coordinator = setup_entry.runtime_data
    simulator.set_counter("heat_heating", 49_998 - 9_999)  # low part rolled, mid not yet
    del coordinator._last_read[Group.COUNTERS]
    await coordinator.async_refresh()
    assert state(hass, "sensor.weishaupt_wpm_heat_for_heating") == "49998"
    simulator.set_counter("heat_heating", 12)  # counter really reset
    del coordinator._last_read[Group.COUNTERS]
    await coordinator.async_refresh()
    assert state(hass, "sensor.weishaupt_wpm_heat_for_heating") == "12"


async def test_unavailable_while_the_gateway_is_gone(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    coordinator = setup_entry.runtime_data
    coordinator.client.timeout = 0.2
    await simulator.stop()
    await coordinator.client.close()
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert state(hass, "sensor.weishaupt_wpm_outdoor_temperature") == STATE_UNAVAILABLE


async def test_recovers_after_being_thrown_out(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    # tools/probe.py started while Home Assistant is connected: the gateway throws HA out.
    port = simulator._server.sockets[0].getsockname()[1]
    probe = ModbusClient("127.0.0.1", port, timeout=0.5, pause=0.0)
    await probe.read_holding_registers(1, 1)
    await probe.close()
    assert simulator.kicked_connections == 1
    coordinator = setup_entry.runtime_data
    simulator.set_value("outdoor_temperature", 4.2)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert state(hass, "sensor.weishaupt_wpm_outdoor_temperature") == "4.2"


async def test_not_ready_without_answer(
    hass: HomeAssistant, simulator: WpmSimulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = dict(ModbusClient.__init__.__kwdefaults__, timeout=0.2)
    monkeypatch.setattr(ModbusClient.__init__, "__kwdefaults__", defaults)
    entry = entry_for(simulator)
    simulator.unit = 7  # the entry asks for unit 1: nobody answers
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_closes_the_connection(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    client = setup_entry.runtime_data.client
    assert client.connected
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    assert not client.connected


async def test_diagnostics(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_entry)
    outdoor = next(item for item in diagnostics["registers"] if item["address"] == 1)
    assert outdoor["value"] == -3.5
    assert diagnostics["unsupported"] == [6, 73, 79]
    assert diagnostics["counters"][0]["value"] == 49_998
    assert diagnostics["writes"] == []


async def test_heating_curve_settings(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    coordinator = setup_entry.runtime_data
    simulator.set(243, 17)  # step -2 at the display
    simulator.set(47, 20)  # 2.0 K
    del coordinator._last_read[Group.SETTINGS]
    await coordinator.async_refresh()
    assert state(hass, "number.weishaupt_wpm_heating_curve_offset_hk1") == "-2.0"
    hysteresis = hass.states.get("sensor.weishaupt_wpm_heating_hysteresis")
    assert hysteresis is not None
    assert hysteresis.state == "2.0"
    assert hysteresis.attributes["unit_of_measurement"] == "K"
    # Only the step is writable; end point and fixed setpoint stay read only.
    assert REGISTERS_BY_KEY["heating_curve_offset"].writable
    assert not REGISTERS_BY_KEY["heating_curve_end_point"].writable
