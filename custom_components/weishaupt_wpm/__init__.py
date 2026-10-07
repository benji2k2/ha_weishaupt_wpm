"""Weishaupt WPM — heat pump manager over Modbus RTU."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_TRANSPORT, CONF_UNIT, WRITABLE_CHOICES
from .coordinator import WpmConfigEntry, WpmCoordinator
from .modbus import ModbusClient

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]


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
    _remove_entities_of_the_other_kind(hass, entry, coordinator)

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


def _remove_entities_of_the_other_kind(
    hass: HomeAssistant, entry: WpmConfigEntry, coordinator: WpmCoordinator
) -> None:
    """After the writable settings changed, drop what now belongs to another platform.

    A writable setting is a number (or the select, or the clock button), a read-only
    one a sensor. Without this, the registry would keep the old entity as orphan.
    """
    registry = er.async_get(hass)
    prefix = f"{coordinator.device_identifier}_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not entity.unique_id.startswith(prefix):
            continue
        key = entity.unique_id[len(prefix) :]
        if key == "set_clock":  # the button's key
            key = "clock"
        if key not in WRITABLE_CHOICES:
            continue
        writable = key in coordinator.writable
        if entity.domain in ("number", "select", "button"):
            stale = not writable
        elif entity.domain == "sensor":
            stale = writable and key != "operating_mode"
        else:
            stale = False
        if stale:
            registry.async_remove(entity.entity_id)
