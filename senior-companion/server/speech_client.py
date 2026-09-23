"""
Duenner Client fuer den Sprachdienst (speech-service/), analog zu
llm_client.py fuer Ollama: ein eigener lokaler Prozess, angesprochen
per HTTP, damit er unabhaengig vom Chat-Server laeuft (CPU-Isolation
von schwaecheren Tablets, siehe docs/ARCHITECTURE.md).
"""
import httpx

SPEECH_SERVICE_URL = "http://127.0.0.1:8100"


async def transcribe(audio_bytes: bytes, filename: str = "aufnahme.webm") -> str:
    """Schickt eine Audioaufnahme zur Erkennung. Gibt den erkannten Text
    zurueck (leerer String, falls nichts verstanden wurde)."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{SPEECH_SERVICE_URL}/transcribe",
            files={"audio": (filename, audio_bytes)},
        )
        resp.raise_for_status()
        return resp.json()["text"]


async def synthesize(text: str, voice: str, speaker_id: int | None = None) -> bytes:
    """Erzeugt gesprochene Sprache (WAV-Bytes) fuer den gegebenen Text
    in der gegebenen Piper-Stimme. speaker_id waehlt bei Mehrsprecher-
    Stimmen (z.B. de_DE-mls-medium) den konkreten Sprecher aus - bei
    Einsprecher-Stimmen wird er vom Sprachdienst ignoriert (None =
    Pipers eigener Standard)."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{SPEECH_SERVICE_URL}/synthesize",
            json={"text": text, "voice": voice, "speaker_id": speaker_id},
        )
        resp.raise_for_status()
        return resp.content


async def voice_speakers(voice_name: str) -> dict:
    """Sprecher-Metadaten (num_speakers/speaker_id_map) einer Stimme -
    fuer das Sprecher-Dropdown im Persona-Designer (main.py's GET
    /admin/voices/{voice_name}/speakers). Ein Ergebnis mit nur EINEM
    Sprecher (kein Fehlerfall im UI-Sinn) bei Nichterreichbarkeit/
    unbekannter Stimme, gleiches Fail-still-Prinzip wie list_voices()."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{SPEECH_SERVICE_URL}/voices/{voice_name}/speakers")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            return {"num_speakers": 1, "speaker_id_map": {}}


async def list_voices() -> list[str]:
    """Tatsaechlich vorhandene Piper-Stimmen - fuer das Stimmen-Dropdown
    im Persona-Designer (main.py's GET /admin/voices). Bei Sprachdienst
    nicht erreichbar leere Liste statt Exception, gleiches Prinzip wie
    llm_client.list_available_models()."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{SPEECH_SERVICE_URL}/voices")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            return []
