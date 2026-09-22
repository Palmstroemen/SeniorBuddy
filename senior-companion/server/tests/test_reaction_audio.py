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
    def __init__(self, voice_id, reaction_phrases):
        self.voice_id = voice_id
        self.reaction_phrases = reaction_phrases


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

    async def fake_synthesize(text, voice):
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

    async def fake_synthesize(text, voice):
        nonlocal call_count
        call_count += 1
        return b"wav-bytes"

    monkeypatch.setattr(reaction_audio.speech_client, "synthesize", fake_synthesize)
    monkeypatch.setattr(reaction_audio.random, "choice", lambda seq: seq[0])
    persona = _StubPersona("de_DE-thorsten-low", {"interrupted": ["Äh?"]})

    await reaction_audio.get_reaction_audio(persona, "interrupted")
    await reaction_audio.get_reaction_audio(persona, "interrupted")

    assert call_count == 1
