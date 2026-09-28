"""A stand-in for the WPM behind a transparent RS485 to Ethernet gateway.

    python3 tools/simulator.py [--port 5020] [--transport rtuovertcp|tcp]
                               [--offset 0] [--missing 6,73,79] [--tick 5]

Behaves like the real setup as far as the integration can tell:

* one TCP client at a time: a new connection throws out the old one, like the
  Waveshare gateway with "Max Clients 1" and "kick off old connection"
  (``kick_old=False`` refuses the new one instead),
* optionally answers in small pieces (``fragment``), as a transparent gateway
  forwards serial bytes as they arrive, and can garble answers (``corrupt_next``),
* RTU frames with CRC (or Modbus TCP with ``--transport tcp``),
* only FC03 and FC06; everything else, including FC16, gets exception 1,
* a read that touches an unused address gets exception 2, so block reads fail
  like on the pCO and the integration has to fall back to single reads,
* FC06 only on registers with a write range, out-of-range values get exception 3,
* values move: outdoor temperature, operating status, runtimes, and heat amounts
  whose lowest register rolls over from 9999 to 0,
* ``--offset -1`` shifts every address by one, as a controller with 0-based
  numbering would appear.

Needs nothing but Python 3.9 or newer.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import struct

try:
    from . import _wpm
except ImportError:  # run as a script
    import _wpm  # type: ignore[no-redef]

modbus = _wpm.modbus
registers = _wpm.registers

_LOGGER = logging.getLogger("simulator")

DEFAULT_MISSING = (6, 73, 79)

# Documented address -> scaled start value.
START_VALUES: dict[int, float] = {
    1: -3.5,
    2: 31.2,
    3: 47.8,
    5: 35.4,
    6: -6.0,
    7: -7.1,
    53: 30.5,
    58: 50.0,
    103: 2,
    104: 0,
    105: 0,
    106: 0,
    222: 1,
    223: 0,
    224: 0,
    252: 5,
    254: 50,
    255: 60,
    352: 40,
    71: 0,
    72: 18342,
    73: 0,
    74: 20511,
    75: 12,
    76: 41007,
    77: 3208,
    78: 45,
    79: 0,
}

# Heat amounts in kWh; the lowest four digits are close to rolling over.
START_COUNTERS: dict[str, int] = {
    "heat_heating": 49_998,
    "heat_hot_water": 12_345,
    "environmental_energy": 38_990,
}

STATUS_CYCLE = (2, 2, 2, 10, 2, 2, 4, 4, 0)


class WpmSimulator:
    """Register memory plus a TCP server that answers like the gateway."""

    def __init__(
        self,
        unit: int = 1,
        transport: str = modbus.TRANSPORT_RTU_OVER_TCP,
        offset: int = 0,
        missing: tuple[int, ...] = DEFAULT_MISSING,
        kick_old: bool = True,
    ) -> None:
        self.unit = unit
        self.transport = transport
        self.offset = offset
        self.missing = set(missing)
        self.memory: dict[int, int] = {}
        self.write_ranges: dict[int, tuple[int, int]] = {}
        self.writes: list[tuple[int, int]] = []
        self.requests = 0
        self.silent_requests = 0  # answer nothing to the next n requests
        self.corrupt_next = 0  # garble the next n answers
        self.fragment = False  # send answers byte by byte
        self.kick_old = kick_old
        self.refused_connections = 0
        self.kicked_connections = 0
        self._current: tuple[asyncio.StreamWriter, asyncio.Task | None] | None = None
        self._tick = 0
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()

        for register in registers.REGISTERS:
            if register.address in self.missing:
                continue
            value = START_VALUES.get(register.address, 0)
            self.memory[self._pdu(register.address)] = register.encode(value)
            if register.writable:
                low = register.encode(register.write_min)
                high = register.encode(register.write_max)
                self.write_ranges[self._pdu(register.address)] = (low, high)
        for counter in registers.COUNTERS:
            if not self.missing.intersection(counter.addresses):
                self.set_counter(counter.key, START_COUNTERS.get(counter.key, 0))

    def _pdu(self, address: int) -> int:
        return address + self.offset

    # --- values -----------------------------------------------------------------

    def get(self, address: int) -> int:
        """Raw value at a documented address."""
        return self.memory[self._pdu(address)]

    def set(self, address: int, raw: int) -> None:
        self.memory[self._pdu(address)] = raw & 0xFFFF

    def set_value(self, key: str, value: float) -> None:
        register = registers.REGISTERS_BY_KEY[key]
        self.set(register.address, register.encode(value))

    def counter(self, key: str) -> int:
        counter = registers.COUNTERS_BY_KEY[key]
        return registers.Counter.combine(*(self.get(address) for address in counter.addresses))

    def set_counter(self, key: str, value: int) -> None:
        counter = registers.COUNTERS_BY_KEY[key]
        self.set(counter.low, value % 10_000)
        self.set(counter.mid, value // 10_000 % 10_000)
        self.set(counter.high, value // 100_000_000)

    def tick(self) -> None:
        """Let time pass: temperatures drift, status cycles, counters grow."""
        self._tick += 1
        t = self._tick
        if 1 not in self.missing:
            self.set_value("outdoor_temperature", round(-3.5 + 2.0 * math.sin(t / 20), 1))
        if 5 not in self.missing:
            self.set_value("flow_temperature", round(35.0 + 1.5 * math.sin(t / 7), 1))
        self.set(103, STATUS_CYCLE[t % len(STATUS_CYCLE)])
        if t % 10 == 0 and 72 not in self.missing:
            self.set(72, self.get(72) + 1)
        for counter in registers.COUNTERS:
            if not self.missing.intersection(counter.addresses):
                self.set_counter(counter.key, self.counter(counter.key) + 1)

    # --- protocol ---------------------------------------------------------------

    def handle_pdu(self, pdu: bytes) -> bytes:
        """Answer one request PDU like the controller would."""
        function = pdu[0]
        if function == modbus.READ_HOLDING_REGISTERS and len(pdu) == 5:
            address, count = struct.unpack(">HH", pdu[1:5])
            if not 1 <= count <= modbus.MAX_READ_COUNT:
                return bytes([function | 0x80, 3])
            wanted = range(address, address + count)
            if any(pdu_address not in self.memory for pdu_address in wanted):
                return bytes([function | 0x80, 2])
            values = [self.memory[pdu_address] for pdu_address in wanted]
            return bytes([function, 2 * count]) + struct.pack(f">{count}H", *values)
        if function == modbus.WRITE_SINGLE_REGISTER and len(pdu) == 5:
            address, value = struct.unpack(">HH", pdu[1:5])
            if address not in self.write_ranges:
                return bytes([function | 0x80, 2])
            low, high = self.write_ranges[address]
            if not low <= value <= high:
                return bytes([function | 0x80, 3])
            self.memory[address] = value
            self.writes.append((address - self.offset, value))
            return pdu
        return bytes([function | 0x80, 1])

    async def _read_rtu_request(self, reader: asyncio.StreamReader) -> tuple[int, bytes] | None:
        head = await reader.readexactly(2)
        if head[1] in (modbus.READ_HOLDING_REGISTERS, modbus.WRITE_SINGLE_REGISTER):
            frame = head + await reader.readexactly(6)
        elif head[1] == 0x10:  # write multiple registers: address, count, byte count
            rest = await reader.readexactly(5)
            frame = head + rest + await reader.readexactly(rest[4] + 2)
        else:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(reader.read(256), 0.05)
            return head[0], bytes([head[1]])
        if modbus.crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
            _LOGGER.warning("Request with bad CRC ignored")
            return None
        return frame[0], frame[1:-2]

    async def _read_tcp_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[int, int, bytes] | None:
        header = await reader.readexactly(7)
        transaction, protocol, length, unit = struct.unpack(">HHHB", header)
        pdu = await reader.readexactly(length - 1)
        if protocol != 0:
            return None
        return transaction, unit, pdu

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if self._current is not None:
            if not self.kick_old:
                self.refused_connections += 1
                writer.close()
                return
            # "Kick off old connection": the newcomer wins, the old client loses its socket.
            old_writer, old_task = self._current
            self.kicked_connections += 1
            old_writer.close()
            if old_task is not None:
                old_task.cancel()
        self._current = (writer, task)
        self._writers.add(writer)
        if task is not None:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        try:
            while True:
                if self.transport == modbus.TRANSPORT_TCP:
                    request = await self._read_tcp_request(reader)
                    if request is None:
                        continue
                    transaction, unit, pdu = request
                else:
                    rtu_request = await self._read_rtu_request(reader)
                    if rtu_request is None:
                        continue
                    unit, pdu = rtu_request
                    transaction = 0
                self.requests += 1
                if unit != self.unit:
                    continue  # another address on the bus: nobody answers
                if self.silent_requests:
                    self.silent_requests -= 1
                    continue
                response = self.handle_pdu(pdu)
                if self.transport == modbus.TRANSPORT_TCP:
                    frame = bytearray(modbus.tcp_frame(transaction, unit, response))
                else:
                    frame = bytearray(modbus.rtu_frame(unit, response))
                if self.corrupt_next:
                    self.corrupt_next -= 1
                    # RTU: broken CRC. TCP: wrong transaction id.
                    frame[-1 if self.transport != modbus.TRANSPORT_TCP else 0] ^= 0xFF
                if self.fragment:
                    for index in range(len(frame)):
                        writer.write(bytes(frame[index : index + 1]))
                        await writer.drain()
                        await asyncio.sleep(0.002)
                else:
                    writer.write(bytes(frame))
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if self._current is not None and self._current[0] is writer:
                self._current = None
            self._writers.discard(writer)
            writer.close()

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        """Start listening; returns the port."""
        self._server = await asyncio.start_server(self._serve, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop listening and drop the connected client, like a gateway losing power."""
        if self._server is None:
            return
        self._server.close()
        for writer in list(self._writers):
            writer.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=2)
        # Since Python 3.12 wait_closed() also waits for open connections; bounded, so a
        # client that never reads cannot hang the caller.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._server.wait_closed(), 2)
        self._server = None


async def _main(args: argparse.Namespace) -> None:
    missing = tuple(int(item) for item in args.missing.split(",") if item.strip())
    simulator = WpmSimulator(args.unit, args.transport, args.offset, missing)
    port = await simulator.start(args.host, args.port)
    print(
        f"WPM simulator on {args.host}:{port}, transport {args.transport}, unit {args.unit}, "
        f"offset {args.offset}, missing {sorted(simulator.missing) or 'none'}"
    )
    try:
        while True:
            await asyncio.sleep(args.tick)
            simulator.tick()
            if simulator.writes:
                address, value = simulator.writes.pop(0)
                print(f"write: register {address} = {value}")
    finally:
        await simulator.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--transport", choices=modbus.TRANSPORTS, default="rtuovertcp")
    parser.add_argument("--offset", type=int, default=0, choices=(0, -1))
    parser.add_argument(
        "--missing",
        default=",".join(str(address) for address in DEFAULT_MISSING),
        help="documented addresses that do not exist (comma separated, empty for none)",
    )
    parser.add_argument("--tick", type=float, default=5.0, help="seconds between value changes")
    logging.basicConfig(level=logging.INFO)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
