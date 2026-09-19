"""
Der "Regisseur" des Gruppenchats: entscheidet, welche anwesende Persona
antwortet, wenn niemand ausdruecklich angesprochen wurde.

Bewusst kein KI-Aufruf noetig - eine gewichtete Zufallsauswahl reicht:
wer laenger nichts gesagt hat, ist eher wieder dran (fuehlt sich
natuerlicher an als Gleichverteilung, ohne eine eigene Redezeit-
Buchhaltung zu brauchen). Explizite Ansprache gewinnt IMMER.
"""
import random

# Deckelt den Gewichts-Vorteil einer sehr lange stillen Persona, damit
# sie nicht quasi-deterministisch an der Reihe ist.
MAX_WEIGHT_SECONDS = 600


def _weight(persona_id: str, last_active_ts: dict[str, float], now: float) -> float:
    elapsed = now - last_active_ts.get(persona_id, 0.0)
    return min(elapsed, MAX_WEIGHT_SECONDS)


def pick_responder(
    present: list[str],
    addressed: str | None,
    last_active_ts: dict[str, float],
    now: float,
) -> str:
    if addressed is not None:
        return addressed
    if len(present) == 1:
        return present[0]
    weights = [_weight(p, last_active_ts, now) for p in present]
    return random.choices(present, weights=weights, k=1)[0]


DEFLECTION_HINT_PROMPT = (
    "Diese Nachricht koennte an eine andere anwesende Person gerichtet "
    "gewesen sein, nicht ausdruecklich an dich. Wenn du im Kontext den "
    "Eindruck hast, dass eigentlich jemand anderes gemeint war, gib das "
    "kurz und natuerlich zurueck (z.B. eine kurze Bemerkung, die das "
    "Wort an die andere Person weiterreicht), statt inhaltlich zu "
    "antworten. Wenn es dagegen auch gut an dich gerichtet gewesen sein "
    "koennte, antworte ganz normal."
)
