"""Read the WPM register by register, to compare with the controller's display.

    python3 tools/probe.py <gateway-ip> [--port 502] [--unit 1]
                           [--transport rtuovertcp|tcp] [--offset 0]
                           [--scan 1-400] [--out probe.md]

Only reads (FC03), never writes. Home Assistant must not be connected to the
gateway at the same time: the gateway accepts one client, and two masters on the
bus would disturb each other.

Steps:

1. Offset check: register 1 (outdoor temperature) read at offset 0 and -1.
   The one that matches the display is right.
2. Every known register on its own: raw value, scaled value, unit.
3. The block reads the integration will use, to see which ranges the controller
   answers in one go.
4. With ``--scan``: every address in the range on its own, to find registers that
   exist beyond the documented ones.

With ``--out`` the result is also written as a Markdown file.

Needs nothing but Python 3.9 or newer.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time

try:
    from . import _wpm
except ImportError:  # run as a script
    import _wpm  # type: ignore[no-redef]

modbus = _wpm.modbus
registers = _wpm.registers


def _unit(register) -> str:
    if register.scale == 0.1:
        return "°C"
    if register.key.startswith("runtime_"):
        return "h"
    if register.key in ("hot_water_setpoint", "hot_water_setpoint_max", "hot_water_setpoint_min"):
        return "°C"
    if register.key == "hot_water_hysteresis":
        return "K"
    return ""


def _code_text(register, raw: int) -> str:
    tables = {
        "status": registers.STATUS_CODES,
        "lock": registers.LOCK_CODES,
        "fault": registers.FAULT_CODES,
        "sensor_fault": registers.SENSOR_FAULT_CODES,
        "operating_mode": registers.OPERATING_MODES,
    }
    table = tables.get(register.key)
    if table is None:
        return ""
    return table.get(raw, registers.UNKNOWN_CODE)


def _signed(raw: int) -> int:
    return raw - 0x10000 if raw >= 0x8000 else raw


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def out(self, line: str = "") -> None:
        print(line)
        self.lines.append(line)

    def table(self, header: list[str], rows: list[list[str]]) -> None:
        self.out("| " + " | ".join(header) + " |")
        self.out("|" + "|".join("---" for _ in header) + "|")
        for row in rows:
            self.out("| " + " | ".join(row) + " |")
        self.out()


async def _read(client, address: int, count: int = 1):
    """Values or the error text."""
    try:
        return await client.read_holding_registers(address, count), None
    except modbus.ModbusExceptionResponse as err:
        return None, f"Exception {err.code} ({modbus.EXCEPTION_NAMES.get(err.code, '?')})"
    except modbus.ModbusTimeout:
        return None, "keine Antwort"


async def probe(args: argparse.Namespace) -> Report:
    report = Report()
    client = modbus.ModbusClient(
        args.host, args.port, args.unit, args.transport, timeout=3.0, pause=0.15, retries=1
    )
    started = time.monotonic()
    try:
        report.out(f"# WPM-Probe {time.strftime('%Y-%m-%d %H:%M')}")
        report.out()
        report.out(
            f"Gateway {args.host}:{args.port}, Transport {args.transport}, "
            f"Slave {args.unit}, Offset {args.offset}"
        )
        report.out()

        report.out("## 1. Offset-Prüfung: Außentemperatur (Register 1)")
        report.out()
        rows = []
        for offset in (0, -1):
            values, error = await _read(client, 1 + offset)
            text = error or f"{_signed(values[0]) / 10:.1f} °C (roh {values[0]})"
            rows.append([str(offset), str(1 + offset), text])
        report.table(["Offset", "PDU-Adresse", "Wert"], rows)
        report.out(
            "→ Die Zeile, die zur Außentemperatur am Display passt, ist der richtige Offset."
        )
        report.out()

        report.out(f"## 2. Alle bekannten Register einzeln (Offset {args.offset})")
        report.out()
        rows = []
        for register in sorted(registers.REGISTERS, key=lambda item: item.address):
            values, error = await _read(client, register.address + args.offset)
            if error:
                rows.append([str(register.address), register.description, "–", error, "", ""])
                continue
            raw = values[0]
            value = register.decode(raw)
            shown = "außerhalb Plausibilität" if value is None else f"{value} {_unit(register)}"
            rows.append(
                [
                    str(register.address),
                    register.description,
                    str(raw),
                    shown.strip(),
                    _code_text(register, raw),
                    "ja" if register.verified else "",
                ]
            )
        report.table(["Reg", "Bedeutung", "Roh", "Wert", "Code", "verifiziert"], rows)

        rows = []
        for counter in registers.COUNTERS:
            values = []
            error = None
            for address in counter.addresses:
                part, error = await _read(client, address + args.offset)
                if error:
                    break
                values.append(part[0])
            if error:
                rows.append([counter.description, "–", error])
                continue
            block, block_error = await _read(client, counter.low + args.offset, 3)
            total = registers.Counter.combine(*values)
            combined = (
                f"{registers.Counter.combine(*block)} kWh (Block)" if block else block_error
            )
            rows.append(
                [counter.description, f"{values}", f"{total} kWh (einzeln), {combined}"]
            )
        report.table(["Wärmemenge", "Teilregister 1-4 / 5-8 / 9-12", "Summe"], rows)

        report.out("## 3. Blocklesen")
        report.out()
        rows = []
        for group, addresses in registers.addresses_by_group().items():
            for start, count in registers.blocks(addresses):
                if count == 1:
                    continue
                values, error = await _read(client, start + args.offset, count)
                rows.append(
                    [group.value, f"{start}–{start + count - 1}", str(count), error or "ok"]
                )
        report.table(["Gruppe", "Register", "Anzahl", "Ergebnis"], rows)

        if args.scan:
            first, last = (int(part) for part in args.scan.split("-"))
            report.out(f"## 4. Scan {first}–{last} (PDU-Adressen, ohne Offset)")
            report.out()
            rows = []
            silent = 0
            for address in range(first, last + 1):
                values, error = await _read(client, address)
                if values is None:
                    silent += error == "keine Antwort"
                    continue
                known = registers.REGISTERS_BY_ADDRESS.get(address - args.offset)
                rows.append(
                    [
                        str(address),
                        str(values[0]),
                        str(_signed(values[0])),
                        known.description if known else "",
                    ]
                )
            report.table(["PDU-Adresse", "uint16", "int16", "bekannt als"], rows)
            report.out(f"{len(rows)} Register antworten, {silent} ohne Antwort.")
            report.out()

        report.out(
            f"Dauer {time.monotonic() - started:.1f} s, Anfragen {client.stats['requests']}, "
            f"Fehler {client.stats['errors']}, Zeitüberschreitungen {client.stats['timeouts']}"
        )
    finally:
        await client.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--transport", choices=modbus.TRANSPORTS, default="rtuovertcp")
    parser.add_argument("--offset", type=int, default=0, choices=(0, -1))
    parser.add_argument("--scan", help="address range to scan, e.g. 1-400")
    parser.add_argument("--out", help="also write the report to this Markdown file")
    args = parser.parse_args()
    try:
        report = asyncio.run(probe(args))
    except modbus.ModbusConnectionError as err:
        raise SystemExit(f"Keine Verbindung zum Gateway: {err}") from None
    if args.out:
        with open(args.out, "w", encoding="utf-8") as file:
            file.write("\n".join(report.lines) + "\n")
        print(f"Gespeichert: {args.out}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
