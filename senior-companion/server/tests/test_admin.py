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
import lookahead
import main

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setattr(main.admin_auth, "ADMIN_TOKEN", "test-admin-token")


def test_admin_routes_require_auth():
    with TestClient(main.app) as client:
        r = client.get("/admin/stats")
    assert r.status_code == 401


def test_list_admin_models_requires_auth():
    with TestClient(main.app) as client:
        r = client.get("/admin/models")
    assert r.status_code == 401


def test_list_admin_models_returns_ollama_tags(monkeypatch):
    async def fake_list_available_models():
        return ["qwen2.5:7b-instruct", "qwen2.5:32b-instruct"]

    monkeypatch.setattr(main.llm_client, "list_available_models", fake_list_available_models)
    with TestClient(main.app) as client:
        r = client.get("/admin/models", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json() == ["qwen2.5:7b-instruct", "qwen2.5:32b-instruct"]


def test_list_admin_voices_requires_auth():
    with TestClient(main.app) as client:
        r = client.get("/admin/voices")
    assert r.status_code == 401


def test_list_admin_voices_returns_speech_service_voices(monkeypatch):
    async def fake_list_voices():
        return ["de_DE-thorsten-low", "de_DE-kerstin-low"]

    monkeypatch.setattr(main.speech_client, "list_voices", fake_list_voices)
    with TestClient(main.app) as client:
        r = client.get("/admin/voices", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json() == ["de_DE-thorsten-low", "de_DE-kerstin-low"]


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
    assert "lookahead" in data
    assert "delivery_rate_by_depth" in data["lookahead"]
    assert "speech_pauses" in data
    assert "problems" in data
    assert "guard_input_blocks" in data["problems"]
    assert "guard_context_blocks" in data["problems"]
    assert "plugin_failures" in data["problems"]
    assert "pending_deletion_directives" in data


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


def test_admin_stats_lookahead_block_reports_depth_breakdown():
    lookahead._levels_built[2] = 4
    lookahead._levels_delivered[2] = 1
    lookahead._discarded_interrupted = 3
    try:
        with TestClient(main.app) as client:
            r = client.get("/admin/stats", headers=ADMIN_HEADERS)
        data = r.json()["lookahead"]
        assert data["levels_built_by_depth"]["2"] == 4
        assert data["levels_delivered_by_depth"]["2"] == 1
        assert data["delivery_rate_by_depth"]["2"] == 0.25
        assert data["discarded_interrupted"] == 3
    finally:
        lookahead._levels_built[2] = 0
        lookahead._levels_delivered[2] = 0
        lookahead._discarded_interrupted = 0


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


def test_set_auto_turns_enabled_takes_effect_and_persists():
    import autoturn
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/auto-turns",
            json={"enabled": False},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 200
    assert autoturn.ENABLED is False
    assert admin_settings.load()["auto_turns_enabled"] is False
    autoturn.ENABLED = True  # aufraeumen


def test_set_auto_turns_enabled_survives_simulated_restart():
    import autoturn
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/config/auto-turns",
            json={"enabled": False},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 200
    autoturn.ENABLED = True
    main._apply_persisted_admin_settings()
    assert autoturn.ENABLED is False
    autoturn.ENABLED = True  # aufraeumen


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


# --- Todesfall-Bestaetigung ------------------------------------------------

def test_confirm_death_requires_auth():
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/confirm-death/deathtest_user", json={"confirm_user_id": "deathtest_user"}
        )
    assert r.status_code == 401


def test_confirm_death_rejects_mismatched_confirm_user_id():
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/confirm-death/deathtest_user",
            json={"confirm_user_id": "jemand_anderes"},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 400


def test_confirm_death_executes_pending_directives_and_preserves_the_rest():
    import memory
    memory.add_message(
        "deathtest_user2", "freundin", "user", "Vertraulich ueber Heinrich",
        topic="Heinrich",
    )
    memory.record_deletion_directive(
        "deathtest_user2", "freundin", "Heinrich", "im Todesfall loeschen",
        mode="on_death",
    )
    memory.add_message("deathtest_user2", "freundin", "user", "Ganz normales Gespraech")
    memory.add_fact("deathtest_user2", "enkel_name", "Max")
    memory.add_story_fragment("deathtest_user2", "story_1", "Eine schoene Geschichte")

    with TestClient(main.app) as client:
        r = client.post(
            "/admin/confirm-death/deathtest_user2",
            json={"confirm_user_id": "deathtest_user2"},
            headers=ADMIN_HEADERS,
        )
    assert r.status_code == 200
    assert r.json()["directives_executed"] == 1

    remaining = memory.recent_messages("deathtest_user2", "freundin", limit=20)
    assert len(remaining) == 1
    assert remaining[0]["content"] == "Ganz normales Gespraech"
    # Fakten und Lebensgeschichten bleiben fuer die Hinterbliebenen erhalten.
    assert memory.list_facts("deathtest_user2")[0]["value"] == "Max"
    with memory.get_db("deathtest_user2") as db:
        story = db.execute(
            "SELECT content FROM story_fragments WHERE story_id='story_1'"
        ).fetchone()
    assert story["content"] == "Eine schoene Geschichte"
