"""
Tests fuer die Fernwartungs-API (/admin/*) in main.py: Config-Aenderung
wirkt sofort UND uebersteht einen simulierten Neustart, Statistiken,
Update-Anforderung.
"""
import time

import pytest
from fastapi.testclient import TestClient

import admin_settings
import honeypot
import main

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setattr(main.admin_auth, "ADMIN_TOKEN", "test-admin-token")


def test_admin_routes_require_auth():
    with TestClient(main.app) as client:
        r = client.get("/admin/stats")
    assert r.status_code == 401


def test_get_persona_gender():
    with TestClient(main.app) as client:
        r = client.get("/admin/config/persona-gender", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json()["freundin"] == "neutral"


def test_set_persona_gender_takes_effect_immediately():
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/persona-gender/freundin",
            json={"gender": "weiblich"},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 200
    assert main.PERSONAS["freundin"].display_name == "Robin (die Freundin)"
    main.PERSONA_GENDER["freundin"] = "neutral"  # aufraeumen fuer andere Tests


def test_set_persona_gender_rejects_unknown_persona():
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/persona-gender/does_not_exist",
            json={"gender": "weiblich"},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 404


def test_set_persona_gender_rejects_invalid_value():
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/persona-gender/freundin",
            json={"gender": "quatsch"},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 400


def test_set_persona_gender_survives_simulated_restart():
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/persona-gender/freundin",
            json={"gender": "maennlich"},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 200

    # Simulierter Neustart: PERSONA_GENDER manuell auf den Default
    # zuruecksetzen (so wie es beim Neuimport von config.py waere),
    # dann die Start-Logik erneut aufrufen - kein echter Prozess-
    # Neustart noetig, um die Persistenz zu beweisen.
    main.PERSONA_GENDER["freundin"] = "neutral"
    main._apply_persisted_admin_settings()
    assert main.PERSONA_GENDER["freundin"] == "maennlich"
    main.PERSONA_GENDER["freundin"] = "neutral"  # aufraeumen


def test_set_ntfy_topic_updates_module_and_persists():
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/ntfy-topic",
            json={"topic": "mein-geheimes-topic"},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 200
    assert honeypot.NTFY_TOPIC == "mein-geheimes-topic"
    assert admin_settings.load()["ntfy_topic"] == "mein-geheimes-topic"


def test_admin_stats_has_expected_fields():
    with TestClient(main.app) as client:
        r = client.get("/admin/stats", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert "uptime_seconds" in data
    assert "users" in data
    assert "total_messages" in data
    assert "plugins_enabled" in data
    assert "priority" in data
    assert "preemptions" in data["priority"]
    assert "honeypot" in data
    assert "trigger_count" in data["honeypot"]
    assert "usage" in data
    assert "sentiment" in data
    assert "external_requests" in data
    assert "story_consent" in data
    assert "persona_usage" in data
    assert "problems" in data
    assert "guard_input_blocks" in data["problems"]
    assert "guard_context_blocks" in data["problems"]
    assert "plugin_failures" in data["problems"]


def test_admin_stats_usage_and_sentiment_reflect_real_users():
    import memory
    memory.add_message("statsuser_a", "freundin", "user", "Hallo")
    with TestClient(main.app) as client:
        r = client.get("/admin/stats", headers=ADMIN_HEADERS)
    data = r.json()
    assert "statsuser_a" in data["usage"]
    assert data["usage"]["statsuser_a"]["total_sessions"] == 1
    assert "statsuser_a" in data["sentiment"]


def test_admin_stats_persona_usage_breaks_down_per_persona():
    import memory
    memory.add_message("friendcircle_user", "freundin", "user", "Hallo Robin")
    memory.add_message("friendcircle_user", "freundin", "user", "Wie geht's?")
    memory.add_message("friendcircle_user", "professor", "user", "Eine Frage")
    with TestClient(main.app) as client:
        r = client.get("/admin/stats", headers=ADMIN_HEADERS)
    persona_usage = r.json()["persona_usage"]["friendcircle_user"]
    assert persona_usage["freundin"]["message_count"] == 2
    assert persona_usage["professor"]["message_count"] == 1


def test_set_satisfaction_interval_takes_effect_and_persists():
    import satisfaction
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/satisfaction-interval",
            json={"days": 3},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 200
    assert satisfaction.CHECKIN_INTERVAL_DAYS == 3
    assert admin_settings.load()["satisfaction_interval_days"] == 3
    satisfaction.CHECKIN_INTERVAL_DAYS = 7  # aufraeumen


def test_set_satisfaction_interval_rejects_invalid_value():
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/satisfaction-interval",
            json={"days": 0},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 400


def test_set_satisfaction_interval_survives_simulated_restart():
    import satisfaction
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/satisfaction-interval",
            json={"days": 5},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 200
    satisfaction.CHECKIN_INTERVAL_DAYS = 7
    main._apply_persisted_admin_settings()
    assert satisfaction.CHECKIN_INTERVAL_DAYS == 5
    satisfaction.CHECKIN_INTERVAL_DAYS = 7  # aufraeumen


def test_admin_feedback_empty_by_default():
    with TestClient(main.app) as client:
        r = client.get("/admin/feedback", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json() == []


def test_admin_feedback_requires_auth():
    with TestClient(main.app) as client:
        r = client.get("/admin/feedback")
    assert r.status_code == 401


def test_admin_feedback_aggregates_across_users():
    import memory
    memory.record_feedback_asked("feeduser_a", "technikerin")
    memory.record_feedback_reply("feeduser_a", "technikerin", "Passt super!")
    with TestClient(main.app) as client:
        r = client.get("/admin/feedback", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["user_id"] == "feeduser_a"
    assert rows[0]["reply"] == "Passt super!"


def test_request_update_writes_marker_file():
    with TestClient(main.app) as client:
        r = client.post("/admin/update", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert admin_settings.UPDATE_MARKER_FILE.exists()
    marker_ts = float(admin_settings.UPDATE_MARKER_FILE.read_text())
    assert abs(time.time() - marker_ts) < 5


def test_update_status_without_prior_log_returns_none():
    with TestClient(main.app) as client:
        r = client.get("/admin/update/status", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json()["log"] is None


def test_update_status_returns_log_tail():
    admin_settings.UPDATE_LOG_FILE.write_text("=== Update gestartet ===\nalles gut\n")
    with TestClient(main.app) as client:
        r = client.get("/admin/update/status", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert "alles gut" in r.json()["log"]
