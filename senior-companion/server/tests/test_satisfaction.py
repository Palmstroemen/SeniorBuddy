"""
Tests fuer server/satisfaction.py - reine Logik ueber memory.py's
Feedback-Tabelle, kein WebSocket/FastAPI noetig.
"""
import time

import memory
import satisfaction


def test_is_due_when_never_asked_before():
    assert satisfaction.is_due("sat_user1", "technikerin") is True


def test_is_due_false_right_after_asking():
    memory.record_feedback_asked("sat_user2", "technikerin")
    assert satisfaction.is_due("sat_user2", "technikerin") is False


def test_is_due_true_once_interval_has_passed():
    with memory.get_db("sat_user3") as db:
        db.execute(
            "INSERT INTO feedback (persona, asked_ts) VALUES (?,?)",
            ("technikerin", time.time() - satisfaction.CHECKIN_INTERVAL_DAYS * 86400 - 10),
        )
    assert satisfaction.is_due("sat_user3", "technikerin") is True


def test_is_due_respects_changed_interval(monkeypatch):
    monkeypatch.setattr(satisfaction, "CHECKIN_INTERVAL_DAYS", 1)
    with memory.get_db("sat_user4") as db:
        db.execute(
            "INSERT INTO feedback (persona, asked_ts) VALUES (?,?)",
            ("technikerin", time.time() - 2 * 86400),  # vor 2 Tagen
        )
    assert satisfaction.is_due("sat_user4", "technikerin") is True


def test_is_due_is_per_persona():
    memory.record_feedback_asked("sat_user5", "technikerin")
    # Andere Persona hat noch nie gefragt -> waere fuer sie faellig,
    # auch wenn die Technikerin gerade erst gefragt hat.
    assert satisfaction.is_due("sat_user5", "freundin") is True
    assert satisfaction.is_due("sat_user5", "technikerin") is False
