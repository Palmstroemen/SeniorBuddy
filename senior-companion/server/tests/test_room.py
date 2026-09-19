"""
Tests fuer server/room.py - Anwesenheits-Tracking (in-memory, analog zu
priority.py's modulglobalem Zustand) und Ansprache-Erkennung fuer den
Gruppenchat.
"""
import pytest

import room


@pytest.fixture(autouse=True)
def _reset_room_state():
    room._present.clear()
    yield
    room._present.clear()


# --- Anwesenheit -----------------------------------------------------------

def test_present_personas_empty_for_new_user():
    assert room.present_personas("gc_user1") == []


def test_touch_marks_persona_present():
    room.touch("gc_user2", "freundin", now=1000.0)
    assert room.present_personas("gc_user2", now=1000.0) == ["freundin"]


def test_present_personas_excludes_stale_entries_past_timeout():
    room.touch("gc_user3", "freundin", now=1000.0)
    # Weit ausserhalb des Timeouts (Standard 900s).
    assert room.present_personas("gc_user3", now=1000.0 + 1000) == []


def test_present_personas_includes_entries_within_timeout():
    room.touch("gc_user4", "freundin", now=1000.0)
    assert room.present_personas("gc_user4", now=1000.0 + 500) == ["freundin"]


def test_present_personas_is_per_user():
    room.touch("gc_user5", "freundin", now=1000.0)
    assert room.present_personas("gc_user6", now=1000.0) == []


def test_is_present_reflects_timeout():
    room.touch("gc_user7", "professor", now=1000.0)
    assert room.is_present("gc_user7", "professor", now=1000.0 + 100) is True
    assert room.is_present("gc_user7", "professor", now=1000.0 + 1000) is False


def test_last_active_ts_returns_none_when_never_touched():
    assert room.last_active_ts("gc_user8", "freundin") is None


def test_last_active_ts_returns_most_recent_touch():
    room.touch("gc_user9", "freundin", now=1000.0)
    room.touch("gc_user9", "freundin", now=2000.0)
    assert room.last_active_ts("gc_user9", "freundin") == 2000.0


def test_touch_tracks_multiple_personas_independently():
    room.touch("gc_user10", "freundin", now=1000.0)
    room.touch("gc_user10", "professor", now=1500.0)
    assert set(room.present_personas("gc_user10", now=1500.0)) == {"freundin", "professor"}


# --- Ansprache-Erkennung ----------------------------------------------------

CANDIDATES = {"freundin": "Robin", "professor": "Wallner", "technikerin": "Toni"}


def test_detect_addressed_persona_matches_name_at_start():
    assert room.detect_addressed_persona("Wallner, was meinst du dazu?", CANDIDATES) == "professor"


def test_detect_addressed_persona_matches_with_short_interjection():
    assert room.detect_addressed_persona("Aber Robin, wenn du sagst...", CANDIDATES) == "freundin"
    assert room.detect_addressed_persona("Und Toni, kannst du das erklaeren?", CANDIDATES) == "technikerin"


def test_detect_addressed_persona_ignores_name_mentioned_mid_sentence():
    assert room.detect_addressed_persona("Ich glaube, Wallner hat da recht.", CANDIDATES) is None


def test_detect_addressed_persona_ignores_unrelated_leading_words():
    assert room.detect_addressed_persona("Ich glaube nicht, dass Robin das mag.", CANDIDATES) is None


def test_detect_addressed_persona_returns_none_when_nobody_named():
    assert room.detect_addressed_persona("Wie war eigentlich dein Tag?", CANDIDATES) is None


def test_detect_addressed_persona_only_matches_given_candidates():
    assert room.detect_addressed_persona("Alex, was denkst du?", CANDIDATES) is None
