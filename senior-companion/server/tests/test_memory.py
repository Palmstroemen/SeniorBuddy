import sqlite3

import memory


def test_messages_are_stored_in_order():
    memory.add_message("alice", "freundin", "user", "Hallo")
    memory.add_message("alice", "freundin", "assistant", "Servus!")
    msgs = memory.recent_messages("alice", "freundin")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "Hallo"


def test_messages_are_isolated_per_persona():
    memory.add_message("alice", "freundin", "user", "A")
    memory.add_message("alice", "professor", "user", "B")
    assert len(memory.recent_messages("alice", "freundin")) == 1
    assert len(memory.recent_messages("alice", "professor")) == 1


def test_messages_are_isolated_per_user():
    memory.add_message("alice", "freundin", "user", "A")
    memory.add_message("bob", "freundin", "user", "B")
    assert len(memory.recent_messages("alice", "freundin")) == 1
    assert len(memory.recent_messages("bob", "freundin")) == 1


def test_transparency_log_is_empty_by_default():
    assert memory.get_transparency_log("bob") == []


def test_transparency_log_records_external_request():
    memory.log_external_request(
        "bob", "weather", "Wetter fuer Spaziergang pruefen",
        {"lat": 48.2, "lon": 16.4},
    )
    log = memory.get_transparency_log("bob")
    assert len(log) == 1
    assert log[0]["plugin"] == "weather"
    assert "48.2" in log[0]["data_sent"]


def test_story_fragment_starts_undecided():
    memory.add_story_fragment("carol", "story_kuehe", "Wir hatten eine Kuh...")
    with memory.get_db("carol") as db:
        row = db.execute(
            "SELECT consent_status FROM story_fragments WHERE story_id=?",
            ("story_kuehe",),
        ).fetchone()
    assert row["consent_status"] == "undecided"


def test_story_consent_can_be_set_and_updated():
    memory.add_story_fragment("carol", "story_hof", "...")
    memory.set_story_consent("carol", "story_hof", "kids", "nicht fuer Tante Grete")
    with memory.get_db("carol") as db:
        row = db.execute(
            "SELECT consent_status, consent_note FROM story_fragments WHERE story_id=?",
            ("story_hof",),
        ).fetchone()
    assert row["consent_status"] == "kids"
    assert row["consent_note"] == "nicht fuer Tante Grete"

    # Wichtig: Einwilligung muss widerrufbar sein (siehe Konzeptgespraech).
    memory.set_story_consent("carol", "story_hof", "deleted")
    with memory.get_db("carol") as db:
        row = db.execute(
            "SELECT consent_status FROM story_fragments WHERE story_id=?",
            ("story_hof",),
        ).fetchone()
    assert row["consent_status"] == "deleted"


def test_each_user_gets_a_separate_database_file(tmp_path):
    memory.add_message("alice", "freundin", "user", "hi")
    memory.add_message("bob", "freundin", "user", "hi")
    assert memory.db_path_for("alice") != memory.db_path_for("bob")
    assert memory.db_path_for("alice").exists()
    assert memory.db_path_for("bob").exists()


# --- Personen-Fakten (RAG-Quelle 1) -------------------------------------

def test_add_fact_and_list_facts_roundtrip():
    memory.add_fact("dave", "enkel_name", "Max", source_persona="freundin")
    facts = memory.list_facts("dave")
    assert len(facts) == 1
    assert facts[0]["key"] == "enkel_name"
    assert facts[0]["value"] == "Max"
    assert facts[0]["source_persona"] == "freundin"


def test_list_facts_dedupes_by_key_keeping_newest():
    with memory.get_db("dave") as db:
        db.execute(
            "INSERT INTO facts (key, value, source_persona, ts) VALUES (?,?,?,?)",
            ("enkel_name", "Maxi", "freundin", 1000.0),
        )
    memory.add_fact("dave", "enkel_name", "Maximilian", source_persona="reporter")

    facts = memory.list_facts("dave")
    assert len(facts) == 1
    assert facts[0]["value"] == "Maximilian"
    assert facts[0]["source_persona"] == "reporter"


def test_facts_are_isolated_per_user():
    memory.add_fact("dave", "wohnort", "Graz")
    memory.add_fact("erin", "wohnort", "Linz")
    assert len(memory.list_facts("dave")) == 1
    assert len(memory.list_facts("erin")) == 1
    assert memory.list_facts("dave")[0]["value"] == "Graz"


def test_list_facts_respects_limit():
    memory.add_fact("dave", "a", "1")
    memory.add_fact("dave", "b", "2")
    memory.add_fact("dave", "c", "3")
    facts = memory.list_facts("dave", limit=2)
    assert len(facts) == 2
    assert [f["key"] for f in facts] == ["c", "b"]


def test_get_fact_returns_none_when_missing():
    assert memory.get_fact("ufact", "anrede:freundin") is None


def test_get_fact_returns_latest_value():
    memory.add_fact("ufact2", "anrede:freundin", "sie")
    memory.add_fact("ufact2", "anrede:freundin", "du")
    assert memory.get_fact("ufact2", "anrede:freundin") == "du"


# --- Schema-Migration (erste ALTER-TABLE-Aenderung des Projekts) -------

def test_migrate_adds_sentiment_and_stance_columns_to_existing_db():
    # Simuliert eine VOR dieser Aenderung angelegte Datenbank: nur die
    # urspruengliche Spaltenmenge, ohne sentiment/stance.
    db_path = memory.db_path_for("premigration_user")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "persona TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, "
        "ts REAL NOT NULL)"
    )
    conn.commit()
    conn.close()

    with memory.get_db("premigration_user") as db:
        cols = [r[1] for r in db.execute("PRAGMA table_info(messages)").fetchall()]
    assert "sentiment" in cols
    assert "stance" in cols


def test_migrate_is_idempotent_across_multiple_db_opens():
    with memory.get_db("idempotent_user"):
        pass
    with memory.get_db("idempotent_user"):
        pass  # darf nicht mit "duplicate column name" crashen


# --- Nutzungsstatistik ---------------------------------------------------

def test_usage_stats_empty_when_no_messages():
    stats = memory.usage_stats("nobody")
    assert stats["total_sessions"] == 0
    assert stats["total_active_minutes"] == 0
    assert stats["distinct_active_days"] == 0
    assert stats["avg_minutes_per_active_day"] == 0
    assert stats["first_message_ts"] is None
    assert stats["last_message_ts"] is None


def test_usage_stats_groups_into_sessions_by_gap():
    with memory.get_db("uframe") as db:
        db.executemany(
            "INSERT INTO messages (persona, role, content, ts) VALUES (?,?,?,?)",
            [
                ("freundin", "user", "a", 1000.0),
                ("freundin", "user", "b", 1000.0 + 60),
                ("freundin", "user", "c", 1000.0 + 120),
                # > 20 Minuten Luecke -> neue Session
                ("freundin", "user", "d", 1000.0 + 120 + 30 * 60),
            ],
        )
    stats = memory.usage_stats("uframe", session_gap_minutes=20)
    assert stats["total_sessions"] == 2
    assert stats["total_active_minutes"] == 3.0  # Session 1: 2min, Session 2 (1 Nachricht): 1min Boden
    assert stats["distinct_active_days"] == 1
    assert stats["avg_minutes_per_active_day"] == 3.0
    assert stats["first_message_ts"] == 1000.0
    assert stats["last_message_ts"] == 1000.0 + 120 + 30 * 60


def test_usage_stats_ignores_assistant_messages_for_session_grouping():
    with memory.get_db("uassist") as db:
        db.executemany(
            "INSERT INTO messages (persona, role, content, ts) VALUES (?,?,?,?)",
            [
                ("freundin", "user", "a", 1000.0),
                ("freundin", "assistant", "reply", 1000.0 + 5),
            ],
        )
    stats = memory.usage_stats("uassist")
    assert stats["total_sessions"] == 1


def test_usage_stats_can_be_scoped_to_a_single_persona():
    with memory.get_db("upersona") as db:
        db.executemany(
            "INSERT INTO messages (persona, role, content, ts) VALUES (?,?,?,?)",
            [
                ("freundin", "user", "a", 1000.0),
                ("freundin", "user", "b", 1000.0 + 60),
                # > 20 Minuten nach der letzten Freundin-Nachricht.
                ("professor", "user", "c", 1000.0 + 60 + 30 * 60),
            ],
        )
    freundin_only = memory.usage_stats("upersona", persona="freundin")
    assert freundin_only["total_sessions"] == 1
    assert freundin_only["first_message_ts"] == 1000.0
    assert freundin_only["last_message_ts"] == 1000.0 + 60

    professor_only = memory.usage_stats("upersona", persona="professor")
    assert professor_only["total_sessions"] == 1
    assert professor_only["first_message_ts"] == 1000.0 + 60 + 30 * 60

    pooled = memory.usage_stats("upersona")
    assert pooled["total_sessions"] == 2  # ungefiltert: Lücke > 20min trennt


# --- Persona-Nutzung ("Freundeskreis") -----------------------------------

def test_persona_usage_stats_empty_when_no_messages():
    assert memory.persona_usage_stats("nobody_persona") == {}


def test_persona_usage_stats_breaks_down_by_persona():
    memory.add_message("upfriends", "freundin", "user", "Hallo Robin!")
    memory.add_message("upfriends", "freundin", "user", "Wie war dein Tag?")
    memory.add_message("upfriends", "professor", "user", "Eine Frage zur Geschichte")
    memory.add_message("upfriends", "professor", "assistant", "Gerne, welche denn?")

    stats = memory.persona_usage_stats("upfriends")
    assert set(stats.keys()) == {"freundin", "professor"}
    assert stats["freundin"]["message_count"] == 2
    assert stats["professor"]["message_count"] == 1
    assert stats["freundin"]["total_sessions"] == 1
    assert stats["professor"]["last_message_ts"] is not None


def test_persona_usage_stats_message_count_ignores_assistant_replies():
    memory.add_message("upfriends2", "freundin", "user", "Frage")
    memory.add_message("upfriends2", "freundin", "assistant", "Antwort")
    memory.add_message("upfriends2", "freundin", "assistant", "Noch eine Antwort")
    stats = memory.persona_usage_stats("upfriends2")
    assert stats["freundin"]["message_count"] == 1


# --- Sentiment-/Haltungs-Klassifikation (Rohdaten fuer sentiment_job) --

def test_unclassified_messages_returns_only_unclassified_user_messages():
    memory.add_message("uclass", "freundin", "user", "Hallo")
    memory.add_message("uclass", "freundin", "assistant", "Servus")
    pending = memory.unclassified_messages("uclass")
    assert len(pending) == 1
    assert pending[0]["content"] == "Hallo"


def test_set_message_classification_updates_row_and_removes_from_pending():
    memory.add_message("uclass2", "freundin", "user", "Ich freu mich")
    pending = memory.unclassified_messages("uclass2")
    message_id = pending[0]["id"]
    memory.set_message_classification("uclass2", message_id, "positiv", "zustimmung")
    assert memory.unclassified_messages("uclass2") == []
    with memory.get_db("uclass2") as db:
        row = db.execute(
            "SELECT sentiment, stance FROM messages WHERE id=?", (message_id,)
        ).fetchone()
    assert row["sentiment"] == "positiv"
    assert row["stance"] == "zustimmung"


def test_unclassified_messages_respects_limit():
    for i in range(5):
        memory.add_message("uclass3", "freundin", "user", f"msg{i}")
    assert len(memory.unclassified_messages("uclass3", limit=2)) == 2


def test_sentiment_stats_counts_groups_and_pending():
    memory.add_message("usent", "freundin", "user", "a")
    memory.add_message("usent", "freundin", "user", "b")
    memory.add_message("usent", "freundin", "user", "c")
    pending = memory.unclassified_messages("usent")
    memory.set_message_classification("usent", pending[0]["id"], "positiv", "zustimmung")
    memory.set_message_classification("usent", pending[1]["id"], "negativ", "widerspruch")
    stats = memory.sentiment_stats("usent")
    assert stats["sentiment"] == {"positiv": 1, "negativ": 1}
    assert stats["stance"] == {"zustimmung": 1, "widerspruch": 1}
    assert stats["unclassified_pending"] == 1


def test_sentiment_stats_empty_by_default():
    stats = memory.sentiment_stats("unobody_sent")
    assert stats["sentiment"] == {}
    assert stats["stance"] == {}
    assert stats["unclassified_pending"] == 0


# --- Internet-Anfragen / Datenfreigabe-Statistik ------------------------

def test_external_request_stats_counts_total_and_per_plugin():
    memory.log_external_request("uext", "weather", "p1", {"q": 1})
    memory.log_external_request("uext", "weather", "p2", {"q": 2})
    memory.log_external_request("uext", "news", "p3", {"q": 3})
    stats = memory.external_request_stats("uext")
    assert stats["total"] == 3
    assert stats["by_plugin"] == {"weather": 2, "news": 1}


def test_external_request_stats_empty_by_default():
    stats = memory.external_request_stats("unobody_ext")
    assert stats["total"] == 0
    assert stats["by_plugin"] == {}


def test_story_consent_stats_counts_by_status():
    memory.add_story_fragment("ucons", "s1", "...")
    memory.add_story_fragment("ucons", "s2", "...")
    memory.set_story_consent("ucons", "s2", "kids")
    stats = memory.story_consent_stats("ucons")
    assert stats == {"undecided": 1, "kids": 1}


def test_story_consent_stats_empty_by_default():
    assert memory.story_consent_stats("unobody_cons") == {}


# --- Zufriedenheits-Feedback (Technikerin-Checkin) ----------------------

def test_record_feedback_asked_then_reply_within_window():
    memory.record_feedback_asked("ufeed", "technikerin")
    ok = memory.record_feedback_reply("ufeed", "technikerin", "Mir gefaellt alles gut")
    assert ok is True
    rows = memory.list_feedback("ufeed")
    assert len(rows) == 1
    assert rows[0]["reply"] == "Mir gefaellt alles gut"
    assert rows[0]["reply_ts"] is not None


def test_record_feedback_reply_is_noop_when_nothing_pending():
    ok = memory.record_feedback_reply("ufeed2", "technikerin", "irgendwas")
    assert ok is False
    assert memory.list_feedback("ufeed2") == []


def test_record_feedback_reply_expires_after_window():
    with memory.get_db("ufeed3") as db:
        db.execute(
            "INSERT INTO feedback (persona, asked_ts) VALUES (?,?)",
            ("technikerin", 1000.0),
        )
    ok = memory.record_feedback_reply(
        "ufeed3", "technikerin", "spaete Antwort", max_age_seconds=600
    )
    assert ok is False


def test_last_feedback_asked_ts_returns_most_recent():
    assert memory.last_feedback_asked_ts("ufeed4", "technikerin") is None
    memory.record_feedback_asked("ufeed4", "technikerin")
    assert memory.last_feedback_asked_ts("ufeed4", "technikerin") is not None


def test_list_feedback_orders_newest_first():
    with memory.get_db("ufeed5") as db:
        db.executemany(
            "INSERT INTO feedback (persona, asked_ts, reply, reply_ts) VALUES (?,?,?,?)",
            [
                ("technikerin", 1000.0, "alt", 1001.0),
                ("technikerin", 2000.0, "neu", 2001.0),
            ],
        )
    rows = memory.list_feedback("ufeed5")
    assert [r["reply"] for r in rows] == ["neu", "alt"]
