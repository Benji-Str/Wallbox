"""Cloud-Ersatzweg: Wenn die Box lokal haengt, ueber die SmartLife-Cloud lesen und schalten (25.11.2026).

Anlass: Zum zweiten Mal (14.11., 25.11.) nahm die Box im LAN keine Anmeldung mehr an — „Die Box nimmt die
Anmeldung nicht an". Die SmartLife-App zeigte zur selben Zeit Werte an: Die Box war gesund, nur ihr lokaler
Zugang hing; erst Strom aus/an half. Nutzer: „Das ist ja keine Loesung, wenn ich immer die Sicherung aus- und
anschalten muss."

So funktioniert es:
* Normal spricht die Steuerung lokal mit der Box — wie bisher, ohne Internet.
* Nur wenn der Nutzer den Ersatzweg EINMAL mit Benutzercode + QR-Code bestaetigt hat (core/schluessel.py mit
  `behalten=True`), liegt die Anmeldung in `data/tuya_cloud.json` (nur fuer den Eigentuemer lesbar). Ohne diese
  Datei gibt es keinen Cloud-Weg — Grundsatz „lokal, ohne Cloud" bleibt der Standard.
* Haengt die Box lokal, liest der Treiber (drivers/tuya.py) den Status ueber die Cloud und schaltet darueber;
  lokal wird nur noch alle LOKAL_WIEDER_S versucht. Antwortet die Box lokal wieder, geht es dorthin zurueck.

Schutz:
* Benutzercode und Tokens verlassen diese Datei nie (keine Antwort, kein Protokoll).
* Gelesen wird hoechstens alle STATUS_S (Tuya begrenzt Abfragen); dazwischen der letzte Stand.
* Werte kommen mit den Datenpunkt-Nummern zurueck, die der Treiber kennt — die Zuordnung Nummer -> Name holt der
  Weg einmal aus der Cloud; fuer die OS-EC01 steht sie ausserdem fest (BEKANNT).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from core.schluessel import CLIENT_ID

STATUS_S = 20.0               # Cloud-Status hoechstens so oft abfragen
LOKAL_WIEDER_S = 60.0         # im Cloud-Betrieb so oft lokal probieren
# Osoeri OS-EC01 (CLAUDE.md): Datenpunkt -> Name in der Cloud
BEKANNT = {1: "forward_energy_total", 3: "work_state", 4: "charge_cur_set", 6: "phase_a", 7: "phase_b",
           8: "phase_c", 9: "power_total", 10: "fault", 13: "connection_state", 14: "work_mode", 18: "switch",
           24: "temp_current"}


class CloudFehler(Exception):
    """Ein Satz fuer Waechter und Oberflaeche — ohne Token, ohne Benutzercode."""


class CloudWeg:
    def __init__(self, datei: Path, sdk=None, uhr=time.time):
        self.datei = Path(datei)
        self._sdk = sdk
        self._uhr = uhr
        self._m = None
        self._lock = threading.Lock()
        self._dp_zu_code: dict[str, dict[int, str]] = {}
        self._cache: dict[str, tuple[float, dict]] = {}
        self.letzter_fehler = ""

    # ---------- Anmeldung ablegen / entfernen ----------
    @property
    def aktiv(self) -> bool:
        return self.datei.exists()

    def speichern(self, zugang: dict) -> None:
        """Anmeldung aus core/schluessel.py (behalten=True) ablegen — Datei nur fuer den Eigentuemer."""
        need = ("user_code", "terminal_id", "endpoint", "token_info")
        if not isinstance(zugang, dict) or not all(zugang.get(k) for k in need):
            raise CloudFehler("Die Cloud-Anmeldung ist unvollstaendig — bitte noch einmal mit dem QR-Code.")
        self._schreiben({k: zugang[k] for k in need})
        self._m = None

    def entfernen(self) -> None:
        with self._lock:
            if self._m is not None:
                try:
                    self._m.unload()
                except Exception:
                    pass
            self._m = None
            self._cache.clear()
            try:
                self.datei.unlink()
            except FileNotFoundError:
                pass

    def _schreiben(self, daten: dict) -> None:
        self.datei.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.datei.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.datei)

    def _laden(self) -> dict:
        try:
            return json.loads(self.datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CloudFehler("Kein Cloud-Ersatzweg eingerichtet.")

    # ---------- Verbindung ----------
    def _modul(self):
        if self._sdk is not None:
            return self._sdk
        try:
            import tuya_sharing
        except ImportError:
            raise CloudFehler("Das Paket tuya-device-sharing-sdk fehlt — „Software aktualisieren“.")
        return tuya_sharing

    def _manager(self):
        if self._m is not None:
            return self._m
        z = self._laden()
        sdk = self._modul()
        weg = self

        class Merker(sdk.SharingTokenListener):           # erneuerte Tokens gleich sichern
            def update_token(self, token_info):
                try:
                    d = weg._laden()
                    d["token_info"] = {k: token_info.get(k) for k in
                                       ("t", "uid", "expire_time", "access_token", "refresh_token")}
                    weg._schreiben(d)
                except Exception:
                    pass
        try:
            self._m = sdk.Manager(CLIENT_ID, z["user_code"], z["terminal_id"], z["endpoint"], z["token_info"],
                                  Merker())
        except Exception:
            raise CloudFehler("Anmeldung bei der SmartLife-Cloud gescheitert — Ersatzweg neu einrichten.")
        return self._m

    def _zuordnung(self, m, device_id: str) -> dict[int, str]:
        if device_id in self._dp_zu_code:
            return self._dp_zu_code[device_id]
        karte = dict(BEKANNT)
        try:
            r = m.customer_api.get(f"/v1.0/m/life/devices/{device_id}/status") or {}
            for rel in ((r.get("result") or {}).get("dpStatusRelationDTOS") or []):
                karte[int(rel["dpId"])] = str(rel["statusCode"])
        except Exception:
            pass                                      # die feste Liste reicht fuer die OS-EC01
        self._dp_zu_code[device_id] = karte
        return karte

    # ---------- Lesen und Schalten ----------
    def status(self, device_id: str) -> dict:
        """Status als {"<dp>": Wert} wie lokal — hoechstens alle STATUS_S neu aus der Cloud."""
        with self._lock:
            jetzt = self._uhr()
            alt = self._cache.get(device_id)
            if alt and jetzt - alt[0] < STATUS_S:
                return dict(alt[1])
            m = self._manager()
            try:
                r = m.customer_api.get("/v1.0/m/life/ha/devices/detail", {"devIds": device_id}) or {}
            except Exception as e:
                self.letzter_fehler = f"Cloud nicht erreichbar ({type(e).__name__})"
                raise CloudFehler(self.letzter_fehler)
            geraet = next((g for g in (r.get("result") or []) if str(g.get("id")) == str(device_id)), None)
            if not r.get("success") or geraet is None:
                self.letzter_fehler = "Die Cloud kennt die Box nicht (anderes SmartLife-Konto?)"
                raise CloudFehler(self.letzter_fehler)
            if geraet.get("online") is False:
                self.letzter_fehler = "Die Box ist auch in der Cloud offline (Strom, WLAN?)"
                raise CloudFehler(self.letzter_fehler)
            werte = {str(s.get("code")): s.get("value") for s in (geraet.get("status") or [])
                     if isinstance(s, dict) and "code" in s}
            dps = {str(dp): werte[code] for dp, code in self._zuordnung(m, device_id).items() if code in werte}
            if not dps:
                self.letzter_fehler = "Die Cloud schickte keine Werte der Box"
                raise CloudFehler(self.letzter_fehler)
            self._cache[device_id] = (jetzt, dps)
            self.letzter_fehler = ""
            return dict(dps)

    def setzen(self, device_id: str, dp: int, wert) -> bool:
        """Einen Datenpunkt ueber die Cloud schreiben (Name statt Nummer). Wirft CloudFehler."""
        with self._lock:
            m = self._manager()
            code = self._zuordnung(m, device_id).get(int(dp))
            if not code:
                raise CloudFehler(f"Datenpunkt {dp} ist in der Cloud unbekannt")
            try:
                r = m.customer_api.post(f"/v1.1/m/thing/{device_id}/commands", None,
                                        {"commands": [{"code": code, "value": wert}]}) or {}
            except Exception as e:
                raise CloudFehler(f"Cloud nicht erreichbar ({type(e).__name__})")
            if not r.get("success"):
                raise CloudFehler("Die Cloud hat den Befehl nicht angenommen")
            alt = self._cache.get(device_id)                # was wir setzen, wissen wir
            if alt:
                alt[1][str(dp)] = wert
            return True
