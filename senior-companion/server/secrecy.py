"""
Sicheres Löschen auf Wunsch + Todesfall-gebundene Löschanweisungen.

Ein vertrauliches Thema wird NIE aus dem Gesprächsverlauf erraten
(weder per Stichwortsuche noch per LLM) - bei einer unwiderruflichen
Aktion sind sowohl falsch-negative (Geheimnis bleibt stehen) als auch
falsch-positive Treffer (Falsches wird gelöscht) inakzeptabel.
Stattdessen wird ein Thema aktiv benannt: die Persona fragt einmal
nach ("Wie sollen wir das nennen?"), memory.py fängt die Antwort im
nächsten Zug ein (gleiches Zeitfenster-Muster wie satisfaction.py).

Eine "Jetzt löschen"-Anfrage wird ebenfalls nicht sofort ausgeführt,
sondern erst nach einer einmaligen Bestätigung ("Ja") - bewusst anders
als die (reversible) Du/Sie-Umschaltung in analysis.py, weil eine
falsch erkannte Regel hier eine permanente, nicht rückgängig machbare
Aktion auslösen würde.

handle_turn() bündelt die gesamte Logik, damit main.py's Chat-Handler
nicht aufgebläht wird (gleicher Gedanke wie plugins/dispatch.py für
Plugins).
"""
import re
from dataclasses import dataclass, field

import memory
from analysis import Rule

# Deutsche Umlaute werden oft transliteriert getippt/diktiert (ae/oe/ue
# statt ä/ö/ü) - jedes Regel-Muster unten nutzt diese Fragmente statt
# einzelner Zeichenklassen, damit beide Schreibweisen greifen.
_AE = "(?:ä|ae)"
_OE = "(?:ö|oe)"
_UE = "(?:ü|ue)"
_SS = "(?:ß|ss)"

CONFIDENTIAL_RULES: list[Rule] = [
    Rule("bleibt_unter_uns", re.compile(r"bleibt\b.{0,15}unter\s+uns", re.I)),
    Rule("ist_ein_geheimnis", re.compile(r"ist\s+(ein\s+|mein\s+)?geheimnis", re.I)),
    Rule("erzaehl_niemandem", re.compile(rf"erz{_AE}hl\w*\s+niemandem", re.I)),
    Rule("niemand_erfahren", re.compile(r"niemand\s+erfahren", re.I)),
    Rule(
        "fuer_dich_behalten",
        re.compile(rf"(behalte|beh{_AE}ltst)\s+(das\s+)?f{_UE}r\s+dich", re.I),
    ),
    Rule("niemand_wissen", re.compile(r"niemand\s+wissen", re.I)),
]

DELETE_NOW_RULES: list[Rule] = [
    Rule(
        "loesch_erzaehlt_gesagt",
        re.compile(rf"l{_OE}sch\w*.{{0,30}}(erz{_AE}hlt|gesagt)", re.I),
    ),
    Rule(
        "loesch_das_alles",
        re.compile(
            rf"(l{_OE}sch\w*\s+(das|es|alles)\b"
            rf"|\b(das|es|alles)\b.{{0,20}}l{_OE}sch)",
            re.I,
        ),
    ),
    Rule("vergiss_was_das", re.compile(r"vergiss,?\s+(was|das)\s+ich", re.I)),
]

# Ein kombiniertes Muster pro Formulierung (Todes-Praefix UND
# Loesch-Absicht im selben Satz), bewusst NICHT zwei unabhaengig
# matchende Fragmente - sonst koennten zwei getrennte Saetze, die je
# fuer sich Tod bzw. Loeschen erwaehnen, faelschlich kombiniert zuenden.
DELETE_ON_DEATH_RULES: list[Rule] = [
    Rule(
        "todesfall_loeschen",
        re.compile(
            rf"(falls?\s+(ich|mir)\b.{{0,25}}(sterbe|tod\b|stirbt|zust{_OE}{_SS}t)"
            rf"|nach\s+meinem\s+tod"
            rf"|im\s+falle\s+meines\s+todes"
            rf"|wenn\s+ich\s+sterbe).{{0,100}}l{_OE}sch",
            re.I | re.S,
        ),
    ),
]

AFFIRMATIVE_RULES: list[Rule] = [
    Rule("ja_allein", re.compile(r"^\s*ja[.!]?\s*$", re.I)),
    Rule("ja_loeschen", re.compile(rf"\bja\b.{{0,20}}l{_OE}sch", re.I)),
    Rule("ja_bitte", re.compile(r"^\s*ja\b.{0,10}bitte", re.I)),
    Rule("ja_mach_das", re.compile(r"\bja\b.{0,10}mach\s+das", re.I)),
]


def detect_confidential_signal(text: str) -> bool:
    return any(rule.pattern.search(text) for rule in CONFIDENTIAL_RULES)


def detect_delete_now(text: str) -> bool:
    return any(rule.pattern.search(text) for rule in DELETE_NOW_RULES)


def detect_delete_on_death(text: str) -> bool:
    return any(rule.pattern.search(text) for rule in DELETE_ON_DEATH_RULES)


def detect_affirmative(text: str) -> bool:
    return any(rule.pattern.search(text) for rule in AFFIRMATIVE_RULES)


NAME_TOPIC_PROMPT = (
    "Die Person hat gerade angedeutet, dass sie dir etwas Vertrauliches "
    "erzaehlen moechte. Frage einfuehlsam und in deinen eigenen Worten, "
    "wie ihr dieses Thema kurz nennen wollt, damit ihr spaeter darauf "
    "zurueckkommen oder es auf Wunsch loeschen koennt."
)

TOPIC_NAMED_PROMPT = (
    "Das Thema '{label}' ist jetzt vermerkt. Bestaetige das kurz und "
    "warm, dann kann die Person frei weitererzaehlen."
)

CONFIRM_DELETION_PROMPT_TEMPLATE = (
    "Die Person moechte alles zum Thema '{label}' loeschen. Frage "
    "einmal ausdruecklich und einfuehlsam nach, ob das wirklich "
    "unwiderruflich geloescht werden soll, und warte auf eine klare "
    "Antwort, bevor irgendetwas passiert. Wiederhole dabei NICHT den "
    "eigentlichen Inhalt des Themas, nur den Namen."
)

# Bewusst OHNE den Themen-Namen/Label im Prompt-Text: die Loeschung
# ist zu diesem Zeitpunkt bereits real geschehen (Nachrichten UND der
# Name selbst sind aus der Datenbank entfernt, siehe
# memory.confirm_pending_deletion()) - wuerde die KI den Namen hier
# trotzdem erwaehnen, wuerde ihre eigene (neue, ungetaggte) Antwort ihn
# wieder in die Datenbank zurueckschreiben und die Loeschung damit
# faktisch unterlaufen. Gefunden per echtem Rauchtest mit einem
# kleinen lokalen Modell, das den Namen in seiner Bestaetigung erneut
# nannte.
DELETION_DONE_PROMPT = (
    "Das besprochene vertrauliche Thema wurde soeben unwiderruflich "
    "geloescht - auch der Name, den ihr dafuer verwendet habt, existiert "
    "jetzt nirgends mehr. Bestaetige das ruhig und warm, OHNE den Namen "
    "oder Inhalt des Themas noch einmal zu nennen."
)

NO_ACTIVE_TOPIC_PROMPT = (
    "Die Person moechte etwas loeschen, aber es ist gerade kein "
    "vermerktes Thema aktiv. Frage ehrlich nach, was genau gemeint ist."
)

DEATH_DIRECTIVE_FILED_PROMPT_TEMPLATE = (
    "Die Anweisung, im Todesfall alles zum Thema '{label}' zu "
    "loeschen, wurde vermerkt - sie wird erst dann ausgefuehrt. "
    "Bestaetige das der Person ruhig. Wiederhole dabei NICHT den "
    "eigentlichen Inhalt des Themas, nur den Namen."
)


@dataclass
class SecrecyOutcome:
    topic_for_tagging: str | None = None
    system_context: list[str] = field(default_factory=list)


def handle_turn(user_id: str, persona_id: str, user_text: str) -> SecrecyOutcome:
    system_context: list[str] = []

    # 1) Themen-Namen einfangen, falls eine Namensfrage offen ist.
    captured = memory.capture_topic_label(user_id, persona_id, user_text.strip())
    if captured:
        label = memory.active_topic(user_id, persona_id)
        system_context.append(TOPIC_NAMED_PROMPT.format(label=label))

    # 2) Loesch-Bestaetigung einfangen, falls offen und die Antwort
    #    affirmativ ist (fuehrt bei Erfolg die eigentliche Loeschung
    #    direkt aus - memory.confirm_pending_deletion() macht das).
    if not captured and detect_affirmative(user_text):
        deleted_label = memory.confirm_pending_deletion(user_id, persona_id)
        if deleted_label:
            system_context.append(DELETION_DONE_PROMPT)

    # 3) Neues Vertraulichkeits-Signal -> Thema oeffnen (nur wenn noch
    #    keins offen/pending ist).
    if not captured and detect_confidential_signal(user_text):
        if memory.active_topic(user_id, persona_id) is None:
            memory.open_pending_topic(user_id, persona_id)
            system_context.append(NAME_TOPIC_PROMPT)

    # 4) Aktuelles Thema VOR einer moeglichen Schliessung ermitteln -
    #    damit die Nachrichten DIESES Turns (die Anweisung selbst UND
    #    die Antwort der Persona darauf) mit dem Thema getaggt werden,
    #    auch wenn es im selben Zug per Todesfall-/Sofort-Anweisung
    #    geschlossen wird. Sonst bliebe z.B. "...zu Grete erzaehlt
    #    habe" selbst ungetaggt und damit fuer immer ungeloescht in
    #    der Datenbank stehen, selbst nachdem die Anweisung spaeter
    #    ausgefuehrt wird - gefunden per echtem Rauchtest.
    topic_this_turn = memory.active_topic(user_id, persona_id)

    # 5) Todesfall-Anfrage zuerst pruefen (spezifischer als eine
    #    einfache Jetzt-loeschen-Anfrage) - "Im Falle meines Todes,
    #    loesche..." darf nicht ZUSAETZLICH als Sofort-Loeschung
    #    behandelt werden.
    if detect_delete_on_death(user_text):
        if topic_this_turn:
            memory.record_deletion_directive(
                user_id, persona_id, topic_this_turn, user_text, mode="on_death"
            )
            memory.close_topic(user_id, persona_id, topic_this_turn)
            system_context.append(
                DEATH_DIRECTIVE_FILED_PROMPT_TEMPLATE.format(label=topic_this_turn)
            )
        else:
            system_context.append(NO_ACTIVE_TOPIC_PROMPT)

    # 6) Jetzt-loeschen-Anfrage (nur wenn nicht schon als Todesfall
    #    behandelt).
    elif detect_delete_now(user_text):
        if topic_this_turn:
            memory.record_deletion_directive(
                user_id, persona_id, topic_this_turn, user_text, mode="pending_confirmation"
            )
            system_context.append(
                CONFIRM_DELETION_PROMPT_TEMPLATE.format(label=topic_this_turn)
            )
        else:
            system_context.append(NO_ACTIVE_TOPIC_PROMPT)

    return SecrecyOutcome(topic_for_tagging=topic_this_turn, system_context=system_context)
