"""
Tests fuer server/honeypot.py: Honeyfile-Anlage, Alarmierung (gemockt),
und ein echter Integrationstest der inotify-Ueberwachung (kein Mock der
Kernmechanik - ein echter Dateizugriff aus dem Test muss den Alarm
ausloesen).
"""
import threading
import time

import pytest

import honeypot


def test_ensure_honeyfile_creates_dir_and_file():
    assert not honeypot.HONEYFILE.exists()
    honeypot.ensure_honeyfile()
    assert honeypot.HONEYFILE.exists()
    assert "admin" in honeypot.HONEYFILE.read_text(encoding="utf-8")


def test_ensure_honeyfile_does_not_overwrite_existing_file():
    honeypot.ensure_honeyfile()
    honeypot.HONEYFILE.write_text("veraendert", encoding="utf-8")
    honeypot.ensure_honeyfile()
    assert honeypot.HONEYFILE.read_text(encoding="utf-8") == "veraendert"


def test_alert_posts_to_ntfy_when_topic_configured(monkeypatch):
    monkeypatch.setattr(honeypot, "NTFY_TOPIC", "test-topic-123")
    calls = []
    monkeypatch.setattr(
        honeypot.httpx, "post",
        lambda url, **kw: calls.append((url, kw)),
    )

    honeypot.alert("Testnachricht")

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://ntfy.sh/test-topic-123"
    assert kwargs["content"] == b"Testnachricht"
    assert kwargs["headers"]["Priority"] == "urgent"


def test_alert_does_nothing_over_the_network_without_topic(monkeypatch):
    monkeypatch.setattr(honeypot, "NTFY_TOPIC", "")
    calls = []
    monkeypatch.setattr(honeypot.httpx, "post", lambda url, **kw: calls.append(url))

    honeypot.alert("Testnachricht")

    assert calls == []


def test_watching_a_real_file_access_triggers_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr(honeypot, "alert", lambda msg: alerts.append(msg))

    stop_event = threading.Event()
    thread = honeypot.start_watching(stop_event)
    try:
        # Echter Dateizugriff - keine Attrappe. Kurze Pause, damit der
        # Watch-Thread den Watch sicher schon registriert hat.
        time.sleep(0.2)
        with open(honeypot.HONEYFILE, "r", encoding="utf-8") as f:
            f.read()

        deadline = time.time() + 5
        while not alerts and time.time() < deadline:
            time.sleep(0.05)

        assert alerts, "Honeypot hat auf einen echten Dateizugriff nicht reagiert"
        assert "Honeyfile" in alerts[0]
    finally:
        stop_event.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
