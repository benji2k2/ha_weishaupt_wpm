"""Setting up, reconfiguring and tuning through the UI."""

from __future__ import annotations

import socket

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_wpm.const import (
    CONF_ADDRESS_OFFSET,
    CONF_COUNTER_INTERVAL,
    CONF_HOT_WATER_MAX,
    CONF_HOT_WATER_MIN,
    CONF_OPERATING_MODES,
    CONF_SETTINGS_INTERVAL,
    CONF_STATUS_INTERVAL,
    CONF_TRANSPORT,
    CONF_UNIT,
    CONF_WRITABLE,
    DOMAIN,
)
from custom_components.weishaupt_wpm.modbus import ModbusClient
from tools.simulator import WpmSimulator

from .conftest import entry_for


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = dict(ModbusClient.__init__.__kwdefaults__)
    defaults.update(timeout=0.3, connect_timeout=0.5)
    monkeypatch.setattr(ModbusClient.__init__, "__kwdefaults__", defaults)


def user_input(port: int, **changes: object) -> dict[str, object]:
    return {
        CONF_HOST: " 127.0.0.1 ",
        CONF_PORT: float(port),
        CONF_UNIT: 1.0,
        CONF_TRANSPORT: "rtuovertcp",
        **changes,
    }


def port_of(simulator: WpmSimulator) -> int:
    return simulator._server.sockets[0].getsockname()[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def start_user_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return result["flow_id"]


async def test_creates_entry(hass: HomeAssistant, simulator: WpmSimulator) -> None:
    flow_id = await start_user_flow(hass)
    port = port_of(simulator)
    result = await hass.config_entries.flow.async_configure(flow_id, user_input(port))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Weishaupt WPM"
    assert result["data"] == {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: port,
        CONF_UNIT: 1,
        CONF_TRANSPORT: "rtuovertcp",
    }
    assert result["result"].unique_id == f"127.0.0.1:{port}/1"


async def test_gateway_unreachable(hass: HomeAssistant) -> None:
    flow_id = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, user_input(free_port()))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "gateway_unreachable"}


async def test_heat_pump_does_not_answer(hass: HomeAssistant, simulator: WpmSimulator) -> None:
    flow_id = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, user_input(port_of(simulator), **{CONF_UNIT: 5.0})
    )
    assert result["errors"] == {"base": "no_answer"}


async def test_wrong_transport(hass: HomeAssistant, simulator: WpmSimulator) -> None:
    flow_id = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, user_input(port_of(simulator), **{CONF_TRANSPORT: "tcp"})
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] in {"no_answer", "invalid_response"}


async def test_already_configured(hass: HomeAssistant, simulator: WpmSimulator) -> None:
    entry_for(simulator).add_to_hass(hass)
    flow_id = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, user_input(port_of(simulator)))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_while_running(
    hass: HomeAssistant, setup_entry: MockConfigEntry, simulator: WpmSimulator
) -> None:
    # Same gateway: the running integration holds the only connection and has to let go.
    result = await setup_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(port_of(simulator))
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_to_another_gateway(hass: HomeAssistant) -> None:
    other = WpmSimulator()
    port = await other.start()
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="10.0.0.9:502/1",
            data={CONF_HOST: "10.0.0.9", CONF_PORT: 502, CONF_UNIT: 1, CONF_TRANSPORT: "tcp"},
        )
        entry.add_to_hass(hass)
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input(port))
        await hass.async_block_till_done()
    finally:
        await other.stop()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PORT] == port
    assert entry.unique_id == f"127.0.0.1:{port}/1"
    assert entry.data[CONF_TRANSPORT] == "rtuovertcp"


OPTIONS_INPUT = {
    CONF_STATUS_INTERVAL: 20.0,
    CONF_SETTINGS_INTERVAL: 600.0,
    CONF_COUNTER_INTERVAL: 1800.0,
    CONF_HOT_WATER_MIN: 45.0,
    CONF_HOT_WATER_MAX: 55.0,
    CONF_OPERATING_MODES: ["cooling", "summer", "winter"],
    CONF_ADDRESS_OFFSET: "minus_1",
    CONF_WRITABLE: ["party_hours", "clock", "operating_mode"],
}


async def test_options_flow(hass: HomeAssistant, simulator: WpmSimulator) -> None:
    entry = entry_for(simulator)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], OPTIONS_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_STATUS_INTERVAL: 20,
        CONF_SETTINGS_INTERVAL: 600,
        CONF_COUNTER_INTERVAL: 1800,
        CONF_HOT_WATER_MIN: 45,
        CONF_HOT_WATER_MAX: 55,
        # Stored in the controller's order, whatever order they were ticked in.
        CONF_OPERATING_MODES: ["summer", "winter", "cooling"],
        CONF_ADDRESS_OFFSET: -1,
        # Also in the fixed order of the choices.
        CONF_WRITABLE: ["clock", "operating_mode", "party_hours"],
    }


async def test_options_default_to_nothing_writable(
    hass: HomeAssistant, simulator: WpmSimulator
) -> None:
    entry = entry_for(simulator)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={})  # fresh setup: no options yet
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    default = next(key for key in schema if key == CONF_WRITABLE).default()
    assert default == []


@pytest.mark.parametrize(
    ("changes", "field", "error"),
    [
        (
            {CONF_HOT_WATER_MIN: 60.0, CONF_HOT_WATER_MAX: 50.0},
            CONF_HOT_WATER_MAX,
            "hot_water_range",
        ),
        ({CONF_OPERATING_MODES: []}, CONF_OPERATING_MODES, "no_operating_modes"),
    ],
)
async def test_options_are_checked(
    hass: HomeAssistant,
    simulator: WpmSimulator,
    changes: dict[str, object],
    field: str,
    error: str,
) -> None:
    entry = entry_for(simulator)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS_INPUT, **changes}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}
    assert CONF_OPERATING_MODES not in entry.options  # nothing stored
