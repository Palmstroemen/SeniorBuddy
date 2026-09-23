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
    yield
    lookahead._chains.clear()
    lookahead._audio_cache.clear()
    lookahead._levels_built = {d: 0 for d in range(1, 6)}
    lookahead._levels_delivered = {d: 0 for d in range(1, 6)}
    lookahead._discarded_interrupted = 0
    lookahead._discarded_suppressed = 0
    lookahead._discarded_stale = 0


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
    chain.levels.append(lookahead.ChainLevel(depth=1, kind="continue", text="Erste Stufe, ganz einzigartig"))
    chain.levels.append(lookahead.ChainLevel(depth=2, kind="continue_new_topic", text="Zweite Stufe, ganz einzigartig"))
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
    ))
    chain.levels.append(lookahead.ChainLevel(
        depth=2, kind="continue_new_topic",
        text="Wie war eigentlich Ihr letzter Urlaub, erzaehlen Sie mal!",
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
    chain.levels.append(lookahead.ChainLevel(depth=1, kind="continue", text="Eins", audio=b"wav"))
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
