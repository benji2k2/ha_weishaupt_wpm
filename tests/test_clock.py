"""Setting the controller clock: only on request, only the parts that differ."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.weishaupt_wpm import coordinator as coordinator_module
from custom_components.weishaupt_wpm.modbus import (
    check_response_pdu,
    write_coil_request_pdu,
)
from tools.simulator import WpmSimulator

from .conftest import entry_for

BUTTON = "button.weishaupt_wpm_set_controller_clock"
DEVIATION = "sensor.weishaupt_wpm_controller_clock_deviation"
WRITE_COUNT = "sensor.weishaupt_wpm_writes_to_the_controller"


def test_coil_request() -> None:
    request = write_coil_request_pdu(103)
    assert request == bytes([0x05, 0x00, 0x67, 0xFF, 0x00])
    assert check_response_pdu(request, request) == request


async def _setup(hass: HomeAssistant, simulator: WpmSimulator, minutes: int):
    simulator.set_clock((dt_util.now() + timedelta(minutes=minutes)).timetuple())
    entry = entry_for(simulator)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _press(hass: HomeAssistant) -> None:
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)


async def test_minute_behind_writes_only_the_minute(
    hass: HomeAssistant, simulator: WpmSimulator, freezer
) -> None:
    freezer.move_to("2026-10-07 13:56:10+00:00")
    entry = await _setup(hass, simulator, -10)
    assert hass.states.get(DEVIATION).state == "-10"
    await _press(hass)
    now = dt_util.now()
    assert simulator.writes == [(214, now.minute)]
    assert simulator.coil_writes == [103]
    assert simulator.get(214) == now.minute
    assert hass.states.get(DEVIATION).state == "0"
    assert hass.states.get(WRITE_COUNT).state == "2"  # value + set coil
    log = list(entry.runtime_data.write_log)
    assert log[-1]["key"] == "clock" and log[-1]["result"] == "ok"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_correct_clock_writes_nothing(
    hass: HomeAssistant, simulator: WpmSimulator, freezer
) -> None:
    freezer.move_to("2026-10-07 13:56:10+00:00")
    entry = await _setup(hass, simulator, 0)
    await _press(hass)
    assert simulator.writes == []
    assert simulator.coil_writes == []
    assert hass.states.get(WRITE_COUNT).state == "0"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_new_day_writes_date_parts_before_the_time(
    hass: HomeAssistant, simulator: WpmSimulator, freezer
) -> None:
    freezer.move_to("2026-10-07 13:56:10+00:00")
    entry = await _setup(hass, simulator, -24 * 60 - 5)  # yesterday, 5 min behind
    await _press(hass)
    assert [address for address, _ in simulator.writes] == [217, 216, 214]  # day, weekday, minute
    assert simulator.coil_writes == [104, 107, 103]
    assert hass.states.get(DEVIATION).state == "0"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_waits_for_the_next_minute_after_the_half(
    hass: HomeAssistant, simulator: WpmSimulator, freezer, monkeypatch: pytest.MonkeyPatch
) -> None:
    freezer.move_to("2026-10-07 13:56:45+00:00")
    entry = await _setup(hass, simulator, -3)
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        freezer.tick(seconds)

    monkeypatch.setattr(coordinator_module, "_sleep", fake_sleep)
    await _press(hass)
    assert waits and 15 <= waits[0] <= 16
    assert simulator.get(214) == dt_util.now().minute == 57
    await hass.config_entries.async_unload(entry.entry_id)


async def test_not_taken_over_without_the_set_coil(
    hass: HomeAssistant, simulator: WpmSimulator, freezer
) -> None:
    """A controller that ignores the coil: the read-back shows it, the press fails."""
    freezer.move_to("2026-10-07 13:56:10+00:00")
    entry = await _setup(hass, simulator, -10)
    simulator.ignore_set_coils = True
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass)
    assert err.value.translation_key == "clock_not_applied"
    assert simulator.get(214) != dt_util.now().minute
    assert entry.runtime_data.write_log[-1]["result"] == "not applied"
    assert hass.states.get(DEVIATION).state == "-10"
    await hass.config_entries.async_unload(entry.entry_id)
