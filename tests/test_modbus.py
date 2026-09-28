"""The Modbus client against the simulator, both transports."""

from __future__ import annotations

import pytest

from custom_components.weishaupt_wpm.modbus import (
    TRANSPORT_RTU_OVER_TCP,
    TRANSPORT_TCP,
    ModbusClient,
    ModbusConnectionError,
    ModbusExceptionResponse,
    ModbusFrameError,
    ModbusTimeout,
    check_response_pdu,
    read_request_pdu,
    rtu_frame,
    with_crc,
    write_request_pdu,
)
from tools.simulator import WpmSimulator


def test_crc_matches_the_modbus_specification() -> None:
    # Example from the Modbus over serial line specification.
    assert with_crc(bytes.fromhex("1103006B0003")).hex() == "1103006b00037687"
    assert rtu_frame(1, read_request_pdu(0, 1)).hex() == "010300000001840a"


def test_request_limits() -> None:
    with pytest.raises(ValueError):
        read_request_pdu(0, 126)
    with pytest.raises(ValueError):
        write_request_pdu(1, 0x10000)


def test_response_checks() -> None:
    request = read_request_pdu(1, 2)
    with pytest.raises(ModbusExceptionResponse) as err:
        check_response_pdu(request, bytes([0x83, 2]))
    assert err.value.code == 2
    with pytest.raises(ModbusFrameError):
        check_response_pdu(request, bytes([0x04, 4, 0, 1, 0, 2]))
    with pytest.raises(ModbusFrameError):
        check_response_pdu(request, bytes([0x03, 2, 0, 1]))
    with pytest.raises(ModbusFrameError):
        check_response_pdu(write_request_pdu(254, 50), write_request_pdu(254, 51))


@pytest.fixture(params=[TRANSPORT_RTU_OVER_TCP, TRANSPORT_TCP])
async def served(request):  # noqa: ANN001, ANN201
    sim = WpmSimulator(transport=request.param)
    port = await sim.start()
    client = ModbusClient("127.0.0.1", port, 1, request.param, timeout=0.5, pause=0.0)
    try:
        yield sim, client, port
    finally:
        await client.close()
        await sim.stop()


async def test_read_and_write(served) -> None:  # noqa: ANN001
    sim, client, _ = served
    assert await client.read_holding_registers(1, 3) == [sim.get(1), sim.get(2), sim.get(3)]
    await client.write_register(254, 55)
    assert await client.read_holding_registers(254, 1) == [55]
    assert sim.writes == [(254, 55)]


async def test_exception_for_a_gap(served) -> None:  # noqa: ANN001
    _, client, _ = served
    with pytest.raises(ModbusExceptionResponse) as err:
        await client.read_holding_registers(1, 7)  # 4 is unused, 6 missing
    assert err.value.code == 2
    # The connection is still usable after an exception answer.
    assert len(await client.read_holding_registers(103, 4)) == 4


async def test_out_of_range_write_is_rejected(served) -> None:  # noqa: ANN001
    sim, client, _ = served
    with pytest.raises(ModbusExceptionResponse) as err:
        await client.write_register(254, 70)
    assert err.value.code == 3
    assert sim.writes == []


async def test_gateway_takes_one_client(served) -> None:  # noqa: ANN001
    sim, client, port = served
    await client.read_holding_registers(1, 1)
    other = ModbusClient("127.0.0.1", port, 1, sim.transport, timeout=0.5, retries=0)
    with pytest.raises(ModbusConnectionError):
        await other.read_holding_registers(1, 1)
    await other.close()
    assert sim.refused_connections == 1


async def test_read_retries_after_a_lost_answer(served) -> None:  # noqa: ANN001
    sim, client, _ = served
    client.timeout = 0.2
    sim.silent_requests = 1
    assert await client.read_holding_registers(103, 1) == [sim.get(103)]
    assert client.stats["timeouts"] == 1
    assert client.stats["connects"] == 2


async def test_write_is_not_repeated(served) -> None:  # noqa: ANN001
    sim, client, _ = served
    client.timeout = 0.2
    sim.silent_requests = 1
    with pytest.raises(ModbusTimeout):
        await client.write_register(254, 55)
    assert sim.writes == []


async def test_wrong_unit_gets_no_answer(served) -> None:  # noqa: ANN001
    sim, client, _ = served
    client.unit = 2
    client.timeout = 0.2
    client.retries = 0
    with pytest.raises(ModbusTimeout):
        await client.read_holding_registers(1, 1)
    assert sim.requests == 1


async def test_nobody_listening() -> None:
    client = ModbusClient("127.0.0.1", 1, connect_timeout=0.5, retries=0)
    with pytest.raises(ModbusConnectionError):
        await client.read_holding_registers(1, 1)
