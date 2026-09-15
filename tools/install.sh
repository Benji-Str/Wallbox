#!/usr/bin/env bash
# Wallbox-Steuerung in einem frischen Debian-/Ubuntu-LXC einrichten.
#
# LAEUFT IM CONTAINER, nicht auf dem Proxmox-Host.
#   bash install_lxc.sh                      # aus einem schon kopierten Ordner
#   REPO=https://github.com/... bash install_lxc.sh   # oder frisch klonen
#
# Richtet ein: Python-Umgebung, Dienst mit Autostart, Datenordner.
# Startet mit der Demo-Konfiguration (simulierte Wallbox) — es wird also
# nichts an echter Hardware angefasst, bis du die Config umstellst.
set -euo pipefail

ZIEL=${ZIEL:-/opt/wallbox}
REPO=${REPO:-}
PORT=${PORT:-8081}
DIENST=wallbox
VORLAGE=config.example.json

echo "==> Pakete"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl >/dev/null

echo "==> Dateien nach $ZIEL"
mkdir -p "$ZIEL"
if [ -n "$REPO" ]; then
  if [ -d "$ZIEL/.git" ]; then git -C "$ZIEL" pull --ff-only
  else git clone "$REPO" "$ZIEL"; fi
elif [ -f "$(dirname "$0")/../app.py" ]; then
  QUELLE=$(cd "$(dirname "$0")/.." && pwd -P)
  if [ "$QUELLE" = "$(cd "$ZIEL" 2>/dev/null && pwd -P)" ]; then
    # Aufruf aus dem Installationsordner heraus — nichts zu kopieren.
    # Ohne diese Abfrage bricht cp mit "are the same file" ab, und genau so
    # ruft man das Skript auf, wenn man den Update-Timer nachruesten will.
    echo "    laeuft bereits in $ZIEL — kopiere nicht"
    [ -d "$ZIEL/.git" ] && git -C "$ZIEL" pull --ff-only || true
  else
    cp -r "$QUELLE"/. "$ZIEL"/
  fi
else
  echo "FEHLER: weder REPO gesetzt noch app.py gefunden." >&2
  echo "        Entweder REPO=<git-url> setzen oder das Projekt hierher kopieren." >&2
  exit 1
fi

echo "==> Python-Umgebung"
python3 -m venv "$ZIEL/.venv"
"$ZIEL/.venv/bin/pip" install -q --upgrade pip
"$ZIEL/.venv/bin/pip" install -q -r "$ZIEL/requirements.txt"

mkdir -p "$ZIEL/data"

echo "==> Dienst $DIENST"
cat > /etc/systemd/system/$DIENST.service <<UNIT
[Unit]
Description=GridMine Wallbox-Steuerung
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ZIEL
Environment=GM_DATA=$ZIEL/data
Environment=GM_CONFIG=$ZIEL/$VORLAGE
Environment=PORT=$PORT
ExecStart=$ZIEL/.venv/bin/python3 $ZIEL/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

if [ "$AUTOUPDATE" = "1" ]; then
  echo "==> automatische Aktualisierung ($UPDATE_TAKT)"
  cat > /etc/systemd/system/$DIENST-update.service <<UNIT
[Unit]
Description=Wallbox-Steuerung aktualisieren
After=network-online.target

[Service]
Type=oneshot
Environment=ZIEL=$ZIEL
Environment=DIENST=$DIENST
Environment=PORT=$PORT
ExecStart=/bin/bash $ZIEL/tools/selfupdate.sh
UNIT

  cat > /etc/systemd/system/$DIENST-update.timer <<UNIT
[Unit]
Description=Wallbox-Steuerung regelmaessig aktualisieren

[Timer]
OnCalendar=$UPDATE_TAKT
# gestreut, damit nicht alle Anlagen zur selben Minute bei GitHub anklopfen
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
UNIT
fi

systemctl daemon-reload
systemctl enable --now $DIENST >/dev/null
[ "$AUTOUPDATE" = "1" ] && systemctl enable --now $DIENST-update.timer >/dev/null
sleep 6

IP=$(hostname -I | awk '{print $1}')
echo
if systemctl is-active --quiet $DIENST; then
  echo "===================================================="
  echo " Laeuft:  http://$IP:$PORT"
  echo
  echo " Kein Login — Geraet im eigenen Netz."
  echo
  echo " Steuerung:   systemctl status|restart|stop $DIENST"
  echo " Log:         journalctl -u $DIENST -f"
 if [ "$AUTOUPDATE" = "1" ]; then
   echo
   echo " Aktualisiert sich selbst ($UPDATE_TAKT) — aber nie mitten im"
   echo " Ladevorgang. Sofort:  bash $ZIEL/tools/selfupdate.sh"
   echo " Abschalten:  systemctl disable --now $DIENST-update.timer"
 fi
  echo " Daten:       $ZIEL/data   (Config, Benutzer, Ladelog)"
  echo
  echo " Es laeuft die DEMO mit simulierter Wallbox — keine echte Hardware."
  echo " Fuer den Echtbetrieb: $ZIEL/data/wallbox.json bearbeiten"
  echo " (type wallbox_mock -> wallbox_tuya, dazu ip/device_id/local_key),"
  echo " dann: systemctl restart $DIENST"
  echo "===================================================="
else
  echo "Dienst startet nicht. Ursache:"; journalctl -u $DIENST -n 30 --no-pager; exit 1
fi
