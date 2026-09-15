#!/usr/bin/env bash
# Automatische Aktualisierung vom PROXMOX-HOST aus einrichten.
#
# LAEUFT AUF DEM HOST, nicht im Container:
#   bash proxmox_autoupdate.sh              # Container 105, stuendlich
#   CTID=203 INTERVALL=15min bash proxmox_autoupdate.sh
#
# Legt an:
#   /usr/local/bin/wallbox-update      holt den Stand und startet neu
#   systemd-Dienst + Timer             ruft es regelmaessig auf
#
# Wichtig: Es wird NICHT neu gestartet, solange geladen wird. Ein Neustart
# mitten im Ladevorgang bricht ihn ab; die Aktualisierung kann warten.
set -euo pipefail

CTID=${CTID:-105}
INTERVALL=${INTERVALL:-hourly}
ZIEL=${ZIEL:-/opt/wallbox}
DIENST=${DIENST:-wallbox}
PORT=${PORT:-8081}

command -v pct >/dev/null || { echo "pct nicht gefunden — laeuft das auf dem Proxmox-Host?" >&2; exit 1; }
pct status "$CTID" >/dev/null 2>&1 || { echo "Container $CTID gibt es nicht." >&2; exit 1; }

echo "==> /usr/local/bin/wallbox-update"
cat > /usr/local/bin/wallbox-update <<SKRIPT
#!/usr/bin/env bash
# Holt den Stand im Container $CTID und startet den Dienst neu — aber nicht
# waehrend eines Ladevorgangs. Mit --force trotzdem.
set -uo pipefail
CTID=$CTID
ZIEL=$ZIEL
DIENST=$DIENST
PORT=$PORT
ERZWINGEN=0
[ "\${1:-}" = "--force" ] && ERZWINGEN=1

melde(){ echo "[wallbox-update] \$*"; }

if ! pct status "\$CTID" 2>/dev/null | grep -q running; then
  melde "Container \$CTID laeuft nicht — nichts zu tun"; exit 0
fi

AUSGABE=\$(pct exec "\$CTID" -- git -C "\$ZIEL" pull --ff-only 2>&1) || {
  melde "git pull fehlgeschlagen: \$AUSGABE"; exit 1; }

if echo "\$AUSGABE" | grep -qiE "already up to date|bereits aktuell"; then
  melde "schon aktuell"; exit 0
fi
melde "neuer Stand geholt"
echo "\$AUSGABE" | sed 's/^/    /'

if [ "\$ERZWINGEN" -eq 0 ]; then
  LAEDT=\$(pct exec "\$CTID" -- bash -c \
    "curl -s -m 5 localhost:\$PORT/api/live | grep -o '\"charging\": *true' | head -1" 2>/dev/null || true)
  if [ -n "\$LAEDT" ]; then
    melde "es wird geladen — Neustart verschoben (naechster Lauf holt es nach)"
    exit 0
  fi
fi

# Abhaengigkeiten koennen sich geaendert haben
pct exec "\$CTID" -- bash -c "[ -x \$ZIEL/.venv/bin/pip ] && \$ZIEL/.venv/bin/pip install -q -r \$ZIEL/requirements.txt" || true
pct exec "\$CTID" -- systemctl restart "\$DIENST"
sleep 5
if pct exec "\$CTID" -- systemctl is-active --quiet "\$DIENST"; then
  melde "Dienst laeuft wieder"
else
  melde "ACHTUNG: Dienst startet nicht"
  pct exec "\$CTID" -- journalctl -u "\$DIENST" -n 20 --no-pager || true
  exit 1
fi
SKRIPT
chmod +x /usr/local/bin/wallbox-update

echo "==> systemd-Dienst und Timer ($INTERVALL)"
cat > /etc/systemd/system/wallbox-update.service <<UNIT
[Unit]
Description=Wallbox-Steuerung im Container $CTID aktualisieren
After=network-online.target pve-guests.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/wallbox-update
UNIT

cat > /etc/systemd/system/wallbox-update.timer <<UNIT
[Unit]
Description=Wallbox-Steuerung regelmaessig aktualisieren

[Timer]
OnCalendar=$INTERVALL
# gestreut, damit nicht alle Haeuser zur selben Minute bei GitHub anklopfen
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now wallbox-update.timer >/dev/null

echo
echo "===================================================="
echo "  Eingerichtet fuer Container $CTID, Takt: $INTERVALL"
echo
echo "  Jetzt sofort pruefen:   wallbox-update"
echo "  Auch beim Laden:        wallbox-update --force"
echo "  Naechster Lauf:         systemctl list-timers wallbox-update"
echo "  Protokoll:              journalctl -u wallbox-update"
echo "  Abschalten:             systemctl disable --now wallbox-update.timer"
echo
echo "  Waehrend eines Ladevorgangs wird NICHT neu gestartet —"
echo "  der naechste Lauf holt es nach."
echo "===================================================="
