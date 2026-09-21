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
import random

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
MAX_CONSECUTIVE_AUTO_TURNS = 4


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
