#!/usr/bin/env bash
# Holt den aktuellen Stand und startet den Dienst neu — LAEUFT IM CONTAINER.
# Wird vom Timer aufgerufen, den tools/install.sh einrichtet.
#
#   bash selfupdate.sh            # nur neu starten, wenn nicht geladen wird
#   bash selfupdate.sh --force    # auch mitten im Ladevorgang
#
# Waehrend eines Ladevorgangs wird bewusst NICHT neu gestartet: das bricht ihn
# ab, und die Aktualisierung kann bis zum naechsten Lauf warten.
set -uo pipefail

ZIEL=${ZIEL:-/opt/wallbox}
DIENST=${DIENST:-wallbox}
PORT=${PORT:-8081}
NEUSTART_CMD=${NEUSTART_CMD:-systemctl restart $DIENST}
PRUEF_CMD=${PRUEF_CMD:-systemctl is-active --quiet $DIENST}
ERZWINGEN=0
[ "${1:-}" = "--force" ] && ERZWINGEN=1

melde(){ echo "[selfupdate] $*"; }

# Sicherheitskopie der eigenen Einstellungen. Ein git pull fasst sie nicht an,
# aber wer einmal erlebt hat, dass nach einem Update die MQTT-Zuordnung weg
# war, will das nicht noch einmal — die Kopie kostet nichts.
CFG="$ZIEL/data/wallbox.json"
if [ -f "$CFG" ]; then
  cp -p "$CFG" "$CFG.vor-update" 2>/dev/null || true
fi

AUSGABE=$(git -C "$ZIEL" pull --ff-only 2>&1) || {
  melde "git pull fehlgeschlagen: $AUSGABE"; exit 1; }

if [ ! -f "$CFG" ] && [ -f "$CFG.vor-update" ]; then
  melde "ACHTUNG: Konfiguration war nach dem Update weg — aus der Kopie zurueckgeholt"
  cp -p "$CFG.vor-update" "$CFG"
fi

if echo "$AUSGABE" | grep -qiE "already up to date|bereits aktuell"; then
  melde "schon aktuell"; exit 0
fi
melde "neuer Stand geholt:"
echo "$AUSGABE" | sed 's/^/    /'

if [ "$ERZWINGEN" -eq 0 ]; then
  if curl -s -m 5 "localhost:$PORT/api/live" 2>/dev/null \
     | grep -qE '"charging":[[:space:]]*true'; then
    melde "es wird geladen — Neustart verschoben, der naechste Lauf holt es nach"
    exit 0
  fi
fi

# Abhaengigkeiten koennen sich mit dem neuen Stand geaendert haben
if [ -x "$ZIEL/.venv/bin/pip" ]; then
  "$ZIEL/.venv/bin/pip" install -q -r "$ZIEL/requirements.txt" || \
    melde "Warnung: pip install nicht vollstaendig"
fi

melde "starte Dienst neu"
$NEUSTART_CMD || { melde "Neustart fehlgeschlagen"; exit 1; }
sleep 5
if $PRUEF_CMD; then
  melde "Dienst laeuft wieder"
else
  melde "ACHTUNG: Dienst startet nicht"
  journalctl -u "$DIENST" -n 20 --no-pager 2>/dev/null || true
  exit 1
fi
