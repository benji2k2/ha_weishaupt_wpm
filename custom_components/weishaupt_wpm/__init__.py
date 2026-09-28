"""Weishaupt WPM — heat pump manager over Modbus RTU."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_TRANSPORT, CONF_UNIT
from .coordinator import WpmConfigEntry, WpmCoordinator
from .modbus import ModbusClient

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: WpmConfigEntry) -> bool:
    """Set up the heat pump from a config entry."""
    client = ModbusClient(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_UNIT],
        entry.data[CONF_TRANSPORT],
    )
    coordinator = WpmCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: WpmConfigEntry) -> None:
    """Apply changed intervals or address offset by reloading."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WpmConfigEntry) -> bool:
    """Unload a config entry and close the connection to the gateway."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.close()
    return unloaded
