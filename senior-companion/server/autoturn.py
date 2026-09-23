"""
Auto-Turns: wenn die Person laengere Zeit schweigt, spricht eine
anwesende Persona von sich aus weiter - dieselbe Regisseur-Mechanik wie
bei einer echten Nutzer-Nachricht (director.pick_responder), nur ohne
neuen Nutzer-Input als Ausloeser. Reine, deterministisch testbare
Funktionen (kein time.time()-Aufruf hier drin, analog zu
director.pick_responder's now-Parameter) - main.py's room_chat traegt
die Zeit von aussen rein.

Selbstbegrenzung ist primaer eine Charaktereigenschaft
(config.PersonaConfig.reengagement_tendency), keine Text-Heuristik -
ein haerterer Zaehler-Deckel (MAX_CONSECUTIVE_AUTO_TURNS) ist nur ein
Sicherheitsnetz, kein primaerer Mechanismus (siehe Projektgedaechtnis:
Text-Stopp-Phrasen wurden bewusst als "zu fragil fuer kleine lokale
Modelle" verworfen).
"""
import difflib
import random
import re

import memory

# Admin-ueberschreibbar (siehe main.py::_apply_persisted_admin_settings,
# Muster identisch zu satisfaction.CHECKIN_INTERVAL_DAYS).
ENABLED = True

# Wie oft main.py::room_chat zwischen zwei echten Nutzer-Nachrichten
# kurz "nachschaut", ob eine Auto-Turn faellig ist (receive-Timeout).
SHORT_PAUSE_SECONDS = 10.0

# Innerhalb dieses Fensters seit der letzten ECHTEN Nutzer-Nachricht
# duerfen Personas noch von sich aus weiterreden ("noch angenehm,
# weiterzuplaudern").
COMFORT_WINDOW_SECONDS = 180.0

# Ab hier: genug Stille, um sich zu verabschieden und ganz still zu
# werden, bis eine echte Nutzer-Nachricht kommt.
WRAPUP_WINDOW_SECONDS = 600.0

# Sicherheitsnetz-Deckel, NICHT der primaere Selbstbegrenzungs-
# Mechanismus (das ist reengagement_tendency / should_continue unten).
# War 4 - live beobachtet (2026-09-22), dass sich Auto-Turns trotz
# Themenwechsel-Prompt und Aehnlichkeits-Sicherung (main.py) nach ein
# paar Wiederholungen ins Belanglose ziehen. Niedriger gesetzt, damit
# spaetestens nach 2 unaufgeforderten Aeusserungen wieder eine echte
# Nutzer-Nachricht noetig ist, statt viermal am Stueck vor sich hin zu
# reden.
MAX_CONSECUTIVE_AUTO_TURNS = 2


def decide_phase(elapsed_since_user: float, consecutive_auto_turns: int) -> str:
    """"continue_eligible" | "quiet" | "wrapup". Wrapup ist rein
    zeitbasiert und hat Vorrang: eine Person, die seit
    WRAPUP_WINDOW_SECONDS nichts gesagt hat, bekommt die Verabschiedung
    unabhaengig davon, ob der Auto-Turn-Deckel schon erreicht ist. Der
    Deckel allein loest KEINE fruehere Verabschiedung aus - er pausiert
    das Weiterplaudern nur bis entweder eine echte Nachricht kommt oder
    die Zeit selbst das Wrapup-Fenster erreicht (zwei unabhaengige
    Gruende fuer Stille verdienen keine gemeinsame
    Verabschiedungs-Nachricht)."""
    if elapsed_since_user >= WRAPUP_WINDOW_SECONDS:
        return "wrapup"
    if elapsed_since_user < COMFORT_WINDOW_SECONDS and consecutive_auto_turns < MAX_CONSECUTIVE_AUTO_TURNS:
        return "continue_eligible"
    return "quiet"


def should_continue(reengagement_tendency: float) -> bool:
    """Wahrscheinlichkeits-Gate fuer die ZULETZT aktive anwesende
    Person (main.py wertet das aus, bevor ueberhaupt feststeht, wer als
    naechstes drankaeme): eine Persona mit hoher Neigung, die Person
    selbst wieder ins Gespraech zu holen (z.B. Freundin, 0.8), soll
    NACH ihrer eigenen Frage an die Person meist pausieren, statt von
    einer anderen Persona uebertoent zu werden - das wuerde die
    eigentliche Einladung an die Person untergraben. Eine Persona mit
    eher abgeschlossenen Aussagen (z.B. Professor, 0.2) darf oefter
    weitergereicht werden."""
    continue_probability = 1.0 - reengagement_tendency
    return random.random() < continue_probability


AUTO_CONTINUE_PROMPT = (
    "Die Person hat gerade nichts gesagt. Schau dir an, was sie zuletzt "
    "in der Unterhaltung erzaehlt hat, und stell dazu eine kurze, "
    "konkrete Nachfrage oder greife einen Punkt daraus auf. Erfinde "
    "dabei KEINE neuen Details, Angebote oder Erlebnisse, die dort "
    "nicht schon vorkamen - lieber konkret nachfragen als frei "
    "dazuerfinden. Fordere die Person auch NICHT zu einer bestimmten "
    "Reaktion auf (z.B. 'lachen Sie mit mir' o.ae.) - das wirkt "
    "aufgesetzt. WICHTIG: Schau nach, was du in der Unterhaltung "
    "zuletzt selbst gesagt hast, und wiederhole das NICHT, auch nicht "
    "in aehnlichen Worten. Faellt dir keine konkrete Nachfrage ein, "
    "bleib lieber ganz still (dann gib einfach gar nichts aus). Halte "
    "es kurz."
)

# Wird verwendet, wenn schon EIN vorheriger Auto-Turn in dieser
# Stille-Phase keine echte Antwort der Person bekommen hat (siehe
# room_chat()'s consecutive_auto_turns) - das aktuelle Thema zieht
# offenbar nicht, wie in einem echten Gespraech probiert man dann
# etwas anderes, statt beim selben Thema zu bleiben.
AUTO_CONTINUE_NEW_TOPIC_PROMPT = (
    "Die Person hat immer noch nichts gesagt, auch nicht auf deine "
    "letzte eigene Aeusserung. Das aktuelle Thema regt sie offenbar "
    "gerade nicht zum Antworten an. Wechsle jetzt bewusst zu einem "
    "GANZ ANDEREN Gespraechsthema - nicht nur eine Umformulierung des "
    "Gleichen, sondern wirklich etwas Neues (z.B. etwas anderes aus "
    "deinem Alltag, eine Frage zu einem ganz anderen Bereich, oder "
    "greife etwas aus einem frueheren Teil der Unterhaltung auf). "
    "Erfinde dabei KEINE neuen Details, Angebote oder Erlebnisse, die "
    "nicht schon vorkamen. Fordere die Person auch NICHT zu einer "
    "bestimmten Reaktion auf. Faellt dir kein neues Thema ein, bleib "
    "lieber ganz still (dann gib einfach gar nichts aus). Halte es "
    "kurz."
)

AUTO_WRAPUP_PROMPT = (
    "Die Person hat sich schon eine Weile nicht mehr gemeldet - "
    "vermutlich ist sie eingeschlafen, hat den Raum verlassen oder ist "
    "einfach abgelenkt. Verabschiede dich kurz, warm und liebevoll (z.B. "
    "in der Art von 'Oh, ich glaube, sie ist eingeschlafen. Dann lassen "
    "wir sie mal ruhen.'), OHNE eine Frage zu stellen oder eine Antwort "
    "zu erwarten. Das ist die letzte Aeusserung, bevor es wieder still "
    "wird."
)

# Beim Verbindungsaufbau in einen leeren Raum (main.py::room_chat,
# direkt nach websocket.accept()) - dieselbe Mechanik wie ein Auto-Turn
# (kein echter Nutzer-Input), nur als allererste Aeusserung statt als
# Fortsetzung einer Stille.
GREETING_PROMPT = (
    "Du beginnst gerade neu ein Gespraech mit der Person - sie hat noch "
    "nichts gesagt. Begruesse sie warm und natuerlich, so wie es zu dir "
    "passt (z.B. erzaehl kurz etwas von dir oder frag freundlich, wie es "
    "ihr geht). Halte es kurz und einladend."
)

AUTO_TURN_PROMPTS = {
    "continue": AUTO_CONTINUE_PROMPT,
    "continue_new_topic": AUTO_CONTINUE_NEW_TOPIC_PROMPT,
    "wrapup": AUTO_WRAPUP_PROMPT,
    "greeting": GREETING_PROMPT,
}

# Wie aehnlich (0..1, difflib-Ratio) eine neu generierte Auto-Turn-
# Aeusserung ihren eigenen letzten Aeusserungen sein darf, bevor sie
# unterdrueckt wird - live beobachtet (2026-09-22): trotz expliziter
# Prompt-Anweisung und einem groesseren Modell fielen Auto-Turns
# wiederholt in fast wortgleiche Wiederholungen zurueck, vermutlich
# durch die eigene Historie selbst verstaerkt. Ein deterministisches
# Sicherheitsnetz, unabhaengig davon, wie gut das jeweilige Modell
# Anweisungen befolgt.
#
# 0.75 war zu lasch: ein echtes Beispiel ("Blumenbeete" vs. "ein
# bestimmtes Springbrunnen" als Garten-Variation derselben Masche) lag
# nur bei ~0.52 Aehnlichkeit - lexikalisch verschieden, thematisch
# aber dieselbe Wiederholung. difflib misst Zeichenketten-, keine
# Bedeutungs-Aehnlichkeit, daher als grobe Kalibrierung gemessen:
# unterschiedliches Thema ~0.24, gleiches Thema/andere Worte ~0.39,
# das reale Beispiel ~0.52. 0.35 faengt beide Wiederholungsfaelle ab,
# ohne echte Themenwechsel zu blockieren.
AUTO_TURN_SIMILARITY_THRESHOLD = 0.35
AUTO_TURN_SIMILARITY_LOOKBACK = 5

# Zusaetzliche, gezielte Pruefung NUR auf den ersten Satz: live
# beobachtet (2026-09-22), dass ein Auto-Turn immer mit demselben
# Einstiegssatz begann ("Ach, der Dobelhofpark, da ist es wirklich
# schön, oder?"), aber gegen Ende variierte - das verduennt die
# Gesamt-Aehnlichkeit (AUTO_TURN_SIMILARITY_THRESHOLD) unter die
# Schwelle, obwohl der wiedererkennbare Teil identisch blieb. Eigene,
# strengere Schwelle nur fuer den ersten Satz (kalibriert: exakt
# gleicher Einstieg = 1.0, leicht umformuliert ~0.75, anderes Thema
# ~0.24 - 0.55 faengt beide Wiederholungsfaelle, nicht echte
# Themenwechsel).
AUTO_TURN_OPENER_SIMILARITY_THRESHOLD = 0.55


# Nur fuer lookahead.py's Ketten-Aufbau angehaengt (NIE fuer
# run_auto_turn()'s reaktiven Pfad - der liefert sofort aus und
# braucht keine Verzweigungs-Metadaten, die Tag-Anweisung wuerde den
# schon fein abgestimmten reaktiven Prompts nur unnoetig Tonfall
# hinzufuegen). Selbst-Tagging in DERSELBEN Generierung statt eines
# zweiten Klassifikations-Calls (wie sentiment_job.py's separater
# Nacht-Lauf) - eine zweite Ollama-Anfrage wuerde genau an der Stelle
# zusaetzliche Latenz kosten, an der dieses Feature ueberhaupt Latenz
# sparen soll. Format-Konvention angelehnt an sentiment_job.py's
# STIMMUNG:/HALTUNG:-Muster, ebenso defensiv geparst (siehe
# lookahead._parse_branch_tag) - ein Fehlformat faellt immer auf
# "keine Verzweigung" zurueck, nie auf eine erfundene.
BRANCH_TAG_INSTRUCTION = (
    "Stelle nur alle 3-4 Saetze eine Frage, dazwischen erzaehl einfach "
    "weiter, ohne zu fragen. Wenn du gerade eine Frage stellst, die "
    "sich mit Ja/Nein oder einer kleinen, konkret genannten Auswahl "
    "beantworten laesst (z.B. 'Moegen Sie lieber X oder Y?'), haenge "
    "GANZ AM ENDE deiner Antwort, nach einer Leerzeile, GENAU dieses "
    "Format an, ohne weitere Worte danach:\n"
    "---\n"
    "VERZWEIGUNG: ja\n"
    "OPTIONEN: <Option 1> | <Option 2>\n"
    "Ist deine Frage offen (z.B. nach einem Namen, einer Meinung, "
    "etwas frei Erzaehltem) oder stellst du gerade keine Frage, haenge "
    "stattdessen genau dieses Format an:\n"
    "---\n"
    "VERZWEIGUNG: keine"
)

# Fuer den synthetischen "hypothetischen Antwort"-Turn beim Aufbau
# EINES Verzweigungs-Zweigs (siehe lookahead._extend_chain) - dieselbe
# "Ollama ist zustandslos, wir steuern das Vorwissen selbst"-Technik
# wie in Runde 1, jetzt auch fuer einen fabrizierten NUTZER-Turn
# verwendet, nicht nur fabrizierte eigene Turns.
BRANCH_ANSWER_CONTINUATION_PROMPT = (
    "Die Person hat gerade mit \"{option}\" geantwortet auf deine "
    "letzte Frage. Setze das Gespraech passend dazu fort. Erfinde "
    "dabei KEINE neuen Details, Angebote oder Erlebnisse, die nicht "
    "schon vorkamen."
)


def _first_sentence(text: str) -> str:
    match = re.match(r"^[\s\S]*?[.!?]+", text)
    return match.group(0) if match else text


def too_similar_to_own_recent(user_id: str, persona_id: str, candidate: str) -> bool:
    """True, wenn candidate leer ist, oder einer der letzten
    AUTO_TURN_SIMILARITY_LOOKBACK eigenen (assistant-)Aeusserungen
    dieser Persona bei dieser Person insgesamt ODER schon im ersten
    Satz zu aehnlich ist."""
    if not candidate.strip():
        return True
    recent = memory.recent_messages(user_id, persona_id, limit=20)
    own_recent = [m["content"] for m in recent if m["role"] == "assistant"]
    own_recent = own_recent[-AUTO_TURN_SIMILARITY_LOOKBACK:]
    candidate_opener = _first_sentence(candidate)
    for prior in own_recent:
        if difflib.SequenceMatcher(None, candidate, prior).ratio() >= AUTO_TURN_SIMILARITY_THRESHOLD:
            return True
        opener_ratio = difflib.SequenceMatcher(
            None, candidate_opener, _first_sentence(prior),
        ).ratio()
        if opener_ratio >= AUTO_TURN_OPENER_SIMILARITY_THRESHOLD:
            return True
    return False
