"""Config flow for the Weishaupt WPM integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
import voluptuous as vol

from .const import (
    CONF_ADDRESS_OFFSET,
    CONF_COUNTER_INTERVAL,
    CONF_SETTINGS_INTERVAL,
    CONF_STATUS_INTERVAL,
    CONF_TRANSPORT,
    CONF_UNIT,
    DEFAULT_ADDRESS_OFFSET,
    DEFAULT_COUNTER_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SETTINGS_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_UNIT,
    DOMAIN,
    MAX_SLOW_INTERVAL,
    MAX_STATUS_INTERVAL,
    MIN_SLOW_INTERVAL,
    MIN_STATUS_INTERVAL,
)
from .modbus import (
    TRANSPORT_RTU_OVER_TCP,
    TRANSPORTS,
    ModbusClient,
    ModbusConnectionError,
    ModbusError,
    ModbusExceptionResponse,
    ModbusTimeout,
)
from .registers import REGISTERS_BY_KEY

_LOGGER = logging.getLogger(__name__)

TITLE = "Weishaupt WPM"

# Option values must be valid translation keys, so no "-1".
OFFSET_OPTIONS = {"0": 0, "minus_1": -1}

# Read while setting up: the outdoor temperature exists on every controller.
_TEST_REGISTER = REGISTERS_BY_KEY["outdoor_temperature"].address


def _offset_option(offset: int) -> str:
    return next((key for key, value in OFFSET_OPTIONS.items() if value == offset), "0")


def _box(minimum: int, maximum: int, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX)
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _connection_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): TextSelector(),
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): _box(1, 65535),
            vol.Required(CONF_UNIT, default=defaults.get(CONF_UNIT, DEFAULT_UNIT)): _box(1, 247),
            vol.Required(
                CONF_TRANSPORT, default=defaults.get(CONF_TRANSPORT, TRANSPORT_RTU_OVER_TCP)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(TRANSPORTS),
                    mode=SelectSelectorMode.LIST,
                    translation_key=CONF_TRANSPORT,
                )
            ),
        }
    )


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_PORT: int(user_input[CONF_PORT]),
        CONF_UNIT: int(user_input[CONF_UNIT]),
        CONF_TRANSPORT: user_input[CONF_TRANSPORT],
    }


def _unique_id(data: dict[str, Any]) -> str:
    return f"{data[CONF_HOST]}:{data[CONF_PORT]}/{data[CONF_UNIT]}"


async def _async_test(data: dict[str, Any]) -> str | None:
    """Read one register. Returns an error key, or None when the controller answered."""
    client = ModbusClient(
        data[CONF_HOST], data[CONF_PORT], data[CONF_UNIT], data[CONF_TRANSPORT], retries=0
    )
    try:
        await client.read_holding_registers(_TEST_REGISTER, 1)
    except ModbusConnectionError:
        return "gateway_unreachable"
    except ModbusTimeout:
        return "no_answer"
    except ModbusExceptionResponse:
        return "invalid_response"
    except ModbusError:
        return "invalid_response"
    except Exception:
        _LOGGER.exception("Unexpected error while contacting %s", data[CONF_HOST])
        return "unknown"
    finally:
        await client.close()
    return None


class WpmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the heat pump by the address of its gateway."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _clean(user_input)
            await self.async_set_unique_id(_unique_id(user_input))
            self._abort_if_unique_id_configured()
            error = await _async_test(user_input)
            if error is None:
                return self.async_create_entry(title=TITLE, data=user_input)
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _clean(user_input)
            unique_id = _unique_id(user_input)
            if unique_id != entry.unique_id:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
            same_gateway = (user_input[CONF_HOST], user_input[CONF_PORT]) == (
                entry.data[CONF_HOST],
                entry.data[CONF_PORT],
            )
            if same_gateway and entry.state is ConfigEntryState.LOADED:
                # The gateway takes one client; the running integration reconnects
                # on its next poll, and the entry is reloaded after a successful test.
                await entry.runtime_data.client.close()
            error = await _async_test(user_input)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, unique_id=unique_id, data_updates=user_input
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> WpmOptionsFlow:  # noqa: ANN001
        return WpmOptionsFlow()


class WpmOptionsFlow(OptionsFlow):
    """Polling intervals and address offset."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_STATUS_INTERVAL: int(user_input[CONF_STATUS_INTERVAL]),
                    CONF_SETTINGS_INTERVAL: int(user_input[CONF_SETTINGS_INTERVAL]),
                    CONF_COUNTER_INTERVAL: int(user_input[CONF_COUNTER_INTERVAL]),
                    CONF_ADDRESS_OFFSET: OFFSET_OPTIONS[user_input[CONF_ADDRESS_OFFSET]],
                }
            )
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_STATUS_INTERVAL,
                        default=options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
                    ): _box(MIN_STATUS_INTERVAL, MAX_STATUS_INTERVAL, "s"),
                    vol.Required(
                        CONF_SETTINGS_INTERVAL,
                        default=options.get(CONF_SETTINGS_INTERVAL, DEFAULT_SETTINGS_INTERVAL),
                    ): _box(MIN_SLOW_INTERVAL, MAX_SLOW_INTERVAL, "s"),
                    vol.Required(
                        CONF_COUNTER_INTERVAL,
                        default=options.get(CONF_COUNTER_INTERVAL, DEFAULT_COUNTER_INTERVAL),
                    ): _box(MIN_SLOW_INTERVAL, MAX_SLOW_INTERVAL, "s"),
                    vol.Required(
                        CONF_ADDRESS_OFFSET,
                        default=_offset_option(
                            options.get(CONF_ADDRESS_OFFSET, DEFAULT_ADDRESS_OFFSET)
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(OFFSET_OPTIONS),
                            mode=SelectSelectorMode.LIST,
                            translation_key=CONF_ADDRESS_OFFSET,
                        )
                    ),
                }
            ),
        )
