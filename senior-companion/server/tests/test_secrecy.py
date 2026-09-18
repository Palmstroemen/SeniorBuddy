"""
Tests fuer server/secrecy.py: Erkennung von Vertraulichkeits-/Lösch-
Signalen (gleiches Rule-Muster wie test_analysis.py/test_security.py)
und die handle_turn()-Orchestrierung, die diese Signale gegen echte
memory.py-Funktionen umsetzt (kein LLM involviert, schnell und
deterministisch testbar).
"""
import pytest

import memory
import secrecy


# --- Vertraulichkeits-Signal ("Thema eroeffnen") -------------------------

CONFIDENTIAL_CASES = [
    "Das bleibt aber unter uns, ja?",
    "Das ist mein Geheimnis, das ich noch niemandem erzaehlt habe.",
    "Erzaehl niemandem davon, versprochen?",
    "Das soll niemand erfahren.",
    "Bitte behalte das fuer dich.",
    "Das darf niemand wissen.",
]


@pytest.mark.parametrize("text", CONFIDENTIAL_CASES)
def test_detect_confidential_signal_recognizes_cases(text):
    assert secrecy.detect_confidential_signal(text) is True


CONFIDENTIAL_ALLOW_CASES = [
    "Wie war dein Tag heute?",
    "Ich habe gestern meine Nachbarin besucht.",
    "Kannst du mir das Wetter sagen?",
    "Mein Enkel weiss schon alles darueber.",
]


@pytest.mark.parametrize("text", CONFIDENTIAL_ALLOW_CASES)
def test_detect_confidential_signal_ignores_unrelated_text(text):
    assert secrecy.detect_confidential_signal(text) is False


# --- Jetzt-loeschen-Anfrage -----------------------------------------------

DELETE_NOW_CASES = [
    "Bitte lösche alles, was ich dir dazu erzählt habe.",
    "Lösch das bitte wieder.",
    "Vergiss, was ich gesagt habe.",
    "Kannst du das bitte wieder löschen?",
]


@pytest.mark.parametrize("text", DELETE_NOW_CASES)
def test_detect_delete_now_recognizes_cases(text):
    assert secrecy.detect_delete_now(text) is True


DELETE_NOW_ALLOW_CASES = [
    "Loeschen ist so ein hartes Wort, oder?",
    "Ich habe meine Einkaufsliste verlegt.",
    "Wie geht es dir heute?",
    "Vergiss nicht, dass wir morgen telefonieren.",
]


@pytest.mark.parametrize("text", DELETE_NOW_ALLOW_CASES)
def test_detect_delete_now_ignores_unrelated_text(text):
    assert secrecy.detect_delete_now(text) is False


# --- Todesfall-Loeschanweisung --------------------------------------------

DELETE_ON_DEATH_CASES = [
    "Im Falle meines Todes, bitte lösche alles was ich dir zu Heinrich erzählt habe.",
    "Nach meinem Tod lösch bitte alles dazu.",
    "Wenn ich sterbe, lösche bitte alles zu diesem Thema.",
    "Falls mir etwas zustößt, lösch das bitte.",
]


@pytest.mark.parametrize("text", DELETE_ON_DEATH_CASES)
def test_detect_delete_on_death_recognizes_cases(text):
    assert secrecy.detect_delete_on_death(text) is True


DELETE_ON_DEATH_ALLOW_CASES = [
    "Ich habe Angst vor dem Tod. Bitte lösche meine alte Einkaufsliste.",
    "Falls ich einkaufen gehe, lösche bitte diese alte Notiz.",
    "Mein Opa ist letztes Jahr gestorben.",
    "Lösch das bitte.",  # Jetzt-Loeschung, aber kein Todesfall-Bezug
]


@pytest.mark.parametrize("text", DELETE_ON_DEATH_ALLOW_CASES)
def test_detect_delete_on_death_ignores_unrelated_or_immediate_cases(text):
    assert secrecy.detect_delete_on_death(text) is False


# --- Bestaetigung einer Loeschung ------------------------------------------

AFFIRMATIVE_CASES = [
    "Ja.",
    "Ja, löschen bitte.",
    "Ja bitte.",
    "Ja, mach das.",
]


@pytest.mark.parametrize("text", AFFIRMATIVE_CASES)
def test_detect_affirmative_recognizes_cases(text):
    assert secrecy.detect_affirmative(text) is True


AFFIRMATIVE_ALLOW_CASES = [
    "Nein, doch nicht.",
    "Ich muss noch darueber nachdenken.",
    "Ja, aber erst morgen erzaehl ich dir mehr.",
    "Wie geht es dir?",
]


@pytest.mark.parametrize("text", AFFIRMATIVE_ALLOW_CASES)
def test_detect_affirmative_ignores_unrelated_text(text):
    assert secrecy.detect_affirmative(text) is False


# --- handle_turn(): Orchestrierung gegen echte memory.py-Funktionen ------

def test_handle_turn_opens_pending_topic_on_confidential_signal():
    outcome = secrecy.handle_turn("secu1", "freundin", "Das bleibt unter uns, ja?")
    assert any("nennen" in line.lower() for line in outcome.system_context)
    assert memory.active_topic("secu1", "freundin") is None  # noch nicht benannt


def test_handle_turn_captures_topic_label_on_next_turn():
    secrecy.handle_turn("secu2", "freundin", "Das bleibt unter uns, ja?")
    outcome = secrecy.handle_turn("secu2", "freundin", "Heinrich")
    assert memory.active_topic("secu2", "freundin") == "Heinrich"
    assert outcome.topic_for_tagging == "Heinrich"


def test_handle_turn_tags_ongoing_messages_with_active_topic():
    secrecy.handle_turn("secu3", "freundin", "Das bleibt unter uns, ja?")
    secrecy.handle_turn("secu3", "freundin", "Heinrich")
    outcome = secrecy.handle_turn("secu3", "freundin", "Er war so charmant damals.")
    assert outcome.topic_for_tagging == "Heinrich"


def test_handle_turn_returns_no_topic_and_no_context_for_plain_message():
    outcome = secrecy.handle_turn("secu4", "freundin", "Wie war dein Tag?")
    assert outcome.topic_for_tagging is None
    assert outcome.system_context == []


def test_handle_turn_delete_now_with_active_topic_creates_pending_confirmation():
    secrecy.handle_turn("secu5", "freundin", "Das bleibt unter uns, ja?")
    secrecy.handle_turn("secu5", "freundin", "Heinrich")
    memory.add_message("secu5", "freundin", "user", "vertraulich", topic="Heinrich")

    outcome = secrecy.handle_turn(
        "secu5", "freundin", "Bitte lösche alles, was ich dir dazu erzählt habe."
    )
    assert any("Heinrich" in line for line in outcome.system_context)
    assert len(memory.recent_messages("secu5", "freundin", limit=20)) == 1  # noch nicht geloescht


def test_handle_turn_confirms_deletion_on_affirmative_reply():
    secrecy.handle_turn("secu6", "freundin", "Das bleibt unter uns, ja?")
    secrecy.handle_turn("secu6", "freundin", "Heinrich")
    memory.add_message("secu6", "freundin", "user", "vertraulich", topic="Heinrich")
    secrecy.handle_turn(
        "secu6", "freundin", "Bitte lösche alles, was ich dir dazu erzählt habe."
    )

    outcome = secrecy.handle_turn("secu6", "freundin", "Ja, bitte löschen.")
    assert any("gel" in line.lower() and "scht" in line.lower() for line in outcome.system_context)
    assert memory.recent_messages("secu6", "freundin", limit=20) == []
    assert memory.active_topic("secu6", "freundin") is None


def test_handle_turn_non_affirmative_reply_does_not_delete():
    secrecy.handle_turn("secu7", "freundin", "Das bleibt unter uns, ja?")
    secrecy.handle_turn("secu7", "freundin", "Heinrich")
    memory.add_message("secu7", "freundin", "user", "vertraulich", topic="Heinrich")
    secrecy.handle_turn(
        "secu7", "freundin", "Bitte lösche alles, was ich dir dazu erzählt habe."
    )

    secrecy.handle_turn("secu7", "freundin", "Nein, doch nicht.")
    assert len(memory.recent_messages("secu7", "freundin", limit=20)) == 1


def test_handle_turn_delete_now_without_active_topic_asks_for_clarification():
    outcome = secrecy.handle_turn(
        "secu8", "freundin", "Bitte lösche alles, was ich dir dazu erzählt habe."
    )
    assert any("welches" in line.lower() or "thema" in line.lower() for line in outcome.system_context)


def test_handle_turn_delete_on_death_files_directive_without_deleting():
    secrecy.handle_turn("secu9", "freundin", "Das bleibt unter uns, ja?")
    secrecy.handle_turn("secu9", "freundin", "Heinrich")
    memory.add_message("secu9", "freundin", "user", "vertraulich", topic="Heinrich")

    outcome = secrecy.handle_turn(
        "secu9", "freundin",
        "Im Falle meines Todes, bitte lösche alles was ich dir zu Heinrich erzählt habe.",
    )
    assert any("Heinrich" in line for line in outcome.system_context)
    assert len(memory.recent_messages("secu9", "freundin", limit=20)) == 1  # nicht geloescht
    pending = memory.pending_directives("secu9")
    assert len(pending) == 1
    assert pending[0]["topic_label"] == "Heinrich"


def test_handle_turn_death_directive_closes_the_topic():
    secrecy.handle_turn("secu10", "freundin", "Das bleibt unter uns, ja?")
    secrecy.handle_turn("secu10", "freundin", "Heinrich")
    secrecy.handle_turn(
        "secu10", "freundin",
        "Im Falle meines Todes, bitte lösche alles was ich dir zu Heinrich erzählt habe.",
    )
    assert memory.active_topic("secu10", "freundin") is None


def test_handle_turn_tags_the_filing_message_itself_so_it_gets_swept_up_later():
    """Regression, per echtem Rauchtest gefunden: die Anweisung ('...zu
    Grete erzaehlt habe') selbst darf nicht ungetaggt bleiben, sonst
    wuerde execute_death_directives() sie nie erfassen - der Name
    bliebe fuer immer im Klartext stehen, obwohl die Anweisung
    ausgefuehrt wurde."""
    secrecy.handle_turn("secu12", "freundin", "Das bleibt unter uns, ja?")
    secrecy.handle_turn("secu12", "freundin", "Grete")
    outcome = secrecy.handle_turn(
        "secu12", "freundin",
        "Im Falle meines Todes, bitte lösche alles was ich dir zu Grete erzählt habe.",
    )
    assert outcome.topic_for_tagging == "Grete"

    memory.add_message(
        "secu12", "freundin", "user",
        "Im Falle meines Todes, bitte lösche alles was ich dir zu Grete erzählt habe.",
        topic=outcome.topic_for_tagging,
    )
    memory.execute_death_directives("secu12")
    assert memory.recent_messages("secu12", "freundin", limit=20) == []


def test_handle_turn_death_directive_takes_precedence_over_immediate_delete():
    """Eine kombinierte Formulierung ('Im Falle meines Todes, loesche
    alles') darf NICHT zusaetzlich als Sofort-Loeschung behandelt
    werden - sonst entstuenden zwei widerspruechliche Anweisungen aus
    einer Nachricht."""
    secrecy.handle_turn("secu11", "freundin", "Das bleibt unter uns, ja?")
    secrecy.handle_turn("secu11", "freundin", "Heinrich")

    secrecy.handle_turn(
        "secu11", "freundin",
        "Im Falle meines Todes, bitte lösche alles was ich dir zu Heinrich erzählt habe.",
    )
    with memory.get_db("secu11") as db:
        rows = db.execute("SELECT mode FROM deletion_directives").fetchall()
    assert [r["mode"] for r in rows] == ["on_death"]
