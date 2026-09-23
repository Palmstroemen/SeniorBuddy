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


# Piper-Mehrsprecher-Stimmen (z.B. de_DE-mls-medium) waehlen die
# konkrete Stimme ueber speaker_id (piper.config.SynthesisConfig) - wir
# reichen den Wert nur durch, die eigentliche Mehrsprecher-Synthese ist
# Pipers Sache, nicht unsere. Zwei Tests: einer beweist die genaue
# Durchreichung (per Spy auf _get_voice, da ein reiner "kommt 200
# zurueck"-Test denselben Wert auch bei kaputter/fehlender
# Durchreichung liefern wuerde - echte Regression waere sonst
# unsichtbar), einer beweist, dass ein echtes Einsprecher-Modell
# speaker_id klaglos akzeptiert (kein Absturz bei num_speakers=1).

def _make_spy_voice(captured):
    """wav_file ist beim echten Aufruf bereits ein offenes
    wave.Wave_write (siehe main.py: `with wave.open(buffer, "wb") as
    wav_file`) - Piper setzt normalerweise selbst Kanal/Breite/Rate,
    bevor es Frames schreibt. Der Spy schreibt keine echten Frames,
    muss diese drei Werte aber trotzdem setzen, sonst schlaegt das
    Schliessen des WAV beim Verlassen des with-Blocks fehl."""
    class _SpyVoice:
        def synthesize_wav(self, text, wav_file, syn_config=None, **kwargs):
            captured["syn_config"] = syn_config
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
    return _SpyVoice()


def test_synthesize_passes_speaker_id_through_to_piper_synthesis_config(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "_get_voice", lambda voice_name: _make_spy_voice(captured))

    resp = client.post(
        "/synthesize",
        json={"text": "Test", "voice": "de_DE-thorsten-low", "speaker_id": 3},
    )
    assert resp.status_code == 200
    assert captured["syn_config"].speaker_id == 3


def test_synthesize_without_speaker_id_passes_none_to_synthesis_config(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "_get_voice", lambda voice_name: _make_spy_voice(captured))

    resp = client.post("/synthesize", json={"text": "Test", "voice": "de_DE-thorsten-low"})
    assert resp.status_code == 200
    assert captured["syn_config"] is None


def test_synthesize_accepts_speaker_id_against_real_single_speaker_voice():
    r = client.post(
        "/synthesize",
        json={
            "text": "Guten Tag, wie geht es Ihnen heute?",
            "voice": "de_DE-thorsten-low",
            "speaker_id": 0,
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert len(r.content) > 1000


def test_list_voices_includes_real_installed_voice():
    r = client.get("/voices")
    assert r.status_code == 200
    assert "de_DE-thorsten-low" in r.json()


# Fuers Sprecher-Dropdown im Persona-Designer (client/js/admin.js): eine
# Mehrsprecher-Stimme (z.B. de_DE-mls-medium) braucht num_speakers/
# speaker_id_map, um ueberhaupt eine Auswahl anbieten zu koennen. Direkt
# aus der .onnx.json gelesen - kein Laden des vollen PiperVoice-Modells
# noetig, nur fuer diese Metadaten.
def test_voice_speakers_reports_single_speaker_for_real_installed_voice():
    r = client.get("/voices/de_DE-thorsten-low/speakers")
    assert r.status_code == 200
    data = r.json()
    assert data["num_speakers"] == 1
    assert data["speaker_id_map"] == {}


def test_voice_speakers_unknown_voice_returns_404():
    r = client.get("/voices/does_not_exist/speakers")
    assert r.status_code == 404


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
