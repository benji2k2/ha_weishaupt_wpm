"""Register definitions, decoding and the translation tables."""

from __future__ import annotations

import json
import pathlib

import pytest

from custom_components.weishaupt_wpm.registers import (
    COUNTERS,
    FAULT_CODES,
    LOCK_CODES,
    OPERATING_MODES,
    REGISTERS,
    REGISTERS_BY_KEY,
    SELECTABLE_OPERATING_MODES,
    SENSOR_FAULT_CODES,
    STATUS_CODES,
    UNKNOWN_CODE,
    Counter,
    Group,
    addresses_by_group,
    blocks,
)

PACKAGE = pathlib.Path(__file__).parents[1] / "custom_components" / "weishaupt_wpm"


def test_addresses_are_unique() -> None:
    addresses = [register.address for register in REGISTERS]
    for counter in COUNTERS:
        addresses.extend(counter.addresses)
    assert len(addresses) == len(set(addresses))


def test_temperatures_are_signed_tenths() -> None:
    outdoor = REGISTERS_BY_KEY["outdoor_temperature"]
    assert outdoor.decode(0xFFDD) == -3.5
    assert outdoor.decode(312) == 31.2
    assert outdoor.encode(-3.5) == 0xFFDD
    # Carel reports a missing sensor as -99.9 or 999.9: not plausible, so no value.
    assert outdoor.decode(outdoor.encode(-99.9)) is None
    assert outdoor.decode(9999) is None


def test_write_ranges() -> None:
    setpoint = REGISTERS_BY_KEY["hot_water_setpoint"]
    assert (setpoint.write_min, setpoint.write_max) == (40, 60)
    mode = REGISTERS_BY_KEY["operating_mode"]
    assert (mode.write_min, mode.write_max) == (0, 3)
    assert tuple(range(4)) == SELECTABLE_OPERATING_MODES
    assert not REGISTERS_BY_KEY["status"].writable
    with pytest.raises(ValueError):
        REGISTERS_BY_KEY["runtime_compressor_1"].encode(70000)


def test_blocks_never_bridge_gaps() -> None:
    status = addresses_by_group()[Group.STATUS]
    assert blocks(status) == [(1, 3), (5, 3), (53, 1), (58, 1), (103, 4)]
    assert blocks(status, {6}) == [(1, 3), (5, 1), (7, 1), (53, 1), (58, 1), (103, 4)]
    counters = addresses_by_group()[Group.COUNTERS]
    assert blocks(counters) == [(71, 9), (303, 6), (334, 3)]


def test_counter_combination() -> None:
    assert Counter.combine(5678, 4, 0) == 45_678
    assert Counter.combine(1, 2, 3) == 300_020_001


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_code_has_a_translation(language: str) -> None:
    strings = json.loads((PACKAGE / "translations" / f"{language}.json").read_text("utf-8"))
    sensors = strings["entity"]["sensor"]
    for key, codes in (
        ("status", STATUS_CODES),
        ("lock", LOCK_CODES),
        ("fault", FAULT_CODES),
        ("sensor_fault", SENSOR_FAULT_CODES),
        ("operating_mode", OPERATING_MODES),
    ):
        assert set(sensors[key]["state"]) == {*codes.values(), UNKNOWN_CODE}, key
    select_states = strings["entity"]["select"]["operating_mode"]["state"]
    assert set(select_states) == {OPERATING_MODES[code] for code in SELECTABLE_OPERATING_MODES}


def test_translations_have_the_same_keys() -> None:
    def keys(node: object, prefix: str = "") -> set[str]:
        if isinstance(node, dict):
            return {k for key, value in node.items() for k in keys(value, f"{prefix}.{key}")}
        return {prefix}

    english = json.loads((PACKAGE / "translations" / "en.json").read_text("utf-8"))
    german = json.loads((PACKAGE / "translations" / "de.json").read_text("utf-8"))
    strings = json.loads((PACKAGE / "strings.json").read_text("utf-8"))
    assert keys(english) == keys(german) == keys(strings)
