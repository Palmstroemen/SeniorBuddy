"""
Tests fuer server/autoturn.py - Phasen-Entscheidung und
Wahrscheinlichkeits-Gate fuer unaufgeforderte Persona-Fortsetzungen bei
Nutzer-Stille. Reine Funktionen, keine Zeit-/Zufalls-Abhaengigkeit von
aussen (analog zu test_director.py).
"""
import random

import autoturn


def test_decide_phase_continue_eligible_within_comfort_window():
    assert autoturn.decide_phase(elapsed_since_user=10.0, consecutive_auto_turns=0) == "continue_eligible"


def test_decide_phase_quiet_after_comfort_window_but_before_wrapup():
    elapsed = autoturn.COMFORT_WINDOW_SECONDS + 1
    assert elapsed < autoturn.WRAPUP_WINDOW_SECONDS
    assert autoturn.decide_phase(elapsed_since_user=elapsed, consecutive_auto_turns=0) == "quiet"


def test_decide_phase_wrapup_once_wrapup_window_elapsed():
    elapsed = autoturn.WRAPUP_WINDOW_SECONDS + 1
    assert autoturn.decide_phase(elapsed_since_user=elapsed, consecutive_auto_turns=0) == "wrapup"


def test_decide_phase_quiet_when_cap_hit_even_within_comfort_window():
    assert autoturn.decide_phase(
        elapsed_since_user=5.0, consecutive_auto_turns=autoturn.MAX_CONSECUTIVE_AUTO_TURNS,
    ) == "quiet"


def test_decide_phase_wrapup_wins_over_cap_once_wrapup_window_elapsed():
    """Zeitbasiertes Wrapup hat Vorrang - der Deckel allein loest KEIN
    frueheres Wrapup aus, aber wenn die Zeit selbst das Wrapup-Fenster
    erreicht, gilt Wrapup unabhaengig vom Deckel-Stand."""
    elapsed = autoturn.WRAPUP_WINDOW_SECONDS + 1
    assert autoturn.decide_phase(
        elapsed_since_user=elapsed, consecutive_auto_turns=autoturn.MAX_CONSECUTIVE_AUTO_TURNS,
    ) == "wrapup"


def test_should_continue_high_tendency_persona_mostly_pauses():
    random.seed(1)
    results = [autoturn.should_continue(0.8) for _ in range(200)]
    assert results.count(True) < results.count(False)


def test_should_continue_low_tendency_persona_mostly_continues():
    random.seed(2)
    results = [autoturn.should_continue(0.2) for _ in range(200)]
    assert results.count(True) > results.count(False)


def test_should_continue_forced_true_via_monkeypatched_random(monkeypatch):
    monkeypatch.setattr(autoturn.random, "random", lambda: 0.0)
    assert autoturn.should_continue(0.8) is True  # 0.0 < (1 - 0.8) = 0.2


def test_should_continue_forced_false_via_monkeypatched_random(monkeypatch):
    monkeypatch.setattr(autoturn.random, "random", lambda: 0.99)
    assert autoturn.should_continue(0.2) is False  # 0.99 >= (1 - 0.2) = 0.8


def test_auto_continue_prompt_is_a_nonempty_string():
    assert isinstance(autoturn.AUTO_CONTINUE_PROMPT, str)
    assert len(autoturn.AUTO_CONTINUE_PROMPT) > 0


def test_auto_wrapup_prompt_is_a_nonempty_string():
    assert isinstance(autoturn.AUTO_WRAPUP_PROMPT, str)
    assert len(autoturn.AUTO_WRAPUP_PROMPT) > 0


def test_auto_wrapup_prompt_differs_from_continue_prompt():
    assert autoturn.AUTO_WRAPUP_PROMPT != autoturn.AUTO_CONTINUE_PROMPT
