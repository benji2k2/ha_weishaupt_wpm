<p align="center">
  <img src="custom_components/weishaupt_wpm/brand/icon.png" width="128" alt="Weishaupt WPM" />
</p>

<h1 align="center">Weishaupt WPM — Home Assistant integration</h1>

<p align="center">
  Temperatures, status, faults, runtimes and heat amounts of a
  <b>Weishaupt heat pump with the WPM heat pump manager</b> over Modbus RTU —
  one device, set up in the UI, fully local.
</p>

<p align="center">
  <a href="README.de.md">Deutsch</a>
</p>

---

> **Status: pre-release.** The register list comes from the Dimplex
> documentation (WPM software J/L/M) and has **not been checked against a real
> unit yet**. Until then, compare every value with the WPM display (see
> [Checking with tools/probe.py](#checking-with-toolsprobepy)).

## Why

Older Weishaupt air/water heat pumps (e.g. WWP L … AD(R)) have a **WPM 5.0M**
heat pump manager — a Carel **pCO5+** running Dimplex software. With the
**Carel PCOS004850** Modbus card (= Weishaupt LWPM 410) it speaks Modbus RTU.
Home Assistant's built-in Modbus integration can read it, but creates no device
and needs many YAML sensors and template helpers. This integration reads the
unit in a few block requests and creates one device with all entities.

The HACS integration `weishaupt_modbus` and Weishaupt's "LAN module" belong to
the **current** Weishaupt generation (WBB, WWP LS) and do not fit these units.

## What you get

| Entity | Notes |
|---|---|
| Outdoor, flow, return, hot water temperature | 0.1 °C |
| Heat source inlet / outlet | Inlet only with an electronic expansion valve (disabled by default) |
| Return setpoint, active hot water setpoint | |
| Status, lock, fault, sensor fault | Text, attribute `code` with the raw value |
| Fault active, lock active | Problem sensors for notifications |
| Active operating mode | Summer, winter, holiday, party, 2nd heat generator, cooling |
| Runtimes | Compressor, pumps, 2nd heat generator, flange heater (h) |
| Heat amounts | Heating, hot water, environmental energy (kWh), each from three registers |
| **Operating mode** (select) | modes chosen in the options; default summer, winter, holiday, party |
| **Hot water setpoint** | range in the options, default 40–60 °C (technically 30–85 °C) |
| **Hot water hysteresis** | 2–15 K |
| **Party hours**, **holiday days** | 0–72 h, 0–150 days |

"2nd heat generator" (heating rod only) and "cooling" are only offered once
enabled in the options. A mode set at the controller that is not offered is
shown by "active operating mode".

**"Auto" at the controller** (switches between winter and summer by the outdoor
temperature) has no value of its own: the controller reports the mode Auto has
chosen, so Home Assistant shows "winter" or "summer". Home Assistant cannot set
Auto; choosing winter or summer there most likely ends it.

**Party and holiday** have their own durations (party hours, holiday days). How
exactly the controller uses them — whether the duration must be set before
switching and whether the values count down — is still to be checked on a real
unit. According to the operating manual it switches back to the previous mode by
itself when the time is up; holiday lowers the heating curve by the set-back
value and blocks hot water.

Registers a unit does not have are detected on the first read and not asked
for again; their entities are unavailable.

## ⚠️ Writing: on request only

Settings are stored in the controller's **non-volatile memory**, which wears
with constant writing. The integration protects it:

- Values outside the range are rejected, not clamped.
- A value that is already set is not written again.
- Each register is written at most **once every 30 seconds**.
- Every write is read back; diagnostics list the last 20 writes.
- A diagnostic sensor counts every write command sent to the controller, kept
  across restarts.

**Controller clock:** the button *Set controller clock* sets the WPM's date and time
to Home Assistant's. Only parts that differ are written, each followed by its
"set" coil (FC05, as documented by Dimplex for software J/L). Nothing presses the
button on its own; the *controller clock deviation* sensor tells an automation when
it is worth it, e.g. once a month when the clock is more than a few minutes off.

**Do not build automations that write periodically** (e.g. moving the hot water
setpoint with the electricity price every few minutes).

## Hardware

- **Carel PCOS004850** Modbus card in the WPM's BMS slot
- RS485 to Ethernet converter, e.g. **Waveshare RS485 TO ETH** (without "B",
  transparent): work mode *TCP server*, port 502, **9600 8N1**
- At the WPM (ESC + ENTER for 5 s, network menu): protocol **MODBUS RTU**,
  address 1, parity none, 1 stop bit, 9600 baud

Menus at the controller's display (WPM software L23):

| Keys | Menu |
|---|---|
| **Menue** held for a few seconds | settings, operating data, history |
| **Menue + Enter** for 5 s | extended menu with more settings (e.g. heating circuit hysteresis) |
| **ESC + Enter** for 5 s | network (Modbus) |

Only look, leave with **ESC**. Do not leave the **clock** menu open while the
clock is set from Home Assistant: on leaving, it writes the old time back.

A real Modbus TCP gateway (e.g. Waveshare "(B)") works too: choose transport
**Modbus TCP**.

The transparent gateway serves **one** client. Home Assistant keeps the
connection open; never run `tools/probe.py` and the integration at the same time.

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories**
2. Repository `https://github.com/benji2k2/ha_weishaupt_wpm`, type **Integration**
3. Download **Weishaupt WPM** and restart Home Assistant

### Manually

Copy `custom_components/weishaupt_wpm` to `/config/custom_components/` and
restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → Weishaupt WPM**

| Field | |
|---|---|
| Host | IP address of the gateway |
| Port | as set in the gateway, usually 502 |
| Modbus address | as set at the WPM, usually 1 |
| Transport | *Modbus RTU over TCP* (transparent gateway) or *Modbus TCP* |

The error messages tell "gateway unreachable" apart from "gateway reachable,
heat pump does not answer" (baud rate, address, A/B swapped).

**Options:** polling intervals for temperatures/status (default 30 s),
settings (5 min), runtimes/heat amounts (15 min), lowest and highest **hot water
setpoint**, the **operating modes offered**, and the **address offset** (0 or −1,
see below).

## Checking with tools/probe.py

Before setting up, right after fitting the card, on any computer in the network
(Python 3.9 or newer, nothing to install):

```bash
python3 tools/probe.py <gateway-ip> --scan 1-400 --out probe.md
```

It only reads. It shows

1. register 1 at offset 0 and −1 — the line that matches the **outdoor
   temperature on the display** is the right offset,
2. every known register with raw and scaled value, to compare with the display,
3. which blocks the controller returns in one go,
4. with `--scan`, every address that answers.

## Development without a unit

`tools/simulator.py` imitates gateway and controller — one client, RTU frames
with CRC, exceptions for unused registers, heat amounts that roll over:

```bash
python3 tools/simulator.py --port 5020
python3 tools/probe.py 127.0.0.1 --port 5020
```

Tests (run against the simulator):

```bash
pip install pytest-homeassistant-custom-component ruff
python -m pytest
ruff check . && ruff format --check .
```

## Not included

- Coils (FC01/FC02): outputs such as "compressor running" or common fault
- Heating curve, 2nd/3rd heating circuit, cooling, pool, ventilation
- Clock synchronisation

## Sources

- Dimplex wiki, Modbus RTU connection: data point list and system status
- Weishaupt operating instructions WPM software L23 (network menu)

This project is not affiliated with or supported by Weishaupt or Dimplex. The
names only identify the supported devices.

## Icons

The icons and logos live in `custom_components/weishaupt_wpm/brand/`. Home Assistant picks
them up from there from **2026.3** onwards. They are made from the Weishaupt logo as used on
[weishaupt.de](https://www.weishaupt.de/).

This project is not affiliated with or endorsed by Max Weishaupt SE. Weishaupt and the
Weishaupt logo are trademarks of Max Weishaupt SE and are used only to identify the supported
hardware.

## License

MIT
