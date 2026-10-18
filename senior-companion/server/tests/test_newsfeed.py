"""
Tests fuer den naechtlichen Tagesagenda-Job (server/newsfeed.py). Wie
test_sentiment_job.py: NIE ein echter Ollama-Server/RSS-Feed,
ausschliesslich gemockter HTTP-Verkehr (pytest-httpx) - httpx_mock
faengt ALLE httpx-Aufrufe ab, egal ob Feed-Abruf oder Ollama-Chat.

Persona-Zustand wird NIE per Attribut-Mutation (persona.rss_feeds = ...)
gesetzt, sondern per dataclasses.replace() - config.PERSONAS[id] kann
fuer unveraenderte Personas dieselbe Objekt-Referenz wie
config._BUILTIN_PERSONAS[id] sein (siehe config.py: eine flache Kopie),
eine In-Place-Mutation wuerde also auch die "Zuruecksetzen auf
Standard"-Vorlage fuer den Rest des Testlaufs verunreinigen.
"""
import asyncio
import dataclasses

import pytest

import admin_settings
import config
import llm_client
import memory
import newsfeed
import priority

SAMPLE_FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Testfeed</title>
<item><title>Titel A</title><description>Beschreibung A</description></item>
<item><title>Titel B</title><description>Beschreibung B</description></item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def _reset_priority_state():
    priority._active_senior_streams = 0
    priority._low_priority_tasks.clear()
    priority._idle_event.set()
    yield
    priority._active_senior_streams = 0
    priority._low_priority_tasks.clear()
    priority._idle_event.set()


@pytest.fixture(autouse=True)
def _reset_persona_state():
    """Wie test_admin_personas.py's gleichnamige Fixture - wirkt hier
    nur korrekt, WEIL set_persona_feeds() unten nie in-place mutiert,
    sondern immer ein frisches Objekt per dataclasses.replace() einsetzt."""
    ids_before = set(config.PERSONAS.keys())
    yield
    for extra_id in set(config.PERSONAS.keys()) - ids_before:
        del config.PERSONAS[extra_id]
    for persona_id in ids_before:
        if persona_id in config._BUILTIN_PERSONAS:
            config.PERSONAS[persona_id] = config._BUILTIN_PERSONAS[persona_id]


def set_persona_feeds(persona_id: str, feeds: list, long_term_agenda: str = "") -> None:
    """Setzt rss_feeds/long_term_agenda fuer einen Test - per
    dataclasses.replace() statt Attribut-Mutation (siehe Modul-Docstring)."""
    current = config.PERSONAS[persona_id]
    config.PERSONAS[persona_id] = dataclasses.replace(
        current, rss_feeds=feeds, long_term_agenda=long_term_agenda,
    )


def clear_all_feeds() -> None:
    for persona_id, persona in list(config.PERSONAS.items()):
        config.PERSONAS[persona_id] = dataclasses.replace(
            persona, rss_feeds=[], daily_agenda="",
        )


def _chat_response(content: str) -> dict:
    return {"json": {"message": {"content": content}, "done": True}}


# --- fetch_feed: reiner HTTP-Abruf + Parsing -----------------------------

async def test_fetch_feed_returns_title_and_summary_pairs(httpx_mock):
    httpx_mock.add_response(url="https://example.com/feed.xml", text=SAMPLE_FEED_XML)

    items = await newsfeed.fetch_feed("https://example.com/feed.xml")

    assert items == [("Titel A", "Beschreibung A"), ("Titel B", "Beschreibung B")]


async def test_fetch_feed_returns_empty_list_on_http_error(httpx_mock):
    httpx_mock.add_response(url="https://example.com/kaputt.xml", status_code=500)

    items = await newsfeed.fetch_feed("https://example.com/kaputt.xml")

    assert items == []


async def test_fetch_feed_returns_empty_list_on_malformed_xml(httpx_mock):
    httpx_mock.add_response(url="https://example.com/muell.xml", text="nicht einmal XML {{{")

    items = await newsfeed.fetch_feed("https://example.com/muell.xml")

    assert items == []


async def test_fetch_feed_caps_at_max_items(httpx_mock):
    many_items = "".join(
        f"<item><title>Titel {i}</title><description>Text {i}</description></item>"
        for i in range(newsfeed.MAX_ITEMS_PER_FEED + 5)
    )
    xml = f'<?xml version="1.0"?><rss version="2.0"><channel>{many_items}</channel></rss>'
    httpx_mock.add_response(url="https://example.com/viele.xml", text=xml)

    items = await newsfeed.fetch_feed("https://example.com/viele.xml")

    assert len(items) == newsfeed.MAX_ITEMS_PER_FEED


# --- _weighted_item_count: reine Funktion, kein I/O ----------------------

def test_weighted_item_count_full_weight_returns_max():
    assert newsfeed._weighted_item_count(100) == newsfeed.MAX_ITEMS_PER_FEED


def test_weighted_item_count_zero_weight_returns_zero():
    assert newsfeed._weighted_item_count(0) == 0


def test_weighted_item_count_low_weight_is_noticeably_lower_but_not_zero():
    low = newsfeed._weighted_item_count(20)
    assert 0 < low < newsfeed.MAX_ITEMS_PER_FEED


# --- build_daily_agenda_for_persona: gemockter Feed-Abruf + Ollama-Call --

async def test_build_daily_agenda_returns_none_without_feeds():
    set_persona_feeds("freundin", [])

    result = await newsfeed.build_daily_agenda_for_persona(config.PERSONAS["freundin"])

    assert result is None


async def test_build_daily_agenda_uses_long_term_agenda_as_lens(httpx_mock):
    set_persona_feeds(
        "freundin",
        [{"url": "https://example.com/feed.xml", "weight": 100}],
        long_term_agenda="lokale Kuriositaeten",
    )
    httpx_mock.add_response(url="https://example.com/feed.xml", text=SAMPLE_FEED_XML)
    captured = {}

    async def fake_generate(model, system_prompt, messages, max_tokens=400):
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return "Verdichtete Tagesagenda."

    original = llm_client.generate
    llm_client.generate = fake_generate
    try:
        result = await newsfeed.build_daily_agenda_for_persona(config.PERSONAS["freundin"])
    finally:
        llm_client.generate = original

    assert result == "Verdichtete Tagesagenda."
    assert "lokale Kuriositaeten" in captured["system_prompt"]
    assert "Titel A" in captured["messages"][0]["content"]


async def test_build_daily_agenda_falls_back_when_long_term_agenda_empty(httpx_mock):
    set_persona_feeds(
        "freundin", [{"url": "https://example.com/feed.xml", "weight": 100}],
    )
    httpx_mock.add_response(url="https://example.com/feed.xml", text=SAMPLE_FEED_XML)
    captured = {}

    async def fake_generate(model, system_prompt, messages, max_tokens=400):
        captured["system_prompt"] = system_prompt
        return "ok"

    original = llm_client.generate
    llm_client.generate = fake_generate
    try:
        await newsfeed.build_daily_agenda_for_persona(config.PERSONAS["freundin"])
    finally:
        llm_client.generate = original

    assert newsfeed.DEFAULT_AGENDA_FALLBACK in captured["system_prompt"]


async def test_build_daily_agenda_weighting_affects_headline_pool_size(httpx_mock):
    set_persona_feeds(
        "freundin",
        [
            {"url": "https://example.com/stark.xml", "weight": 100},
            {"url": "https://example.com/schwach.xml", "weight": 20},
        ],
    )
    many_items = "".join(
        f"<item><title>Titel {i}</title><description>Text {i}</description></item>"
        for i in range(newsfeed.MAX_ITEMS_PER_FEED)
    )
    xml = f'<?xml version="1.0"?><rss version="2.0"><channel>{many_items}</channel></rss>'
    httpx_mock.add_response(url="https://example.com/stark.xml", text=xml)
    httpx_mock.add_response(url="https://example.com/schwach.xml", text=xml)
    captured = {}

    async def fake_generate(model, system_prompt, messages, max_tokens=400):
        captured["headline_text"] = messages[0]["content"]
        return "ok"

    original = llm_client.generate
    llm_client.generate = fake_generate
    try:
        await newsfeed.build_daily_agenda_for_persona(config.PERSONAS["freundin"])
    finally:
        llm_client.generate = original

    line_count = captured["headline_text"].count("\n") + 1
    assert line_count == newsfeed.MAX_ITEMS_PER_FEED + newsfeed._weighted_item_count(20)


# --- refresh_persona_agenda: Einzel-Persona-Refresh (Sofort-Trigger) -----

async def test_refresh_persona_agenda_returns_false_without_feeds():
    clear_all_feeds()

    result = await newsfeed.refresh_persona_agenda("freundin")

    assert result is False


async def test_refresh_persona_agenda_returns_false_for_unknown_persona():
    result = await newsfeed.refresh_persona_agenda("gibt_es_nicht")

    assert result is False


async def test_refresh_persona_agenda_updates_and_persists(httpx_mock):
    clear_all_feeds()
    set_persona_feeds(
        "freundin", [{"url": "https://example.com/feed.xml", "weight": 100}],
    )
    httpx_mock.add_response(url="https://example.com/feed.xml", text=SAMPLE_FEED_XML)
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        **_chat_response("Frische Tagesagenda."),
    )

    result = await newsfeed.refresh_persona_agenda("freundin")

    assert result is True
    assert config.PERSONAS["freundin"].daily_agenda == "Frische Tagesagenda."
    saved = admin_settings.load()["personas"]
    freundin_saved = next(p for p in saved if p["id"] == "freundin")
    assert freundin_saved["daily_agenda"] == "Frische Tagesagenda."


async def test_refresh_persona_agenda_logs_transparency(httpx_mock):
    memory.add_message("refresh_transparency_user", "freundin", "user", "Hallo")
    clear_all_feeds()
    set_persona_feeds(
        "freundin", [{"url": "https://example.com/feed.xml", "weight": 100}],
    )
    httpx_mock.add_response(url="https://example.com/feed.xml", text=SAMPLE_FEED_XML)
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        **_chat_response("Agenda."),
    )

    await newsfeed.refresh_persona_agenda("freundin")

    log = memory.get_transparency_log("refresh_transparency_user")
    assert any(entry["plugin"] == "newsfeed:freundin" for entry in log)


# --- build_daily_agendas: der eigentliche Scheduler-Job -------------------

async def test_build_daily_agendas_skips_personas_without_feeds(httpx_mock):
    clear_all_feeds()

    await newsfeed.build_daily_agendas()

    assert all(p.daily_agenda == "" for p in config.PERSONAS.values())


async def test_build_daily_agendas_updates_agenda_and_persists(httpx_mock):
    clear_all_feeds()
    set_persona_feeds(
        "freundin", [{"url": "https://example.com/feed.xml", "weight": 100}],
    )

    httpx_mock.add_response(url="https://example.com/feed.xml", text=SAMPLE_FEED_XML)
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        **_chat_response("Neue Tagesagenda fuer Robin."),
    )

    await newsfeed.build_daily_agendas()

    assert config.PERSONAS["freundin"].daily_agenda == "Neue Tagesagenda fuer Robin."
    saved = admin_settings.load()["personas"]
    freundin_saved = next(p for p in saved if p["id"] == "freundin")
    assert freundin_saved["daily_agenda"] == "Neue Tagesagenda fuer Robin."


async def test_build_daily_agendas_does_not_mutate_builtin_template(httpx_mock):
    """Regressionstest fuer den _BUILTIN_PERSONAS-Objektidentitaets-
    Stolperstein (siehe Modul-Docstring) - waere der Job auf
    persona.daily_agenda = agenda zurueckgefallen, wuerde dieser Test
    fehlschlagen."""
    clear_all_feeds()
    set_persona_feeds(
        "freundin", [{"url": "https://example.com/feed.xml", "weight": 100}],
    )

    httpx_mock.add_response(url="https://example.com/feed.xml", text=SAMPLE_FEED_XML)
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        **_chat_response("Neue Tagesagenda."),
    )

    await newsfeed.build_daily_agendas()

    assert config._BUILTIN_PERSONAS["freundin"].daily_agenda == ""


async def test_build_daily_agendas_logs_transparency_for_known_users(httpx_mock):
    memory.add_message("transparency_user", "freundin", "user", "Hallo")
    clear_all_feeds()
    set_persona_feeds(
        "freundin", [{"url": "https://example.com/feed.xml", "weight": 100}],
    )

    httpx_mock.add_response(url="https://example.com/feed.xml", text=SAMPLE_FEED_XML)
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        **_chat_response("Agenda."),
    )

    await newsfeed.build_daily_agendas()

    log = memory.get_transparency_log("transparency_user")
    assert any(entry["plugin"] == "newsfeed:freundin" for entry in log)


async def test_build_daily_agendas_registers_and_unregisters_as_low_priority(httpx_mock):
    clear_all_feeds()
    set_persona_feeds(
        "freundin", [{"url": "https://example.com/feed.xml", "weight": 100}],
    )

    httpx_mock.add_response(url="https://example.com/feed.xml", text=SAMPLE_FEED_XML)
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        **_chat_response("Agenda."),
    )

    await newsfeed.build_daily_agendas()

    assert priority._low_priority_tasks == set()


async def test_build_daily_agendas_is_cancelled_by_senior_stream():
    async def never_returns(*args, **kwargs):
        await asyncio.sleep(5)

    clear_all_feeds()
    set_persona_feeds(
        "freundin", [{"url": "https://example.com/feed.xml", "weight": 100}],
    )

    original = newsfeed.fetch_feed
    newsfeed.fetch_feed = never_returns
    try:
        task = asyncio.create_task(newsfeed.build_daily_agendas())
        await asyncio.sleep(0.05)
        priority.senior_stream_started()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
    finally:
        newsfeed.fetch_feed = original
