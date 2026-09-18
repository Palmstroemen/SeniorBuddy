"""
Konfiguration des Sprachdienstes (STT/TTS), separat von server/config.py -
eigener Prozess, eigene Konfiguration, genau wie Ollama nicht Teil von
server/ ist.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VOICES_DIR = BASE_DIR / "voices"

# "tiny" fuer schnelle Tests, "small" ist die empfohlene Produktionsgroesse
# (bessere Erkennung, noch CPU-tauglich). Ueber Umgebungsvariable
# ueberschreibbar, damit Tests ohne grossen Modell-Download laufen.
WHISPER_MODEL = os.environ.get("SPEECH_SERVICE_WHISPER_MODEL", "small")
WHISPER_DEVICE = "cpu"  # GPU bleibt bewusst Ollama vorbehalten
WHISPER_COMPUTE_TYPE = "int8"  # schnell und sparsam auf CPU
