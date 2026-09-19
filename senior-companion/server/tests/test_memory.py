import sqlite3
import time

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


# --- Vertrauliche Themen: Namensvergabe (aktives Thema pro Persona) -----

def test_open_pending_topic_creates_unlabeled_row():
    topic_id = memory.open_pending_topic("topicname_user", "freundin")
    assert topic_id is not None
    with memory.get_db("topicname_user") as db:
        row = db.execute(
            "SELECT label, closed_ts FROM active_topics WHERE id=?", (topic_id,)
        ).fetchone()
    assert row["label"] is None
    assert row["closed_ts"] is None


def test_open_pending_topic_is_noop_when_one_already_pending():
    first = memory.open_pending_topic("topicname_user2", "freundin")
    second = memory.open_pending_topic("topicname_user2", "freundin")
    assert first == second
    with memory.get_db("topicname_user2") as db:
        count = db.execute("SELECT COUNT(*) FROM active_topics").fetchone()[0]
    assert count == 1


def test_capture_topic_label_fills_pending_row_within_window():
    memory.open_pending_topic("topicname_user3", "freundin")
    ok = memory.capture_topic_label("topicname_user3", "freundin", "Heinrich")
    assert ok is True
    assert memory.active_topic("topicname_user3", "freundin") == "Heinrich"


def test_capture_topic_label_noop_when_nothing_pending():
    ok = memory.capture_topic_label("topicname_user4", "freundin", "Heinrich")
    assert ok is False


def test_capture_topic_label_expires_after_window():
    with memory.get_db("topicname_user5") as db:
        db.execute(
            "INSERT INTO active_topics (persona, label, opened_ts) VALUES (?,?,?)",
            ("freundin", None, 1000.0),
        )
    ok = memory.capture_topic_label(
        "topicname_user5", "freundin", "Heinrich", max_age_seconds=600
    )
    assert ok is False


# --- Vertrauliche Themen: aktives Thema + Idle-Sicherheitsnetz ---------

def test_active_topic_returns_none_when_no_topic_opened():
    assert memory.active_topic("activetopic_user1", "freundin") is None


def test_active_topic_returns_label_for_recently_active_topic():
    memory.open_pending_topic("activetopic_user2", "freundin")
    memory.capture_topic_label("activetopic_user2", "freundin", "Heinrich")
    assert memory.active_topic("activetopic_user2", "freundin") == "Heinrich"


def test_active_topic_returns_none_for_stale_idle_topic():
    with memory.get_db("activetopic_user3") as db:
        db.execute(
            "INSERT INTO active_topics (persona, label, opened_ts) VALUES (?,?,?)",
            ("freundin", "Heinrich", time.time() - 90000),  # > 24h her, nichts getaggt
        )
    assert memory.active_topic("activetopic_user3", "freundin") is None


def test_active_topic_ignores_closed_topics():
    with memory.get_db("activetopic_user4") as db:
        db.execute(
            "INSERT INTO active_topics (persona, label, opened_ts, closed_ts) "
            "VALUES (?,?,?,?)",
            ("freundin", "Heinrich", time.time(), time.time()),
        )
    assert memory.active_topic("activetopic_user4", "freundin") is None


def test_active_topic_is_per_persona():
    memory.open_pending_topic("activetopic_user5", "freundin")
    memory.capture_topic_label("activetopic_user5", "freundin", "Heinrich")
    assert memory.active_topic("activetopic_user5", "reporter") is None


def test_close_topic_marks_closed_ts():
    memory.open_pending_topic("closetopic_user", "freundin")
    memory.capture_topic_label("closetopic_user", "freundin", "Heinrich")
    memory.close_topic("closetopic_user", "freundin", "Heinrich")
    assert memory.active_topic("closetopic_user", "freundin") is None


def test_list_topics_returns_only_open_labeled_topics():
    memory.open_pending_topic("listtopic_user", "freundin")
    memory.capture_topic_label("listtopic_user", "freundin", "Heinrich")
    with memory.get_db("listtopic_user") as db:
        db.execute(
            "INSERT INTO active_topics (persona, label, opened_ts, closed_ts) "
            "VALUES (?,?,?,?)",
            ("freundin", "Altes Thema", time.time(), time.time()),
        )
        db.execute(
            "INSERT INTO active_topics (persona, label, opened_ts) VALUES (?,?,?)",
            ("freundin", None, time.time()),
        )
    topics = memory.list_topics("listtopic_user", "freundin")
    assert topics == ["Heinrich"]


# --- Nachrichten-Taggierung ----------------------------------------------

def test_add_message_stores_topic_when_given():
    memory.add_message("topic_user", "freundin", "user", "geheim", topic="heinrich")
    with memory.get_db("topic_user") as db:
        row = db.execute(
            "SELECT topic FROM messages WHERE content='geheim'"
        ).fetchone()
    assert row["topic"] == "heinrich"


def test_add_message_topic_defaults_to_none():
    memory.add_message("topic_user2", "freundin", "user", "normal")
    with memory.get_db("topic_user2") as db:
        row = db.execute(
            "SELECT topic FROM messages WHERE content='normal'"
        ).fetchone()
    assert row["topic"] is None


# --- Löschanweisungen: sofort (mit Bestätigung) und im Todesfall -------

def test_record_deletion_directive_creates_row():
    directive_id = memory.record_deletion_directive(
        "directive_user", "freundin", "heinrich", "bitte loeschen",
        mode="pending_confirmation",
    )
    assert directive_id is not None
    with memory.get_db("directive_user") as db:
        row = db.execute(
            "SELECT * FROM deletion_directives WHERE id=?", (directive_id,)
        ).fetchone()
    assert row["topic_label"] == "heinrich"
    assert row["mode"] == "pending_confirmation"
    assert row["executed_ts"] is None


def test_confirm_pending_deletion_deletes_messages_and_marks_executed():
    memory.open_pending_topic("del_user", "freundin")
    memory.capture_topic_label("del_user", "freundin", "heinrich")
    memory.add_message(
        "del_user", "freundin", "user", "Mein Geheimnis ueber Heinrich.",
        topic="heinrich",
    )
    memory.add_message(
        "del_user", "freundin", "assistant", "Das bleibt unter uns.",
        topic="heinrich",
    )
    memory.add_message("del_user", "freundin", "user", "Ganz normales Gespraech.")
    memory.record_deletion_directive(
        "del_user", "freundin", "heinrich", "Bitte loesch das",
        mode="pending_confirmation",
    )

    result = memory.confirm_pending_deletion("del_user", "freundin")
    assert result == "heinrich"

    remaining = memory.recent_messages("del_user", "freundin", limit=20)
    assert len(remaining) == 1
    assert remaining[0]["content"] == "Ganz normales Gespraech."

    # Nicht nur die Nachrichten sind weg - auch der Themen-NAME selbst
    # darf danach in keiner Tabelle mehr im Klartext stehen (sonst
    # waere die Loeschung nicht wirklich "sicher").
    with memory.get_db("del_user") as db:
        row = db.execute(
            "SELECT executed_ts, topic_label, trigger_phrase FROM deletion_directives"
        ).fetchone()
        topics = db.execute("SELECT COUNT(*) FROM active_topics").fetchone()[0]
    assert row["executed_ts"] is not None
    assert row["topic_label"] == memory.SCRUBBED_LABEL
    assert row["trigger_phrase"] == memory.SCRUBBED_LABEL
    assert topics == 0


def test_confirm_pending_deletion_only_touches_matching_persona_and_topic():
    memory.add_message(
        "del_user2", "freundin", "user", "geheim", topic="heinrich"
    )
    memory.add_message(
        "del_user2", "professor", "user", "andere Persona, gleicher Name",
        topic="heinrich",
    )
    memory.record_deletion_directive(
        "del_user2", "freundin", "heinrich", "loesch das", mode="pending_confirmation"
    )
    memory.confirm_pending_deletion("del_user2", "freundin")
    assert memory.recent_messages("del_user2", "freundin", limit=20) == []
    assert len(memory.recent_messages("del_user2", "professor", limit=20)) == 1


def test_confirm_pending_deletion_leaves_untagged_mentions_of_the_same_name_alone():
    """Kernversprechen des Feature: das Loeschen eines getaggten Themas
    (z.B. 'Heinrich') basiert NICHT auf einer Stichwortsuche nach dem
    Namen ueber den gesamten Verlauf - nur was WAEHREND des offenen
    Themas getaggt wurde, ist betroffen. Eine unabhaengig erzaehlte
    'gute' Geschichte ueber dieselbe Person (kein aktives Thema zu dem
    Zeitpunkt, also topic=None) bleibt erhalten."""
    memory.add_message(
        "goodstory_user", "freundin", "user",
        "Heinrich hat mir immer lustige Geschichten von frueher erzaehlt.",
    )  # kein Thema aktiv -> topic=None, ganz normale Erinnerung

    memory.open_pending_topic("goodstory_user", "freundin")
    memory.capture_topic_label("goodstory_user", "freundin", "heinrich")
    memory.add_message(
        "goodstory_user", "freundin", "user", "Das darf niemand wissen.",
        topic="heinrich",
    )
    memory.record_deletion_directive(
        "goodstory_user", "freundin", "heinrich", "loesch das",
        mode="pending_confirmation",
    )
    memory.confirm_pending_deletion("goodstory_user", "freundin")

    remaining = memory.recent_messages("goodstory_user", "freundin", limit=20)
    assert len(remaining) == 1
    assert "lustige Geschichten" in remaining[0]["content"]


def test_confirm_pending_deletion_noop_when_nothing_pending():
    assert memory.confirm_pending_deletion("del_user3", "freundin") is None


def test_confirm_pending_deletion_expires_after_window():
    memory.add_message("del_user4", "freundin", "user", "geheim", topic="heinrich")
    with memory.get_db("del_user4") as db:
        db.execute(
            "INSERT INTO deletion_directives "
            "(persona, topic_label, trigger_phrase, mode, created_ts) "
            "VALUES (?,?,?,?,?)",
            ("freundin", "heinrich", "loesch das", "pending_confirmation", 1000.0),
        )
    result = memory.confirm_pending_deletion(
        "del_user4", "freundin", max_age_seconds=600
    )
    assert result is None
    assert len(memory.recent_messages("del_user4", "freundin", limit=20)) == 1


def test_confirm_pending_deletion_closes_the_topic():
    memory.open_pending_topic("del_user5", "freundin")
    memory.capture_topic_label("del_user5", "freundin", "heinrich")
    memory.record_deletion_directive(
        "del_user5", "freundin", "heinrich", "loesch das", mode="pending_confirmation"
    )
    memory.confirm_pending_deletion("del_user5", "freundin")
    assert memory.active_topic("del_user5", "freundin") is None


# --- Echtes, sicheres Loeschen: VACUUM entfernt Inhalt wirklich von der --
# --- Platte, nicht nur logisch aus der Abfrage ---------------------------

def test_vacuum_shrinks_file_and_removes_deleted_content_from_disk():
    secret_text = "Ein ausfuehrliches Geheimnis ueber Heinrich. " * 200
    memory.add_message("vac_user", "freundin", "user", secret_text, topic="heinrich")
    path = memory.db_path_for("vac_user")
    size_before = path.stat().st_size
    assert b"Heinrich" in path.read_bytes()

    with memory.get_db("vac_user") as db:
        db.execute("DELETE FROM messages WHERE persona=? AND topic=?", ("freundin", "heinrich"))
    memory.vacuum("vac_user")

    raw_after = path.read_bytes()
    assert b"Heinrich" not in raw_after
    assert path.stat().st_size < size_before


# --- Todesfall: gespeicherte Löschanweisungen ausführen -----------------

def test_pending_directives_returns_only_open_on_death_rows():
    memory.add_message("death_user", "freundin", "user", "geheimnis", topic="heinrich")
    memory.record_deletion_directive(
        "death_user", "freundin", "heinrich", "im Todesfall loeschen", mode="on_death"
    )
    memory.record_deletion_directive(
        "death_user", "freundin", "anderes", "sofort loeschen",
        mode="pending_confirmation",
    )
    pending = memory.pending_directives("death_user")
    assert len(pending) == 1
    assert pending[0]["topic_label"] == "heinrich"


def test_execute_death_directives_deletes_and_marks_executed():
    memory.open_pending_topic("death_user2", "freundin")
    memory.capture_topic_label("death_user2", "freundin", "heinrich")
    memory.add_message(
        "death_user2", "freundin", "user", "vertraulich ueber Heinrich",
        topic="heinrich",
    )
    memory.add_message("death_user2", "freundin", "user", "ganz normal")
    memory.record_deletion_directive(
        "death_user2", "freundin", "heinrich", "im Todesfall loeschen", mode="on_death"
    )

    count = memory.execute_death_directives("death_user2")
    assert count == 1

    remaining = memory.recent_messages("death_user2", "freundin", limit=20)
    assert len(remaining) == 1
    assert remaining[0]["content"] == "ganz normal"

    # Auch hier: der Themen-Name selbst darf nicht im Klartext
    # stehenbleiben, nur die Tatsache, DASS etwas ausgefuehrt wurde.
    with memory.get_db("death_user2") as db:
        row = db.execute(
            "SELECT executed_ts, topic_label FROM deletion_directives"
        ).fetchone()
        topics = db.execute("SELECT COUNT(*) FROM active_topics").fetchone()[0]
    assert row["executed_ts"] is not None
    assert row["topic_label"] == memory.SCRUBBED_LABEL
    assert topics == 0


def test_execute_death_directives_is_idempotent():
    memory.record_deletion_directive(
        "death_user3", "freundin", "heinrich", "im Todesfall loeschen", mode="on_death"
    )
    first = memory.execute_death_directives("death_user3")
    second = memory.execute_death_directives("death_user3")
    assert first == 1
    assert second == 0


def test_execute_death_directives_only_touches_on_death_mode():
    memory.add_message(
        "death_user4", "freundin", "user", "sofort-thema", topic="sofort"
    )
    memory.record_deletion_directive(
        "death_user4", "freundin", "sofort", "jetzt loeschen",
        mode="pending_confirmation",
    )
    count = memory.execute_death_directives("death_user4")
    assert count == 0
    assert len(memory.recent_messages("death_user4", "freundin", limit=20)) == 1


def test_execute_death_directives_returns_zero_when_nothing_pending():
    assert memory.execute_death_directives("death_user6") == 0


def test_execute_death_directives_vacuums_exactly_once(monkeypatch):
    calls = []
    monkeypatch.setattr(memory, "vacuum", lambda user_id: calls.append(user_id))
    memory.record_deletion_directive(
        "death_user5", "freundin", "a", "x", mode="on_death"
    )
    memory.record_deletion_directive(
        "death_user5", "freundin", "b", "y", mode="on_death"
    )
    count = memory.execute_death_directives("death_user5")
    assert count == 2
    assert calls == ["death_user5"]


# --- Gruppenchat: geteiltes Gedaechtnis ueber links_to_id ----------------

def test_migrate_adds_links_to_id_column_to_existing_db():
    db_path = memory.db_path_for("premigration_links_user")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "persona TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, "
        "ts REAL NOT NULL)"
    )
    conn.commit()
    conn.close()

    with memory.get_db("premigration_links_user") as db:
        cols = [r[1] for r in db.execute("PRAGMA table_info(messages)").fetchall()]
    assert "links_to_id" in cols


def test_add_linked_message_creates_a_row_pointing_at_the_master():
    master_id = memory.add_message("link_user1", "freundin", "assistant", "Hallo!")
    linked_id = memory.add_linked_message("link_user1", "professor", "assistant", master_id)
    with memory.get_db("link_user1") as db:
        row = db.execute(
            "SELECT links_to_id, persona FROM messages WHERE id=?", (linked_id,)
        ).fetchone()
    assert row["links_to_id"] == master_id
    assert row["persona"] == "professor"


def test_recent_messages_resolves_linked_row_content_via_master():
    master_id = memory.add_message("link_user2", "freundin", "assistant", "Es regnet heute.")
    memory.add_linked_message("link_user2", "professor", "assistant", master_id)

    professor_view = memory.recent_messages("link_user2", "professor", limit=20)
    assert len(professor_view) == 1
    assert professor_view[0]["content"] == "Es regnet heute."
    assert professor_view[0]["role"] == "assistant"


def test_recent_messages_skips_row_with_dangling_link():
    with memory.get_db("link_user3") as db:
        db.execute(
            "INSERT INTO messages (persona, role, content, ts, links_to_id) "
            "VALUES (?,?,?,?,?)",
            ("professor", "assistant", "", 1000.0, 999999),  # zeigt ins Leere
        )
    assert memory.recent_messages("link_user3", "professor", limit=20) == []


def test_recent_messages_unaffected_for_rows_without_a_link():
    memory.add_message("link_user4", "freundin", "user", "Ganz normal.")
    view = memory.recent_messages("link_user4", "freundin", limit=20)
    assert len(view) == 1
    assert view[0]["content"] == "Ganz normal."


# --- Loesch-/Befoerderungs-Semantik fuer verlinkte Nachrichten -----------

def _add_master_and_two_links(user_id):
    master_id = memory.add_message(user_id, "freundin", "assistant", "Das Original.")
    link_a = memory.add_linked_message(user_id, "professor", "assistant", master_id)
    link_b = memory.add_linked_message(user_id, "technikerin", "assistant", master_id)
    return master_id, link_a, link_b


def test_delete_own_view_removes_only_that_persona_link_master_untouched():
    master_id, link_a, link_b = _add_master_and_two_links("del_link_user1")
    memory.delete_own_view("del_link_user1", link_a)

    assert memory.recent_messages("del_link_user1", "professor", limit=20) == []
    # Master und der andere Link bleiben unberuehrt.
    assert len(memory.recent_messages("del_link_user1", "freundin", limit=20)) == 1
    assert len(memory.recent_messages("del_link_user1", "technikerin", limit=20)) == 1


def test_delete_master_with_handoff_promotes_oldest_remaining_link():
    master_id, link_a, link_b = _add_master_and_two_links("del_link_user2")
    memory.delete_master_with_handoff("del_link_user2", master_id)

    # Freundin (der urspruengliche Master) hat die Nachricht nicht mehr.
    assert memory.recent_messages("del_link_user2", "freundin", limit=20) == []
    # professor (die aeltere verbleibende Verlinkung) ist jetzt der neue Master.
    professor_view = memory.recent_messages("del_link_user2", "professor", limit=20)
    assert len(professor_view) == 1
    assert professor_view[0]["content"] == "Das Original."
    with memory.get_db("del_link_user2") as db:
        row = db.execute("SELECT links_to_id FROM messages WHERE id=?", (link_a,)).fetchone()
    assert row["links_to_id"] is None


def test_delete_master_with_handoff_repoints_all_other_links_to_new_master():
    master_id, link_a, link_b = _add_master_and_two_links("del_link_user3")
    memory.delete_master_with_handoff("del_link_user3", master_id)

    with memory.get_db("del_link_user3") as db:
        row = db.execute("SELECT links_to_id FROM messages WHERE id=?", (link_b,)).fetchone()
    assert row["links_to_id"] == link_a  # zeigt jetzt auf den neuen Master
    # technikerin sieht den Inhalt weiterhin korrekt aufgeloest.
    technikerin_view = memory.recent_messages("del_link_user3", "technikerin", limit=20)
    assert technikerin_view[0]["content"] == "Das Original."


def test_delete_master_with_handoff_no_remaining_links_just_deletes():
    master_id = memory.add_message("del_link_user4", "freundin", "assistant", "Einzelne Nachricht.")
    memory.delete_master_with_handoff("del_link_user4", master_id)
    assert memory.recent_messages("del_link_user4", "freundin", limit=20) == []


def test_delete_utterance_entirely_removes_master_and_all_links():
    master_id, link_a, link_b = _add_master_and_two_links("del_link_user5")
    memory.delete_utterance_entirely("del_link_user5", master_id)

    assert memory.recent_messages("del_link_user5", "freundin", limit=20) == []
    assert memory.recent_messages("del_link_user5", "professor", limit=20) == []
    assert memory.recent_messages("del_link_user5", "technikerin", limit=20) == []


def test_confirm_pending_deletion_never_touches_linked_rows_on_other_topics():
    """Regressionswaechter: die bestehenden secrecy.py-Loeschpfade
    duerfen verlinkte Gruppenchat-Nachrichten auf einem ANDEREN Thema
    nicht mitreissen."""
    memory.open_pending_topic("del_link_user6", "freundin")
    memory.capture_topic_label("del_link_user6", "freundin", "heinrich")
    memory.add_message(
        "del_link_user6", "freundin", "user", "geheim ueber heinrich",
        topic="heinrich",
    )
    memory.record_deletion_directive(
        "del_link_user6", "freundin", "heinrich", "loesch das",
        mode="pending_confirmation",
    )

    # Unabhaengige, geteilte Gruppenchat-Nachricht auf einem anderen Thema.
    other_master_id = memory.add_message("del_link_user6", "freundin", "assistant", "Normales Gespraech.")
    memory.add_linked_message("del_link_user6", "professor", "assistant", other_master_id)

    memory.confirm_pending_deletion("del_link_user6", "freundin")

    professor_view = memory.recent_messages("del_link_user6", "professor", limit=20)
    assert len(professor_view) == 1
    assert professor_view[0]["content"] == "Normales Gespraech."


def test_has_pending_secrecy_interaction_true_while_topic_name_unanswered():
    memory.open_pending_topic("pending_secrecy_user1", "freundin")
    assert memory.has_pending_secrecy_interaction("pending_secrecy_user1", "freundin") is True


def test_has_pending_secrecy_interaction_false_once_topic_named():
    memory.open_pending_topic("pending_secrecy_user2", "freundin")
    memory.capture_topic_label("pending_secrecy_user2", "freundin", "heinrich")
    assert memory.has_pending_secrecy_interaction("pending_secrecy_user2", "freundin") is False


def test_has_pending_secrecy_interaction_true_while_deletion_unconfirmed():
    memory.record_deletion_directive(
        "pending_secrecy_user3", "freundin", "heinrich", "loesch das",
        mode="pending_confirmation",
    )
    assert memory.has_pending_secrecy_interaction("pending_secrecy_user3", "freundin") is True


def test_has_pending_secrecy_interaction_false_for_other_persona():
    memory.open_pending_topic("pending_secrecy_user4", "freundin")
    assert memory.has_pending_secrecy_interaction("pending_secrecy_user4", "professor") is False


def test_has_pending_secrecy_interaction_false_when_nothing_open():
    assert memory.has_pending_secrecy_interaction("pending_secrecy_user5", "freundin") is False
