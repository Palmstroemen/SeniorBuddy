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
