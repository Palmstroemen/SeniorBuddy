"""
Vorausschauende Kettengenerierung fuer Auto-Turns (siehe
docs/ARCHITECTURE.md, Abschnitt "Vision: Kontinuierliches,
vorausschauendes Sprechen"). Diese erste Runde baut bewusst nur eine
LINEARE Kette (Tiefe 1-5, keine Verzweigung) - Verzweigung folgt in
einer spaeteren Runde.

Waehrend eine Persona anwesend und still ist, baut ein Hintergrund-Task
bis zu 5 Kandidatensaetze im Voraus, jeder mit den zuvor gebauten
Stufen als SYNTHETISCHE (nicht echte) Historie - Ollama ist zustandslos
pro Call, wir steuern selbst, welches "Vorwissen" jede Stufe bekommt.
Wird eine Stufe tatsaechlich gebraucht, liegt sie schon fertig da (kein
LLM-Call zum Lieferzeitpunkt).

Kettenzustand ist bewusst rein In-Memory, nicht in memory.py persistiert
(wie room.py/priority.py) - eine noch nicht ausgelieferte Kette ist
spekulativ und wird erst beim tatsaechlichen Aussprechen zu einer
echten Konversations-Tatsache (dann ganz normal ueber memory.add_message,
siehe main.py).

Cancellation wird NICHT neu erfunden: _extend_chain/_render_head_audio
laufen als ganz normale Low-Priority-Tasks (priority.py) - jeder echte
Senior-Stream canceled sie automatisch. _chains/generation-Buchhaltung
ist das Einzige, was priority.py nicht von sich aus erledigt, siehe
discard_chain().
"""
import asyncio
import dataclasses
import logging

import autoturn
import config
import llm_client
import memory
import priority
import speech_client

log = logging.getLogger("lookahead")

# Wie viele Stufen eine Kette maximal vorausbaut - eigener Name statt
# eines wiederholten Literals, u.a. fuer debug_state() unten (Live-KPI-
# Anzeige waehrend der Entwicklung, siehe main.py's
# /api/lookahead-debug/{user_id}).
TARGET_DEPTH = 5


@dataclasses.dataclass
class ChainLevel:
    depth: int                 # 1..5, fest ab Erzeugung (fuer Stats), nicht umnummeriert
    kind: str                  # "continue" | "continue_new_topic"
    text: str
    audio: bytes | None = None  # nur fuer den aktuellen Head belegt


@dataclasses.dataclass
class Chain:
    user_id: str
    persona_id: str
    levels: list = dataclasses.field(default_factory=list)  # list[ChainLevel], depth-sortiert, Head zuerst
    build_task: asyncio.Task | None = None
    audio_task: asyncio.Task | None = None
    # Bei jedem Discard/Consume hochgezaehlt - verhindert, dass ein
    # gerade abgebrochener/ueberholter Hintergrund-Task noch in eine
    # neue/verkuerzte Liste hineinschreibt (siehe _extend_chain/
    # _render_head_audio, die vor jedem Schreibzugriff neu pruefen).
    generation: int = 0


_chains: dict = {}          # user_id -> Chain, hoechstens eine pro Nutzer:in
_audio_cache: dict = {}     # (voice_id, text) -> wav bytes, nur fuer den jeweils aktuellen Head

_levels_built = {d: 0 for d in range(1, 6)}      # Schluessel = Tiefe bei Erzeugung
_levels_delivered = {d: 0 for d in range(1, 6)}  # Schluessel = urspruengliche Tiefe
_discarded_interrupted = 0   # echte Nutzer-Nachricht kam dazwischen
_discarded_suppressed = 0    # Kandidat war beim Konsum zu aehnlich zu eigener juengster Historie
_discarded_stale = 0         # Kette nie erreicht (Wrapup / Persona nicht mehr anwesend)


def _spawn_build_task(user_id: str, chain: Chain) -> None:
    task = asyncio.create_task(_extend_chain(user_id, chain.generation))
    priority.register_low_priority_task(task)
    task.add_done_callback(priority.unregister_low_priority_task)
    chain.build_task = task


def _spawn_audio_task(user_id: str, chain: Chain) -> None:
    task = asyncio.create_task(_render_head_audio(user_id, chain.generation))
    priority.register_low_priority_task(task)
    task.add_done_callback(priority.unregister_low_priority_task)
    chain.audio_task = task


def start_chain_for_speaker(user_id: str, persona) -> None:
    """Aufruf direkt nachdem run_turn()/run_auto_turn() ihre eigene
    "done"-Nachricht gesendet haben (siehe main.py) - macht das System
    self-sustaining. Eine evtl. vorhandene Kette fuer eine ANDERE
    Persona gilt als unterbrochen; eine fuer dieselbe Persona (kommt in
    der Praxis kaum vor, siehe main.py-Integration) wird still ersetzt,
    ohne einen Discard-Grund zu zaehlen."""
    existing = _chains.get(user_id)
    if existing is not None:
        reason = "interrupted" if existing.persona_id != persona.id else None
        discard_chain(user_id, reason=reason)

    chain = Chain(user_id=user_id, persona_id=persona.id)
    _chains[user_id] = chain
    _spawn_build_task(user_id, chain)


async def _extend_chain(user_id: str, generation: int) -> None:
    """Haengt Stufe fuer Stufe an, bis die Kette Tiefe 5 erreicht hat
    oder sie unterwegs verworfen/ueberholt wird (generation-Mismatch).
    Baut die Historie fuer jede Stufe aus der ECHTEN juengsten Historie
    PLUS den bereits gebauten Kettenstufen als hypothetische
    Assistant-Turns - der "Vorwissen selbst steuern"-Trick, der Ollama
    (zustandslos pro Call) nicht verwirrt und spaeter direkt auf
    Verzweigung uebertragbar ist."""
    global _levels_built
    try:
        while True:
            chain = _chains.get(user_id)
            if chain is None or chain.generation != generation:
                return
            if len(chain.levels) >= TARGET_DEPTH:
                return

            depth = len(chain.levels) + 1
            kind = "continue" if depth == 1 else "continue_new_topic"
            persona = config.PERSONAS.get(chain.persona_id)
            if persona is None:
                return

            history = memory.recent_messages(user_id, chain.persona_id, limit=20)
            chat_messages = [
                {"role": m["role"], "content": m["content"]} for m in history
            ]
            for lvl in chain.levels:
                chat_messages.append({"role": "assistant", "content": lvl.text})
            chat_messages.append({
                "role": "system", "content": autoturn.AUTO_TURN_PROMPTS[kind],
            })

            tokens: list = []
            async for token in llm_client.stream(
                persona.model, persona.system_prompt, chat_messages,
                max_tokens=persona.max_tokens,
            ):
                tokens.append(token)
            full_response = "".join(tokens)

            # Chain kann sich waehrend des await-Aufrufs oben veraendert
            # haben (verworfen, oder eine ganz neue Kette fuer denselben
            # user_id gestartet) - vor dem Schreiben erneut pruefen.
            chain = _chains.get(user_id)
            if chain is None or chain.generation != generation:
                return

            chain.levels.append(ChainLevel(depth=depth, kind=kind, text=full_response))
            _levels_built[depth] = _levels_built.get(depth, 0) + 1

            if len(chain.levels) == 1:
                _spawn_audio_task(user_id, chain)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Best-effort im Hintergrund - ein Modell-/Netzwerk-Ausfall
        # soll nicht stillschweigend als "Task exception was never
        # retrieved" verschwinden (gleiches Muster wie
        # main.py::_run_handoff_summary).
        log.warning("Ketten-Aufbau fehlgeschlagen fuer user_id=%s.", user_id, exc_info=True)


async def _render_head_audio(user_id: str, generation: int) -> None:
    """Rendert NUR den ersten Satz des aktuellen Heads vor (die
    Standard-Chunk-Grenze, die der Client ohnehin zuerst anfragt) und
    legt das Ergebnis in _audio_cache ab, damit /api/tts (main.py) es
    findet, statt neu zu synthetisieren."""
    chain = _chains.get(user_id)
    if chain is None or chain.generation != generation or not chain.levels:
        return
    persona = config.PERSONAS.get(chain.persona_id)
    if persona is None:
        return
    head = chain.levels[0]
    first_sentence = autoturn._first_sentence(head.text)

    try:
        audio = await speech_client.synthesize(first_sentence, persona.voice_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Best-effort im Hintergrund (gleiches Muster wie
        # _extend_chain oben) - ein Ausfall des Sprachdienstes darf
        # das Vorrendern nur wegfallen lassen, /api/tts synthetisiert
        # bei einem Cache-Miss ohnehin ganz normal live nach.
        log.warning("Audio-Vorrendern fehlgeschlagen fuer user_id=%s.", user_id, exc_info=True)
        return

    chain = _chains.get(user_id)
    if (
        chain is None or chain.generation != generation
        or not chain.levels or chain.levels[0] is not head
    ):
        return
    head.audio = audio
    _audio_cache[(persona.voice_id, first_sentence)] = audio


def consume_head(user_id: str, persona_id: str):
    """Aufruf aus room_chat()'s "continue_eligible"-Zweig VOR dem
    reaktiven run_auto_turn()-Fallback. Gibt None zurueck (unveraenderter
    reaktiver Pfad), wenn keine passende, nicht-leere Kette bereitsteht.
    Prueft jede Stufe FRISCH auf Aehnlichkeit zur eigenen juengsten
    Historie (die kann seit dem Bauen gewachsen sein) und ueberspringt
    unterdrueckte Stufen, bevor sie zurueckgegeben wird."""
    global _discarded_suppressed
    chain = _chains.get(user_id)
    if chain is None or chain.persona_id != persona_id:
        return None

    head = None
    while chain.levels:
        candidate = chain.levels[0]
        if autoturn.too_similar_to_own_recent(user_id, persona_id, candidate.text):
            chain.levels.pop(0)
            _discarded_suppressed += 1
            continue
        head = chain.levels.pop(0)
        break

    if head is None:
        return None

    _levels_delivered[head.depth] = _levels_delivered.get(head.depth, 0) + 1
    chain.generation += 1
    if chain.build_task is not None and not chain.build_task.done():
        chain.build_task.cancel()
    if chain.audio_task is not None and not chain.audio_task.done():
        chain.audio_task.cancel()

    _spawn_build_task(user_id, chain)
    if chain.levels:
        _spawn_audio_task(user_id, chain)

    return head


def discard_chain(user_id: str, reason: str | None = None) -> None:
    """reason = "interrupted" | "stale" | None (stiller Ersatz ohne
    Zaehlung, siehe start_chain_for_speaker). Sicherer No-Op, wenn
    keine Kette existiert - das ist der Normalfall bei den meisten
    echten Nachrichten."""
    global _discarded_interrupted, _discarded_stale
    chain = _chains.pop(user_id, None)
    if chain is None:
        return

    chain.generation += 1
    if chain.build_task is not None and not chain.build_task.done():
        chain.build_task.cancel()
    if chain.audio_task is not None and not chain.audio_task.done():
        chain.audio_task.cancel()

    if reason == "interrupted":
        _discarded_interrupted += 1
    elif reason == "stale":
        _discarded_stale += 1


def chain_persona_id(user_id: str) -> str | None:
    """Fuer room_chat()'s "quiet"-Zweig (main.py): erlaubt zu pruefen,
    ob eine bestehende Kette zu einer Persona gehoert, die nicht mehr
    anwesend ist, ohne dass main.py direkt in _chains greifen muss."""
    chain = _chains.get(user_id)
    return chain.persona_id if chain is not None else None


def debug_state(user_id: str) -> dict | None:
    """Fuer main.py's GET /api/lookahead-debug/{user_id} - eine sehr
    knappe Live-KPI-Anzeige waehrend der Entwicklung (Session-Notiz
    2026-09-23: bewusst nur Zahlen, kein Baum, keine Kandidaten-Texte -
    K.I.S.S., ausdruecklich als temporaeres Entwicklungs-Werkzeug
    gedacht, spaeter wieder entfernen/verstecken). None, wenn gerade
    keine Kette fuer diese Person existiert."""
    chain = _chains.get(user_id)
    if chain is None:
        return None
    head = chain.levels[0] if chain.levels else None
    return {
        "persona_id": chain.persona_id,
        "levels_built": len(chain.levels),
        "target_depth": TARGET_DEPTH,
        "head_has_audio": head.audio is not None if head else False,
    }


def stats() -> dict:
    """Fuer /admin/stats (main.py) - delivery_rate_by_depth ist die
    fuer die "lohnt sich Tiefe 5?"-Entscheidung direkt relevante,
    vorberechnete Zahl."""
    delivery_rate = {}
    for depth in range(1, 6):
        built = _levels_built.get(depth, 0)
        delivered = _levels_delivered.get(depth, 0)
        delivery_rate[str(depth)] = round(delivered / built, 2) if built else 0.0
    return {
        "levels_built_by_depth": {str(d): n for d, n in _levels_built.items()},
        "levels_delivered_by_depth": {str(d): n for d, n in _levels_delivered.items()},
        "delivery_rate_by_depth": delivery_rate,
        "discarded_interrupted": _discarded_interrupted,
        "discarded_suppressed": _discarded_suppressed,
        "discarded_stale": _discarded_stale,
        "active_chains": len(_chains),
    }
