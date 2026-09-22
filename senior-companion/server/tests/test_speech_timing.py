"""
Tests fuer server/speech_timing.py: sammelt Rohdaten darueber, wie
lange Nutzer:innen zwischen zwei erkannten Sprachphrasen pausieren
(client-seitig gemeldet, siehe app.js) - reine Beobachtung fuer eine
spaetere Kalibrierung der Dauer-Zuhoeren-Funktion, noch keine
automatische Anpassung (siehe Session-Notiz 2026-09-22).
"""
import pytest

import speech_timing


@pytest.fixture(autouse=True)
def _reset_speech_timing_state():
    speech_timing._pause_samples.clear()
    yield
    speech_timing._pause_samples.clear()


def test_record_pause_appears_in_stats():
    speech_timing.record_pause("user_a", 2.5)
    result = speech_timing.stats()
    assert result["user_a"]["count"] == 1
    assert result["user_a"]["avg_seconds"] == 2.5


def test_stats_computes_avg_median_max_across_samples():
    for seconds in [1.0, 2.0, 3.0, 4.0, 5.0]:
        speech_timing.record_pause("user_b", seconds)
    result = speech_timing.stats()
    assert result["user_b"]["count"] == 5
    assert result["user_b"]["avg_seconds"] == 3.0
    assert result["user_b"]["median_seconds"] == 3.0
    assert result["user_b"]["max_seconds"] == 5.0


def test_stats_is_empty_dict_when_nothing_recorded():
    assert speech_timing.stats() == {}


def test_negative_pause_is_ignored():
    speech_timing.record_pause("user_c", -1.0)
    assert "user_c" not in speech_timing.stats()


def test_samples_are_kept_separate_per_user():
    speech_timing.record_pause("user_d", 1.0)
    speech_timing.record_pause("user_e", 9.0)
    result = speech_timing.stats()
    assert result["user_d"]["avg_seconds"] == 1.0
    assert result["user_e"]["avg_seconds"] == 9.0


def test_sample_count_is_capped_to_avoid_unbounded_growth():
    for i in range(speech_timing.MAX_SAMPLES_PER_USER + 50):
        speech_timing.record_pause("user_f", 1.0)
    assert len(speech_timing._pause_samples["user_f"]) == speech_timing.MAX_SAMPLES_PER_USER
