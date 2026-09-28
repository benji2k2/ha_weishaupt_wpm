"""Constants for the Weishaupt WPM integration."""

from __future__ import annotations

DOMAIN = "weishaupt_wpm"

CONF_UNIT = "unit"
CONF_TRANSPORT = "transport"
CONF_STATUS_INTERVAL = "status_interval"
CONF_SETTINGS_INTERVAL = "settings_interval"
CONF_COUNTER_INTERVAL = "counter_interval"
CONF_ADDRESS_OFFSET = "address_offset"

DEFAULT_PORT = 502
DEFAULT_UNIT = 1
DEFAULT_STATUS_INTERVAL = 30
DEFAULT_SETTINGS_INTERVAL = 300
DEFAULT_COUNTER_INTERVAL = 900
DEFAULT_ADDRESS_OFFSET = 0

MIN_STATUS_INTERVAL = 10
MAX_STATUS_INTERVAL = 300
MIN_SLOW_INTERVAL = 60
MAX_SLOW_INTERVAL = 3600

# Settings land in the controller's non-volatile memory. A register is written at
# most once within this time, whatever an automation asks for.
WRITE_MIN_INTERVAL = 30

# The low part of a heat amount may roll over between two reads of the three parts;
# a drop smaller than this is such a glitch, not a counter reset.
COUNTER_GLITCH = 10_000

MANUFACTURER = "Weishaupt"
MODEL = "WPM"
