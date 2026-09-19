"""
Zusammenfassungs-Uebergabe: auf ausdruecklichen Wunsch ("Robin, erzaehl
das mal Wallner") fasst die aktuelle Persona ihr bisheriges, NICHT
vertrauliches Gespraech mit der Person kurz zusammen und hinterlegt das
als Hintergrund-Kontext fuer die Ziel-Persona - die es dann beim
naechsten eigenen Zug ganz natuerlich "schon weiss", ohne dass es ihr
extra erzaehlt werden musste.

Bewusst die kleine Schwester des Gruppenchats (room.py/director.py):
KEINE gleichzeitige Anwesenheit noetig, funktioniert auch rein ueber
/ws/chat/{user_id}/{persona_id}. Die eigentliche Verdichtung laeuft
als Low-Priority-Hintergrund-Aufgabe (priority.py) NACH dem sichtbaren
Zug, nicht davor - siehe main.py::maybe_spawn_handoff().

Bewusst .search() statt .match()/satzanfang-verankert wie room.py's
detect_addressed_persona: eine Uebergabe-Bitte muss nicht am
Satzanfang stehen ("Kannst du das mal Robin erzaehlen") - anders als
bei room.py absichtlich, nicht angeglichen lassen.
"""
import re

_AE = "(?:ä|ae)"

# Bewusst OHNE "sag\w*" - zu breit, haette z.B. "sag mal, was hat
# Wallner gesagt?" faelschlich als Uebergabe-Wunsch erkannt.
TELLING_VERBS = rf"(erz{_AE}hl\w*|berichte\w*)"


def _handoff_pattern(name: str) -> re.Pattern:
    # Verb und Name koennen in beiden Reihenfolgen auftreten ("erzaehl
    # das mal Wallner" vs. "kannst du Wallner davon berichten") - daher
    # zwei Alternativen statt einer festen Reihenfolge.
    escaped_name = re.escape(name)
    return re.compile(
        rf"(?:\b{TELLING_VERBS}\b.{{0,20}}\b{escaped_name}\b"
        rf"|\b{escaped_name}\b.{{0,20}}\b{TELLING_VERBS}\b)",
        re.I,
    )


def detect_handoff_target(
    user_text: str, candidates: dict[str, str], exclude_persona_id: str,
) -> str | None:
    """candidates: persona_id -> Anzeigename (z.B. aus config.PERSONAS).
    exclude_persona_id: die Quell-Persona selbst - kann nie ihr eigenes
    Ziel sein. Gibt die persona_id der ERSTEN passenden Kandidatin
    zurueck, sonst None."""
    for persona_id, name in candidates.items():
        if persona_id == exclude_persona_id:
            continue
        if _handoff_pattern(name).search(user_text):
            return persona_id
    return None


ACK_HANDOFF_PROMPT = (
    "Die Person hat dich gebeten, das eben Besprochene an {target_name} "
    "weiterzugeben. Bestaetige das kurz und warm in deinen eigenen "
    "Worten (z.B. dass du es ausrichten wirst), ohne die Zusammenfassung "
    "hier selbst auszuformulieren - das geschieht im Hintergrund."
)

CANNOT_SHARE_PROMPT = (
    "Die Person hat dich gebeten, das Besprochene an {target_name} "
    "weiterzugeben - aber alles, worueber ihr bisher gesprochen habt, "
    "ist vertraulich vermerkt. Erklaere freundlich, dass du das nicht "
    "weitergeben kannst, OHNE den vertraulichen Inhalt selbst noch "
    "einmal zu nennen oder anzudeuten."
)

SUMMARY_SYSTEM_PROMPT = (
    "Du faellst niemandem ins Wort, du bist ein reines "
    "Zusammenfassungs-Werkzeug. Fasse das folgende Gespraech zwischen "
    "einer aelteren Person und ihrer Gespraechspartnerin in 2-3 kurzen, "
    "sachlichen Saetzen zusammen - als Hintergrundwissen fuer eine "
    "ANDERE Person, die an diesem Gespraech nicht beteiligt war. Keine "
    "Anrede, keine Meta-Kommentare, nur die Kernpunkte."
)
