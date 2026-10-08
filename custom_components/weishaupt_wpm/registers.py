"""Every data point of the WPM, in one place.

Source: Dimplex wiki, "Modbus RTU - Datenpunktliste", WPM software J/L/M, address
range 1...207 (the Weishaupt WPM 5.0M runs Dimplex software, here L23.2).

Addresses are the numbers as documented. The Modbus PDU address is
``address + offset``; the offset is 0 unless verification at the display shows
that everything is shifted by one (option in the integration, ``--offset`` in the
tools).

``verified`` flips to True only after a value was compared with the display of the
real controller. Until then every entry is taken from the documentation.

No Home Assistant imports, Python 3.9 compatible (used by tools/probe.py and
tools/simulator.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DataType(str, Enum):
    INT16 = "int16"
    UINT16 = "uint16"


class Group(str, Enum):
    """Polling tiers: values that change quickly are read more often."""

    STATUS = "status"
    SETTINGS = "settings"
    COUNTERS = "counters"


@dataclass(frozen=True)
class Register:
    """One holding register."""

    key: str
    address: int
    group: Group
    description: str
    data_type: DataType = DataType.UINT16
    scale: float = 1.0
    offset: float = 0.0
    # Plausibility: decoded values outside are treated as "no value" (missing sensor).
    valid_min: float | None = None
    valid_max: float | None = None
    # Writing is only allowed for registers that have a write range.
    write_min: float | None = None
    write_max: float | None = None
    # May not exist on every unit (depends on model and configuration).
    optional: bool = False
    verified: bool = False

    @property
    def writable(self) -> bool:
        return self.write_min is not None and self.write_max is not None

    def decode(self, raw: int) -> float | int | None:
        """Scaled value, or None when it is outside the plausible range."""
        value: float | int = raw
        if self.data_type is DataType.INT16 and raw >= 0x8000:
            value = raw - 0x10000
        if self.scale != 1:
            value = value * self.scale
        if self.offset != 0:
            value = value + self.offset
        if self.scale != 1 or self.offset != 0:
            value = round(value, 3)
        if self.valid_min is not None and value < self.valid_min:
            return None
        if self.valid_max is not None and value > self.valid_max:
            return None
        return value

    def encode(self, value: float) -> int:
        """Raw register value for a scaled value."""
        value = value - self.offset
        raw = round(value / self.scale)
        if self.data_type is DataType.INT16:
            if not -0x8000 <= raw <= 0x7FFF:
                raise ValueError(f"{value} does not fit {self.key}")
            return raw & 0xFFFF
        if not 0 <= raw <= 0xFFFF:
            raise ValueError(f"{value} does not fit {self.key}")
        return raw


@dataclass(frozen=True)
class Counter:
    """A heat or energy amount spread over three registers (digits 1-4, 5-8, 9-12)."""

    key: str
    low: int
    mid: int
    high: int
    description: str
    optional: bool = False
    verified: bool = False

    @property
    def addresses(self) -> tuple[int, int, int]:
        return (self.low, self.mid, self.high)

    @staticmethod
    def combine(low: int, mid: int, high: int) -> int:
        return high * 100_000_000 + mid * 10_000 + low


def _temperature(
    key: str, address: int, description: str, *, optional: bool = False, verified: bool = False
) -> Register:
    return Register(
        key,
        address,
        Group.STATUS,
        description,
        DataType.INT16,
        0.1,
        valid_min=-50.0,
        valid_max=100.0,
        optional=optional,
        verified=verified,
    )


def _runtime(
    key: str, address: int, description: str, *, optional: bool = False, verified: bool = False
) -> Register:
    return Register(key, address, Group.COUNTERS, description, optional=optional, verified=verified)


REGISTERS: tuple[Register, ...] = (
    # Operating data (0.1 °C)
    _temperature("outdoor_temperature", 1, "Außentemperatur (R1)"),
    _temperature(
        "return_temperature",
        2,
        "Temperatur Rücklauf (R2), am Display „Heizkreis 1 Ist“",
        verified=True,
    ),
    _temperature("hot_water_temperature", 3, "Temperatur Warmwasser (R3)", verified=True),
    _temperature(
        "flow_temperature",
        5,
        "Temperatur Vorlauf (R9), am Display „Wärmepumpe Vorlauf“",
        verified=True,
    ),
    _temperature(
        "heat_source_inlet_temperature",
        6,
        "Temperatur Wärmequelleneintritt (R24), nur mit elektronischem Expansionsventil",
        optional=True,
    ),
    _temperature(
        "heat_source_outlet_temperature", 7, "Temperatur Wärmequellenaustritt (R6)", optional=True
    ),
    _temperature(
        "return_setpoint",
        53,
        "Temperatur Rücklaufsoll, am Display „Heizkreis 1 Soll“",
        verified=True,
    ),
    _temperature("hot_water_setpoint_active", 58, "Temperatur Warmwassersoll", verified=True),
    Register(
        "pressure_8",
        8,
        Group.STATUS,
        "Hochdruck, am Display „Drucksensoren Wärmepumpe“ (nicht in der Dimplex-Liste)",
        scale=0.1,
        optional=True,
        verified=True,
    ),
    Register(
        "pressure_101",
        101,
        Group.STATUS,
        "Niederdruck, am Display „Drucksensoren Wärmepumpe“ (nicht in der Dimplex-Liste)",
        scale=0.1,
        optional=True,
        verified=True,
    ),
    # System status (codes, see tables below)
    Register("status", 103, Group.STATUS, "Statusmeldungen"),
    Register("lock", 104, Group.STATUS, "Sperrmeldungen"),
    Register("fault", 105, Group.STATUS, "Störmeldungen"),
    Register("sensor_fault", 106, Group.STATUS, "Sensorfehler"),
    # Operating mode
    Register(
        "operating_mode",
        222,
        Group.SETTINGS,
        "Betriebsmodus",
        valid_min=0,
        valid_max=5,
        write_min=0,
        write_max=5,
        verified=True,  # 0 = summer and 1 = winter seen at the display
    ),
    Register(
        "party_hours",
        223,
        Group.SETTINGS,
        "Anzahl Partystunden",
        valid_max=72,
        write_min=0,
        write_max=72,
        verified=True,
    ),
    Register(
        "holiday_days",
        224,
        Group.SETTINGS,
        "Anzahl Urlaubstage",
        valid_max=150,
        write_min=0,
        write_max=150,
        verified=True,
    ),
    # Heating circuit 1. Only the heating curve step (243) can be changed, like every write
    # only on an explicit user action. Step (17 = -2, 20 = +1, 16 = -3), hysteresis and end
    # point were compared with the display; fixed setpoint and room temperature are hidden
    # there when the controller follows the heating curve.
    Register(
        "room_temperature_setpoint",
        46,
        Group.SETTINGS,
        "Raumtemperatur Solltemperatur",
        scale=0.1,
        valid_min=10.0,
        valid_max=35.0,
        write_min=15.0,
        write_max=30.0,
    ),
    Register(
        "heating_hysteresis",
        47,
        Group.SETTINGS,
        "Heizung Hysterese",
        scale=0.1,
        valid_min=0.5,
        valid_max=5.0,
        write_min=0.5,
        write_max=5.0,
        verified=True,
    ),
    Register(
        "heating_curve_offset",
        243,
        Group.SETTINGS,
        "Heizkurve Verschiebung (Stufe)",
        offset=-19.0,
        valid_min=-19.0,
        valid_max=19.0,
        write_min=-19.0,
        write_max=19.0,
        verified=True,
    ),
    Register(
        "heating_curve_fixed_setpoint",
        244,
        Group.SETTINGS,
        "Festwertsolltemperatur (HK1)",
        valid_min=18.0,
        valid_max=60.0,
        write_min=18.0,
        write_max=60.0,
    ),
    Register(
        "heating_curve_end_point",
        245,
        Group.SETTINGS,
        "Heizkurvenendpunkt (HK1)",
        valid_min=20.0,
        valid_max=70.0,
        write_min=20.0,
        write_max=70.0,
        verified=True,
    ),
    # Hot water settings in whole °C / K (254 = 46 while 58 shows 46.0, display 46.0 °C).
    # The write range of the setpoint is the technical one from the Weishaupt manual
    # (30...85 °C); the integration's options narrow it (default 40...60 °C).
    Register(
        "hot_water_hysteresis",
        252,
        Group.SETTINGS,
        "Warmwasser Hysterese",
        valid_min=0,
        valid_max=30,
        write_min=2,
        write_max=15,
        verified=True,
    ),
    Register(
        "hot_water_setpoint",
        254,
        Group.SETTINGS,
        "Warmwasser Solltemperatur",
        valid_min=10,
        valid_max=85,
        write_min=30,
        write_max=85,
        verified=True,
    ),
    Register(
        "hot_water_setpoint_max",
        255,
        Group.SETTINGS,
        "Warmwasser Solltemperatur Maximal",
        valid_min=10,
        valid_max=85,
        write_min=10,
        write_max=85,
        verified=True,
    ),
    Register(
        "hot_water_setpoint_min",
        352,
        Group.SETTINGS,
        "Warmwasser Solltemperatur Minimal",
        valid_min=10,
        valid_max=85,
        optional=True,
        write_min=10,
        write_max=85,
    ),
    # Runtimes (hours)
    _runtime(
        "runtime_aux_pump", 71, "Laufzeit Zusatzumwälzpumpe (M16)", optional=True, verified=True
    ),
    _runtime("runtime_compressor_1", 72, "Laufzeit Verdichter 1", verified=True),
    _runtime("runtime_compressor_2", 73, "Laufzeit Verdichter 2", optional=True),
    _runtime("runtime_primary_pump", 74, "Laufzeit Primärpumpe / Ventilator (M11)", verified=True),
    _runtime("runtime_second_heat_generator", 75, "Laufzeit 2. Wärmeerzeuger (E10)", verified=True),
    _runtime("runtime_heating_pump", 76, "Laufzeit Heizungspumpe (M13)", verified=True),
    _runtime("runtime_hot_water_pump", 77, "Laufzeit Warmwasserpumpe (M18)", verified=True),
    _runtime("runtime_flange_heater", 78, "Laufzeit Flanschheizung (E9)", verified=True),
    _runtime("runtime_pool_pump", 79, "Laufzeit Schwimmbadpumpe (M19)", optional=True),
)

COUNTERS: tuple[Counter, ...] = (
    Counter("heat_heating", 303, 304, 305, "Wärmemenge Heizen", verified=True),
    Counter("heat_hot_water", 306, 307, 308, "Wärmemenge Warmwasser", verified=True),
    Counter("environmental_energy", 334, 335, 336, "Umweltenergie", verified=True),
)


@dataclass(frozen=True)
class ClockField:
    """One part of the controller's date and time (Dimplex "Zeitabgleich", software J/L).

    A written value only takes effect after writing 1 to its "set" coil right away;
    the coil resets itself to 0.
    """

    key: str
    address: int
    set_coil: int
    low: int
    high: int


# In the order they are written: the date first, the minute last.
CLOCK_FIELDS: tuple[ClockField, ...] = (
    ClockField("year", 218, 106, 0, 99),
    ClockField("month", 215, 105, 1, 12),
    ClockField("day", 217, 104, 1, 31),
    ClockField("weekday", 216, 107, 1, 7),  # 1 = Monday, like isoweekday()
    ClockField("hour", 213, 102, 0, 23),
    ClockField("minute", 214, 103, 0, 59),
)

REGISTERS_BY_KEY: dict[str, Register] = {register.key: register for register in REGISTERS}
REGISTERS_BY_ADDRESS: dict[int, Register] = {register.address: register for register in REGISTERS}
COUNTERS_BY_KEY: dict[str, Counter] = {counter.key: counter for counter in COUNTERS}


def addresses_by_group() -> dict[Group, list[int]]:
    """All documented addresses to poll, per group, sorted."""
    groups: dict[Group, list[int]] = {group: [] for group in Group}
    for register in REGISTERS:
        groups[register.group].append(register.address)
    for counter in COUNTERS:
        groups[Group.COUNTERS].extend(counter.addresses)
    groups[Group.SETTINGS].extend(field.address for field in CLOCK_FIELDS)
    return {group: sorted(set(addresses)) for group, addresses in groups.items()}


def blocks(
    addresses: list[int], skip: set[int] | frozenset[int] = frozenset()
) -> list[tuple[int, int]]:
    """Contiguous (start, count) runs, leaving out ``skip``.

    Only strictly neighbouring registers are combined, gaps are never bridged. The
    WPM 5.0M (L23.2) answers unused addresses with 0, but the Dimplex documentation
    allows an exception there, and a block containing one would fail as a whole.
    """
    runs: list[tuple[int, int]] = []
    for address in sorted(set(addresses) - set(skip)):
        if runs and runs[-1][0] + runs[-1][1] == address:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((address, 1))
    return runs


# Register 222. Which modes are offered for selection is an option of the integration;
# by default not "2nd heat generator" (heating rod alone) and not "cooling" (needs a
# cooling setup).
OPERATING_MODES: dict[int, str] = {
    0: "summer",
    1: "winter",
    2: "holiday",
    3: "party",
    4: "second_heat_generator",
    5: "cooling",
}
DEFAULT_OPERATING_MODES: tuple[int, ...] = (0, 1, 2, 3)

# Register 103, software L/M.
STATUS_CODES: dict[int, str] = {
    0: "off",
    1: "off",
    2: "heating",
    3: "pool",
    4: "hot_water",
    5: "cooling",
    10: "defrost",
    11: "flow_monitoring",
    24: "mode_change_delay",
    30: "locked",
}

# Register 104, software L/M. 0 is not listed in the documentation; taken as "no lock".
LOCK_CODES: dict[int, str] = {
    0: "none",
    2: "volume_flow",
    5: "function_check",
    6: "operating_limit_high_temperature",
    7: "system_check",
    8: "cooling_changeover_delay",
    9: "pump_prerun",
    10: "minimum_standstill",
    11: "grid_load",
    12: "switching_cycle_lock",
    13: "hot_water_reheating",
    14: "renewable",
    15: "utility_lock",
    16: "soft_starter",
    17: "flow",
    18: "heat_pump_operating_limit",
    19: "high_pressure",
    20: "low_pressure",
    21: "heat_source_operating_limit",
    23: "system_limit",
    24: "primary_circuit_load",
    25: "external_lock",
    29: "inverter",
    31: "warm_up",
    33: "eev_initialisation",
    34: "second_heat_generator_released",
    35: "fault",
}

# Register 105, software L/M. Entries marked with "!" in the documentation carry the
# suffix "_alarm" here.
FAULT_CODES: dict[int, str] = {
    0: "none",
    1: "n17_1",
    2: "n17_2",
    3: "n17_3",
    4: "n17_4",
    6: "electronic_expansion_valve",
    10: "wpio",
    12: "inverter",
    13: "wqif",
    15: "sensor_fault",
    16: "brine_low_pressure",
    19: "primary_circuit_alarm",
    20: "defrost_alarm",
    21: "brine_low_pressure_alarm",
    22: "hot_water_alarm",
    23: "compressor_load_alarm",
    24: "coding_alarm",
    25: "low_pressure_alarm",
    26: "frost_protection_alarm",
    28: "high_pressure_alarm",
    29: "temperature_difference_alarm",
    30: "hot_gas_thermostat_alarm",
    31: "flow_alarm",
    32: "warm_up_alarm",
}

# Register 106, software L/M. 0 is not listed in the documentation; taken as "no error".
SENSOR_FAULT_CODES: dict[int, str] = {
    0: "none",
    1: "outdoor_r1",
    2: "return_r2",
    3: "hot_water_r3",
    4: "coding_r7",
    5: "flow_r9",
    6: "heating_circuit_2_r5",
    7: "heating_circuit_3_r13",
    8: "renewable_r13",
    9: "room_1",
    10: "room_2",
    11: "heat_source_outlet_r6",
    12: "heat_source_inlet_r24",
    14: "collector_r23",
    15: "low_pressure_r25",
    16: "high_pressure_r26",
    17: "room_humidity_1",
    18: "room_humidity_2",
    19: "frost_protection_cooling",
    20: "hot_gas",
    21: "return_r2_1",
    22: "pool_r20",
    23: "flow_passive_cooling_r11",
    24: "return_passive_cooling_r4",
    26: "solar_tank_r22",
    28: "demand_heating_r2_2",
    29: "rtm_econ",
    30: "demand_cooling_r39",
    37: "oil_temperature_compressor_1",
    39: "oil_temperature_compressor_2",
    41: "hot_gas_compressor_1",
    43: "hot_gas_compressor_2",
    45: "evaporator_air_inlet",
    48: "flow_sensor_secondary",
    49: "pressure_sensor_secondary",
    50: "flow_sensor_primary",
    51: "pressure_sensor_primary",
    52: "suction_gas",
}

# Shown when the controller reports a code that is not in the tables above.
UNKNOWN_CODE = "other"
