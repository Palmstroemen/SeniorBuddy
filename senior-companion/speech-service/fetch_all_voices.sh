#!/usr/bin/env bash
# Laedt ALLE Piper-Stimmen, die die mitgelieferten Personas aktuell
# brauchen (siehe voice_id in server/config.py) - nicht nur die eine
# Test-Stimme aus setup.sh. Ueberspringt bereits vorhandene Dateien,
# kann also gefahrlos mehrfach laufen (z.B. nach dem Hinzufuegen einer
# neuen Persona mit neuer Stimme).
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p voices

# speaker/tier - ergibt zusammen den Piper-Dateinamen de_DE-<speaker>-<tier>
VOICES=(
  "thorsten/low"
  "kerstin/low"
  "karlsson/low"
  "ramona/low"
  "pavoque/low"
)

for entry in "${VOICES[@]}"; do
  speaker="${entry%/*}"
  tier="${entry#*/}"
  name="de_DE-${speaker}-${tier}"
  base="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/${speaker}/${tier}"

  if [ -f "voices/${name}.onnx" ] && [ -f "voices/${name}.onnx.json" ]; then
    echo "== ${name}: schon vorhanden, ueberspringe =="
    continue
  fi

  echo "== ${name}: lade =="
  curl -fL -o "voices/${name}.onnx" "${base}/${name}.onnx"
  curl -fL -o "voices/${name}.onnx.json" "${base}/${name}.onnx.json"
done

echo ""
echo "== Fertig. Dienst neu starten, damit alle Stimmen geladen werden: =="
echo "  sudo systemctl restart speech-service"
