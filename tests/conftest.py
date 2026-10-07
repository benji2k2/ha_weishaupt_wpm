"""Shared fixtures: a simulated WPM on 127.0.0.1 and a config entry pointing at it."""

from __future__ import annotations

from collections.abc import AsyncIterator

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weishaupt_wpm.const import (
    CONF_TRANSPORT,
    CONF_UNIT,
    CONF_WRITABLE,
    DOMAIN,
    WRITABLE_CHOICES,
)
from custom_components.weishaupt_wpm.modbus import ModbusClient
from tools.simulator import WpmSimulator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: ANN001
    """Load custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def allow_local_tcp(socket_enabled):  # noqa: ANN001
    """The simulator is a real TCP server on 127.0.0.1."""
    return


@pytest.fixture(autouse=True)
def no_pause_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real controller needs a breather between requests, the simulator does not."""
    defaults = dict(ModbusClient.__init__.__kwdefaults__, pause=0.0)
    monkeypatch.setattr(ModbusClient.__init__, "__kwdefaults__", defaults)


@pytest.fixture
async def simulator() -> AsyncIterator[WpmSimulator]:
    sim = WpmSimulator()
    await sim.start()
    try:
        yield sim
    finally:
        await sim.stop()


def entry_for(simulator: WpmSimulator, **options: object) -> MockConfigEntry:
    """A config entry for the simulator; everything writable unless the test says otherwise."""
    options.setdefault(CONF_WRITABLE, list(WRITABLE_CHOICES))
    port = simulator._server.sockets[0].getsockname()[1]
    return MockConfigEntry(
        domain=DOMAIN,
        title="Weishaupt WPM",
        unique_id=f"127.0.0.1:{port}/1",
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_UNIT: simulator.unit,
            CONF_TRANSPORT: simulator.transport,
        },
        options=options,
    )


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, simulator: WpmSimulator
) -> AsyncIterator[MockConfigEntry]:
    entry = entry_for(simulator)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    # Close the connection before the simulator goes away (also after a reload).
    await hass.async_block_till_done()
    if entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
