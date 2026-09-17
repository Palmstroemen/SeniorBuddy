"""
Tests fuer server/priority.py - reines Asyncio, kein FastAPI/WebSocket
noetig. Primaerer Korrektheitsnachweis fuer die Praeemptions-Logik des
Aussererordentlichen-Nutzer-Zugangs: eine laufende Low-Priority-Aufgabe
MUSS abgebrochen werden, sobald ein Senior-Stream beginnt - auch wenn
das Timing knapp ist (Race-Fix).
"""
import asyncio

import pytest

import priority


@pytest.fixture(autouse=True)
def _reset_priority_state():
    """Modul-globaler Zustand - zwischen Tests zuruecksetzen, sonst
    beeinflussen sich Tests gegenseitig."""
    priority._active_senior_streams = 0
    priority._low_priority_tasks.clear()
    priority._idle_event.set()
    yield
    priority._active_senior_streams = 0
    priority._low_priority_tasks.clear()
    priority._idle_event.set()


def test_senior_stream_active_toggles_correctly():
    assert priority.senior_stream_active() is False
    priority.senior_stream_started()
    assert priority.senior_stream_active() is True
    priority.senior_stream_finished()
    assert priority.senior_stream_active() is False


def test_multiple_overlapping_senior_streams_need_all_to_finish():
    priority.senior_stream_started()
    priority.senior_stream_started()
    assert priority.senior_stream_active() is True
    priority.senior_stream_finished()
    assert priority.senior_stream_active() is True  # noch einer offen
    priority.senior_stream_finished()
    assert priority.senior_stream_active() is False


async def test_wait_until_idle_unblocks_once_last_senior_stream_finishes():
    priority.senior_stream_started()
    waiter = asyncio.create_task(priority.wait_until_idle())
    await asyncio.sleep(0.05)
    assert not waiter.done()
    priority.senior_stream_finished()
    await asyncio.wait_for(waiter, timeout=1)
    assert waiter.done()


async def test_senior_stream_started_cancels_registered_low_priority_task():
    ran_to_completion = False

    async def low_priority_work():
        nonlocal ran_to_completion
        await asyncio.sleep(5)
        ran_to_completion = True

    task = asyncio.create_task(low_priority_work())
    priority.register_low_priority_task(task)

    priority.senior_stream_started()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert ran_to_completion is False


async def test_unregistered_task_survives_senior_stream_started():
    async def low_priority_work():
        await asyncio.sleep(0.05)
        return "fertig"

    task = asyncio.create_task(low_priority_work())
    # bewusst NICHT registriert
    priority.senior_stream_started()
    result = await task
    assert result == "fertig"


async def test_register_after_senior_already_active_cancels_immediately():
    """Race-Fix: wenn zwischen Idle-Check und Registrierung bereits ein
    Senior-Stream gestartet ist, darf die Aufgabe nicht ungeschuetzt
    durchlaufen."""
    async def low_priority_work():
        await asyncio.sleep(5)

    priority.senior_stream_started()  # Senior ist zuerst aktiv

    task = asyncio.create_task(low_priority_work())
    priority.register_low_priority_task(task)  # kommt "zu spaet"

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


def test_unregister_removes_task_from_future_cancellation():
    # Kein echter asyncio.Task noetig - reiner Mengen-Test.
    fake_task = object()
    priority._low_priority_tasks.add(fake_task)
    priority.unregister_low_priority_task(fake_task)
    assert fake_task not in priority._low_priority_tasks
