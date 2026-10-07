"""Polling and writing for the Weishaupt WPM integration."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADDRESS_OFFSET,
    CONF_COUNTER_INTERVAL,
    CONF_HOT_WATER_MAX,
    CONF_HOT_WATER_MIN,
    CONF_OPERATING_MODES,
    CONF_SETTINGS_INTERVAL,
    CONF_STATUS_INTERVAL,
    COUNTER_GLITCH,
    DEFAULT_ADDRESS_OFFSET,
    DEFAULT_COUNTER_INTERVAL,
    DEFAULT_HOT_WATER_MAX,
    DEFAULT_HOT_WATER_MIN,
    DEFAULT_SETTINGS_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    WRITE_MIN_INTERVAL,
)
from .modbus import ModbusClient, ModbusError, ModbusExceptionResponse
from .registers import (
    CLOCK_FIELDS,
    COUNTERS,
    DEFAULT_OPERATING_MODES,
    OPERATING_MODES,
    REGISTERS_BY_ADDRESS,
    REGISTERS_BY_KEY,
    Counter,
    Group,
    addresses_by_group,
    blocks,
)

_LOGGER = logging.getLogger(__name__)

# Indirection so tests can skip the wait for the next full minute.
_sleep = asyncio.sleep


@dataclass(slots=True)
class WpmData:
    """Latest raw values by documented address, plus the combined heat amounts."""

    raw: dict[int, int] = field(default_factory=dict)
    counters: dict[str, int | None] = field(default_factory=dict)

    def value(self, key: str) -> float | int | None:
        """Scaled value of a register, None if missing or implausible."""
        register = REGISTERS_BY_KEY[key]
        raw = self.raw.get(register.address)
        return None if raw is None else register.decode(raw)


type WpmConfigEntry = ConfigEntry[WpmCoordinator]


class WpmCoordinator(DataUpdateCoordinator[WpmData]):
    """Reads the controller in three tiers and guards every write."""

    config_entry: WpmConfigEntry

    def __init__(self, hass: HomeAssistant, entry: WpmConfigEntry, client: ModbusClient) -> None:
        options = entry.options
        self.intervals: dict[Group, int] = {
            Group.STATUS: options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
            Group.SETTINGS: options.get(CONF_SETTINGS_INTERVAL, DEFAULT_SETTINGS_INTERVAL),
            Group.COUNTERS: options.get(CONF_COUNTER_INTERVAL, DEFAULT_COUNTER_INTERVAL),
        }
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=timedelta(seconds=self.intervals[Group.STATUS]),
        )
        self.client = client
        self.offset: int = int(options.get(CONF_ADDRESS_OFFSET, DEFAULT_ADDRESS_OFFSET))
        # The user's narrower limits on top of the technical write ranges.
        self.hot_water_range: tuple[int, int] = (
            options.get(CONF_HOT_WATER_MIN, DEFAULT_HOT_WATER_MIN),
            options.get(CONF_HOT_WATER_MAX, DEFAULT_HOT_WATER_MAX),
        )
        allowed = options.get(
            CONF_OPERATING_MODES, [OPERATING_MODES[code] for code in DEFAULT_OPERATING_MODES]
        )
        self.operating_modes: tuple[int, ...] = tuple(
            code for code, key in OPERATING_MODES.items() if key in allowed
        )
        self.unsupported: set[int] = set()
        self.group_addresses = addresses_by_group()
        self.write_log: deque[dict[str, Any]] = deque(maxlen=20)
        # Write commands sent to the controller since setup; the sensor adds the restored total.
        self.write_count = 0
        # Controller clock minus Home Assistant's clock in minutes, as of the last read.
        self.clock_deviation: int | None = None
        self._raw: dict[int, int] = {}
        self._counters: dict[str, int | None] = {}
        self._last_read: dict[Group, float] = {}
        self._last_write: dict[str, float] = {}

    @property
    def device_identifier(self) -> str:
        """Stable identifier for the device registry."""
        return self.config_entry.unique_id or self.config_entry.entry_id

    async def _async_update_data(self) -> WpmData:
        now = time.monotonic()
        # Status is read on every run (the run interval is the status interval, and a
        # refresh after a write should show the effect). The slower tiers follow their
        # own interval, with one second of slack so a tier is not skipped by an early tick.
        due = [
            group
            for group in Group
            if group is Group.STATUS
            or group not in self._last_read
            or now - self._last_read[group] >= self.intervals[group] - 1
        ]
        try:
            for group in due:
                await self._async_read_group(group)
                self._last_read[group] = now
        except ModbusError as err:
            raise UpdateFailed(f"Could not read the heat pump: {err}") from err
        if Group.SETTINGS in due:
            self._update_clock_deviation()
        self._combine_counters()
        return self._snapshot()

    def _snapshot(self) -> WpmData:
        return WpmData(raw=dict(self._raw), counters=dict(self._counters))

    async def _async_read_group(self, group: Group) -> None:
        for start, count in blocks(self.group_addresses[group], self.unsupported):
            try:
                values = await self.client.read_holding_registers(start + self.offset, count)
            except ModbusExceptionResponse:
                # The pCO refuses a block that touches a register it does not have.
                # Find out which one and leave it out from now on.
                await self._async_read_singly(start, count)
                continue
            for index, value in enumerate(values):
                self._raw[start + index] = value

    async def _async_read_singly(self, start: int, count: int) -> None:
        for address in range(start, start + count):
            try:
                values = await self.client.read_holding_registers(address + self.offset, 1)
            except ModbusExceptionResponse as err:
                self.unsupported.add(address)
                self._raw.pop(address, None)
                register = REGISTERS_BY_ADDRESS.get(address)
                _LOGGER.info(
                    "Register %s (%s) is not available on this controller: %s",
                    address,
                    register.key if register else "heat amount",
                    err,
                )
                continue
            self._raw[address] = values[0]

    def _combine_counters(self) -> None:
        for counter in COUNTERS:
            parts = [self._raw.get(address) for address in counter.addresses]
            if any(part is None for part in parts):
                self._counters[counter.key] = None
                continue
            total = Counter.combine(*parts)  # type: ignore[arg-type]
            previous = self._counters.get(counter.key)
            if previous is not None and previous - COUNTER_GLITCH < total < previous:
                _LOGGER.debug(
                    "%s dropped from %s to %s, keeping the previous value",
                    counter.key,
                    previous,
                    total,
                )
                continue
            self._counters[counter.key] = total

    def _controller_clock(self) -> datetime | None:
        parts = {f.key: self._raw.get(f.address) for f in CLOCK_FIELDS}
        if any(value is None for value in parts.values()):
            return None
        try:
            return datetime(
                2000 + parts["year"],  # type: ignore[operator]
                parts["month"],  # type: ignore[arg-type]
                parts["day"],  # type: ignore[arg-type]
                parts["hour"],  # type: ignore[arg-type]
                parts["minute"],  # type: ignore[arg-type]
                tzinfo=dt_util.get_default_time_zone(),
            )
        except ValueError:
            return None

    def _update_clock_deviation(self) -> None:
        controller = self._controller_clock()
        if controller is None:
            self.clock_deviation = None
            return
        now = dt_util.now().replace(second=0, microsecond=0)
        self.clock_deviation = round((controller - now).total_seconds() / 60)

    async def async_set_clock(self) -> None:
        """Set the controller's date and time to Home Assistant's, on explicit request.

        Only parts that differ are written, each followed by its "set" coil, and the
        clock is read back afterwards. The controller keeps no seconds, so after the
        half minute the write waits for the next full minute.
        """
        now_mono = time.monotonic()
        last = self._last_write.get("clock")
        if last is not None and now_mono - last < WRITE_MIN_INTERVAL:
            wait = int(WRITE_MIN_INTERVAL - (now_mono - last)) + 1
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_too_soon",
                translation_placeholders={"seconds": str(wait)},
            )
        self._last_write["clock"] = now_mono
        current = dt_util.now()
        if current.second >= 30:
            await _sleep(60 - current.second - current.microsecond / 1e6 + 0.2)

        start = min(f.address for f in CLOCK_FIELDS)
        count = max(f.address for f in CLOCK_FIELDS) - start + 1
        try:
            await self._async_read_clock(start, count)
        except ModbusError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        target = dt_util.now()
        wanted = {
            "year": target.year % 100,
            "month": target.month,
            "day": target.day,
            "weekday": target.isoweekday(),
            "hour": target.hour,
            "minute": target.minute,
        }
        changes = [f for f in CLOCK_FIELDS if self._raw.get(f.address) != wanted[f.key]]
        if not changes:
            self._update_clock_deviation()
            self.async_set_updated_data(self._snapshot())
            return

        entry: dict[str, Any] = {
            "time": dt_util.utcnow().isoformat(),
            "key": "clock",
            "before": {f.key: self._raw.get(f.address) for f in CLOCK_FIELDS},
            "written": {f.key: wanted[f.key] for f in changes},
        }
        self.write_log.append(entry)
        try:
            for clock_field in changes:
                self.write_count += 1
                self.async_update_listeners()
                await self.client.write_register(
                    clock_field.address + self.offset, wanted[clock_field.key]
                )
                self.write_count += 1
                self.async_update_listeners()
                await self.client.write_coil(clock_field.set_coil + self.offset)
        except ModbusExceptionResponse as err:
            entry["result"] = f"rejected: {err}"
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_rejected",
                translation_placeholders={"code": str(err.code)},
            ) from err
        except ModbusError as err:
            entry["result"] = f"failed: {err}"
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        try:
            await self._async_read_clock(start, count)
        except ModbusError as err:
            entry["result"] = f"written, read-back failed: {err}"
            await self.async_request_refresh()
            return
        entry["read_back"] = {f.key: self._raw.get(f.address) for f in CLOCK_FIELDS}
        applied = all(self._raw.get(f.address) == wanted[f.key] for f in changes)
        entry["result"] = "ok" if applied else "not applied"
        self._update_clock_deviation()
        self.async_set_updated_data(self._snapshot())
        if not applied:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="clock_not_applied")

    async def _async_read_clock(self, start: int, count: int) -> None:
        values = await self.client.read_holding_registers(start + self.offset, count)
        for index, value in enumerate(values):
            self._raw[start + index] = value

    def write_range(self, key: str) -> tuple[float, float]:
        """Allowed range for writing a register: technical range, narrowed by options."""
        register = REGISTERS_BY_KEY[key]
        assert register.write_min is not None and register.write_max is not None
        if key == "hot_water_setpoint":
            low, high = self.hot_water_range
            return max(low, register.write_min), min(high, register.write_max)
        return register.write_min, register.write_max

    async def async_write(self, key: str, value: float) -> None:
        """Write a setting on explicit request, never more than needed.

        The value must be inside the register's write range. Writing the value that
        is already set does nothing. A register is written at most once per
        WRITE_MIN_INTERVAL seconds. The register is read back afterwards.
        """
        register = REGISTERS_BY_KEY[key]
        if not register.writable:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_writable",
                translation_placeholders={"key": key},
            )
        low, high = self.write_range(key)
        if not low <= value <= high:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="out_of_range",
                translation_placeholders={
                    "value": f"{value:g}",
                    "min": f"{low:g}",
                    "max": f"{high:g}",
                },
            )
        if key == "operating_mode" and int(value) not in self.operating_modes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="mode_not_allowed",
                translation_placeholders={"mode": OPERATING_MODES.get(int(value), str(value))},
            )
        raw = register.encode(value)
        if self._raw.get(register.address) == raw:
            return

        now = time.monotonic()
        last = self._last_write.get(key)
        if last is not None and now - last < WRITE_MIN_INTERVAL:
            wait = int(WRITE_MIN_INTERVAL - (now - last)) + 1
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_too_soon",
                translation_placeholders={"seconds": str(wait)},
            )
        self._last_write[key] = now
        entry: dict[str, Any] = {
            "time": dt_util.utcnow().isoformat(),
            "key": key,
            "address": register.address,
            "before": self._raw.get(register.address),
            "written": raw,
        }
        self.write_log.append(entry)
        # Counted before sending: a write that times out may still have reached the controller.
        self.write_count += 1
        self.async_update_listeners()
        try:
            await self.client.write_register(register.address + self.offset, raw)
        except ModbusExceptionResponse as err:
            entry["result"] = f"rejected: {err}"
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_rejected",
                translation_placeholders={"code": str(err.code)},
            ) from err
        except ModbusError as err:
            entry["result"] = f"failed: {err}"
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        try:
            values = await self.client.read_holding_registers(register.address + self.offset, 1)
        except ModbusError as err:
            entry["result"] = f"written, read-back failed: {err}"
            await self.async_request_refresh()
            return
        self._raw[register.address] = values[0]
        entry["read_back"] = values[0]
        entry["result"] = "ok" if values[0] == raw else "not applied"
        self.async_set_updated_data(self._snapshot())
        if values[0] != raw:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_not_applied",
                translation_placeholders={"value": str(values[0])},
            )
