"""
Sprachdienst (STT/TTS) - eigener Prozess, eigenes venv, komplett
getrennt vom Chat-Server (server/), genau wie Ollama schon ein
eigenstaendiger Prozess ist. server/speech_client.py spricht per HTTP
mit diesem Dienst, wie llm_client.py mit Ollama.

Kernlogik (Modell-Ladeverhalten, Caching) orientiert sich an den
geprueften Funktionen aus YulYens_AI (src/stt/whisper_stt.py,
src/tts/piper_tts.py) - nur als eigener Dienst statt In-Process-Import,
weil hier (anders als dort) CPU-Isolation von schwaecheren Tablets und
vom Chat-Server ausdruecklich gewuenscht ist.
"""
import io
import logging
import tempfile
import wave
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, UploadFile
from pydantic import BaseModel

import config

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("speech-service")

_whisper_model = None
_voice_cache: dict[str, "PiperVoice"] = {}


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        log.info("Lade Whisper-Modell '%s' (device=%s)...", config.WHISPER_MODEL, config.WHISPER_DEVICE)
        _whisper_model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
        )
    return _whisper_model


def _get_voice(voice_name: str):
    if voice_name not in _voice_cache:
        from piper.voice import PiperVoice

        model_path = config.VOICES_DIR / f"{voice_name}.onnx"
        if not model_path.exists():
            raise HTTPException(
                404,
                f"Stimme '{voice_name}' nicht gefunden ({model_path}). "
                f"Siehe speech-service/setup.sh bzw. README fuer weitere Stimmen.",
            )
        log.info("Lade Piper-Stimme '%s'...", voice_name)
        _voice_cache[voice_name] = PiperVoice.load(str(model_path))
    return _voice_cache[voice_name]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Eager statt lazy laden - sonst traegt die ERSTE echte Anfrage
    # (nach main.py's speech_client-Timeout) den vollen Download-/
    # Ladeaufwand des Whisper-Modells und laeuft in einen Timeout. Genau
    # das Problem, fuer das server/scheduler.py's Vorwaerm-Logik schon
    # bei Ollama existiert - hier dieselbe Idee, nur beim Start statt
    # kurz vor einem Termin.
    log.info("Lade Modelle vor...")
    _get_whisper_model()
    for onnx_file in sorted(config.VOICES_DIR.glob("*.onnx")):
        try:
            _get_voice(onnx_file.stem)
        except Exception:
            log.exception("Stimme '%s' konnte beim Start nicht geladen werden", onnx_file.stem)
    log.info("Bereit.")
    yield


app = FastAPI(title="Sprachdienst (STT/TTS)", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe(audio: UploadFile):
    """Nimmt eine Browser-Aufnahme entgegen (ueblich: audio/webm mit
    Opus-Codec) und gibt den erkannten Text zurueck. Kein Format-Umbau
    noetig - faster-whisper decodiert per PyAV/ffmpeg selbst, wie schon
    bei YulYens_AI. Schreibt kurz in eine Temp-Datei (statt BytesIO),
    weil die Format-Erkennung ueber die Dateiendung zuverlaessiger ist."""
    suffix = Path(audio.filename or "").suffix or ".webm"
    data = await audio.read()
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(data)
        tmp.flush()
        model = _get_whisper_model()
        segments, _ = model.transcribe(tmp.name, language="de")
        text = " ".join(segment.text.strip() for segment in segments).strip()
    return {"text": text}


class SynthesizeRequest(BaseModel):
    text: str
    voice: str


@app.post("/synthesize")
async def synthesize(body: SynthesizeRequest):
    voice = _get_voice(body.voice)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize_wav(body.text, wav_file)
    return Response(content=buffer.getvalue(), media_type="audio/wav")
