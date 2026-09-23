#!/usr/bin/env bash
# Laedt ein paar zusaetzliche, hoeherwertige deutsche Piper-Stimmen zum
# Ausprobieren nach - separat von setup.sh (die nur die eine
# Basis-Stimme fuers Ersteinrichten holt). Ueberschreibt/loescht nichts
# Bestehendes, alles landet einfach zusaetzlich in voices/.
#
# Nur "thorsten" hat aktuell ueberhaupt eine medium/high-Stufe in
# Piper (siehe https://github.com/rhasspy/piper/blob/master/VOICES.md) -
# kerstin/ramona/pavoque (Robin/Alex/Professor Wallner) gibt es bislang
# nur als "low". Diese drei hier sind also fuers Erste testweise fuer
# Toni (technikerin, config.py) gedacht, plus die emotionale Variante
# als zusaetzliche Geschmacksprobe.
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p voices

fetch_voice() {
  local rel_path="$1"     # z.B. thorsten/high/de_DE-thorsten-high
  local base="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE"
  local filename
  filename="$(basename "$rel_path")"

  if [ ! -f "voices/${filename}.onnx" ]; then
    echo "== Lade ${filename}.onnx =="
    curl -fL -o "voices/${filename}.onnx" "${base}/${rel_path}.onnx"
  else
    echo "== ${filename}.onnx schon vorhanden, ueberspringe =="
  fi

  if [ ! -f "voices/${filename}.onnx.json" ]; then
    curl -fL -o "voices/${filename}.onnx.json" "${base}/${rel_path}.onnx.json"
  fi
}

fetch_voice "thorsten/high/de_DE-thorsten-high"
fetch_voice "thorsten_emotional/medium/de_DE-thorsten_emotional-medium"

echo ""
echo "== Fertig. Neu geladene Stimmen: =="
ls -1 voices/*.onnx

echo ""
echo "== Sprachdienst neu starten, damit GET /voices/Persona-Designer sie sieht =="
