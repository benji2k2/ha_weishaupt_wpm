"""A deliberately small Modbus client for the WPM heat pump manager.

It speaks three function codes: read holding registers (FC03), write a single
register (FC06) and write a single coil (FC05). FC05 is only used for the "set"
bits that make the controller take over a written date or time. The Carel
PCOS004850 card does not support writing several registers at once.

Two transports over one TCP connection:

* ``rtuovertcp``: RTU frames with CRC, passed through by a transparent RS485 to
  Ethernet converter (Waveshare RS485 TO ETH without "B").
* ``tcp``: Modbus TCP with MBAP header, for a real Modbus TCP gateway.

All requests go through one lock, with a short pause between them, because the
controller handles one request at a time. After a timeout or a garbled answer the
connection is dropped and opened again, so that late bytes cannot be mistaken for
the next answer.

This module has no Home Assistant imports and runs on Python 3.9, so that
``tools/probe.py`` and ``tools/simulator.py`` work with the system ``python3`` of
a Mac.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time

_LOGGER = logging.getLogger(__name__)

READ_HOLDING_REGISTERS = 0x03
WRITE_SINGLE_REGISTER = 0x06
WRITE_SINGLE_COIL = 0x05
COIL_ON = 0xFF00
MAX_READ_COUNT = 125

TRANSPORT_RTU_OVER_TCP = "rtuovertcp"
TRANSPORT_TCP = "tcp"
TRANSPORTS = (TRANSPORT_RTU_OVER_TCP, TRANSPORT_TCP)

EXCEPTION_NAMES = {
    1: "illegal function",
    2: "illegal data address",
    3: "illegal data value",
    4: "server device failure",
    5: "acknowledge",
    6: "server device busy",
    10: "gateway path unavailable",
    11: "gateway target device failed to respond",
}


class ModbusError(Exception):
    """Base class for everything that can go wrong on the bus."""


class ModbusConnectionError(ModbusError):
    """The TCP connection could not be opened or was lost."""


class ModbusTimeout(ModbusError):
    """No complete answer arrived in time."""


class ModbusFrameError(ModbusError):
    """An answer arrived but was malformed (CRC, unit, function or length)."""


class ModbusExceptionResponse(ModbusError):
    """The controller answered with a Modbus exception code."""

    def __init__(self, function: int, code: int) -> None:
        self.function = function
        self.code = code
        name = EXCEPTION_NAMES.get(code, "unknown")
        super().__init__(f"exception {code} ({name}) for function {function:#04x}")


def crc16(data: bytes) -> int:
    """Modbus RTU CRC-16 (polynomial 0xA001, initial value 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(frame: bytes) -> bytes:
    """Append the CRC, low byte first."""
    return frame + struct.pack("<H", crc16(frame))


def read_request_pdu(address: int, count: int) -> bytes:
    """PDU for FC03."""
    if not 0 <= address <= 0xFFFF:
        raise ValueError(f"address {address} out of range")
    if not 1 <= count <= MAX_READ_COUNT or address + count > 0x10000:
        raise ValueError(f"count {count} out of range")
    return struct.pack(">BHH", READ_HOLDING_REGISTERS, address, count)


def write_request_pdu(address: int, value: int) -> bytes:
    """PDU for FC06."""
    if not 0 <= address <= 0xFFFF:
        raise ValueError(f"address {address} out of range")
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"value {value} out of range")
    return struct.pack(">BHH", WRITE_SINGLE_REGISTER, address, value)


def write_coil_request_pdu(address: int, on: bool = True) -> bytes:
    """PDU for FC05."""
    if not 0 <= address <= 0xFFFF:
        raise ValueError(f"address {address} out of range")
    return struct.pack(">BHH", WRITE_SINGLE_COIL, address, COIL_ON if on else 0)


def rtu_frame(unit: int, pdu: bytes) -> bytes:
    """Complete RTU frame: unit, PDU, CRC."""
    return with_crc(bytes([unit]) + pdu)


def tcp_frame(transaction: int, unit: int, pdu: bytes) -> bytes:
    """Complete Modbus TCP frame: MBAP header and PDU."""
    return struct.pack(">HHHB", transaction, 0, len(pdu) + 1, unit) + pdu


def check_response_pdu(request: bytes, response: bytes) -> bytes:
    """Validate a response PDU against its request and return it.

    Raises ModbusExceptionResponse for exception answers and ModbusFrameError for
    anything that does not fit the request.
    """
    function = request[0]
    if not response:
        raise ModbusFrameError("empty response")
    if response[0] == function | 0x80:
        if len(response) != 2:
            raise ModbusFrameError("malformed exception response")
        raise ModbusExceptionResponse(function, response[1])
    if response[0] != function:
        raise ModbusFrameError(f"function {response[0]:#04x} in answer to {function:#04x}")
    if function == READ_HOLDING_REGISTERS:
        count = struct.unpack(">H", request[3:5])[0]
        if len(response) != 2 + 2 * count or response[1] != 2 * count:
            raise ModbusFrameError("wrong length in read response")
    elif function in (WRITE_SINGLE_REGISTER, WRITE_SINGLE_COIL):
        if response != request:
            raise ModbusFrameError("write response does not echo the request")
    return response


def decode_registers(response: bytes) -> list[int]:
    """Register values from a validated FC03 response PDU."""
    count = response[1] // 2
    return list(struct.unpack(f">{count}H", response[2 : 2 + 2 * count]))


async def read_rtu_response(reader: asyncio.StreamReader, unit: int) -> bytes:
    """Read one RTU answer and return its PDU after checking unit and CRC."""
    head = await reader.readexactly(2)
    function = head[1]
    if function & 0x80:
        rest = await reader.readexactly(3)
    elif function == READ_HOLDING_REGISTERS:
        length = await reader.readexactly(1)
        rest = length + await reader.readexactly(length[0] + 2)
    elif function in (WRITE_SINGLE_REGISTER, WRITE_SINGLE_COIL):
        rest = await reader.readexactly(6)
    else:
        raise ModbusFrameError(f"unexpected function {function:#04x}")
    frame = head + rest
    if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
        raise ModbusFrameError("CRC mismatch")
    if frame[0] != unit:
        raise ModbusFrameError(f"answer from unit {frame[0]}, expected {unit}")
    return frame[1:-2]


async def read_tcp_response(reader: asyncio.StreamReader, transaction: int, unit: int) -> bytes:
    """Read one Modbus TCP answer and return its PDU after checking the header."""
    header = await reader.readexactly(7)
    got_transaction, protocol, length, got_unit = struct.unpack(">HHHB", header)
    if protocol != 0 or not 2 <= length <= 254:
        raise ModbusFrameError("invalid MBAP header")
    pdu = await reader.readexactly(length - 1)
    if got_transaction != transaction:
        raise ModbusFrameError(f"transaction {got_transaction}, expected {transaction}")
    if got_unit != unit:
        raise ModbusFrameError(f"answer from unit {got_unit}, expected {unit}")
    return pdu


class ModbusClient:
    """One connection to the gateway, one request at a time."""

    def __init__(
        self,
        host: str,
        port: int,
        unit: int = 1,
        transport: str = TRANSPORT_RTU_OVER_TCP,
        *,
        timeout: float = 4.0,
        connect_timeout: float = 5.0,
        pause: float = 0.1,
        retries: int = 1,
    ) -> None:
        if transport not in TRANSPORTS:
            raise ValueError(f"unknown transport {transport!r}")
        self.host = host
        self.port = port
        self.unit = unit
        self.transport = transport
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.pause = pause
        self.retries = retries
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock: asyncio.Lock | None = None
        self._last_io = 0.0
        self._transaction = 0
        self.stats = {"requests": 0, "errors": 0, "timeouts": 0, "connects": 0}

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        """Read ``count`` registers starting at PDU address ``address``."""
        request = read_request_pdu(address, count)
        response = await self._request(request, retries=self.retries)
        return decode_registers(response)

    async def write_register(self, address: int, value: int) -> None:
        """Write one register. Not repeated on failure: the caller reads back instead."""
        request = write_request_pdu(address, value)
        await self._request(request, retries=0)

    async def write_coil(self, address: int, on: bool = True) -> None:
        """Write one coil. Not repeated on failure either."""
        request = write_coil_request_pdu(address, on)
        await self._request(request, retries=0)

    async def close(self) -> None:
        """Close the connection. The next request opens it again."""
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _request(self, pdu: bytes, retries: int) -> bytes:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            for attempt in range(retries + 1):
                try:
                    return await self._attempt(pdu)
                except ModbusExceptionResponse:
                    raise
                except ModbusError as err:
                    self.stats["errors"] += 1
                    await self.close()
                    if attempt < retries:
                        _LOGGER.debug("Retrying after %s", err)
                        continue
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def _attempt(self, pdu: bytes) -> bytes:
        await self._connect()
        wait = self._last_io + self.pause - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self.stats["requests"] += 1
        try:
            response = await asyncio.wait_for(self._exchange(pdu), self.timeout)
        except asyncio.TimeoutError as err:
            self.stats["timeouts"] += 1
            raise ModbusTimeout(f"no answer from {self.host}:{self.port}") from err
        except asyncio.IncompleteReadError as err:
            raise ModbusConnectionError("connection closed by the gateway") from err
        except OSError as err:
            raise ModbusConnectionError(str(err) or type(err).__name__) from err
        finally:
            self._last_io = time.monotonic()
        return check_response_pdu(pdu, response)

    async def _connect(self) -> None:
        if self.connected:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.connect_timeout
            )
        except asyncio.TimeoutError as err:
            raise ModbusConnectionError(f"connecting to {self.host}:{self.port} timed out") from err
        except OSError as err:
            raise ModbusConnectionError(
                f"cannot connect to {self.host}:{self.port}: {err.strerror or err}"
            ) from err
        self.stats["connects"] += 1

    async def _exchange(self, pdu: bytes) -> bytes:
        reader, writer = self._reader, self._writer
        assert reader is not None and writer is not None
        if self.transport == TRANSPORT_TCP:
            self._transaction = (self._transaction + 1) & 0xFFFF
            writer.write(tcp_frame(self._transaction, self.unit, pdu))
            await writer.drain()
            return await read_tcp_response(reader, self._transaction, self.unit)
        writer.write(rtu_frame(self.unit, pdu))
        await writer.drain()
        return await read_rtu_response(reader, self.unit)
