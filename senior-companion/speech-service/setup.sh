#!/usr/bin/env bash
# Ersteinrichtung des Sprachdienstes (STT/TTS) - eigener Prozess,
# eigenes venv, komplett getrennt von server/.
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "== Test-Stimme laden (de_DE-thorsten-low, ~60 MB) =="
mkdir -p voices
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/low"
if [ ! -f voices/de_DE-thorsten-low.onnx ]; then
  curl -fL -o voices/de_DE-thorsten-low.onnx "$VOICE_BASE/de_DE-thorsten-low.onnx"
fi
if [ ! -f voices/de_DE-thorsten-low.onnx.json ]; then
  curl -fL -o voices/de_DE-thorsten-low.onnx.json "$VOICE_BASE/de_DE-thorsten-low.onnx.json"
fi

echo ""
echo "== Fertig. Weitere Stimmen (siehe server/config.py, voice_id je Persona) =="
echo "genauso laden: https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE"
echo "z.B. de_DE-kerstin-low fuer die 'weiblich'-Varianten."
echo ""
echo "== Start zum Testen: =="
echo "  source .venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 8100"
