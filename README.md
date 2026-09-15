# Wallbox-Steuerung

PV-Überschussladen für **Tuya-/SmartLife-Wallboxen** — rein lokal im eigenen
Netz, ohne Cloud. Fünf Lademodi nach dem Vorbild von openWB, optional mit
MID-Zähler für die Abrechnung.

Entwickelt und geprüft an einer **Osoeri OS-EC01** (22 kW, 3-phasig, Typ 2).
Andere Tuya-Wallboxen (dé, Feyree u. a.) nutzen dieselbe Geräteklasse `qccdz`
und sollten mit angepassten Datenpunkten ebenso laufen.

## Sofort starten (ohne Hardware)
```bash
pip install -r requirements.txt
python3 app.py
# -> http://localhost:8081   (simulierte Wallbox, simulierter Zähler)
```
Alle Lademodi lassen sich so durchspielen, ohne dass Hardware angeschlossen ist.

## Lademodi
| Modus | Verhalten |
|---|---|
| **Stop** | aus |
| **Sofortladen** | fester Ladestrom, unabhängig von der Sonne |
| **PV** | nur Überschuss — mit Ein-/Ausschaltschwelle **und Zeitverzögerung** |
| **Min+PV** | immer mindestens Mindeststrom, Überschuss kommt obendrauf |
| **Zielladen** | X kWh bis Uhrzeit Y; PV bevorzugt, Netz erst wenn die Zeit knapp wird |
| **Zeitladen** | feste Zeitfenster mit festem Strom (Nachttarif), ohne Zähler |

**Zeitladen** nimmt eine Liste von Fenstern:
```json
"zeit_plaene": [
  {"aktiv": true, "von": "22:00", "bis": "06:00", "tage": [0,1,2,3,4], "strom_a": 32},
  {"aktiv": true, "von": "13:00", "bis": "15:00", "tage": [5,6], "strom_a": 16}
]
```
`tage`: 0 = Montag … 6 = Sonntag, bezogen auf den **Beginn** des Fensters.
Ein Fenster über Mitternacht gehört also zum Starttag: „Mo–Fr 22:00–06:00"
umfasst die Nacht von Freitag auf Samstag, aber nicht die von Sonntag auf
Montag. Zeitladen und Sofortladen brauchen keinen Zähler — wer nach Tarif
lädt, will laden, auch wenn der Zähler ausfällt.

Die Zeitverzögerungen sind der Kern: Eine Wolke für 30 s darf einen
Ladevorgang nicht abbrechen. Während der Ausschaltverzögerung wird auf das
Geräte-Minimum gedrosselt statt abgeschaltet.

## Architektur
```
Zähler ──► Überschuss ──► Lademodus ──► Watt-Ziel ──► Treiber ──► Ampere
                                                       (Tuya-LAN, TCP 6668)
```
| Baustein | Datei |
|---|---|
| Lademodi + Schwellen | `core/charge.py` |
| Ladepunkt (Treiber + Modus + Log) | `core/chargepoint.py` |
| Ladelog | `core/chargelog.py` |
| Treiber Tuya / Simulation | `drivers/tuya.py`, `drivers/mock.py` |
| Netzzähler | `meter/grid.py` |
| Werte per MQTT | `meter/mqtt.py` |
| MID-Zähler (Modbus) | `meter/mid.py` |
| API + Oberfläche | `app.py`, `web/index.html` |

## Einrichtung an der echten Wallbox
Tuya-Boxen sprechen **kein OCPP und kein Modbus** — die Steuerung läuft über
das Tuya-LAN-Protokoll (TCP 6668). Dafür wird einmalig der `local_key`
ausgelesen; danach läuft alles lokal und die Wallbox darf im Router vom
Internet getrennt werden.

```bash
python3 tools/wallbox_probe.py 192.168.1.8   # was bietet die Box an?
python3 tools/tuya_setup.py --ip 192.168.1.8 # local_key + Datenpunkte
python3 tools/tuya_scan.py watch <ip> <id> <key> 3.4   # Datenpunkte prüfen
```
`tuya_setup.py` braucht einmalig Access ID und Secret aus einem kostenlosen
Cloud-Projekt auf platform.tuya.com (Data Center *Central Europe*, Dienst
*IoT Core* freischalten, SmartLife-Konto per QR verknüpfen).

Danach in `<GM_DATA>/wallbox.json` (Vorlage: `config.example.json`):
```json
{
  "type": "tuya", "ip": "192.168.1.8",
  "device_id": "…", "local_key": "…", "protocol": "3.4",
  "phases": 3, "volt": 230, "min_a": 8, "max_a": 32,
  "dp_switch": 18, "dp_current": 4, "dp_power": 9,
  "dp_state": 3, "dp_temp": 24, "dp_mode": 14
}
```

### Datenpunkte der Osoeri OS-EC01 (Kategorie `qccdz`)
| DP | Code | | Bedeutung |
|---|---|---|---|
| 4 | `charge_cur_set` | rw | Ladestrom, **8–32 A** in 1-A-Schritten |
| 18 | `switch` | rw | Laden ein/aus |
| 14 | `work_mode` | rw | wird auf `charge_now` gezwungen, sonst überschreiben App-Zeitpläne den Strom |
| 9 | `power_total` | ro | Leistung (Rohwert = Watt) |
| 3 | `work_state` | ro | `charger_charging` / `_insert` / `_wait` / `_pause` / `_end` / `_fault` |
| 6/7/8 | `phase_a/b/c` | ro | Strom je Phase |
| 24 | `temp_current` | ro | Temperatur |
| 1 | `forward_energy_total` | ro | Zählerstand (0,01 kWh) |

## Eigenheiten, die man kennen sollte
- **Keine Phasenumschaltung.** Die Box hat dafür keinen Datenpunkt — DP33
  `mode_set` sieht danach aus, meldet aber nur, *welche Lademodi* sie kann
  (Sofort, Prozent, Menge, Zeit, Verzögert). Welche Phasen laden, entscheidet
  die Zuleitung. Die Einstellung `phases` (1–3) sagt der Software nur, wie sie
  Watt in Ampere umrechnet; steht sie falsch, regelt sie daneben. Aus DP6/7/8
  (Strom je Phase) liest die Steuerung mit, wie viele Phasen **wirklich**
  laden, und warnt bei Abweichung.
- **Untergrenze 8 A** laut Gerätemodell (Norm wären 6 A, die Box lässt weniger
  nicht zu). Dreiphasig sind das **5,5 kW Mindestlast** — darunter bleibt nur
  „aus". Einphasig wären es 1,84 kW und die Regelung griffe deutlich feiner.
- **Taktschutz**: Ein/Aus wird begrenzt, Fahrzeuge mögen Ladeabbrüche nicht.
- **Nie aufrunden**: Das Watt-Ziel wird auf volle Ampere *abgerundet*, damit
  die Box nie mehr zieht als Überschuss vorhanden ist.

## Alle Werte über MQTT
Statt eines direkt angeschlossenen Zählers können **alle** Messwerte von einem
Broker kommen — Victron/VRM, Home Assistant, evcc, openWB, Shelly, ioBroker.
Einzustellen im Reiter **Einstellungen**, ohne Dienst-Neustart.

| Thema | Pflicht | Bedeutung |
|---|---|---|
| Netzleistung | **ja** | daraus wird der Überschuss gerechnet |
| PV-Erzeugung | nein | nur Anzeige |
| Hausspeicher (Leistung) | nein | positiv = lädt |
| Ladestand (%) | nein | steuert den Speicher-Vorrang |
| Hausverbrauch | nein | nur Anzeige |

Die Nutzlast darf eine nackte Zahl oder JSON sein; `json_key` auch
verschachtelt (`data.p`). Weil Systeme „positiv" und die Einheit
unterschiedlich auslegen, sind **Vorzeichen** und **Faktor** einstellbar
(1 bei Watt, 1000 bei kW).

**Themen finden statt raten:** Der Knopf *Themen suchen* hört sechs Sekunden
am Broker mit, listet alles auf, was hereinkommt, und schlägt je Thema eine
Zuordnung vor. Ein Klick trägt es ins richtige Feld ein.

**Werte veralten:** Kommt zu einem Thema länger als `stale_s` (Vorgabe 30 s)
nichts, gelten die Werte als unbrauchbar und die PV-Modi pausieren mit
klarer Begründung. Mit einem eingefrorenen Zählerstand weiterzuregeln wäre
schlimmer. **Sofortladen** braucht den Zähler nicht und läuft weiter.

Umgekehrt legt die Steuerung ihren Zustand auf den Broker, wenn
`publish_prefix` gesetzt ist: `<prefix>/power`, `/target`, `/mode`,
`/charging`, `/plugged`, `/state`, `/session_kwh`.

## Hausspeicher — wer bekommt den Überschuss zuerst?
Ohne Regel gewinnt immer der Speicher, weil er schneller reagiert. Deshalb:

`battery_release_soc` — **ab diesem Ladestand darf das Auto die
Ladeleistung des Speichers beanspruchen.**

| Wert | Verhalten |
|---|---|
| `100` (Vorgabe) | Speicher hat Vorrang, das Auto bekommt nur den Rest |
| `80` | bis 80 % lädt der Speicher, darüber geht es ins Auto |
| `0` | Auto zuerst |

Entlädt der Speicher, ist dort nichts zu holen. Und: **unbekannt ist nicht
null** — fehlt das Speicher-Thema oder der Ladestand, greift die Regel
nicht, statt einen Stillstand zu unterstellen.

## MID-Zähler (Abrechnung)
Der interne Zähler der Box ist ein Betriebswert ohne Beglaubigung. Für jede
Abrechnung gehört ein MID-Zähler in den Abgang; er wird über Modbus gelesen
und liefert die Zählerstände fürs Ladelog.
```json
"mid_meter": {"preset": "eastron", "mode": "tcp", "host": "192.168.1.50", "unit": 1}
```
Voreinstellungen für Eastron, ABB und Finder. Die **Wortreihenfolge** ist die
häufigste Fehlerquelle und deshalb einstellbar (`word_order`).

> **MID ist nicht gleich eichrechtskonform.** Für den Verkauf von Ladestrom an
> Dritte braucht es eine eichrechtskonforme Ladeeinrichtung mit signierten
> Messdaten. Für interne Abrechnung und Erstattung reicht der MID-Zähler.

## Mehrere Anlagen an einem Zähler
Greifen zwei Regler unabhängig nach demselben Überschuss, schaukelt sich das
auf. Dafür gibt es zwei Haken:
- `GET /api/load` meldet, was diese Anlage beansprucht (`claimed_w`).
- `peer_load_url` in der Config zieht die Last der anderen Anlage ab.

Genau **eine** Seite zieht ab, die andere meldet nur.

## Betrieb
```bash
docker compose up --build          # Container
bash tools/install.sh              # oder als Dienst im LXC (/opt/wallbox)
bash tools/make_selfinstaller.sh   # ein Skript mit allem drin, ohne Git
```
| Variable | Bedeutung |
|---|---|
| `GM_DATA` | Ordner für Konfiguration und Ladelog |
| `GM_CONFIG` | Startvorlage, falls noch keine eigene Konfiguration existiert |
| `PORT` | Standard 8081 |

**Kein Login** — gedacht als Gerät im eigenen Netz. Nicht ins Internet stellen.

## Tests
```bash
for t in tests/test_*.py; do python3 "$t"; done
```
Decken Lademodi samt Wolkendurchgang, Zielzeit und ausgefallenem Zähler ab,
dazu Ampere-Umrechnung, Taktschutz, Störungserkennung, Modbus-Dekodierung,
MQTT (Nutzlast, Vorzeichen, Faktor, Phasen-Summe, Veralten) und den
Speicher-Vorrang.
