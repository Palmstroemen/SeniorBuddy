"""
Tests fuer den naechtlichen Sentiment-Klassifikations-Job
(server/sentiment_job.py). Wie test_llm_client.py: NIE ein echter
Ollama-Server, ausschliesslich gemockter HTTP-Verkehr (pytest-httpx).
"""
import asyncio

import httpx
import pytest

import llm_client
import memory
import priority
import sentiment_job


@pytest.fixture(autouse=True)
def _reset_priority_state():
    priority._active_senior_streams = 0
    priority._low_priority_tasks.clear()
    priority._idle_event.set()
    yield
    priority._active_senior_streams = 0
    priority._low_priority_tasks.clear()
    priority._idle_event.set()


def _chat_response(content: str) -> httpx.ByteStream:
    escaped = content.replace("\n", "\\n")
    return httpx.ByteStream(
        (
            '{"message": {"content": "%s"}, "done": false}\n'
            '{"message": {"content": ""}, "done": true}\n' % escaped
        ).encode()
    )


# --- _parse_classification: reine Funktion, kein I/O --------------------

def test_parse_classification_well_formed_positiv_zustimmung():
    sentiment, stance = sentiment_job._parse_classification(
        "STIMMUNG: positiv\nHALTUNG: zustimmung"
    )
    assert sentiment == "positiv"
    assert stance == "zustimmung"


def test_parse_classification_well_formed_negativ_widerspruch():
    sentiment, stance = sentiment_job._parse_classification(
        "STIMMUNG: negativ\nHALTUNG: widerspruch"
    )
    assert sentiment == "negativ"
    assert stance == "widerspruch"


def test_parse_classification_haltung_keine_means_no_stance():
    sentiment, stance = sentiment_job._parse_classification(
        "STIMMUNG: neutral\nHALTUNG: keine"
    )
    assert sentiment == "neutral"
    assert stance is None


def test_parse_classification_is_case_insensitive():
    sentiment, stance = sentiment_job._parse_classification(
        "Stimmung: POSITIV\nHaltung: Zustimmung"
    )
    assert sentiment == "positiv"
    assert stance == "zustimmung"


def test_parse_classification_falls_back_on_garbage():
    sentiment, stance = sentiment_job._parse_classification(
        "Tut mir leid, ich kann das nicht einschaetzen."
    )
    assert sentiment == "neutral"
    assert stance is None


def test_parse_classification_ignores_unknown_values():
    sentiment, stance = sentiment_job._parse_classification(
        "STIMMUNG: euphorisch\nHALTUNG: vielleicht"
    )
    assert sentiment == "neutral"
    assert stance is None


# --- _classify_one / _run: gemockter Ollama-Aufruf -----------------------

async def test_classify_one_writes_result_to_db(httpx_mock):
    memory.add_message("sentiment_user", "freundin", "user", "Das freut mich total!")
    message_id = memory.unclassified_messages("sentiment_user")[0]["id"]

    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        stream=_chat_response("STIMMUNG: positiv\\nHALTUNG: keine"),
    )
    await sentiment_job._classify_one("sentiment_user", message_id, "Das freut mich total!")

    with memory.get_db("sentiment_user") as db:
        row = db.execute(
            "SELECT sentiment, stance FROM messages WHERE id=?", (message_id,)
        ).fetchone()
    assert row["sentiment"] == "positiv"
    assert row["stance"] is None


async def test_run_classifies_all_pending_messages_across_users(httpx_mock):
    memory.add_message("run_user_a", "freundin", "user", "Nein, das stimmt nicht.")
    memory.add_message("run_user_b", "freundin", "user", "Ja genau, sehr gerne.")

    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        stream=_chat_response("STIMMUNG: negativ\\nHALTUNG: widerspruch"),
    )
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        stream=_chat_response("STIMMUNG: positiv\\nHALTUNG: zustimmung"),
    )

    await sentiment_job._run()

    assert memory.unclassified_messages("run_user_a") == []
    assert memory.unclassified_messages("run_user_b") == []
    stats_a = memory.sentiment_stats("run_user_a")
    stats_b = memory.sentiment_stats("run_user_b")
    assert stats_a["sentiment"] == {"negativ": 1}
    assert stats_b["sentiment"] == {"positiv": 1}


# --- classify_pending_messages: Low-Priority-Registrierung ---------------

async def test_classify_pending_messages_registers_and_unregisters_as_low_priority(httpx_mock):
    memory.add_message("prio_user", "freundin", "user", "Alles gut hier.")
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        stream=_chat_response("STIMMUNG: neutral\\nHALTUNG: keine"),
    )
    await sentiment_job.classify_pending_messages()
    assert priority._low_priority_tasks == set()


async def test_classify_pending_messages_is_cancelled_by_senior_stream():
    async def never_returns(*args, **kwargs):
        await asyncio.sleep(5)

    original = llm_client.generate
    llm_client.generate = never_returns
    try:
        memory.add_message("cancel_user", "freundin", "user", "Hallo?")
        task = asyncio.create_task(sentiment_job.classify_pending_messages())
        await asyncio.sleep(0.05)
        priority.senior_stream_started()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
    finally:
        llm_client.generate = original
