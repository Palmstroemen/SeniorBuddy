"""
Tests fuer server/director.py - die "Regisseur"-Logik des Gruppenchats:
explizite Ansprache gewinnt immer, sonst eine gewichtete Zufallsauswahl
unter den anwesenden Personas (laenger stille Personas sind eher dran).
"""
import random

import pytest

import director


def test_pick_responder_returns_addressed_persona_when_given():
    result = director.pick_responder(
        ["freundin", "professor"], addressed="professor", last_active_ts={}, now=1000.0
    )
    assert result == "professor"


def test_pick_responder_addressed_wins_even_if_not_in_present_list():
    # room.py macht eine angesprochene, bisher abwesende Persona VOR dem
    # Aufruf des Regisseurs anwesend - der Regisseur selbst vertraut der
    # Ansprache bedingungslos.
    result = director.pick_responder(
        ["freundin"], addressed="professor", last_active_ts={}, now=1000.0
    )
    assert result == "professor"


def test_pick_responder_single_present_persona_shortcircuits_random_call(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("random.choices sollte hier nicht aufgerufen werden")
    monkeypatch.setattr(director.random, "choices", _boom)
    result = director.pick_responder(["freundin"], addressed=None, last_active_ts={}, now=1000.0)
    assert result == "freundin"


def test_pick_responder_favors_the_longer_silent_persona():
    random.seed(42)
    last_active = {"freundin": 1000.0 - 500, "professor": 1000.0 - 10}
    counts = {"freundin": 0, "professor": 0}
    for _ in range(200):
        picked = director.pick_responder(
            ["freundin", "professor"], addressed=None, last_active_ts=last_active, now=1000.0
        )
        counts[picked] += 1
    assert counts["freundin"] > counts["professor"]


def test_pick_responder_weight_is_capped_for_very_long_silence():
    random.seed(7)
    last_active = {"freundin": 1000.0 - 100000, "professor": 1000.0 - 50000}
    counts = {"freundin": 0, "professor": 0}
    for _ in range(200):
        picked = director.pick_responder(
            ["freundin", "professor"], addressed=None, last_active_ts=last_active, now=1000.0
        )
        counts[picked] += 1
    # Beide weit ueber dem Deckel still - keine soll die andere erdruecken.
    assert counts["freundin"] > 50
    assert counts["professor"] > 50


def test_pick_responder_never_active_persona_gets_full_weight():
    result = director.pick_responder(["freundin"], addressed=None, last_active_ts={}, now=1000.0)
    assert result == "freundin"


def test_deflection_hint_prompt_is_a_nonempty_string():
    assert isinstance(director.DEFLECTION_HINT_PROMPT, str)
    assert len(director.DEFLECTION_HINT_PROMPT) > 0
