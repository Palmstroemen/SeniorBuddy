"""
Sammelt, wie lange Nutzer:innen zwischen zwei erkannten Sprachphrasen
pausieren (client-seitig ueber die Web Speech API gemessen, siehe
app.js) - reine Beobachtungsdaten fuer eine spaetere Kalibrierung der
Dauer-Zuhoeren-Funktion (siehe /ws/room, Session-Notiz 2026-09-22):
lange, natuerliche Sprechpausen (z.B. bei aelteren, langsam sprechenden
Nutzer:innen) sollen irgendwann nicht mehr faelschlich als "fertig
gesprochen" gewertet werden. Fuer diese Runde bewusst nur Rohdaten
sammeln, noch KEINE automatische Anpassung - erst wenn echte Gespraeche
genug Messwerte geliefert haben, siehe Projektgedaechtnis.
"""

# Deckel gegen unbegrenztes Wachstum - genug fuer eine grobe Verteilung
# (Median/Mittelwert/Max), Prozessspeicher ist bewusst nicht persistiert
# (wie priority.py/honeypot.py - reine Beobachtung, kein Fakt).
MAX_SAMPLES_PER_USER = 200

_pause_samples: dict[str, list[float]] = {}


def record_pause(user_id: str, seconds: float) -> None:
    """Ignoriert negative/unplausible Werte still - der Client filtert
    bereits grob vor (siehe app.js), aber ein defektes Zeitmessung darf
    die Statistik nicht verfaelschen."""
    if seconds < 0:
        return
    samples = _pause_samples.setdefault(user_id, [])
    samples.append(seconds)
    if len(samples) > MAX_SAMPLES_PER_USER:
        del samples[0]


def stats() -> dict:
    """Fuer /admin/stats (main.py) - pro Nutzer:in eine grobe
    Verteilung, damit sich spaeter mit echten Daten beurteilen laesst,
    ob/wie eine Kalibrierung der Dauer-Zuhoeren-Funktion sinnvoll ist."""
    result = {}
    for user_id, samples in _pause_samples.items():
        if not samples:
            continue
        sorted_samples = sorted(samples)
        n = len(sorted_samples)
        result[user_id] = {
            "count": n,
            "avg_seconds": round(sum(sorted_samples) / n, 2),
            "median_seconds": round(sorted_samples[n // 2], 2),
            "max_seconds": round(sorted_samples[-1], 2),
        }
    return result
