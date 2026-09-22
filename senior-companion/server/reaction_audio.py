"""
Kurze, vorab synthetisierte Reaktionssaetze fuer bestimmte Gespraechs-
situationen (siehe PersonaConfig.reaction_phrases in config.py) - z.B.
wenn eine Persona unterbrochen wird. Bewusst generisch ueber eine
freie "Situation"-Zeichenkette gehalten, nicht auf eine feste Liste
eingeschraenkt, damit spaetere Situationen (Person schweigt, andere
Persona faellt ins Wort, ...) ohne Code-Aenderung hier ergaenzt werden
koennen - nur neue Eintraege in reaction_phrases noetig.

Wichtig fuer den Zweck ("sofort abspielbar, ohne die Unterbrechung
noch laenger zu verzoegern"): pro (Stimme, Satz)-Kombination wird nur
EINMAL pro Prozesslaufzeit synthetisiert, danach kommt die Antwort
aus dem Cache. Bewusst lazy (beim ersten Bedarf) statt eager beim
Start synthetisiert - haelt den Serverstart einfach, auf Kosten einer
etwas langsameren allerersten Unterbrechung pro Person/Stimme.
"""
import random

import speech_client

_cache: dict[tuple[str, str], bytes] = {}


async def get_reaction_audio(persona, situation: str) -> bytes | None:
    """None, wenn diese Persona fuer die gegebene Situation keine
    Reaktionssaetze hinterlegt hat - der Aufrufer soll dann einfach
    still bleiben, kein Fehlerfall."""
    phrases = persona.reaction_phrases.get(situation)
    if not phrases:
        return None
    text = random.choice(phrases)
    key = (persona.voice_id, text)
    if key not in _cache:
        _cache[key] = await speech_client.synthesize(text, persona.voice_id)
    return _cache[key]
