<p align="center">
  <img src="custom_components/weishaupt_wpm/brand/icon.png" width="128" alt="Weishaupt WPM" />
</p>

<h1 align="center">Weishaupt WPM — Home-Assistant-Integration</h1>

<p align="center">
  Temperaturen, Status, Störungen, Laufzeiten und Wärmemengen einer
  <b>Weishaupt-Wärmepumpe mit Wärmepumpenmanager WPM</b> per Modbus RTU —
  ein Gerät, Einrichtung über die Oberfläche, alles lokal.
</p>

<p align="center">
  <a href="README.md">English</a>
</p>

---

> **Stand: Vorabversion.** Die Registerliste stammt aus der Dimplex-Dokumentation
> (WPM-Software J/L/M). Sie ist noch **nicht an einer echten Anlage geprüft**.
> Bis dahin ist jeder Wert mit dem Display am WPM abzugleichen (siehe
> [Prüfen mit tools/probe.py](#prüfen-mit-toolsprobepy)).

## Warum

Ältere Weishaupt-Luft/Wasser-Wärmepumpen (z. B. WWP L … AD(R)) haben einen
Wärmepumpenmanager **WPM 5.0M**, technisch ein Carel **pCO5+** mit
Dimplex-Software. Mit der Modbus-Karte **Carel PCOS004850** (= Weishaupt LWPM 410)
spricht er Modbus RTU. Die eingebaute Modbus-Integration von Home Assistant
kann das lesen, legt aber kein Gerät an und braucht viele YAML-Sensoren und
Template-Helfer. Diese Integration liest die Anlage in wenigen Blockabfragen und
legt ein Gerät mit allen Entitäten an.

Die HACS-Integration `weishaupt_modbus` und das Weishaupt-„LAN-Modul“ gehören
zur **neuen** Weishaupt-Generation (WBB, WWP LS) und passen nicht zu diesen
Anlagen.

## Was du bekommst

| Entität | Hinweis |
|---|---|
| Außen-, Vorlauf-, Rücklauf-, Warmwassertemperatur | 0,1 °C |
| Wärmequelle Eintritt / Austritt | Eintritt nur mit elektronischem Expansionsventil (standardmäßig deaktiviert) |
| Rücklauf-Soll, Warmwasser-Soll aktiv | |
| Status, Sperre, Störung, Sensorfehler | Klartext, Attribut `code` mit dem Rohwert |
| Störung aktiv, Sperre aktiv | Problem-Sensoren für Benachrichtigungen |
| Betriebsmodus aktiv | Sommer, Winter, Urlaub, Party, 2. Wärmeerzeuger, Kühlen |
| Laufzeiten | Verdichter, Pumpen, 2. Wärmeerzeuger, Flanschheizung (h) |
| Wärmemengen | Heizen, Warmwasser, Umweltenergie (kWh), aus je drei Registern |
| **Betriebsmodus** (Auswahl) | welche Modi, legst du in den Optionen fest; Vorgabe Sommer, Winter, Urlaub, Party |
| **Warmwasser-Solltemperatur** | Bereich in den Optionen, Vorgabe 40–60 °C (technisch 30–85 °C) |
| **Warmwasser-Hysterese** | 2–15 K |
| **Partystunden**, **Urlaubstage** | 0–72 h, 0–150 Tage |

„2. Wärmeerzeuger“ (nur Heizstab) und „Kühlen“ bietet die Auswahl erst an, wenn
sie in den Optionen freigegeben sind. Ist am Regler ein Modus gesetzt, der nicht
angeboten wird, zeigt „Betriebsmodus aktiv“ ihn an.

**Party und Urlaub** haben eigene Dauer-Werte (Partystunden, Urlaubstage). Wie
der Regler sie genau verwendet — ob die Dauer vor dem Umschalten gesetzt sein
muss, ob er danach selbst zurückschaltet und ob die Werte herunterzählen —, ist
noch an der Anlage zu prüfen.

Register, die es an einer Anlage nicht gibt, erkennt die Integration beim
ersten Lesen und fragt sie danach nicht mehr ab; ihre Entitäten sind nicht
verfügbar.

## ⚠️ Schreiben: nur auf Knopfdruck

Einstellungen landen im **nichtflüchtigen Speicher** des Reglers. Ständiges
Schreiben kann ihn verschleißen. Die Integration schützt sich selbst:

- Werte außerhalb des Bereichs werden abgelehnt, nicht begrenzt.
- Ist der Wert schon eingestellt, wird nicht geschrieben.
- Jedes Register wird höchstens **einmal in 30 Sekunden** geschrieben.
- Nach jedem Schreiben wird zurückgelesen; die Diagnosedaten zeigen die letzten
  20 Schreibvorgänge.
- Ein Diagnose-Sensor zählt jeden Schreibbefehl an den Regler, auch über
  Neustarts hinweg.

**Uhr des Reglers:** Der Knopf *Uhrzeit des Reglers stellen* setzt Datum und Uhrzeit
des WPM auf die von Home Assistant. Geschrieben werden nur abweichende Teile, jeweils
mit dem zugehörigen „set“-Coil (FC05, laut Dimplex ab Software J/L). Von selbst
drückt niemand den Knopf; der Sensor *Abweichung der Regler-Uhr* zeigt einer
Automation, wann es sich lohnt, z. B. einmal im Monat ab einigen Minuten Abweichung.

**Keine Automationen bauen, die regelmäßig schreiben** (z. B. den
Warmwasser-Sollwert nach Strompreis alle paar Minuten verstellen).

## Hardware

- Modbus-Karte **Carel PCOS004850** im BMS-Steckplatz des WPM
- RS485-Ethernet-Wandler, z. B. **Waveshare RS485 TO ETH** (ohne „B“,
  transparent): Work Mode *TCP Server*, Port 502, **9600 8N1**
- Am WPM (ESC + ENTER 5 s, Menü Netzwerk): Protokoll **MODBUS RTU**, Adresse 1,
  Parität None, 1 Stoppbit, 9600 Baud

Ein echtes Modbus-TCP-Gateway (z. B. Waveshare „(B)“) geht ebenfalls:
Übertragung **Modbus TCP** wählen.

Das transparente Gateway bedient **einen** Client. Home Assistant hält die
Verbindung offen; `tools/probe.py` und die Integration nie gleichzeitig nutzen.

## Installation

### HACS

1. HACS → ⋮ → **Benutzerdefinierte Repositories**
2. Repository `https://github.com/benji2k2/ha_weishaupt_wpm`, Typ **Integration**
3. **Weishaupt WPM** herunterladen und Home Assistant neu starten

### Von Hand

`custom_components/weishaupt_wpm` nach `/config/custom_components/` kopieren und
Home Assistant neu starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Weishaupt WPM**

| Feld | |
|---|---|
| Host | IP-Adresse des Gateways |
| Port | im Gateway eingestellt, meist 502 |
| Modbus-Adresse | am WPM eingestellt, meist 1 |
| Übertragung | *Modbus RTU über TCP* (transparentes Gateway) oder *Modbus TCP* |

Die Fehlermeldungen unterscheiden „Gateway nicht erreichbar“ von „Gateway
erreichbar, Wärmepumpe antwortet nicht“ (Baudrate, Adresse, A/B vertauscht).

**Optionen:** Abfrageintervalle für Temperaturen/Status (Standard 30 s),
Einstellungen (5 min), Laufzeiten/Wärmemengen (15 min), niedrigster und höchster
**Warmwasser-Sollwert**, die **angebotenen Betriebsmodi** und der
**Adress-Offset** (0 oder −1, siehe unten).

## Prüfen mit tools/probe.py

Vor dem Einrichten, direkt nach dem Einbau der Karte, auf einem Rechner im Netz
(nur Python 3.9 oder neuer, nichts zu installieren):

```bash
python3 tools/probe.py <gateway-ip> --scan 1-400 --out probe.md
```

Das Skript liest nur. Es zeigt

1. Register 1 mit Offset 0 und −1 — die Zeile, die zur **Außentemperatur am
   Display** passt, ist der richtige Offset,
2. jedes bekannte Register mit Rohwert und umgerechnetem Wert zum Abgleich mit
   dem Display,
3. welche Blöcke der Regler am Stück liefert,
4. mit `--scan` alle antwortenden Adressen.

## Entwicklung ohne Anlage

`tools/simulator.py` bildet Gateway und Regler nach — ein Client, RTU-Rahmen mit
CRC, Exceptions bei unbelegten Registern, Wärmemengen mit Übertrag:

```bash
python3 tools/simulator.py --port 5020
python3 tools/probe.py 127.0.0.1 --port 5020
```

Tests (laufen gegen den Simulator):

```bash
pip install pytest-homeassistant-custom-component ruff
python -m pytest
ruff check . && ruff format --check .
```

## Nicht enthalten

- Coils (FC01/FC02): Ausgänge wie „Verdichter läuft“ oder Sammelstörung
- Heizkurve, 2./3. Heizkreis, Kühlen, Schwimmbad, Lüftung
- Uhrzeitabgleich

## Quellen

- Dimplex-Wiki, Modbus RTU Anbindung: Datenpunktliste und Systemstatus
- Weishaupt Bedienungsanleitung WPM Software L23 (Menü Netzwerk)

Dieses Projekt steht in keiner Verbindung zu Weishaupt oder Dimplex und wird
von ihnen nicht unterstützt. Die Namen dienen nur zur Kennzeichnung der
unterstützten Geräte.

## Icons

Icons und Logos liegen in `custom_components/weishaupt_wpm/brand/`. Home Assistant nutzt sie
von dort ab **2026.3**. Sie sind aus dem Weishaupt-Logo erstellt, wie es auf
[weishaupt.de](https://www.weishaupt.de/) verwendet wird.

Dieses Projekt ist nicht mit der Max Weishaupt SE verbunden und wird nicht von ihr unterstützt.
Weishaupt und das Weishaupt-Logo sind Marken der Max Weishaupt SE und werden nur zur
Kennzeichnung der unterstützten Hardware verwendet.

## Lizenz

MIT
