"""
Tests fuer server/lookahead.py: die vorausschauende Ketten-Generierung
fuer Auto-Turns (siehe docs/ARCHITECTURE.md, Abschnitt "Vision:
Kontinuierliches, vorausschauendes Sprechen"). Diese erste Runde baut
nur eine lineare Kette (Tiefe 1-5, keine Verzweigung).

llm_client.stream() und speech_client.synthesize() werden durchgaengig
gemockt (kein echtes Ollama/Sprachdienst noetig, gleiches Prinzip wie
test_reaction_audio.py). Wo immer moeglich werden Ketten per Hand aus
ChainLevel-Objekten gebaut statt echte Hintergrund-Tasks laufen zu
lassen - vermeidet Timing-Abhaengigkeit (siehe test_room_chat.py's
eigene, im Verlauf dieser Session gesammelte Erfahrung mit Race-
bedingten Haengern rund um Hintergrund-Tasks).
"""
import asyncio

import pytest

import config
import lookahead
import memory


@pytest.fixture(autouse=True)
def _reset_lookahead_state():
    """Modul-globaler Zustand - zwischen Tests zuruecksetzen, sonst
    beeinflussen sich Tests gegenseitig (gleiches Muster wie
    test_priority.py's _reset_priority_state)."""
    lookahead._chains.clear()
    lookahead._audio_cache.clear()
    lookahead._levels_built = {d: 0 for d in range(1, 6)}
    lookahead._levels_delivered = {d: 0 for d in range(1, 6)}
    lookahead._discarded_interrupted = 0
    lookahead._discarded_suppressed = 0
    lookahead._discarded_stale = 0
    lookahead._forks_offered = 0
    lookahead._forks_confirmed = 0
    lookahead._forks_catchall_taken = 0
    lookahead._nodes_pruned_on_confirm = 0
    yield
    lookahead._chains.clear()
    lookahead._audio_cache.clear()
    lookahead._levels_built = {d: 0 for d in range(1, 6)}
    lookahead._levels_delivered = {d: 0 for d in range(1, 6)}
    lookahead._discarded_interrupted = 0
    lookahead._discarded_suppressed = 0
    lookahead._discarded_stale = 0
    lookahead._forks_offered = 0
    lookahead._forks_confirmed = 0
    lookahead._forks_catchall_taken = 0
    lookahead._nodes_pruned_on_confirm = 0


class _StubPersona:
    def __init__(self, id="robin", model="tinyllama", system_prompt="Du bist Robin.",
                 max_tokens=400, voice_id="de_DE-thorsten-low"):
        self.id = id
        self.model = model
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.voice_id = voice_id


def _canned_stream_factory(replies):
    """Liefert eine fake_stream-Funktion, die bei jedem Aufruf den
    naechsten Text aus `replies` liefert (ein Token = der ganze Text,
    ausreichend fuer diese Tests) - gleiches Muster wie in
    test_room_chat.py's replies/counter-Tests."""
    counter = {"n": 0}

    async def fake_stream(model, system_prompt, messages, max_tokens=400):
        idx = counter["n"]
        counter["n"] += 1
        yield replies[idx % len(replies)]

    return fake_stream, counter


async def _await_chain_built(user_id, target_len=5, timeout=2.0):
    """Beschraenktes Polling statt blockierendem await auf chain.build_task
    - siehe Begruendung in server/tests/test_room_chat.py."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        chain = lookahead._chains.get(user_id)
        if chain is not None and len(chain.levels) >= target_len:
            return chain
        await asyncio.sleep(0.01)
    raise AssertionError("Kette wurde nicht rechtzeitig fertig gebaut")


async def test_extend_chain_builds_up_to_five_levels_from_empty(monkeypatch):
    replies = [f"Kandidat {n}" for n in range(1, 6)]
    fake_stream, _ = _canned_stream_factory(replies)
    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_user_a", persona)
    chain = await _await_chain_built("lookahead_user_a", target_len=5)

    assert [lvl.depth for lvl in chain.levels] == [1, 2, 3, 4, 5]
    assert [lvl.text for lvl in chain.levels] == replies
    assert lookahead._levels_built == {1: 1, 2: 1, 3: 1, 4: 1, 5: 1}


async def test_extend_chain_level_two_onward_includes_prior_levels_as_hypothetical_history(monkeypatch):
    replies = ["Erste Stufe", "Zweite Stufe"]
    captured_messages = []

    async def fake_stream(model, system_prompt, messages, max_tokens=400):
        captured_messages.append(messages)
        idx = len(captured_messages) - 1
        yield replies[idx % len(replies)]

    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_user_b", persona)
    await _await_chain_built("lookahead_user_b", target_len=2)

    assert len(captured_messages) >= 2
    second_call_messages = captured_messages[1]
    assert {"role": "assistant", "content": "Erste Stufe"} in second_call_messages
    real_history = memory.recent_messages("lookahead_user_b", "robin", limit=20)
    assert not any(m["content"] == "Erste Stufe" for m in real_history)


def test_consume_head_returns_none_when_no_chain_exists():
    assert lookahead.consume_head("lookahead_user_c", "robin") is None


def test_consume_head_returns_none_when_chain_belongs_to_different_persona():
    chain = lookahead.Chain(user_id="lookahead_user_d", persona_id="freundin")
    chain.levels.append(lookahead.ChainLevel(depth=1, kind="continue", text="Hallo"))
    lookahead._chains["lookahead_user_d"] = chain

    assert lookahead.consume_head("lookahead_user_d", "professor") is None


def test_consume_head_pops_head_and_promotes_next_level(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    chain = lookahead.Chain(user_id="lookahead_user_e", persona_id="robin")
    chain.levels.append(lookahead.ChainLevel(
        depth=1, kind="continue", text="Erste Stufe, ganz einzigartig",
        path="1", parent_path=None,
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="Zweite Stufe, ganz einzigartig",
        path="2", parent_path="1",
    ))
    lookahead._chains["lookahead_user_e"] = chain

    result = lookahead.consume_head("lookahead_user_e", "robin")

    assert result.text == "Erste Stufe, ganz einzigartig"
    assert result.depth == 1
    assert chain.levels[0].text == "Zweite Stufe, ganz einzigartig"
    assert lookahead._levels_delivered[1] == 1


def test_consume_head_skips_suppressed_level_and_tries_next(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_user_f"
    memory.add_message(user_id, "robin", "assistant", "Ach, der Dobelhofpark ist wirklich schoen, oder?")

    chain = lookahead.Chain(user_id=user_id, persona_id="robin")
    chain.levels.append(lookahead.ChainLevel(
        depth=1, kind="continue",
        text="Ach, der Dobelhofpark ist wirklich schoen, oder?",
        path="1", parent_path=None,
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic",
        text="Wie war eigentlich Ihr letzter Urlaub, erzaehlen Sie mal!",
        path="2", parent_path="1",
    ))
    lookahead._chains[user_id] = chain

    result = lookahead.consume_head(user_id, "robin")

    assert result.text == "Wie war eigentlich Ihr letzter Urlaub, erzaehlen Sie mal!"
    assert lookahead._discarded_suppressed == 1


def test_consume_head_returns_none_when_all_levels_suppressed(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_user_g"
    memory.add_message(user_id, "robin", "assistant", "Immer dasselbe, immer dasselbe.")

    chain = lookahead.Chain(user_id=user_id, persona_id="robin")
    chain.levels.append(lookahead.ChainLevel(depth=1, kind="continue", text="Immer dasselbe, immer dasselbe."))
    lookahead._chains[user_id] = chain

    result = lookahead.consume_head(user_id, "robin")

    assert result is None
    assert lookahead._discarded_suppressed == 1


def test_discard_chain_removes_it_and_increments_interrupted_counter():
    chain = lookahead.Chain(user_id="lookahead_user_h", persona_id="robin")
    lookahead._chains["lookahead_user_h"] = chain

    lookahead.discard_chain("lookahead_user_h", reason="interrupted")

    assert "lookahead_user_h" not in lookahead._chains
    assert lookahead._discarded_interrupted == 1


def test_discard_chain_is_a_safe_noop_when_no_chain_exists():
    lookahead.discard_chain("lookahead_user_never_had_a_chain", reason="interrupted")
    assert lookahead._discarded_interrupted == 0


async def test_start_chain_for_speaker_registers_low_priority_task_and_senior_stream_cancels_it(monkeypatch):
    async def slow_stream(model, system_prompt, messages, max_tokens=400):
        await asyncio.sleep(5)
        yield "sollte nie ankommen"

    monkeypatch.setattr(lookahead.llm_client, "stream", slow_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_user_i", persona)
    chain = lookahead._chains["lookahead_user_i"]
    await asyncio.sleep(0.05)

    import priority
    priority.senior_stream_started()

    with pytest.raises(asyncio.CancelledError):
        await chain.build_task
    assert chain.build_task.cancelled()
    priority.senior_stream_finished()


def test_stats_delivery_rate_by_depth_computed_correctly():
    lookahead._levels_built[2] = 4
    lookahead._levels_delivered[2] = 1

    result = lookahead.stats()

    assert result["delivery_rate_by_depth"]["2"] == 0.25


def test_stats_delivery_rate_is_zero_not_error_when_nothing_built_at_that_depth():
    result = lookahead.stats()
    assert result["delivery_rate_by_depth"]["5"] == 0.0


def test_debug_state_returns_none_when_no_chain_exists():
    assert lookahead.debug_state("lookahead_debug_user_a") is None


def test_debug_state_reports_levels_built_and_target_depth():
    chain = lookahead.Chain(user_id="lookahead_debug_user_b", persona_id="freundin")
    chain.levels.append(lookahead.ChainLevel(depth=1, kind="continue", text="Eins"))
    chain.levels.append(lookahead.ChainLevel(depth=2, kind="continue_new_topic", text="Zwei"))
    lookahead._chains["lookahead_debug_user_b"] = chain

    state = lookahead.debug_state("lookahead_debug_user_b")

    assert state["persona_id"] == "freundin"
    assert state["levels_built"] == 2
    assert state["target_depth"] == lookahead.TARGET_DEPTH


def test_debug_state_reports_whether_head_has_audio():
    chain = lookahead.Chain(user_id="lookahead_debug_user_c", persona_id="freundin")
    chain.levels.append(lookahead.ChainLevel(depth=1, kind="continue", text="Eins", audio_ready=True))
    lookahead._chains["lookahead_debug_user_c"] = chain

    assert lookahead.debug_state("lookahead_debug_user_c")["head_has_audio"] is True


def test_debug_state_head_has_audio_false_without_rendered_audio():
    chain = lookahead.Chain(user_id="lookahead_debug_user_d", persona_id="freundin")
    chain.levels.append(lookahead.ChainLevel(depth=1, kind="continue", text="Eins"))
    lookahead._chains["lookahead_debug_user_d"] = chain

    assert lookahead.debug_state("lookahead_debug_user_d")["head_has_audio"] is False


def test_debug_state_head_has_audio_false_when_chain_has_no_levels_yet():
    chain = lookahead.Chain(user_id="lookahead_debug_user_e", persona_id="freundin")
    lookahead._chains["lookahead_debug_user_e"] = chain

    state = lookahead.debug_state("lookahead_debug_user_e")
    assert state["levels_built"] == 0
    assert state["head_has_audio"] is False


async def test_extend_chain_stops_writing_after_generation_bumped(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def controlled_stream(model, system_prompt, messages, max_tokens=400):
        started.set()
        await release.wait()
        yield "zu spaet"

    monkeypatch.setattr(lookahead.llm_client, "stream", controlled_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    user_id = "lookahead_user_j"
    chain = lookahead.Chain(user_id=user_id, persona_id="robin")
    lookahead._chains[user_id] = chain
    task = asyncio.create_task(lookahead._extend_chain(user_id, chain.generation))

    await asyncio.wait_for(started.wait(), timeout=1)
    chain.generation += 1  # simuliert einen Discard/Consume waehrend des Baus
    release.set()
    await task

    assert chain.levels == []


# --- Runde 2: Verzweigungsbaum ---------------------------------------------
#
# _FORK_TAG_RAW ist eine kanonische, von BRANCH_TAG_INSTRUCTION
# angeforderte Modell-Antwort (siehe lookahead._parse_branch_tag) -
# wiederverwendet ueber mehrere Tests, damit der Tag-Wortlaut an einer
# Stelle lebt.
_FORK_TAG_RAW = (
    "Moegen Sie lieber Politik oder Musik?\n"
    "---\n"
    "VERZWEIGUNG: ja\n"
    "OPTIONEN: Politik | Musik"
)


def _indexed_stream_factory(replies_by_index, default="Weiter."):
    """Wie _canned_stream_factory, aber mit einer expliziten
    Antwort PRO Aufrufindex (0-basiert) statt einer zyklischen Liste -
    noetig, um gezielt EINEN bestimmten Generierungs-Call (z.B. "der
    zweite Aufruf verzweigt") zu steuern, waehrend alle anderen Aufrufe
    einen harmlosen Default-Text bekommen."""
    counter = {"n": 0}

    async def fake_stream(model, system_prompt, messages, max_tokens=400):
        idx = counter["n"]
        counter["n"] += 1
        yield replies_by_index.get(idx, default)

    return fake_stream, counter


async def _await_predicate(user_id, predicate, timeout=2.0):
    """Wie _await_chain_built, aber mit einer frei waehlbaren
    Bedingung ueber die Chain statt einer festen Mindestlaenge -
    noetig fuer Tests, die auf einen bestimmten Baum-Zustand warten
    (z.B. "ein fork_root ist entstanden"), nicht nur auf eine Tiefe."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        chain = lookahead._chains.get(user_id)
        if chain is not None and predicate(chain):
            return chain
        await asyncio.sleep(0.01)
    raise AssertionError("Bedingung wurde nicht rechtzeitig erfuellt")


async def test_extend_chain_root_node_gets_path_one_and_no_parent(monkeypatch):
    replies = [f"Kandidat {n}" for n in range(1, 6)]
    fake_stream, _ = _canned_stream_factory(replies)
    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_path_a", persona)
    chain = await _await_chain_built("lookahead_path_a", target_len=5)

    root = next(lvl for lvl in chain.levels if lvl.depth == 1)
    assert root.path == "1"
    assert root.parent_path is None


async def test_extend_chain_linear_child_keeps_parent_suffix_and_increments_depth_digit(monkeypatch):
    replies = [f"Kandidat {n}" for n in range(1, 6)]
    fake_stream, _ = _canned_stream_factory(replies)
    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_path_b", persona)
    chain = await _await_chain_built("lookahead_path_b", target_len=5)

    by_depth = {lvl.depth: lvl for lvl in chain.levels}
    assert by_depth[2].path == "2"
    assert by_depth[2].parent_path == "1"
    assert by_depth[3].path == "3"
    assert by_depth[3].parent_path == "2"


def test_extend_chain_ancestor_history_walks_parent_path_not_full_levels_list():
    chain = lookahead.Chain(user_id="lookahead_path_c", persona_id="robin")
    chain.levels.append(lookahead.ChainLevel(depth=1, kind="continue", text="Wurzel", path="1", parent_path=None))
    chain.levels.append(lookahead.ChainLevel(depth=2, kind="continue_new_topic", text="Zweig A", path="2a", parent_path="1"))
    chain.levels.append(lookahead.ChainLevel(depth=2, kind="continue_new_topic", text="Unverwandter Zweig B", path="2b", parent_path="1"))
    chain.levels.append(lookahead.ChainLevel(depth=3, kind="continue_new_topic", text="Fortsetzung von A", path="3a", parent_path="2a"))

    # "1" ist der parent_path eines hypothetischen Kindes von "Wurzel"
    # selbst (z.B. bevor "2a"/"2b" gebaut wurden) - liefert nur die
    # Wurzel.
    assert lookahead._ancestor_texts(chain, "1") == ["Wurzel"]
    # "2a" ist der parent_path von "3a" - die Vorfahren-Liste, mit der
    # "3a" tatsaechlich gebaut wurde, MUSS "Zweig A" enthalten, aber
    # NICHT den unverwandten Geschwister-Zweig "Zweig B".
    deep = lookahead._ancestor_texts(chain, "2a")
    assert deep == ["Wurzel", "Zweig A"]
    assert "Unverwandter Zweig B" not in deep


def test_parse_branch_tag_returns_no_branch_on_missing_tag():
    clean, options = lookahead._parse_branch_tag("Nur ein normaler Satz ohne Tag.")
    assert clean == "Nur ein normaler Satz ohne Tag."
    assert options == []


def test_parse_branch_tag_extracts_clean_text_and_options_when_present():
    clean, options = lookahead._parse_branch_tag(_FORK_TAG_RAW)
    assert clean == "Moegen Sie lieber Politik oder Musik?"
    assert options == ["Politik", "Musik"]


def test_parse_branch_tag_falls_back_to_no_branch_on_malformed_optionen_line():
    raw = (
        "Frage?\n"
        "---\n"
        "VERZWEIGUNG: ja\n"
        "OPTIONEN: nur eine einzige option ohne trenner"
    )
    clean, options = lookahead._parse_branch_tag(raw)
    assert clean == "Frage?"
    assert options == []


def test_parse_branch_tag_treats_single_or_zero_options_as_no_branch():
    raw = (
        "Wie heisst das Enkelkind?\n"
        "---\n"
        "VERZWEIGUNG: ja\n"
        "OPTIONEN: Nur Eine"
    )
    _, options = lookahead._parse_branch_tag(raw)
    assert options == []


async def test_extend_chain_builds_fork_root_and_queues_one_child_call_per_option(monkeypatch):
    replies = {0: "Erster Satz.", 1: _FORK_TAG_RAW}
    fake_stream, _ = _indexed_stream_factory(replies)
    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_fork_a", persona)
    chain = lookahead._chains["lookahead_fork_a"]
    await asyncio.wait_for(chain.build_task, timeout=3.0)

    fork_root = next(lvl for lvl in chain.levels if lvl.branch_kind == "fork_root")
    assert fork_root.text == "Moegen Sie lieber Politik oder Musik?"
    children = [lvl for lvl in chain.levels if lvl.parent_path == fork_root.path]
    assert len(children) == 3  # 2 Optionen + 1 Auffangzweig
    assert len([c for c in children if c.branch_kind == "fork_option"]) == 2
    assert lookahead._forks_offered == 1


async def test_extend_chain_fork_children_get_letter_suffixes_a_b_c_in_option_order(monkeypatch):
    replies = {0: "Erster Satz.", 1: _FORK_TAG_RAW}
    fake_stream, _ = _indexed_stream_factory(replies)
    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_fork_b", persona)
    chain = lookahead._chains["lookahead_fork_b"]
    await asyncio.wait_for(chain.build_task, timeout=3.0)

    fork_root = next(lvl for lvl in chain.levels if lvl.branch_kind == "fork_root")
    children = sorted(
        (lvl for lvl in chain.levels if lvl.parent_path == fork_root.path),
        key=lambda lvl: lvl.path,
    )
    assert [c.path[-1] for c in children] == ["a", "b", "c"]
    assert children[0].trigger_condition == "politik"
    assert children[1].trigger_condition == "musik"
    assert children[2].branch_kind == "fork_catchall"


async def test_extend_chain_fork_always_gets_a_catchall_child_even_when_model_omits_one(monkeypatch):
    replies = {0: "Erster Satz.", 1: _FORK_TAG_RAW}
    fake_stream, _ = _indexed_stream_factory(replies)
    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_fork_c", persona)
    chain = lookahead._chains["lookahead_fork_c"]
    await asyncio.wait_for(chain.build_task, timeout=3.0)

    fork_root = next(lvl for lvl in chain.levels if lvl.branch_kind == "fork_root")
    catchalls = [
        lvl for lvl in chain.levels
        if lvl.parent_path == fork_root.path and lvl.branch_kind == "fork_catchall"
    ]
    assert len(catchalls) == 1


async def test_extend_chain_catchall_child_trigger_condition_is_the_reserved_sentinel(monkeypatch):
    replies = {0: "Erster Satz.", 1: _FORK_TAG_RAW}
    fake_stream, _ = _indexed_stream_factory(replies)
    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_fork_d", persona)
    chain = lookahead._chains["lookahead_fork_d"]
    await asyncio.wait_for(chain.build_task, timeout=3.0)

    fork_root = next(lvl for lvl in chain.levels if lvl.branch_kind == "fork_root")
    catchall = next(
        lvl for lvl in chain.levels
        if lvl.parent_path == fork_root.path and lvl.branch_kind == "fork_catchall"
    )
    assert catchall.trigger_condition == lookahead.CATCHALL_TRIGGER


async def test_extend_chain_only_nearest_fork_gets_built_deeper_forks_stay_linear(monkeypatch):
    replies = {
        0: "Erster Satz.",
        1: _FORK_TAG_RAW,
        2: "Fortsetzung A.",
        3: "Fortsetzung B.",
        4: _FORK_TAG_RAW,  # "4a" versucht selbst zu verzweigen - darf nicht klappen
    }
    fake_stream, _ = _indexed_stream_factory(replies, default="Weiter.")
    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_fork_nested", persona)
    chain = lookahead._chains["lookahead_fork_nested"]
    await asyncio.wait_for(chain.build_task, timeout=3.0)

    node_4a = next(lvl for lvl in chain.levels if lvl.path == "4a")
    assert node_4a.branch_kind == "linear"
    assert len([lvl for lvl in chain.levels if lvl.branch_kind == "fork_root"]) == 1


async def test_extend_chain_respects_sentences_since_last_fork_floor(monkeypatch):
    replies = {0: _FORK_TAG_RAW}
    fake_stream, _ = _indexed_stream_factory(replies, default="Weiter.")
    monkeypatch.setattr(lookahead.llm_client, "stream", fake_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    lookahead.start_chain_for_speaker("lookahead_fork_floor", persona)
    chain = lookahead._chains["lookahead_fork_floor"]
    await asyncio.wait_for(chain.build_task, timeout=3.0)

    root = next(lvl for lvl in chain.levels if lvl.depth == 1)
    assert root.branch_kind == "linear"
    assert root.text == "Moegen Sie lieber Politik oder Musik?"
    assert lookahead._forks_offered == 0


def test_consume_head_mode_a_promotes_single_linear_child_same_as_runde_one(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_mode_a_linear"
    chain = lookahead.Chain(user_id=user_id, persona_id="robin")
    chain.levels.append(lookahead.ChainLevel(
        depth=1, kind="continue", text="Erste Stufe, ganz einzigartig",
        path="1", parent_path=None,
    ))
    lookahead._chains[user_id] = chain

    result = lookahead.consume_head(user_id, "robin")

    assert result.text == "Erste Stufe, ganz einzigartig"
    assert chain.cursor_path == "1"


def test_consume_head_mode_a_delivers_fork_root_question_as_a_normal_turn(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_mode_a_fork"
    chain = lookahead.Chain(user_id=user_id, persona_id="robin")
    chain.levels.append(lookahead.ChainLevel(
        depth=1, kind="continue", text="Moegen Sie lieber Politik oder Musik?",
        path="1", parent_path=None, branch_kind="fork_root",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="Politik-Fortsetzung, ganz einzigartig",
        path="2a", parent_path="1", branch_kind="fork_option", trigger_condition="politik",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="Musik-Fortsetzung, ganz einzigartig",
        path="2b", parent_path="1", branch_kind="fork_option", trigger_condition="musik",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="", path="2c", parent_path="1",
        branch_kind="fork_catchall", trigger_condition=lookahead.CATCHALL_TRIGGER,
    ))
    lookahead._chains[user_id] = chain

    result = lookahead.consume_head(user_id, "robin")

    assert result.text == "Moegen Sie lieber Politik oder Musik?"
    assert result.branch_kind == "fork_root"
    assert chain.cursor_path == "1"
    assert {lvl.path for lvl in chain.levels} == {"2a", "2b", "2c"}


def test_consume_head_mode_a_returns_none_when_cursor_awaits_reply_and_reply_not_yet_given():
    user_id = "lookahead_mode_a_awaiting"
    chain = lookahead.Chain(user_id=user_id, persona_id="robin", cursor_path="1")
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="Politik-Fortsetzung",
        path="2a", parent_path="1", branch_kind="fork_option", trigger_condition="politik",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="Musik-Fortsetzung",
        path="2b", parent_path="1", branch_kind="fork_option", trigger_condition="musik",
    ))
    lookahead._chains[user_id] = chain

    assert lookahead.consume_head(user_id, "robin") is None


def _build_pending_fork_chain(user_id):
    chain = lookahead.Chain(user_id=user_id, persona_id="robin", cursor_path="1")
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="Politik-Fortsetzung",
        path="2a", parent_path="1", branch_kind="fork_option", trigger_condition="politik",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="Musik-Fortsetzung",
        path="2b", parent_path="1", branch_kind="fork_option", trigger_condition="musik",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="", path="2c", parent_path="1",
        branch_kind="fork_catchall", trigger_condition=lookahead.CATCHALL_TRIGGER,
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=3, kind="continue_new_topic", text="Politik-Fortsetzung, Stufe 2",
        path="3a", parent_path="2a",
    ))
    lookahead._chains[user_id] = chain
    return chain


def test_consume_head_mode_b_confirms_matching_option_by_substring(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_mode_b_a"
    chain = _build_pending_fork_chain(user_id)

    result = lookahead.consume_head(user_id, "robin", user_reply_text="Ich haette gerne ueber Politik gesprochen")

    assert result.text == "Politik-Fortsetzung"
    assert lookahead._forks_confirmed == 1


def test_consume_head_mode_b_yes_no_synonym_table_matches_synonym_not_just_literal_word(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_mode_b_yesno"
    chain = lookahead.Chain(user_id=user_id, persona_id="robin", cursor_path="1")
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="Ja-Fortsetzung",
        path="2a", parent_path="1", branch_kind="fork_option", trigger_condition="ja",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="Nein-Fortsetzung",
        path="2b", parent_path="1", branch_kind="fork_option", trigger_condition="nein",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="", path="2c", parent_path="1",
        branch_kind="fork_catchall", trigger_condition=lookahead.CATCHALL_TRIGGER,
    ))
    lookahead._chains[user_id] = chain

    result = lookahead.consume_head(user_id, "robin", user_reply_text="Na klar, gerne!")

    assert result.text == "Ja-Fortsetzung"


def test_consume_head_mode_b_falls_back_to_none_on_no_option_match(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_mode_b_nomatch"
    chain = _build_pending_fork_chain(user_id)

    result = lookahead.consume_head(user_id, "robin", user_reply_text="Erzaehlen Sie mir lieber vom Wetter")

    assert result is None
    assert lookahead._forks_catchall_taken == 1
    assert lookahead._forks_confirmed == 0


def test_consume_head_mode_b_first_match_wins_on_ambiguous_multi_match(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_mode_b_ambiguous"
    chain = _build_pending_fork_chain(user_id)

    result = lookahead.consume_head(user_id, "robin", user_reply_text="Politik oder Musik, beides waere schoen")

    assert result.trigger_condition == "politik"


def test_consume_head_mode_b_updates_cursor_path_to_confirmed_child(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_mode_b_cursor"
    chain = _build_pending_fork_chain(user_id)

    lookahead.consume_head(user_id, "robin", user_reply_text="Musik waer schoen")

    assert chain.cursor_path == "2b"


def test_confirm_branch_prunes_sibling_options_from_chain_levels(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_prune_a"
    chain = _build_pending_fork_chain(user_id)

    lookahead.consume_head(user_id, "robin", user_reply_text="Politik bitte")

    remaining_paths = {lvl.path for lvl in chain.levels}
    assert "2b" not in remaining_paths
    assert "2c" not in remaining_paths
    assert "2a" not in remaining_paths  # confirmed selbst wird auch entfernt, siehe Runde 1's pop()


def test_confirm_branch_prunes_descendants_of_pruned_siblings_too(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_prune_b"
    chain = _build_pending_fork_chain(user_id)
    # Ein Nachkomme von "2b" (dem NICHT bestaetigten Zweig) existiert schon.
    chain.levels.append(lookahead.ChainLevel(
        depth=3, kind="continue_new_topic", text="Musik-Fortsetzung, Stufe 2",
        path="3b", parent_path="2b",
    ))

    lookahead.consume_head(user_id, "robin", user_reply_text="Politik bitte")

    remaining_paths = {lvl.path for lvl in chain.levels}
    assert "3b" not in remaining_paths


def test_confirm_branch_keeps_confirmed_childs_own_descendants(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_prune_c"
    chain = _build_pending_fork_chain(user_id)

    lookahead.consume_head(user_id, "robin", user_reply_text="Politik bitte")

    remaining_paths = {lvl.path for lvl in chain.levels}
    assert "3a" in remaining_paths


async def test_confirm_branch_bumps_generation_and_cancels_stale_build_task(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def controlled_stream(model, system_prompt, messages, max_tokens=400):
        started.set()
        await release.wait()
        yield "zu spaet"

    monkeypatch.setattr(lookahead.llm_client, "stream", controlled_stream)
    persona = _StubPersona()
    monkeypatch.setattr(lookahead.config, "PERSONAS", {"robin": persona})

    user_id = "lookahead_prune_d"
    chain = _build_pending_fork_chain(user_id)
    task = asyncio.create_task(lookahead._extend_chain(user_id, chain.generation))
    chain.build_task = task
    await asyncio.wait_for(started.wait(), timeout=1)

    old_generation = chain.generation
    lookahead.consume_head(user_id, "robin", user_reply_text="Politik bitte")

    assert chain.generation == old_generation + 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


def test_confirm_branch_increments_nodes_pruned_on_confirm_counter(monkeypatch):
    monkeypatch.setattr(lookahead, "_spawn_build_task", lambda user_id, chain: None)
    monkeypatch.setattr(lookahead, "_spawn_audio_task", lambda user_id, chain: None)

    user_id = "lookahead_prune_e"
    chain = _build_pending_fork_chain(user_id)

    lookahead.consume_head(user_id, "robin", user_reply_text="Politik bitte")

    # "2b" und "2c" werden verworfen (der bestaetigte Knoten "2a"
    # selbst zaehlt nicht als "gepruned", er wird regulaer ausgeliefert).
    assert lookahead._nodes_pruned_on_confirm == 2


def test_stats_reports_forks_offered_confirmed_and_catchall_counts():
    lookahead._forks_offered = 3
    lookahead._forks_confirmed = 2
    lookahead._forks_catchall_taken = 1

    result = lookahead.stats()

    assert result["forks_offered"] == 3
    assert result["forks_confirmed"] == 2
    assert result["forks_catchall_taken"] == 1


def test_stats_reports_nodes_pruned_on_confirm():
    lookahead._nodes_pruned_on_confirm = 5
    assert lookahead.stats()["nodes_pruned_on_confirm"] == 5


def test_debug_state_reports_branching_true_when_fork_root_present():
    chain = lookahead.Chain(user_id="lookahead_debug_branch_a", persona_id="freundin")
    chain.levels.append(lookahead.ChainLevel(
        depth=1, kind="continue", text="Frage?", path="1", parent_path=None, branch_kind="fork_root",
    ))
    lookahead._chains["lookahead_debug_branch_a"] = chain

    assert lookahead.debug_state("lookahead_debug_branch_a")["branching"] is True


def test_debug_state_reports_branching_false_for_plain_linear_chain():
    chain = lookahead.Chain(user_id="lookahead_debug_branch_b", persona_id="freundin")
    chain.levels.append(lookahead.ChainLevel(depth=1, kind="continue", text="Eins"))
    lookahead._chains["lookahead_debug_branch_b"] = chain

    assert lookahead.debug_state("lookahead_debug_branch_b")["branching"] is False


def test_debug_state_levels_built_still_returns_int_count_not_error_with_fork_present():
    chain = lookahead.Chain(user_id="lookahead_debug_branch_c", persona_id="freundin")
    chain.levels.append(lookahead.ChainLevel(
        depth=1, kind="continue", text="Frage?", path="1", parent_path=None, branch_kind="fork_root",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="A", path="2a", parent_path="1", branch_kind="fork_option",
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic", text="B", path="2b", parent_path="1", branch_kind="fork_option",
    ))
    lookahead._chains["lookahead_debug_branch_c"] = chain

    state = lookahead.debug_state("lookahead_debug_branch_c")
    assert isinstance(state["levels_built"], int)
    assert state["levels_built"] == 3
