"""
Echte End-to-End-Tests fuer den Sprachdienst - keine Mocks der
Kernmechanik. /synthesize laeuft gegen die real heruntergeladene Stimme
de_DE-thorsten-low; /transcribe wird mit genau dem Ergebnis von
/synthesize gefuettert (Piper -> Whisper Roundtrip), was beide Pfade
unabhaengig beweist, ohne eine echte Mikrofonaufnahme zu brauchen.

Nutzt bewusst das kleine "tiny"-Whisper-Modell (per Env-Var), damit der
Test schnell ist und keinen grossen Download braucht.
"""
import io
import os
import wave

os.environ.setdefault("SPEECH_SERVICE_WHISPER_MODEL", "tiny")

import pytest
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_synthesize_returns_valid_wav_bytes():
    r = client.post(
        "/synthesize",
        json={"text": "Guten Tag, wie geht es Ihnen heute?", "voice": "de_DE-thorsten-low"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert len(r.content) > 1000

    with wave.open(io.BytesIO(r.content), "rb") as wav_file:
        assert wav_file.getnframes() > 0
        assert wav_file.getnchannels() == 1


def test_synthesize_unknown_voice_returns_404():
    r = client.post("/synthesize", json={"text": "Test", "voice": "does_not_exist"})
    assert r.status_code == 404


def test_list_voices_includes_real_installed_voice():
    r = client.get("/voices")
    assert r.status_code == 200
    assert "de_DE-thorsten-low" in r.json()


def test_transcribe_roundtrip_with_real_synthesized_speech():
    synth = client.post(
        "/synthesize",
        json={
            "text": "Der Himmel ist heute schön blau und die Sonne scheint.",
            "voice": "de_DE-thorsten-low",
        },
    )
    assert synth.status_code == 200

    r = client.post(
        "/transcribe",
        files={"audio": ("aufnahme.wav", synth.content, "audio/wav")},
    )
    assert r.status_code == 200
    text = r.json()["text"].lower()
    assert text, "Transkription war leer - Roundtrip fehlgeschlagen"
    # Nicht auf exakten Wortlaut pruefen (Whisper "tiny" ist nicht
    # perfekt) - aber mindestens EIN Schluesselwort sollte ankommen.
    assert any(word in text for word in ["himmel", "blau", "sonne"])
