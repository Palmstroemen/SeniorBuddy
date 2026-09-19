"""
Direkte (websocket-lose) Tests fuer main.prepare_handoff() /
main.spawn_handoff_task() und die dahinterliegende Hintergrund-Task
(_run_handoff_summary): deterministisch ueber den zurueckgegebenen
Task awaitbar, kein Polling noetig.

Zwei getrennte Funktionen statt einer: prepare_handoff() erkennt die
Uebergabe-Bitte und liefert nur die Prompt-Zeile fuer den sichtbaren
Zug, spawn_handoff_task() erzeugt die eigentliche Hintergrund-Task -
und MUSS vom Aufrufer erst NACH dessen eigenem
senior_stream_started()/finished()-Block aufgerufen werden. Grund:
senior_stream_started() bricht ALLE registrierten Low-Priority-Tasks
ab, ausnahmslos - wuerde man beides VOR dem eigenen sichtbaren
Streaming-Zug aufrufen, wuerde genau dessen eigener
senior_stream_started()-Aufruf die gerade erst registrierte Task
sofort wieder abbrechen, noch bevor sie ihren ersten Schritt machen
konnte (per echtem Rauchtest mit einem echten lokalen Modell entdeckt,
nicht nur angenommen - siehe test_spawn_handoff_task_survives_the_
callers_own_senior_stream_bracket unten, die genau dieses Szenario
nachstellt)."""
import asyncio

import pytest

import main
import memory
import priority


@pytest.fixture(autouse=True)
def _reset_priority_state():
    """Modul-globaler Zustand (siehe test_priority.py) - zwischen
    Tests zuruecksetzen, sonst beeinflusst z.B. senior_stream_started()
    aus einem Test die naechsten."""
    priority._active_senior_streams = 0
    priority._low_priority_tasks.clear()
    priority._idle_event.set()
    yield
    priority._active_senior_streams = 0
    priority._low_priority_tasks.clear()
    priority._idle_event.set()


async def test_handoff_writes_summary_to_target_persona(monkeypatch):
    async def fake_generate(model, system_prompt, messages, max_tokens=400):
        return "Kurze Zusammenfassung des Gespraechs."

    monkeypatch.setattr(main.llm_client, "generate", fake_generate)
    memory.add_message("handoff_task_user1", "freundin", "user", "Hallo Robin!")
    memory.add_message("handoff_task_user1", "freundin", "assistant", "Hallo!")

    outcome = main.prepare_handoff(
        "handoff_task_user1", "freundin", "Robin, erzaehl das mal Wallner",
    )
    assert outcome.target_id == "professor"
    task = main.spawn_handoff_task("handoff_task_user1", "freundin", outcome)
    assert task is not None
    await task

    professor_view = memory.recent_messages("handoff_task_user1", "professor")
    assert any(
        m["role"] == "system" and "Von Robin erzaehlt" in m["content"]
        and "Kurze Zusammenfassung des Gespraechs." in m["content"]
        for m in professor_view
    )


async def test_prepare_handoff_returns_empty_outcome_when_no_target_detected():
    outcome = main.prepare_handoff("handoff_task_user2", "freundin", "Wie war dein Tag?")
    assert outcome.target_id is None
    assert outcome.system_context_line is None
    assert main.spawn_handoff_task("handoff_task_user2", "freundin", outcome) is None


async def test_prepare_handoff_returns_cannot_share_when_everything_confidential():
    memory.add_message(
        "handoff_task_user3", "freundin", "user", "Geheimnis ueber Heinrich.",
        topic="Heinrich",
    )
    outcome = main.prepare_handoff(
        "handoff_task_user3", "freundin", "Robin, erzaehl das mal Wallner",
    )
    assert outcome.target_id is None
    assert outcome.system_context_line is not None
    assert "Wallner" in outcome.system_context_line


async def test_spawn_handoff_task_is_registered_and_cancellable(monkeypatch):
    async def never_returns(*args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(main.llm_client, "generate", never_returns)
    memory.add_message("handoff_task_user4", "freundin", "user", "Hallo Robin!")

    outcome = main.prepare_handoff(
        "handoff_task_user4", "freundin", "Robin, erzaehl das mal Wallner",
    )
    task = main.spawn_handoff_task("handoff_task_user4", "freundin", outcome)
    assert task in priority._low_priority_tasks

    priority.senior_stream_started()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert task not in priority._low_priority_tasks


async def test_spawn_handoff_task_logs_and_swallows_generate_failure(monkeypatch, caplog):
    async def failing_generate(*args, **kwargs):
        raise RuntimeError("Ollama nicht erreichbar")

    monkeypatch.setattr(main.llm_client, "generate", failing_generate)
    memory.add_message("handoff_task_user5", "freundin", "user", "Hallo Robin!")

    outcome = main.prepare_handoff(
        "handoff_task_user5", "freundin", "Robin, erzaehl das mal Wallner",
    )
    task = main.spawn_handoff_task("handoff_task_user5", "freundin", outcome)
    await task  # darf NICHT selbst raisen - Fehler wird geschluckt/geloggt

    assert memory.recent_messages("handoff_task_user5", "professor") == []


async def test_spawn_handoff_task_survives_the_callers_own_senior_stream_bracket(monkeypatch):
    """Regressionswaechter fuer den beim echten Rauchtest gefundenen
    Bug: wird die Hintergrund-Task VOR dem eigenen
    senior_stream_started()-Aufruf desselben Zugs erzeugt (statt wie
    main.py's chat()/run_turn() es jetzt richtig machen, erst DANACH),
    bricht dieser eigene Aufruf sie sofort wieder ab. Dieser Test
    simuliert exakt main.py's korrekte Reihenfolge: prepare_handoff
    VOR dem sichtbaren Streaming, spawn_handoff_task ERST NACH
    senior_stream_finished() desselben Zugs - und beweist, dass die
    Task dann tatsaechlich durchlaeuft, statt nie ihren ersten Schritt
    zu bekommen."""
    async def fake_generate(model, system_prompt, messages, max_tokens=400):
        return "Zusammenfassung."

    monkeypatch.setattr(main.llm_client, "generate", fake_generate)
    memory.add_message("handoff_task_user6", "freundin", "user", "Hallo Robin!")

    outcome = main.prepare_handoff(
        "handoff_task_user6", "freundin", "Robin, erzaehl das mal Wallner",
    )
    assert outcome.target_id == "professor"

    # Der eigene sichtbare Zug DIESES Turns - genau wie in chat()/
    # run_turn(): senior_stream_started() VOR spawn_handoff_task().
    priority.senior_stream_started()
    priority.senior_stream_finished()

    task = main.spawn_handoff_task("handoff_task_user6", "freundin", outcome)
    assert task is not None
    await task

    professor_view = memory.recent_messages("handoff_task_user6", "professor")
    assert any(m["role"] == "system" and "Zusammenfassung." in m["content"] for m in professor_view)
