"""Den local_key der Wallbox neu holen — mit dem Benutzercode der SmartLife-App und einem QR-Code.

Anlass (17.11.2026): Seit 14.11. meldet die Box „Check device key or version" — sie hat sehr
wahrscheinlich einen neuen Schluessel. Der bisherige Weg (Cloud-Projekt auf iot.tuya.com, Access ID und
Secret, tools/tuya_setup.py auf einem PC) ist muehsam. Home Assistant holt Tuya-Geraete seit 2024 ohne
Entwicklerkonto: Benutzercode aus der App (Ich → Einstellungen → Konto und Sicherheit), QR-Code
anzeigen, in der App scannen und bestaetigen. Die dafuer gebaute Bibliothek (`tuya-device-sharing-sdk`,
Modul `tuya_sharing`) liefert die Geraeteliste mit `local_key`. Genau das passiert hier.

Grundsaetze:
* Der Schluessel verlaesst diese Klasse nur zum Eintragen in die Konfiguration — nie in eine Antwort,
  nie in ein Protokoll. Der Benutzercode auch nicht.
* Danach wird die Anmeldung bei Tuya wieder beendet (`Manager.unload`): kein dauerhafter Cloud-Zugang.
* Stellt nichts an der Box um. Nur Lesen der Geraeteliste.
"""
from __future__ import annotations

import re
import time

# Kennung und Schema, mit denen Home Assistant sich anmeldet (homeassistant/components/tuya/const.py).
CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA = "haauthorize"
FRIST_S = 180.0                   # so lange gilt ein QR-Code
_CODE = re.compile(r"^[A-Za-z0-9]{4,32}$")


class Fehler(Exception):
    """Ein Satz fuer die Oberflaeche — ohne Code, ohne Schluessel."""


class SchluesselHolen:
    def __init__(self, sdk=None, uhr=time.time):
        self._sdk = sdk           # zum Testen eine Nachbildung von tuya_sharing
        self._uhr = uhr
        self._lauf = None         # {"code", "token", "bis", "login"}

    def _modul(self):
        if self._sdk is not None:
            return self._sdk
        try:
            import tuya_sharing
        except ImportError:
            raise Fehler("Das Paket tuya-device-sharing-sdk fehlt — „Software aktualisieren“ "
                         "installiert es mit.")
        return tuya_sharing

    def start(self, benutzercode) -> str:
        """QR-Inhalt fuer die SmartLife-App. Ein neuer Start ersetzt einen alten."""
        code = str(benutzercode or "").strip()
        if not _CODE.match(code):
            raise Fehler("Der Benutzercode steht in der SmartLife-App unter Ich → Einstellungen → "
                         "Konto und Sicherheit (Buchstaben und Ziffern).")
        sdk = self._modul()
        login = sdk.LoginControl()
        try:
            antwort = login.qr_code(CLIENT_ID, SCHEMA, code) or {}
        except Exception:
            raise Fehler("Tuya ist gerade nicht erreichbar — hat die Steuerung Internet?")
        token = ((antwort.get("result") or {}).get("qrcode") if antwort.get("success") else None)
        if not token:
            raise Fehler("Tuya hat den Benutzercode nicht angenommen. Bitte in der App nachsehen und "
                         "genau so eintippen (Groß- und Kleinschreibung).")
        self._lauf = {"code": code, "token": str(token), "bis": self._uhr() + FRIST_S, "login": login}
        return f"tuyaSmart--qrLogin?token={token}"

    def pruefen(self, device_id: str, behalten: bool = False) -> dict:
        """Wurde der QR-Code in der App bestaetigt? Dann den Schluessel der Box holen.

        {"stand": "wartet"} · {"stand": "abgelaufen"} · {"stand": "fertig", "local_key", "name"}
        `local_key` nur fuer den Aufrufer zum Eintragen — er gibt ihn nie weiter.
        `behalten=True` (Cloud-Ersatzweg, 25.11.2026): die Anmeldung NICHT beenden, sondern als `zugang`
        zurueckgeben — nur fuer core/tuya_cloud.CloudWeg.speichern, nie fuer eine Antwort."""
        lauf = self._lauf
        if not lauf:
            return {"stand": "abgelaufen"}
        if self._uhr() > lauf["bis"]:
            self._lauf = None
            return {"stand": "abgelaufen"}
        try:
            ok, info = lauf["login"].login_result(lauf["token"], CLIENT_ID, lauf["code"])
        except Exception:
            return {"stand": "wartet"}
        if not ok:
            return {"stand": "wartet"}
        self._lauf = None                          # ein QR-Code, eine Anmeldung
        sdk = self._modul()
        try:
            token_info = {k: info.get(k) for k in ("t", "uid", "expire_time", "access_token", "refresh_token")}
            m = sdk.Manager(CLIENT_ID, lauf["code"], info.get("terminal_id"), info.get("endpoint"), token_info)
        except Exception:
            raise Fehler("Anmeldung bei Tuya hat nicht geklappt — bitte noch einmal von vorn.")
        try:
            m.update_device_cache()
            geraete = list((getattr(m, "device_map", None) or {}).values())
        except Exception:
            raise Fehler("Die Geräteliste kam nicht an — bitte noch einmal von vorn.")
        finally:
            if not behalten:
                try:
                    m.unload()                     # Anmeldung bei Tuya beenden
                except Exception:
                    pass
        box = next((g for g in geraete if str(getattr(g, "id", "")) == str(device_id or "")), None)
        if box is None:
            namen = ", ".join(str(getattr(g, "name", "?"))[:30] for g in geraete[:6]) or "keine"
            raise Fehler(f"In diesem SmartLife-Konto ist die Wallbox nicht dabei (gefunden: {namen}). "
                         "Ist sie in einem anderen Konto?")
        key = str(getattr(box, "local_key", "") or "")
        if not key:
            raise Fehler("Tuya hat für die Wallbox keinen Schlüssel geliefert.")
        aus = {"stand": "fertig", "local_key": key, "name": str(getattr(box, "name", "") or "")[:40]}
        if behalten:
            aus["zugang"] = {"user_code": lauf["code"], "terminal_id": info.get("terminal_id"),
                             "endpoint": info.get("endpoint"), "token_info": token_info}
        return aus
