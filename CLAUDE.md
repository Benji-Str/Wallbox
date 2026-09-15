# Projektgedächtnis — Wallbox-Steuerung

PV-Überschussladen für eine **Tuya-Wallbox**, rein lokal im LAN, ohne Cloud.
Lademodi nach dem Vorbild von openWB. Eigenständiges Projekt; die
Miner-Plattform liegt getrennt in `Benji-Str/gridmine-steuerung`.

## Die Anlage (echte Werte)
| | |
|---|---|
| Wallbox | Osoeri **OS-EC01**, 22 kW, Typ 2, Baujahr 2023 |
| IP | `192.168.1.8` |
| Tuya device_id | `bf09d30ac290349520gosa` |
| Protokoll | `3.4` · Tuya-LAN, TCP 6668 |
| Gerätekategorie | `qccdz` (Ladesäule) |
| Anschluss | **3-phasig** → 8 A Minimum = **5,5 kW Mindestlast** |
| MQTT-Broker | `192.168.1.60` — liefert Netz, PV, Speicher, SoC, Verbrauch |
| Steuerung läuft | Proxmox-LXC **105**, `192.168.1.150:8081`, Dienst `wallbox` |
| Installationsort | `/opt/wallbox`, Daten in `/opt/wallbox/data` |

**Der `local_key` steht in `data/wallbox.json` und gehört NICHT ins Repo.**
Falls er fehlt: `tools/tuya_setup.py` holt ihn (einmalig über ein kostenloses
Cloud-Projekt auf platform.tuya.com, danach nie wieder).

## Datenpunkte der OS-EC01
| DP | Code | | Bedeutung |
|---|---|---|---|
| 4 | `charge_cur_set` | rw | Ladestrom **8–32 A** |
| 18 | `switch` | rw | Laden ein/aus |
| 14 | `work_mode` | rw | wird auf `charge_now` gezwungen |
| 9 | `power_total` | ro | Leistung, Rohwert = Watt |
| 3 | `work_state` | ro | `charger_charging` / `_insert` / `_fault` … |
| 6/7/8 | `phase_a/b/c` | ro | Strom je Phase, Faktor 0,1 |
| 13 | `connection_state` | ro | CP-Spannung — die wichtigste Diagnose |
| 10 | `fault` | ro | Bitmap, 16 Störungen |
| 24 | `temp_current` | ro | Temperatur |
| 1 | `forward_energy_total` | ro | Zählerstand, 0,01 kWh — kommt lokal **nicht immer** mit |

**Keine Phasenumschaltung.** DP33 `mode_set` sieht danach aus, meldet aber nur,
welche Lademodi die Box kennt. Welche Phasen laden, entscheidet die Zuleitung.

## MQTT-Zuordnung (Broker 192.168.1.60, nachgerechnet)
Die Anlage veröffentlicht nur **Einzelphasen**, keine Summen.

| Feld | Thema | |
|---|---|---|
| Netz L1/L2/L3 | `Ac/Grid/L1/Power` … `L3` | Watt, positiv = Bezug |
| Verbrauch L1/L2/L3 | `VerbrauchL1` … `L3` | Watt |
| Speicher | `Entladen` | **bidirektional trotz des Namens**: negativ = entlädt, positiv = lädt |
| Ladestand | `Soc` | Prozent. Es gibt auch `soc` (59) — das ist verdächtig identisch mit `Batterie V` = 59,1 und wohl die Spannung |
| PV | `solar/totalPower` | bei Nacht 0, tagsüber noch zu bestätigen |

`json_key` leer (nackte Zahlen), `grid_sign` 1, `battery_sign` 1, `scale` 1.

Gegengeprüft über die Energiebilanz: PV 0 + Netz −2,2 W + Entladung 1207 W
= 1204,8 W gegen Hausverbrauch 1199 W — Abweichung 5,8 W.

Es gibt drei Netz-Quellen (`Ac/Grid/L*`, `GridL*`, `gridem24`), alle innerhalb
von 6 W. Gewählt sind die Victron-Systemwerte, weil phasengetreu.

Auf demselben Broker liegen auch Tasmota-Geräte und die Mining-Themen der
anderen Anlage — die gehören nicht hierher.

## OFFENES PROBLEM (Stand der Übergabe)
Das Fahrzeug meldet **„Ladefehler"**. Gemessener Zustand der Box:

```
work_state       = charger_insert     Auto steckt
connection_state = controlpi_9v_pwm   Box gibt frei, Auto fordert nicht an
switch           = False              Schütz AUS
fault            = (leer)             keine Störung
```

**Vermutung:** Die Box sendet PWM (Freigabe), liefert aber keinen Strom, weil
der Modus `stop` war. Manche Fahrzeuge melden das als Ladefehler.

**Nächster Schritt:** Modus `sofort` mit 10 A setzen und prüfen, ob der Schütz
schließt und Strom fließt (`charging`, `power_w`, `cp_text` in `/api/live`).
Kommt `controlpi_6v_pwm`, ist alles in Ordnung.

**Ebenfalls offen:** Der Dienst startete zuletzt nicht. Ursache steht im
Journal — `journalctl -u wallbox -n 40`. Die vier üblichen Gründe: kaputte
Konfiguration, fehlende Pakete nach einem `git pull`, belegter Port 8081,
oder außerhalb von systemd gestartet.

## Aufzeichnung (seit dem Display-Ausbau)
| | |
|---|---|
| Minutenwerte | `<GM_DATA>/verlauf/JJJJ-MM-TT.csv` — rund 60 kB am Tag, aelter als 400 Tage wird geloescht |
| Tagesenergie | `<GM_DATA>/tagesenergie.json` — winzig, bleibt fuer immer |
| Laufende Stunde | nur im Arbeitsspeicher (`core/verlauf.py`), fuer die Balken am Display |

Energie wird **aufintegriert** (W · s), nicht aus den Minutenwerten
nachgerechnet — sonst fehlte jede ausgelassene Minute. Faellt der Dienst aus,
laeuft eine `luecken_s`-Uhr mit, und die Oberflaeche sagt „20 min fehlen"
statt eine zu kleine Zahl als Wahrheit auszugeben.

## Wo die Konfiguration liegt — der haeufigste Stolperstein
`GM_DATA` (Dienst: `<Installation>/data`). **Ohne** die Variable — also beim
Start von Hand — wurde frueher der Projektordner genommen. Zwei Orte fuer
dieselbe Datei; beim naechsten Dienst-Neustart, meist nach einem Update,
schien alles geloescht. Seitdem:
* ohne `GM_DATA` wird ebenfalls `<Projekt>/data` genommen,
* eine an einem alten Ort liegende `wallbox.json` zieht beim Start um
  (`.alt` bleibt liegen),
* `selfupdate.sh` legt vor jedem `git pull` eine Kopie an,
* die Oberflaeche zeigt, **woraus gelesen** und **wohin gespeichert** wird.

## RFID/Karte — was die Box hergibt
Das Tuya-Datenmodell der OS-EC01 kennt **keinen Kartenleser-Datenpunkt**
(DPs: 1,3,4,6,7,8,9,10,13,14,15,18,23,24,25,27,28,33; DP10 nennt nur die
Stoerung `card_reader_fault`). Die Steuerung kann also nicht erfahren, welche
Karte vorgehalten wurde. Eine Freigabe-Verwaltung in der Software waere
deshalb eine zweite, unabhaengige Sperre — nicht dieselbe. Sie wurde bewusst
wieder ausgebaut. Ziel ist stattdessen, die Kartenpflicht **an der Box**
abzuschalten; welcher DP das ist, findet `tools/dp_dump.py --watch`.

## Aufbau
```
Zähler ──► Überschuss ──► Lademodus ──► Watt-Ziel ──► Treiber ──► Ampere
```
| Baustein | Datei |
|---|---|
| Lademodi + Schwellen | `core/charge.py` |
| Ladepunkt (Treiber + Modus + Log) | `core/chargepoint.py` |
| Strompreis (aWattar) | `core/preis.py` |
| Ladelog | `core/chargelog.py` |
| Treiber echt / Simulation | `drivers/tuya.py`, `drivers/mock.py` |
| Netzzähler, MQTT, MID | `meter/grid.py`, `meter/mqtt.py`, `meter/mid.py` |
| API + Oberfläche | `app.py`, `web/index.html` |

Sieben Lademodi: `stop` `sofort` `pv` `minpv` `ziel` `zeit` `eco`.

| Baustein | Datei |
|---|---|
| Kurzzeitverlauf (Arbeitsspeicher) | `core/verlauf.py` |
| Dauerhafte Aufzeichnung | `core/historie.py` |
| Display an der Wallbox | `web/display.html` → `/display` |
| Handy quer (Vollbild) | `web/ui.html` → `/ui` und `/UI` |

Fahrzeugfoto: mitgeliefert als `web/auto.png`; ein eigenes kommt ueber
`POST /api/vehicle/bild` nach `<GM_DATA>/fahrzeug.<ext>` und hat Vorrang.
Fehlt beides, zeichnet die Oberflaeche wieder ihre Kombi-Silhouette.

## Arbeiten an diesem Projekt
```bash
python3 app.py                       # startet mit config.example.json (Simulation)
for t in tests/test_*.py; do python3 "$t"; done     # 9 Testreihen
```
Am Gerät:
```bash
systemctl restart wallbox
journalctl -u wallbox -f
curl -s localhost:8081/api/live | python3 -m json.tool
```

## Gepflegte Gewohnheiten in diesem Code
- **Kommentare und Oberfläche auf Deutsch**, Bezeichner ebenso wo sinnvoll.
  Kommentare erklären *warum*, nicht *was*.
- **Unbekannt ist nicht null.** Fehlt ein Messwert, ist er `None` — nicht 0.
  Darauf bauen mehrere Regeln (Speicher-Vorrang, Phasenzahl, Zählerstand).
- **Watt-Ziele werden auf volle Ampere abgerundet**, nie auf. Die Box darf
  nie mehr ziehen als Überschuss vorhanden ist.
- **Schreibzugriffe im Treiber werfen nicht.** Eine unerreichbare Box darf
  nicht den ganzen Regeltakt abbrechen.
- **Zeitverzögerungen statt Deadband.** Eine Wolke für 30 s darf einen
  Ladevorgang nicht abbrechen.
- Jede Änderung mit **Test**; die Regelung ist ohne Netzzugriff testbar
  (simulierte Uhr, nachgebildeter Treiber).
- Geheimnisse (`local_key`, Broker-Passwort) nie ins Repo, nie in Logs, nie
  im Klartext über die API zurückgeben.

## Was als Nächstes offen ist
0. **Kartenpflicht an der Box abschalten.** `tools/dp_dump.py --watch` laufen
   lassen, Karte vorhalten, den umspringenden DP notieren. Findet sich keiner,
   ist es eine reine Geraete-Einstellung und muss einmalig in SmartLife
   umgestellt werden (danach wieder ohne Cloud).
1. **Ladefehler klären** (siehe oben) — hat Vorrang.
2. **MQTT eintragen** — Zuordnung steht oben, muss nur noch gespeichert werden.
   Offen: ob `solar/totalPower` tagsüber wirklich die PV-Summe führt.
3. **Speicher-Vorrang** einstellen (Vorschlag: 80 %).
4. Mindestlast: 5,5 kW dreiphasig ist für PV-Überschuss grob. Optionen sind
   einphasige Zuleitung (dann 1,8 kW) oder Min+PV.
