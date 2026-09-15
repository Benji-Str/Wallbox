#!/usr/bin/env bash
# Erzeugt ein einzelnes, selbstentpackendes Installationsskript.
#
#   bash tools/make_selfinstaller.sh [ziel.sh]
#
# Gedacht fuer Container ohne Git-Zugang: das Ergebnis enthaelt alle Dateien
# der Wallbox-Steuerung als eingebettetes Archiv und richtet Python-Umgebung,
# Dienst und Datenordner ein. Bewusst ein Generator statt einer eingecheckten
# Riesendatei — sonst veraltet die Kopie beim naechsten Commit.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${1:-wallbox_install.sh}

DATEIEN="app.py config.example.json requirements.txt
core/__init__.py core/paths.py core/charge.py core/chargepoint.py core/chargelog.py
drivers/__init__.py drivers/base.py drivers/tuya.py drivers/mock.py
meter/__init__.py meter/grid.py meter/mid.py
web/index.html web/style.css"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
tar czf "$TMP/nutzlast.tgz" $DATEIEN

{
cat <<'KOPF'
#!/usr/bin/env bash
# Wallbox-Steuerung — alles in einer Datei, keine Anmeldung noetig.
#
#   bash wallbox_install.sh
#
# Startet mit der SIMULIERTEN Wallbox — es wird keine echte Hardware
# angefasst, bis /opt/gridmine/data/wallbox.json umgestellt wird.
set -euo pipefail

ZIEL=${ZIEL:-/opt/wallbox}
PORT=${PORT:-8081}
DIENST=wallbox

echo "==> Pakete"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip >/dev/null

echo "==> Dateien nach $ZIEL"
mkdir -p "$ZIEL" "$ZIEL/data"
base64 -d <<'PAYLOAD_ENDE' | tar xz -C "$ZIEL"
KOPF
base64 -w76 "$TMP/nutzlast.tgz"
cat <<'FUSS'
PAYLOAD_ENDE

echo "==> Python-Umgebung (dauert 1-2 Minuten)"
python3 -m venv "$ZIEL/.venv"
"$ZIEL/.venv/bin/pip" install -q --upgrade pip
"$ZIEL/.venv/bin/pip" install -q -r "$ZIEL/requirements.txt"

echo "==> Dienst $DIENST"
cat > /etc/systemd/system/$DIENST.service <<UNIT
[Unit]
Description=Wallbox-Steuerung
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ZIEL
Environment=GM_DATA=$ZIEL/data
Environment=GM_CONFIG=$ZIEL/config.example.json
Environment=PORT=$PORT
ExecStart=$ZIEL/.venv/bin/python3 $ZIEL/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now $DIENST >/dev/null
sleep 6

IP=$(hostname -I | awk '{print $1}')
echo
if systemctl is-active --quiet $DIENST; then
  echo "===================================================="
  echo "  Laeuft:  http://$IP:$PORT"
  echo
  echo "  Kein Login — Geraet im eigenen Netz."
  echo "  Es laeuft die SIMULIERTE Wallbox, keine echte Hardware."
  echo
  echo "  Auf die echte Box umstellen:"
  echo "    nano $ZIEL/data/wallbox.json"
  echo "      type: wallbox_mock  ->  wallbox_tuya"
  echo "      ip / device_id / local_key eintragen"
  echo "    systemctl restart $DIENST"
  echo
  echo "  Log:  journalctl -u $DIENST -f"
  echo "===================================================="
else
  echo "Dienst startet nicht:"; journalctl -u $DIENST -n 30 --no-pager; exit 1
fi
FUSS
} > "$OUT"

chmod +x "$OUT"
bash -n "$OUT"
echo "erzeugt: $OUT  ($(stat -c%s "$OUT") B)"
