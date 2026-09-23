"""
Vorausschauende Kettengenerierung fuer Auto-Turns (siehe
docs/ARCHITECTURE.md, Abschnitt "Vision: Kontinuierliches,
vorausschauendes Sprechen"). Runde 1 baute nur eine LINEARE Kette.
Runde 2 (dieses Modul jetzt) baut einen BAUM: bei einer Ja/Nein- oder
kleinen Auswahl-Frage werden die moeglichen Antwort-Fortsetzungen
vorausschauend mitgebaut, bevor die echte Antwort da ist.

Baum-Datenstruktur bewusst FLACH, nicht verschachtelt (Session-Design-
Entscheidung, siehe Projektgedaechtnis "agenda-driven-autoturn-vision"):
Chain.levels bleibt eine flache list[ChainLevel], jeder Knoten traegt
einen materialisierten Pfad-Tag (`path`, z.B. "1", "2b", "3ba") plus
ein EXPLIZIT gespeichertes `parent_path` (nie aus `path` geparst).
Damit bleiben consume_head(), die generation-Staleness-Pruefung und
die /admin/stats-Zaehler einfache Listen-Iterationen, keine Rekursion
auf dem Container noetig.

Nur der NAECHSTE Verzweigungspunkt bekommt echte Geschwister-Generierung
(_has_unresolved_fork gated) - tiefer liegende hypothetische
Verzweigungen werden diese Runde bewusst NICHT gebaut (siehe Plan
"Lookahead Runde 2"), das haelt die Hintergrundarbeit bei
Ollamas strikter Anfrage-Serialisierung beschraenkt.

Waehrend eine Persona anwesend und still ist, baut ein Hintergrund-Task
bis zu 5 Kandidatenstufen pro Zweig im Voraus, jede mit den zuvor
gebauten Vorfahren (ueber parent_path erlaufen) als SYNTHETISCHE
(nicht echte) Historie - Ollama ist zustandslos pro Call, wir steuern
selbst, welches "Vorwissen" jede Stufe bekommt. Wird eine Stufe
tatsaechlich gebraucht, liegt sie schon fertig da (kein LLM-Call zum
Lieferzeitpunkt).

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

# Wie viele Stufen ein einzelner Zweig maximal vorausbaut - eigener
# Name statt eines wiederholten Literals, u.a. fuer debug_state()
# unten (Live-KPI-Anzeige waehrend der Entwicklung, siehe main.py's
# /api/lookahead-debug/{user_id}).
TARGET_DEPTH = 5

# Erst ab dieser Anzahl an Saetzen seit der letzten Verzweigung (oder
# seit Kettenbeginn) darf ein Knoten ueberhaupt verzweigen - Backstop
# fuer die "3-4 Saetze, dann eine Frage"-Erzaehl-Kadenz, unabhaengig
# davon, wie gut sich das jeweilige Modell an BRANCH_TAG_INSTRUCTION
# haelt (gleiches Prinzip wie autoturn.AUTO_TURN_SIMILARITY_THRESHOLD:
# ein deterministisches Sicherheitsnetz, kein Vertrauen ins Modell
# allein).
FORK_MIN_SENTENCES = 2

# Reservierter trigger_condition-Wert fuer den Auffangzweig - per
# Identitaet geprueft (siehe _match_trigger), nie durch Substring-
# Vergleich getroffen.
CATCHALL_TRIGGER = "*"

_FORK_LETTERS = "abcdefghijklmnopqrstuvwxyz"

# Nur fuer "ja"/"nein" konsultiert (siehe _match_trigger) - Auswahl-
# Trigger wie "politik"/"musik" bleiben reiner Substring-Vergleich, da
# das woertlich die Worte sind, die die Persona selbst angeboten hat.
_YES_NO_SYNONYMS = {
    "ja": ["ja", "klar", "gerne", "genau", "na sicher", "natuerlich", "jep", "jup"],
    "nein": ["nein", "nö", "eher nicht", "lieber nicht", "auf keinen fall"],
}


@dataclasses.dataclass
class ChainLevel:
    depth: int                     # 1..TARGET_DEPTH pro Zweig, fest ab Erzeugung
    kind: str                      # "continue" | "continue_new_topic"
    text: str
    path: str = "1"                # materialisierter Pfad-Tag, z.B. "1", "2b", "3ba"
    parent_path: str | None = None  # explizit, NIE aus path geparst; None nur fuer die Wurzel
    branch_kind: str = "linear"    # "linear" | "fork_root" | "fork_option" | "fork_catchall"
    trigger_condition: str | None = None  # welche echte Antwort DIESEN Knoten bestaetigt
    status: str = "ready"          # "building" (Platzhalter, Text fehlt noch) | "ready"
    audio_ready: bool = False      # Bytes selbst liegen in _audio_cache, nicht hier
    sentences_since_last_fork: int = 0


@dataclasses.dataclass
class Chain:
    user_id: str
    persona_id: str
    levels: list = dataclasses.field(default_factory=list)  # list[ChainLevel], flach
    cursor_path: str | None = None  # Pfad des zuletzt BESTAETIGTEN Knotens; None = noch nichts bestaetigt
    build_task: asyncio.Task | None = None
    audio_task: asyncio.Task | None = None
    # Bei jedem Discard/Consume hochgezaehlt - verhindert, dass ein
    # gerade abgebrochener/ueberholter Hintergrund-Task noch in eine
    # neue/verkuerzte Liste hineinschreibt (siehe _extend_chain/
    # _render_head_audio, die vor jedem Schreibzugriff neu pruefen).
    generation: int = 0


class _VirtualRoot:
    """Steht fuer "noch kein Knoten existiert" - _find_extendable_leaf
    gibt dies zurueck, wenn chain.levels leer ist, damit die
    Wurzel-Erzeugung (Tiefe 1) denselben Code-Pfad wie jede andere
    Blatt-Erweiterung durchlaeuft, statt ein separater Sonderfall zu
    sein."""
    depth = 0
    path = None
    branch_kind = "linear"
    sentences_since_last_fork = 0


_VIRTUAL_ROOT = _VirtualRoot()


_chains: dict = {}          # user_id -> Chain, hoechstens eine pro Nutzer:in
_audio_cache: dict = {}     # (voice_id, text) -> wav bytes, nur fuer den jeweils aktuellen Head

_levels_built = {d: 0 for d in range(1, 6)}      # Schluessel = Tiefe bei Erzeugung
_levels_delivered = {d: 0 for d in range(1, 6)}  # Schluessel = urspruengliche Tiefe
_discarded_interrupted = 0   # echte Nutzer-Nachricht kam dazwischen
_discarded_suppressed = 0    # Kandidat war beim Konsum zu aehnlich zu eigener juengster Historie
_discarded_stale = 0         # Kette nie erreicht (Wrapup / Persona nicht mehr anwesend)

# Verzweigungs-spezifische Zaehler (Runde 2) - separat von den
# obigen Discard-Zaehlern, die kettenweit sind; diese hier sind
# knotenweit (siehe docs/ARCHITECTURE.md "Verwerfen ist ein
# akzeptierter Preis").
_forks_offered = 0          # wie oft ueberhaupt ein fork_root gebaut wurde
_forks_confirmed = 0        # wie oft eine echte Antwort zu einer fork_option passte
_forks_catchall_taken = 0   # wie oft keine Option passte (Auffangzweig-Fall)
_nodes_pruned_on_confirm = 0  # wie viele Geschwister-Knoten (+ Nachkommen) beim Bestaetigen verworfen wurden


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


def _find_node(chain: Chain, path: str | None):
    if path is None:
        return None
    return next((lvl for lvl in chain.levels if lvl.path == path), None)


def _strip_leading_digits(path: str) -> str:
    return path.lstrip("0123456789")


def _has_unresolved_fork(chain: Chain) -> bool:
    """True, solange irgendwo im Baum ein noch nicht aufgeloester
    Verzweigungspunkt existiert (fork_root wartet auf Auslieferung
    ODER wurde schon ausgeliefert/gefragt und wartet noch auf die
    echte Antwort, in dem Fall existiert fork_root selbst nicht mehr
    in chain.levels - siehe _prune_siblings_and_confirm -, aber seine
    fork_option/fork_catchall-Kinder schon). Solange True, darf KEIN
    zweiter Knoten irgendwo im Baum verzweigen - "nur der naechste
    Verzweigungspunkt", siehe Modul-Docstring."""
    return any(lvl.branch_kind in ("fork_root", "fork_option", "fork_catchall") for lvl in chain.levels)


def _head_child(chain: Chain):
    """Der eine Knoten, der als naechstes ausgeliefert wuerde (Mode A)
    - das direkte Kind des Cursors, sofern eines existiert und nicht
    selbst ein noch unentschiedener fork_option/fork_catchall ist
    (die warten auf eine echte Antwort, siehe consume_head Mode B)."""
    return next(
        (lvl for lvl in chain.levels
         if lvl.parent_path == chain.cursor_path and lvl.branch_kind in ("linear", "fork_root")),
        None,
    )


def _ancestor_texts(chain: Chain, parent_path: str | None) -> list[str]:
    """Vorfahren-Texte in Wurzel-zu-Blatt-Reihenfolge, erlaufen ueber
    parent_path - NICHT ueber "alle Eintraege in chain.levels" (das
    wuerde bei einem Baum den Text eines unverwandten Geschwister-
    Zweigs mit hineinziehen). Bricht sauber ab, sobald ein parent_path
    auf einen bereits bestaetigten/entfernten Knoten zeigt (siehe
    _prune_siblings_and_confirm, das bestaetigte Knoten aus
    chain.levels entfernt) - das ist genau die Stelle, ab der echte
    memory.recent_messages()-Historie uebernimmt."""
    texts: list[str] = []
    current = parent_path
    while current is not None:
        node = _find_node(chain, current)
        if node is None:
            break
        texts.append(node.text)
        current = node.parent_path
    texts.reverse()
    return texts


def _find_pending_placeholder(chain: Chain):
    pending = [lvl for lvl in chain.levels if lvl.status == "building"]
    if not pending:
        return None
    pending.sort(key=lambda lvl: lvl.path)
    return pending[0]


def _find_extendable_leaf(chain: Chain):
    """Das flachste "Blatt", das noch ein naechstes Kind braucht - ein
    fertiger, nicht-Auffangzweig-Knoten unterhalb TARGET_DEPTH ohne
    eigene Kinder. Ein fork_root zaehlt NICHT als Blatt (seine Kinder
    wurden schon beim Entdecken der Verzweigung angelegt, siehe
    _fill_placeholder)."""
    if not chain.levels:
        return _VIRTUAL_ROOT
    parents_with_children = {lvl.parent_path for lvl in chain.levels}
    leaves = [
        lvl for lvl in chain.levels
        if lvl.status == "ready"
        and lvl.branch_kind != "fork_catchall"
        and lvl.depth < TARGET_DEPTH
        and lvl.path not in parents_with_children
    ]
    if not leaves:
        return None
    leaves.sort(key=lambda lvl: (lvl.depth, lvl.path))
    return leaves[0]


def _make_placeholder(leaf) -> ChainLevel:
    suffix = "" if leaf.path is None else _strip_leading_digits(leaf.path)
    new_depth = leaf.depth + 1
    sentences = (
        0 if leaf.branch_kind in ("fork_option", "fork_catchall")
        else leaf.sentences_since_last_fork + 1
    )
    return ChainLevel(
        depth=new_depth,
        kind="continue" if new_depth == 1 else "continue_new_topic",
        text="",
        path=f"{new_depth}{suffix}",
        parent_path=leaf.path,
        branch_kind="linear",
        status="building",
        sentences_since_last_fork=sentences,
    )


def _create_fork_children(fork_root: ChainLevel, options: list[str]) -> list[ChainLevel]:
    suffix = _strip_leading_digits(fork_root.path)
    new_depth = fork_root.depth + 1
    children = []
    for i, option in enumerate(options):
        letter = _FORK_LETTERS[i]
        children.append(ChainLevel(
            depth=new_depth,
            kind="continue_new_topic",
            text="",
            path=f"{new_depth}{suffix}{letter}",
            parent_path=fork_root.path,
            branch_kind="fork_option",
            trigger_condition=option.strip().lower(),
            status="building",
            sentences_since_last_fork=0,
        ))
    catchall_letter = _FORK_LETTERS[len(options)]
    children.append(ChainLevel(
        depth=new_depth,
        kind="continue_new_topic",
        text="",
        path=f"{new_depth}{suffix}{catchall_letter}",
        parent_path=fork_root.path,
        branch_kind="fork_catchall",
        trigger_condition=CATCHALL_TRIGGER,
        status="ready",  # kein LLM-Call - wird nie ausgeliefert, siehe consume_head Mode B
        sentences_since_last_fork=0,
    ))
    return children


def _parse_branch_tag(raw: str) -> tuple[str, list[str]]:
    """Trennt den von BRANCH_TAG_INSTRUCTION angeforderten
    VERZWEIGUNG:/OPTIONEN:-Block vom eigentlichen Text ab - gleiches
    defensives Muster wie sentiment_job._parse_classification: ein
    fehlendes/fehlformatiertes Tag faellt IMMER auf "keine Verzweigung"
    zurueck, nie auf eine erfundene (ein kleines lokales Modell haelt
    sich nicht immer exakt ans vorgegebene Format)."""
    marker_idx = raw.rfind("---")
    if marker_idx == -1:
        return raw.strip(), []

    clean = raw[:marker_idx].strip()
    tag_block = raw[marker_idx:]

    branch = "keine"
    options: list[str] = []
    for line in tag_block.splitlines():
        line = line.strip()
        lower = line.lower()
        if lower.startswith("verzweigung:"):
            value = line.split(":", 1)[1].strip().lower()
            if value in ("ja", "keine"):
                branch = value
        elif lower.startswith("optionen:"):
            raw_options = line.split(":", 1)[1]
            options = [opt.strip() for opt in raw_options.split("|") if opt.strip()]

    if branch != "ja" or len(options) < 2:
        return clean, []
    return clean, options


def _match_trigger(reply: str, trigger: str) -> bool:
    reply_lower = reply.lower()
    if trigger in _YES_NO_SYNONYMS:
        return any(word in reply_lower for word in _YES_NO_SYNONYMS[trigger])
    return trigger in reply_lower


async def _fill_placeholder(user_id: str, generation: int, node: ChainLevel, persona) -> None:
    """Fuellt EINEN vorher angelegten "building"-Platzhalter mit
    echtem Text (ein LLM-Call). Wird sowohl fuer normale Blatt-
    Erweiterung als auch fuer eine einzelne fork_option verwendet -
    der Unterschied liegt nur im letzten System-Prompt (siehe unten)."""
    global _levels_built, _forks_offered
    chain = _chains.get(user_id)
    if chain is None or chain.generation != generation:
        return

    ancestor_texts = _ancestor_texts(chain, node.parent_path)
    history = memory.recent_messages(user_id, chain.persona_id, limit=20)
    chat_messages = [{"role": m["role"], "content": m["content"]} for m in history]
    for text in ancestor_texts:
        chat_messages.append({"role": "assistant", "content": text})

    is_fork_option = node.branch_kind == "fork_option"
    allow_fork = False
    if is_fork_option:
        chat_messages.append({
            "role": "system",
            "content": autoturn.BRANCH_ANSWER_CONTINUATION_PROMPT.format(option=node.trigger_condition),
        })
    else:
        chat_messages.append({"role": "system", "content": autoturn.AUTO_TURN_PROMPTS[node.kind]})
        allow_fork = (
            not _has_unresolved_fork(chain)
            and node.sentences_since_last_fork >= FORK_MIN_SENTENCES
        )
        if allow_fork:
            chat_messages.append({"role": "system", "content": autoturn.BRANCH_TAG_INSTRUCTION})

    tokens: list = []
    async for token in llm_client.stream(
        persona.model, persona.system_prompt, chat_messages,
        max_tokens=persona.max_tokens,
    ):
        tokens.append(token)
    full_response = "".join(tokens)

    # Chain kann sich waehrend des await-Aufrufs oben veraendert haben
    # (verworfen, oder eine ganz neue Kette fuer denselben user_id
    # gestartet) - vor dem Schreiben erneut pruefen. Ein bereits
    # eingefuegter, jetzt ueberholter Platzhalter wird entfernt, damit
    # eine abgebrochene Generierung keine sichtbare Spur (leerer
    # "building"-Knoten) hinterlaesst.
    chain = _chains.get(user_id)
    if chain is None:
        return
    if chain.generation != generation:
        stale = _find_node(chain, node.path)
        if stale is not None and stale.status == "building":
            chain.levels.remove(stale)
        return

    live_node = _find_node(chain, node.path)
    if live_node is None or live_node.status != "building":
        return

    if is_fork_option:
        live_node.text = full_response.strip()
        live_node.status = "ready"
    else:
        clean_text, options = _parse_branch_tag(full_response)
        allow_fork = allow_fork and not _has_unresolved_fork(chain)
        if allow_fork and options:
            live_node.text = clean_text
            live_node.status = "ready"
            live_node.branch_kind = "fork_root"
            _forks_offered += 1
            for child in _create_fork_children(live_node, options):
                chain.levels.append(child)
        else:
            live_node.text = clean_text
            live_node.status = "ready"

    _levels_built[live_node.depth] = _levels_built.get(live_node.depth, 0) + 1

    if live_node.parent_path == chain.cursor_path:
        _spawn_audio_task(user_id, chain)


async def _extend_chain(user_id: str, generation: int) -> None:
    """Baut Platzhalter fuer Platzhalter, bis kein Blatt mehr
    Erweiterung braucht (jeder aktive Zweig hat TARGET_DEPTH erreicht,
    oder ist ein noch unbestaetigter fork_catchall, der nie weiter
    ausgebaut wird) - oder die Kette unterwegs verworfen/ueberholt
    wird (generation-Mismatch)."""
    try:
        while True:
            chain = _chains.get(user_id)
            if chain is None or chain.generation != generation:
                return
            persona = config.PERSONAS.get(chain.persona_id)
            if persona is None:
                return

            pending = _find_pending_placeholder(chain)
            if pending is None:
                leaf = _find_extendable_leaf(chain)
                if leaf is None:
                    return
                pending = _make_placeholder(leaf)
                chain.levels.append(pending)

            await _fill_placeholder(user_id, generation, pending, persona)
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
    if chain is None or chain.generation != generation:
        return
    head = _head_child(chain)
    if head is None:
        return
    persona = config.PERSONAS.get(chain.persona_id)
    if persona is None:
        return
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
    if chain is None or chain.generation != generation:
        return
    head_now = _head_child(chain)
    if head_now is None or head_now.path != head.path:
        return
    head_now.audio_ready = True
    _audio_cache[(persona.voice_id, first_sentence)] = audio


def _prune_siblings_and_confirm(chain: Chain, confirmed: ChainLevel) -> None:
    """Bestaetigt `confirmed` als neuen Cursor: entfernt ihn selbst aus
    chain.levels (wie Runde 1's chain.levels.pop(0) - ab jetzt zaehlt
    er als ausgeliefert, nicht mehr als Kandidat) sowie ALLE
    Geschwister (gleicher parent_path, anderer path) UND deren
    Nachkommen (bei dieser Runde hoechstens eine Ebene, da tiefere
    Verzweigungen nie gebaut werden). generation wird wie gehabt
    hochgezaehlt und laufende Hintergrund-Tasks abgebrochen."""
    global _nodes_pruned_on_confirm
    sibling_paths = {
        lvl.path for lvl in chain.levels
        if lvl.parent_path == confirmed.parent_path and lvl.path != confirmed.path
    }
    to_remove = set(sibling_paths)
    changed = True
    while changed:
        changed = False
        for lvl in chain.levels:
            if lvl.parent_path in to_remove and lvl.path not in to_remove and lvl.path != confirmed.path:
                to_remove.add(lvl.path)
                changed = True

    pruned = 0
    for path in to_remove:
        node = _find_node(chain, path)
        if node is not None:
            chain.levels.remove(node)
            pruned += 1
    _nodes_pruned_on_confirm += pruned

    if confirmed in chain.levels:
        chain.levels.remove(confirmed)

    chain.cursor_path = confirmed.path
    chain.generation += 1
    if chain.build_task is not None and not chain.build_task.done():
        chain.build_task.cancel()
    if chain.audio_task is not None and not chain.audio_task.done():
        chain.audio_task.cancel()


def _respawn_after_confirm(user_id: str, chain: Chain) -> None:
    _spawn_build_task(user_id, chain)
    if _head_child(chain) is not None:
        _spawn_audio_task(user_id, chain)


def _consume_next_ready_child(chain: Chain, user_id: str, persona_id: str):
    """Mode A (siehe consume_head) - liefert das naechste Kind des
    Cursors, unabhaengig davon, ob es ein normaler Fortsetzungssatz
    oder eine fork_root-Frage ist. Prueft jeden Kandidaten FRISCH auf
    Aehnlichkeit zur eigenen juengsten Historie (die kann seit dem
    Bauen gewachsen sein) und laeuft bei Unterdrueckung eine Ebene
    tiefer weiter, bis ein brauchbarer Kandidat gefunden wird oder der
    Zweig endet."""
    global _discarded_suppressed
    current_parent_path = chain.cursor_path
    candidate = None
    while True:
        siblings = [lvl for lvl in chain.levels if lvl.parent_path == current_parent_path]
        next_candidate = next(
            (lvl for lvl in siblings if lvl.branch_kind in ("linear", "fork_root") and lvl.status == "ready"),
            None,
        )
        if next_candidate is None:
            candidate = None
            break
        if autoturn.too_similar_to_own_recent(user_id, persona_id, next_candidate.text):
            _discarded_suppressed += 1
            chain.levels.remove(next_candidate)
            current_parent_path = next_candidate.path
            continue
        candidate = next_candidate
        break

    if candidate is None:
        return None

    _levels_delivered[candidate.depth] = _levels_delivered.get(candidate.depth, 0) + 1
    _prune_siblings_and_confirm(chain, candidate)
    _respawn_after_confirm(user_id, chain)
    return candidate


def _confirm_branch_reply(chain: Chain, user_id: str, reply_text: str):
    """Mode B (siehe consume_head) - der Cursor sitzt gerade an einem
    Knoten, dessen Frage schon gestellt wurde (Mode A hat den
    fork_root bereits ausgeliefert UND aus chain.levels entfernt, siehe
    _prune_siblings_and_confirm - der Cursor-STRING bleibt trotzdem
    gueltig, da Kind-Suche rein ueber parent_path-Strings laeuft, nicht
    ueber das fork_root-Objekt selbst); jetzt ist eine echte Antwort
    da. Erkennt "wartet auf Antwort" daran, dass der Cursor
    fork_option-Kinder hat - kein separater Lookup des (schon
    entfernten) fork_root-Knotens noetig. Passt die Antwort gegen jede
    fork_option's trigger_condition, bestaetigt bei Treffer diesen
    Zweig. Bei keinem Treffer: NICHT den vorgefertigten Auffangzweig-
    Text ausliefern (der kennt die konkrete Antwort nicht) - stattdessen
    None zurueckgeben, main.py verwirft die Kette normal und generiert
    reaktiv MIT der echten Antwort als echter Historie weiter."""
    global _forks_confirmed, _forks_catchall_taken
    options = [
        lvl for lvl in chain.levels
        if lvl.parent_path == chain.cursor_path and lvl.branch_kind == "fork_option"
    ]
    if not options:
        return None

    for option in options:
        if _match_trigger(reply_text, option.trigger_condition):
            _forks_confirmed += 1
            _levels_delivered[option.depth] = _levels_delivered.get(option.depth, 0) + 1
            _prune_siblings_and_confirm(chain, option)
            _respawn_after_confirm(user_id, chain)
            return option

    _forks_catchall_taken += 1
    return None


def consume_head(user_id: str, persona_id: str, user_reply_text: str | None = None):
    """Mode A (user_reply_text=None, main.py's "continue_eligible"-Zweig,
    VOR dem reaktiven run_auto_turn()-Fallback): liefert das naechste
    Kind des Cursors aus, egal ob normale Fortsetzung oder fork_root-
    Frage. Mode B (user_reply_text gesetzt, main.py's Pfad fuer eine
    echte Nutzer-Nachricht): bestaetigt einen Verzweigungs-Zweig gegen
    die echte Antwort, siehe _confirm_branch_reply. Gibt in beiden
    Modi None zurueck, wenn nichts Passendes bereitsteht (main.py faellt
    dann auf den bestehenden reaktiven Pfad zurueck, unveraendert zu
    Runde 1)."""
    chain = _chains.get(user_id)
    if chain is None or chain.persona_id != persona_id:
        return None

    if user_reply_text is not None:
        return _confirm_branch_reply(chain, user_id, user_reply_text)

    return _consume_next_ready_child(chain, user_id, persona_id)


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
    knappe Live-KPI-Anzeige waehrend der Entwicklung (bewusst nur
    Zahlen, kein Baum, keine Kandidaten-Texte - K.I.S.S., ausdruecklich
    als temporaeres Entwicklungs-Werkzeug gedacht, spaeter wieder
    entfernen/verstecken). `branching` ist additiv (Runde 2) - das
    bestehende Widget (N/5 + Lautsprecher-Icon) funktioniert
    unveraendert weiter, ohne dieses Feld zu kennen. None, wenn gerade
    keine Kette fuer diese Person existiert."""
    chain = _chains.get(user_id)
    if chain is None:
        return None
    head = _head_child(chain)
    return {
        "persona_id": chain.persona_id,
        "levels_built": len(chain.levels),
        "target_depth": TARGET_DEPTH,
        "head_has_audio": head.audio_ready if head is not None else False,
        "branching": any(lvl.branch_kind == "fork_root" for lvl in chain.levels),
    }


def stats() -> dict:
    """Fuer /admin/stats (main.py) - delivery_rate_by_depth ist die
    fuer die "lohnt sich Tiefe 5?"-Entscheidung direkt relevante,
    vorberechnete Zahl (ueber alle Zweige aggregiert). Die vier
    forks_*/nodes_pruned_on_confirm-Zaehler sind Runde 2's Antwort auf
    "wie oft wird JEDER Zweig tatsaechlich gebraucht", nicht nur jede
    Tiefe."""
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
        "forks_offered": _forks_offered,
        "forks_confirmed": _forks_confirmed,
        "forks_catchall_taken": _forks_catchall_taken,
        "nodes_pruned_on_confirm": _nodes_pruned_on_confirm,
    }
