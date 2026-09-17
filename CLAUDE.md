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

## Diese Steuerung ist eine Erweiterung von GridMine
GridMine ist das Hauptsystem. Diese Steuerung meldet ihren Zustand auf
`gridmine/wallbox/<id>/status` (retained, ueber die MQTT-Verbindung des
Zaehlers) und erscheint damit auf dem Wanddisplay drueben. Der Vertrag steht
in GridMines `core/kacheln.py`, unsere Seite in `app.py::_melde_bus` und
`tests/test_busmeldung.py`. **Der Bus ist Anzeige, keine Steuerung** — die
Watt-Kopplung laeuft weiter ueber `/api/load`.

Unerreichbar heisst `null`, nicht `0`, und `zustand: "offline"` gilt vor
allem anderen. Wer schweigt, sagt nichts ueber sein Kabel.

## Geteilt mit der GridMine-Steuerung
`core/paths.py` ist in **beiden Projekten dieselbe Datei**. Aendert sie sich
hier, gehoert sie drueben mitgezogen (und umgekehrt) — sie loest dasselbe
Problem und soll nicht auseinanderlaufen. Dasselbe gilt fuer
`tests/test_datenort.py`, der drueben die Benutzerliste statt der Config
prueft.

Was sonst noch doppelt liegt und irgendwann angeglichen gehoert:
`meter/grid.py` hier gegen `meter/mecmeter.py` drueben (hier weiter
entwickelt: MQTT, Einzelphasen, MID) und der aWattar-Abruf in `core/preis.py`
gegen den in `core/signals.py` drueben (hier vollstaendiger: AT und DE,
guenstigste N Stunden, Rueckfall auf zuletzt geholte Preise).

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

## Start in der Oberflaeche schaltet nicht durch — die zwei Ursachen
Symptom aus dem Betrieb: In der Oberflaeche auf „Sofort" gedrueckt, der Schuetz
bleibt aus. Karte an die Box gehalten — laedt. Es gibt genau zwei Ursachen, und
`/api/live` unterscheidet sie:

| `charging` | `switch_on` | heisst |
|---|---|---|
| `false` | – | Die Regelung will gar nicht. `reason` sagt warum (Modus, Ueberschuss unter Mindestlast, Zaehler fehlt) |
| `true` | `false`, `sperre_s > 0` | Der **Taktschutz** sperrt — es geht kein Befehl hinaus |
| `true` | `false`, `sperre_s = 0` | Der Befehl geht hinaus, die Box nimmt ihn nicht an → **Kartenpflicht** |
| `true` | `true`, `power_w = 0` | Box ist frei, das Auto zieht nicht (`cp_text` ansehen) |

Behoben ist seither: Ein **Moduswechsel von Hand verkuerzt den Taktschutz auf
hoechstens `HAND_SPERRE_S` (10 s)** — `ChargePoint.set_mode` →
`driver.takt_freigeben()`. Der Schutz ist gegen die Regelung gedacht, die bei
jeder Wolke schalten wuerde, nicht gegen einen Menschen, der einmal auf einen
Knopf drueckt; fuer ihn war die Sperre vorher ein Knopf ohne Wirkung und ohne
Meldung. Ganz aufgehoben wird sie nicht: Wer zwischen zwei Modi hin und her
drueckt, liesse den Schuetz sonst im Sekundentakt klappern.

Sichtbar ist es jetzt auch: `live()` liefert `sperre_s` und
`nicht_geschaltet_s` (wie lange schon laden gewollt ist, ohne dass der Schuetz
zugeht). Die Oberflaeche warnt ab 30 s und nennt beide Ursachen. Gemessen wird
die Dauer, nicht der Augenblick — direkt nach dem Einschalten stammt der
gelesene Zustand noch von vor dem Schreiben, das waere bei jedem Start ein
Fehlalarm. Festgehalten in `tests/test_taktschutz.py`.

## RFID/Karte — GEMESSEN: sie schaltet DP18
Am Geraet mit `dp_dump.py --watch` nachgesehen, waehrend eine Karte vorgehalten
wurde. Das Ergebnis:

```
04:32:03  DP 18: False -> True   switch — Laden ein/aus
```

**Die Karte setzt denselben Datenpunkt, den auch die Steuerung schreibt.** Es
gibt keinen eigenen Kartenleser-DP und keine zweite Freigabe davor — DP18 ist
der Schalter, und die Karte ist nur ein zweiter Weg dorthin. Die frueher hier
vermutete „Kartenpflicht, die Netzwerkbefehle ignoriert" gibt es in dieser Form
also nicht: Kommt unser `DP18 = True` an der Box an, laedt sie genauso.

Die Box meldet lokal 11 DPs (1, 3, 4, 9, 10, 13, 14, 18, 23, 24, 25) — weniger
als das Cloud-Datenmodell nennt. DP23 ist die Firmware-Version (`V1.0.3`).
Welche Karte es war, erfaehrt die Steuerung weiterhin nicht.

**Folge fuer die Regelung:** Ein per Karte gestarteter Ladevorgang ist fuer uns
nicht von einem selbst gestarteten zu unterscheiden — und im Modus `stop` oder
in einem PV-Modus ohne Ueberschuss schaltet der naechste Takt ihn wieder aus.

**So ist es gewollt** (Entscheidung des Betreibers, 16.09.): Die Oberflaeche
steuert, nicht die Karte. Ein Start an der Box ist kein Vorrang und wird vom
eingestellten Modus ueberschrieben. Die Karte bleibt der Weg, wenn die
Steuerung nicht erreichbar ist — mehr nicht. Es gibt deshalb bewusst **keine**
Regel „Karte gilt als Sofortladen fuer N Stunden".

## Ladestrom aendern: diese Box kann es nur beim Einschalten
Am Geraet festgestellt: Die OS-EC01 uebernimmt einen **waehrend des Ladens**
geschriebenen DP4 nicht. Aendern geht nur ueber aus — neuer Wert — kurz warten
— wieder ein.

**Die Reihenfolge ist entscheidend: erst aus, dann den Wert.** Ein DP4
im Betrieb verpufft — die Box nimmt ihn gar nicht erst an. Wer ihn vor dem
Ausschalten schreibt, hat den Ladevorgang fuer nichts unterbrochen. Zur
Sicherheit wird er unmittelbar vor dem Einschalten noch einmal gesetzt: genau
dann uebernimmt sie ihn.

Eingebaut als `strom_neustart` (Vorgabe **aus**, andere Boxen brauchen es
nicht). Weil jede Aenderung einen Schuetzvorgang und eine Ladepause kostet —
an einem Sonnentag sonst leicht fuenfzig — ist sie dreifach gebremst:

| Schraube | Vorgabe | wozu |
|---|---|---|
| `neustart_ab_a` | 2 A | kleinere Spruenge sind keinen Neustart wert |
| `neustart_intervall_s` | 600 s | Mindestabstand zwischen zwei Aenderungen |
| `neustart_pause_s` | 60 s | so lange bleibt sie aus, damit der Wert greift |
| (Anlaufschutz) | 180 s | waehrend der Fahrzeug-Aushandlung gar nichts |

Der Ablauf laeuft **nicht blockierend**: `_aushandeln()` setzt den Wert und
schaltet aus, `_neustart_ab` haelt die Tuer zu, und der naechste Regeltakt
schaltet ueber das normale `resume()` wieder ein. Ein Treiber, der eine Minute
lang schlaeft, legt den ganzen Regelkreis lahm.

`live()` liefert `strom_neustart` und `stromwechsel_s`; die Oberflaeche sagt
an, wann der Strom wieder geaendert werden kann.
`tests/test_stromwechsel.py` haelt alle vier Grenzen fest.

**Folge fuers Ueberschussladen:** Der Ladestrom folgt der Sonne nur noch grob —
alle zehn Minuten eine Stufe. Das ist der Preis dieser Box, keine Schwaeche der
Regelung.

## Beim Einschalten zaehlt jeder Befehl
An der Anlage: Mit der Karte laedt die Box, mit der Software nicht. Die Karte
setzt **einen** Datenpunkt. Die Software schickte beim Einschalten **vier**
Befehle ohne Pause hintereinander — Strom, Betriebsart, Strom noch einmal,
Schuetz. Tuya-Geraete nehmen schnelle Folgen ueber dieselbe Verbindung nicht
verlaesslich an; geht der letzte verloren, bleibt der Schuetz offen, und `_set`
schluckt den Fehlschlag (es darf nicht werfen, sonst bricht der Regel-Tick ab).

Drei Aenderungen:
* **Nur schreiben, was noetig ist.** `get_stats` merkt die gelesenen
  Datenpunkte in `_letzte_dps`; Betriebsart und Ladestrom werden uebersprungen,
  wenn sie schon stimmen. Steht alles richtig, geht genau ein Befehl hinaus —
  wie bei der Karte.
* **Abstand und zweiter Versuch** in `_set_sync` (`schreib_pause_s`, 0,3 s).
  Laeuft im Thread, das `sleep` haelt also nichts auf. Nach einem Fehlschlag
  baut `_drop()` eine frische Verbindung auf, und der zweite Versuch geht
  meist durch.
* **Ein verlorener Schaltbefehl steht im Protokoll**
  (`Schuetz EIN — FEHLGESCHLAGEN — <Grund>`). Vorher war er nur eine
  DP-Fehlerzeile zwischen anderen.

`tests/test_schreibzugriffe.py` haelt die Reihenfolge fest: Strom **vor**
Schuetz, Schuetz **zuletzt**, und je Fall die genaue Zahl der Befehle.

## Anlaufschutz: das Fahrzeug braucht Ruhe
An der Anlage gemessen: neun Schaltvorgaenge in zehn Minuten, Control Pilot
durchgehend `9 V + PWM`, geladen **0,0 kWh**. Ein Fahrzeug des VW-Konzerns
braucht nach dem Einschalten rund eine Minute, bis es von 9 V auf 6 V geht und
Strom zieht. Wird in dieser Zeit abgeschaltet, kommt die Aushandlung nie
zustande — und es sieht aus wie ein Fehler am Auto, obwohl niemand ihm die Zeit
gelassen hat.

`ANLAUF_S` (180 s) im Treiber: Nach dem Einschalten darf die **Regelung**
drosseln, aber nicht abschalten. Ein Mensch darf sehr wohl —
`takt_freigeben()` raeumt den Anlaufschutz mit weg, denn wer auf Stop drueckt,
meint es auch so. `live()` zeigt `anlauf_s`, die Oberflaeche sagt es an.
Festgehalten in `tests/test_anlaufschutz.py`.

**Welches Werkzeug wann** — sie schliessen sich gegenseitig aus, weil die Box
im lokalen Netz nur **eine** Verbindung annimmt:

| Situation | Werkzeug | Dienst |
|---|---|---|
| Was macht unsere Regelung beim Schalten? | `tools/schaltlog.py` | **laeuft** (er fragt `/api/live`) |
| Laedt das Auto ueberhaupt, ohne uns? | `tools/ladelog.py` | **gestoppt** |
| Welcher Datenpunkt ist das? | `tools/dp_dump.py --watch` | **gestoppt** |

`ladelog.py` ist der Versuch, der die Schuldfrage klaert: Steuerung aus, Karte
vorhalten, mitschreiben. Laedt das Auto dann, liegt es an unserer Software;
laedt es auch dann nicht, liegt es nicht an ihr. Er prueft selbst, ob der
Dienst noch antwortet, und weigert sich — sonst misst man den Streit um die
Verbindung und nicht die Anlage. Er schreibt **nichts**, nur `status()`. Am
Ende steht ein Urteil im Klartext.

**`tools/schaltlog.py` schreibt einen Schaltvorgang Sekunde fuer Sekunde mit:**
```bash
python3 tools/schaltlog.py --start --ampere 12 --sekunden 240
```
Setzt den Modus, protokolliert dann jede Aenderung mit Modus, Ladewunsch,
Schuetzzustand, Watt, Ampere, Control Pilot und allen laufenden Sperren, und
legt alles in `<GM_DATA>/schaltlog-*.txt` zum Verschicken ab. Laeuft mit dem
**System-Python** — nur Standardbibliothek, kein `.venv` noetig.

**Er fragt `/api/live`, nicht die Box.** Eine Tuya-Wallbox nimmt im lokalen
Netz nur **eine** Verbindung an: `dp_dump.py --watch` streitet sich mit dem
Dienst darum, und Schaltbefehle koennen dabei verlorengehen — das Messgeraet
zerstoert die Messung. `dp_dump.py` ist zum Suchen unbekannter Datenpunkte da,
am besten mit gestopptem Dienst; zum Beobachten des Betriebs nimmt man
`schaltlog.py`. Beide Werkzeuge sagen das jetzt selbst,
`tests/test_schaltlog.py` haelt es fest.

**`/protokoll` im Browser** zeigt Zustand und die letzten 20 Schaltvorgaenge
als Klartext — mit Uhrzeit, ein/aus, Erfolg, Ampere, `work_state`, Control
Pilot und Grund. Dafuer haelt der Treiber sie in einem Ringpuffer
(`driver.schaltungen`), nicht nur im Journal. Das ist der Unterschied zwischen
einer Fehlersuche, die der Betreiber selbst machen kann, und einer, bei der er
eine Linux-Konsole braucht — an genau dem Punkt ist eine ganze Nacht
verlorengegangen. Aus der Oberflaeche verlinkt.

**Und jeder Schaltvorgang steht auch im Journal, mit Grund:**
```
[wallbox Wallbox] Schuetz EIN — sofort: Sofortladen 16 A
[wallbox Wallbox] Schuetz AUS — stop: Modus Stop
```
Das fehlte, und deshalb war eine Stunde lang nicht zu klaeren, ob die Regelung,
ein Mensch oder die Box selbst geschaltet hat. `journalctl -u wallbox | grep
Schuetz` beantwortet das jetzt in einer Zeile.

## Ueberschuss = `-grid_w`, nicht `feed_in_w`
An der echten Anlage mitgelesen: Die Box wurde nachts im Minutentakt ein- und
ausgeschaltet, und im Protokoll stand „10890 W Ueberschuss", waehrend alles aus
dem Netz kam. `feed_in_w` ist `max(0, -grid_w)` und damit **nie negativ**; zieht
die Box aus dem Netz, steht dort 0. `ChargePoint.tick` zaehlt den Eigenverbrauch
der Box wieder dazu (zu Recht — sonst regelt sie sich bei jedem Takt selbst
weg), und aus 11 kW Bezug wurde ein Ueberschuss von 11 kW. Der PV-Modus lud
dann die ganze Nacht zum vollen Arbeitspreis weiter.

`app.py` uebergibt deshalb den **vorzeichenbehafteten** Zaehlerwert:
`ueberschuss_w = -grid_w + battery_extra_w - peer_w`. Festgehalten in
`tests/test_ueberschuss.py`. Zur Anzeige bleibt `feed_in_w` richtig — dort ist
die echte Einspeisung gemeint.

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
| Wanddisplay hochkant | `web/wand.html` → `/wand` |
| Tipp-Spiel am Display | `web/spiel.html` → `/spiel` (eigene Figuren) |
| Tagesverlauf-Graph | `web/tagesverlauf.js` — von `/` UND `/wand` benutzt |
| Akkugroesse lernen | `core/akku.py` (nur Untergrenze, nie eine Zusage) |

Zeichen in der Adresszeile: `web/favicon.svg` (Blitz auf dem Blau des Ladens,
`--lad`), ausgeliefert unter `/favicon.svg`, `/favicon.ico` und
`/icon-180.png`. Das PNG ist fuer „Zum Home-Bildschirm" am Handy — iOS nimmt
dafuer kein SVG und legt sonst einen Bildschirmausschnitt ab. Erzeugt wurde es
ohne Zusatzpaket (zlib + Ueberabtastung), `tests/test_icon.py` prueft Format,
Groesse und dass **jede** Seite es einbindet.

Fahrzeugfoto: mitgeliefert als `web/auto.png`; ein eigenes kommt ueber
`POST /api/vehicle/bild` nach `<GM_DATA>/fahrzeug.<ext>` und hat Vorrang.
Fehlt beides, zeichnet die Oberflaeche wieder ihre Kombi-Silhouette.

## Arbeiten an diesem Projekt
```bash
python3 app.py                       # startet mit config.example.json (Simulation)
for t in tests/test_*.py; do python3 "$t"; done     # 9 Testreihen
```
Am Gerät laeuft der Dienst aus `/opt/wallbox/.venv` — an der Konsole tippt man
`python3` und erwischt den System-Python, in dem **kein** Paket aus
`requirements.txt` liegt. Werkzeuge deshalb so starten:
```bash
/opt/wallbox/.venv/bin/python3 tools/dp_dump.py --watch
```
`core.paths.venv_hinweis()` sagt das bei einem fehlenden Paket von selbst und
nennt den vollstaendigen Befehl samt Argumenten (`tests/test_werkzeuge.py`).
`GM_DATA` braucht es dabei nicht: Ohne die Variable nimmt `core/paths.py`
ohnehin `<Projekt>/data`.

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
