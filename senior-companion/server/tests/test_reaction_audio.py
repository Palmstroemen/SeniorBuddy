"""
Tests fuer reaction_audio.py: kurze, vorab synthetisierte Reaktions-
saetze fuer bestimmte Gespraechssituationen (z.B. "wird unterbrochen"),
siehe PersonaConfig.reaction_phrases. speech_client.synthesize() wird
durchgaengig gemockt (kein echter Sprachdienst noetig, siehe
test_llm_client.py fuer dasselbe Prinzip bei Ollama).
"""
import pytest

import config
import reaction_audio


@pytest.fixture(autouse=True)
def _reset_reaction_audio_cache():
    """_cache ist absichtlich Modul-globaler Zustand (soll ueber die
    gesamte Prozesslaufzeit wirken) - zwischen Tests aber isoliert
    zuruecksetzen, sonst wuerde ein frueherer Test denselben (Stimme,
    Satz)-Cache-Eintrag hinterlassen und einen spaeteren Test
    verfaelschen."""
    reaction_audio._cache.clear()
    yield
    reaction_audio._cache.clear()


class _StubPersona:
    def __init__(self, voice_id, reaction_phrases, voice_speaker_id=None):
        self.voice_id = voice_id
        self.reaction_phrases = reaction_phrases
        self.voice_speaker_id = voice_speaker_id


async def test_get_reaction_audio_returns_none_for_unknown_situation(monkeypatch):
    persona = _StubPersona("v1", {"interrupted": ["Äh?"]})
    result = await reaction_audio.get_reaction_audio(persona, "silence")
    assert result is None


async def test_get_reaction_audio_returns_none_when_no_phrases_at_all(monkeypatch):
    persona = _StubPersona("v1", {})
    result = await reaction_audio.get_reaction_audio(persona, "interrupted")
    assert result is None


async def test_get_reaction_audio_synthesizes_a_chosen_phrase(monkeypatch):
    calls = []

    async def fake_synthesize(text, voice, speaker_id=None):
        calls.append((text, voice))
        return b"wav-bytes"

    monkeypatch.setattr(reaction_audio.speech_client, "synthesize", fake_synthesize)
    monkeypatch.setattr(reaction_audio.random, "choice", lambda seq: seq[0])
    persona = _StubPersona("de_DE-thorsten-low", {"interrupted": ["Äh?", "Moment!"]})

    result = await reaction_audio.get_reaction_audio(persona, "interrupted")

    assert result == b"wav-bytes"
    assert calls == [("Äh?", "de_DE-thorsten-low")]


async def test_get_reaction_audio_caches_by_voice_and_phrase(monkeypatch):
    call_count = 0

    async def fake_synthesize(text, voice, speaker_id=None):
        nonlocal call_count
        call_count += 1
        return b"wav-bytes"

    monkeypatch.setattr(reaction_audio.speech_client, "synthesize", fake_synthesize)
    monkeypatch.setattr(reaction_audio.random, "choice", lambda seq: seq[0])
    persona = _StubPersona("de_DE-thorsten-low", {"interrupted": ["Äh?"]})

    await reaction_audio.get_reaction_audio(persona, "interrupted")
    await reaction_audio.get_reaction_audio(persona, "interrupted")

    assert call_count == 1


async def test_get_reaction_audio_picks_du_variant_when_anrede_is_du(monkeypatch):
    captured = {}

    async def fake_synthesize(text, voice, speaker_id=None):
        captured["text"] = text
        return b"wav-bytes"

    monkeypatch.setattr(reaction_audio.speech_client, "synthesize", fake_synthesize)
    persona = _StubPersona("v1", {
        "resumed": {"sie": ["Schön, dass Sie wieder da sind."], "du": ["Schön, dass du wieder da bist."]},
    })

    await reaction_audio.get_reaction_audio(persona, "resumed", anrede="du")

    assert captured["text"] == "Schön, dass du wieder da bist."


async def test_get_reaction_audio_defaults_to_sie_variant(monkeypatch):
    captured = {}

    async def fake_synthesize(text, voice, speaker_id=None):
        captured["text"] = text
        return b"wav-bytes"

    monkeypatch.setattr(reaction_audio.speech_client, "synthesize", fake_synthesize)
    persona = _StubPersona("v1", {
        "resumed": {"sie": ["Schön, dass Sie wieder da sind."], "du": ["Schön, dass du wieder da bist."]},
    })

    await reaction_audio.get_reaction_audio(persona, "resumed")

    assert captured["text"] == "Schön, dass Sie wieder da sind."


async def test_get_reaction_audio_passes_persona_speaker_id_to_synthesize(monkeypatch):
    captured = {}

    async def fake_synthesize(text, voice, speaker_id=None):
        captured["speaker_id"] = speaker_id
        return b"wav-bytes"

    monkeypatch.setattr(reaction_audio.speech_client, "synthesize", fake_synthesize)
    monkeypatch.setattr(reaction_audio.random, "choice", lambda seq: seq[0])
    persona = _StubPersona("de_DE-mls-medium", {"interrupted": ["Äh?"]}, voice_speaker_id=2)

    await reaction_audio.get_reaction_audio(persona, "interrupted")

    assert captured["speaker_id"] == 2


async def test_get_reaction_audio_cache_key_includes_speaker_id(monkeypatch):
    """Zwei Personas mit gleichem voice_id, aber unterschiedlichem
    speaker_id, duerfen sich beim Cachen NICHT gegenseitig die falsche
    (Mehrsprecher-)Stimme unterschieben."""
    call_count = 0

    async def fake_synthesize(text, voice, speaker_id=None):
        nonlocal call_count
        call_count += 1
        return f"wav-fuer-sprecher-{speaker_id}".encode()

    monkeypatch.setattr(reaction_audio.speech_client, "synthesize", fake_synthesize)
    monkeypatch.setattr(reaction_audio.random, "choice", lambda seq: seq[0])
    persona_a = _StubPersona("de_DE-mls-medium", {"interrupted": ["Äh?"]}, voice_speaker_id=1)
    persona_b = _StubPersona("de_DE-mls-medium", {"interrupted": ["Äh?"]}, voice_speaker_id=2)

    result_a = await reaction_audio.get_reaction_audio(persona_a, "interrupted")
    result_b = await reaction_audio.get_reaction_audio(persona_b, "interrupted")

    assert call_count == 2
    assert result_a == b"wav-fuer-sprecher-1"
    assert result_b == b"wav-fuer-sprecher-2"


async def test_get_reaction_audio_falls_back_to_sie_for_unknown_anrede_value(monkeypatch):
    captured = {}

    async def fake_synthesize(text, voice, speaker_id=None):
        captured["text"] = text
        return b"wav-bytes"

    monkeypatch.setattr(reaction_audio.speech_client, "synthesize", fake_synthesize)
    persona = _StubPersona("v1", {
        "resumed": {"sie": ["Schön, dass Sie wieder da sind."], "du": ["Schön, dass du wieder da bist."]},
    })

    await reaction_audio.get_reaction_audio(persona, "resumed", anrede="quatsch")

    assert captured["text"] == "Schön, dass Sie wieder da sind."
