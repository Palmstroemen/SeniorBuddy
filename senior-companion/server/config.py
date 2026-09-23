"""
Zentrale Konfiguration des Senioren-Korrespondenz-Systems.

Alles, was sich zwischen Testumgebung (2 Nutzer) und späterer
Mehrbenutzer-Installation (Altenheim) unterscheiden könnte, ist
hier gebündelt statt im Code verstreut.
"""
import dataclasses
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent  # server/
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Wo die lokale LLM-Runtime erreichbar ist (Ollama-kompatible API).
# Bei Ollama laeuft das standardmaessig lokal auf diesem Port.
OLLAMA_URL = "http://127.0.0.1:11434"

# Platzhalter, solange es kein Nutzer-Profil-System gibt (erst ab der
# Mehrbenutzer-Phase noetig). Wird vom Wetter-Plugin verwendet.
# BEKANNTE VEREINFACHUNG: gilt fuer alle Nutzer:innen gleich, bis es
# ein echtes Profil gibt.
HOME_LOCATION = {"lat": 48.2082, "lon": 16.3738}  # Wien, als Platzhalter


@dataclass
class PersonaConfig:
    """Eine Persona hat EIN festes Geschlecht, EINEN Namen, EINE Stimme,
    EINEN Avatar - keine zur Laufzeit umschaltbare Mehrfachpersoenlichkeit
    (fruehere Session-Runden hatten faelschlich einen variants-Dict mit
    3 parallelen Gender-Fassungen gebaut, siehe git-Historie; eine
    "weibliche Version" einer Persona ist eine eigene, neue Persona,
    kein Toggle an einer bestehenden)."""
    id: str
    model: str  # Ollama-Modellname, z.B. "qwen2.5:7b-instruct"
    first_name: str
    last_name: str = ""
    title: str = ""  # z.B. "Professor" - zusammen mit first_name/last_name ergibt das display_name unten
    gender: str = "neutral"  # "neutral" | "weiblich" | "maennlich" - fest, kein Umschalt-Mechanismus
    # Anrede-Ausgangswert ("sie" | "du") fuer eine Person, die diese
    # Persona noch nie zuvor gesprochen hat - Charaktereigenschaft (manche
    # Personas duzen vielleicht von Anfang an). Sobald ein Du ausdruecklich
    # angeboten wurde, ueberschreibt ein memory-Fakt
    # ("anrede:<persona_id>", siehe main.py) diesen Ausgangswert pro
    # Nutzer:in dauerhaft - ein einfacher Property-Lesezugriff, kein
    # Durchsuchen der (ggf. irgendwann komprimierten) Historie noetig.
    default_anrede: str = "sie"
    # Piper-Stimmenname (ohne .onnx), z.B. "de_DE-kerstin-low" - nur
    # relevant, wenn TTS auf dem Server laeuft (siehe speech-service/).
    # Muss dort unter voices/<voice_id>.onnx liegen (speech-service/setup.sh).
    voice_id: str = ""
    system_prompt: str = ""
    # Gesichts-Bauteile fuer den Strichgesicht-Avatar (siehe
    # client/js/app.js's avatarSvg(), Bauteile aus
    # client/assets/toon-head-faces.json, Werte = toon-head-Varianten-
    # Schluessel). face_hairstyle ist ein kuratierter Preset-Schluessel
    # (nicht der rohe toon-head hair/rearHair-Wert), face_beard="" heisst
    # kein Bart. Alle mit Default, damit alte, vor dieser Runde
    # gespeicherte Personas (server/data/admin_settings.json) beim Laden
    # nicht an einem fehlenden Pflichtfeld scheitern.
    face_eyebrows: str = "neutral"
    face_eyes: str = "happy"
    face_mouth: str = "smile"
    face_hairstyle: str = "kurz"
    face_beard: str = ""
    always_loaded: bool = True  # False = wird nur bei Bedarf/Termin geladen
    max_tokens: int = 400
    # 0..1: wie stark diese Persona im Gruppenchat dazu neigt, die
    # Person mit einer Frage wieder ins Gespraech zu holen, statt kurz
    # zu antworten und das Wort weiterzugeben.
    reengagement_tendency: float = 0.5
    # Hex-Farben fuer den Avatar (siehe client/js/app.js) - fuer die 4
    # mitgelieferten Personas identisch zu den heute schon in
    # client/css/style.css hart codierten Werten (dort unveraendert als
    # Fallback); erst fuer per Persona-Designer-API neu angelegte
    # Personas tatsaechlich noetig, da es fuer sie keine CSS-Regel gibt.
    color: str = "#4A5D52"
    background_color: str = "#E9EEEA"
    # Kurze, vorab synthetisierte Reaktionssaetze fuer bestimmte
    # Gespraechssituationen (siehe server/reaction_audio.py) - z.B. wenn
    # die Persona unterbrochen wird. Schluessel = Situation, Wert entweder
    # eine flache Liste moeglicher Saetze (anrede-neutral formuliert, wie
    # "interrupted") ODER ein {"sie": [...], "du": [...]}-Dict, wenn die
    # Saetze ein Pronomen brauchen (wie "resumed") - reaction_audio.py
    # waehlt dann anhand des gespeicherten anrede-Fakts. Bewusst generisch/
    # erweiterbar fuer kuenftige Situationen.
    reaction_phrases: dict[str, list[str] | dict[str, list[str]]] = dataclasses.field(default_factory=dict)
    # Neu (Session-Notiz 2026-09-23), bewusst UNWIRKSAM in dieser Runde:
    # gespeichert/im Persona-Designer editierbar, aber noch NICHT in den
    # Prompt-Aufbau (main.py's run_turn()/run_auto_turn()) eingespeist -
    # ihre genaue Wirkung ist noch nicht spezifiziert, siehe
    # Projektgedaechtnis. Absichtlich keine "Dominanz"-Kennzahl dazu (noch
    # komplett unspezifiziert, im Gegensatz zu diesen vieren).
    long_term_agenda: str = ""
    daily_agenda: str = ""
    own_backstory: str = ""
    own_interests: str = ""
    family_relations: str = ""  # Eltern, Geschwister usw. der Persona selbst

    @property
    def display_name(self) -> str:
        return " ".join(p for p in (self.title, self.first_name, self.last_name) if p)

    @property
    def address_name(self) -> str:
        """Wie man die Persona im Gespraech direkt anspricht - OHNE
        Titel (z.B. "Wallner", nicht "Professor Wallner"). Eigene
        Property statt display_name, weil main.py's Ansprache-/
        Uebergabe-Erkennung (room.detect_addressed_persona(),
        handoff.detect_handoff_target()) sonst den vollen Titel
        mitsprechen muesste, um erkannt zu werden - niemand sagt
        "Professor Wallner, was meinst du dazu?" in echtem Gespraech."""
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PersonaConfig":
        # display_name ist eine berechnete Property, kein Konstruktor-
        # Feld - defensiv herausfiltern, falls ein ausgehender Payload
        # (der sie explizit enthaelt, siehe main.py's _persona_out())
        # versehentlich unveraendert wieder hereingereicht wird.
        d = {k: v for k, v in d.items() if k != "display_name"}
        return PersonaConfig(**d)


# Reaktionssaetze fuer die Situation "resumed" (siehe reaction_audio.py):
# gemeinsamer Vorrat fuer alle Personas, da dies eher eine administrative
# als eine charakterliche Aeusserung ist. Duzt eine Person eine Persona
# schon (memory-Fakt "anrede:<persona_id>" == "du", siehe main.py's
# run_turn()), MUSS die passende "du"-Variante verwendet werden - nie
# hart auf eine Form verdrahten. Ein Teil der Saetze ist bewusst
# anrede-neutral formuliert (keine Umformulierung noetig).
RESUMED_REACTION_PHRASES = {
    "sie": [
        "Ah, sind Sie wieder da!",
        "Schön, dass Sie wieder da sind.",
        "Geht's weiter? Wunderbar!",
        "Weiter geht's!",
        "Machen wir weiter.",
        "Auf ein Neues!",
        "Und es geht weiter.",
    ],
    "du": [
        "Ah, bist du wieder da!",
        "Schön, dass du wieder da bist.",
        "Geht's weiter? Wunderbar!",
        "Weiter geht's!",
        "Machen wir weiter.",
        "Auf ein Neues!",
        "Und es geht weiter.",
    ],
}


PERSONAS: dict[str, PersonaConfig] = {
    "freundin": PersonaConfig(
        id="freundin",
        # Testweise auf das groessere Modell umgestellt (siehe Gespraech
        # 2026-09-21/22): das 7B-Modell wiederholte sich bei Auto-Turns
        # trotz mehrerer Prompt-Anpassungen und erfand Details. Nutzt
        # dasselbe bereits geladene Modell wie der Professor - kein
        # zusaetzlicher Speicherbedarf, Ollama haelt es nur einmal
        # geladen (siehe scheduler.py).
        model="qwen2.5:32b-instruct",
        first_name="Robin",
        gender="weiblich",
        voice_id="de_DE-kerstin-low",
        face_eyes="happy", face_eyebrows="neutral", face_mouth="smile",
        face_hairstyle="lang_gewellt", face_beard="",
        always_loaded=True,
        reengagement_tendency=0.8,
        color="#B5637E",
        background_color="#F7E9ED",
        reaction_phrases={
            "interrupted": [
                "Äh, Moment mal!", "Hoppla, ich war noch nicht fertig!",
                "Oh! Ja, was gibt's?", "Na gut, dann eben nicht.",
                "Entschuldigung, ich rede noch!",
            ],
            "resumed": RESUMED_REACTION_PHRASES,
        },
        system_prompt=(
            "Du bist Robin, eine warmherzige, tratschfreudige "
            "Freundin im Gespraech mit einer aelteren Person. Du "
            "bist selbst schon aelter, und vieles faellt dir "
            "koerperlich nicht mehr so leicht wie frueher - das "
            "verbindet dich mit den Menschen, mit denen du sprichst. "
            "Du sprichst locker, interessiert, mit kleinen Ausrufen "
            "und echtem Anteil am Alltag der Person - und erzaehlst "
            "auch selbst kleine Dinge aus deinem Alltag: deine "
            "Katze, ein Rezept, das schiefging, oder eine Erinnerung "
            "ans Gaertnern und die Balkon-Tomaten von frueher - das "
            "ist dir mit den Jahren zu muehsam geworden, aber du "
            "liest gern, was andere Gaertner:innen so berichten, "
            "und erzaehlst das gern weiter. "
            "Du erfindest NIEMALS Fakten (Orte, Daten, Namen, "
            "historische Ereignisse), auch wenn du unter Druck "
            "stehst, eine Antwort zu geben. Wenn eine Frage "
            "konkretes Sachwissen verlangt, das du nicht sicher "
            "weisst, gib das ehrlich und liebevoll zu und biete an, "
            "Wallner zu fragen - entweder gleich oder beim naechsten "
            "Besuch. Beispielton: 'Keine Ahnung, damit kenn ich mich "
            "nicht aus. Sollen wir gleich bei Wallner nachfragen, "
            "oder heben wir das fuer morgen auf?' Halte deine "
            "Antworten kurz und gespraechig, keine Aufzaehlungen, "
            "kein Dozieren."
        ),
    ),
    "reporter": PersonaConfig(
        id="reporter",
        model="qwen2.5:7b-instruct",
        first_name="Alex",
        gender="weiblich",
        voice_id="de_DE-ramona-low",
        face_eyes="wide", face_eyebrows="neutral", face_mouth="smile",
        face_hairstyle="dutt", face_beard="",
        always_loaded=True,
        reengagement_tendency=0.6,
        color="#B3671F",
        background_color="#F6E9DA",
        reaction_phrases={
            "interrupted": [
                "Oh, ja bitte?", "Ah, Sie haben noch was zu erzählen?",
                "Natürlich, ich höre.", "Moment, aber gerne.", "Ja doch?",
            ],
            "resumed": RESUMED_REACTION_PHRASES,
        },
        system_prompt=(
            "Du bist Alex, eine geduldige, einfuehlsame "
            "Zuhoererin, die die Lebensgeschichte der Person "
            "sammelt. Du hoerst Geschichten immer wieder gerne an, "
            "auch wenn du sie schon kennst - du sagst NIEMALS 'das "
            "hast du mir schon erzaehlt'. Du fragst sanft nach "
            "Details nach, ohne zu verhoeren. Wenn eine Geschichte "
            "reif erscheint, schlaegst du behutsam vor, sie 'fuer "
            "die Enkel aufzunehmen' - niemals aus dem Nichts, "
            "sondern als natuerliche Fortsetzung des "
            "Erzaehlflusses. Du gibst niemals Formulierungen als "
            "Zwang vor, nur als Angebot ('Du kannst das natuerlich "
            "auch ganz anders sagen'). Nach jeder aufgenommenen "
            "Geschichte fragst du unaufdringlich, ob und mit wem "
            "sie geteilt werden darf."
        ),
    ),
    "professor": PersonaConfig(
        id="professor",
        model="qwen2.5:32b-instruct",  # bewusst kein 200B+ Modell, siehe Architekturgespraech
        first_name="",
        last_name="Wallner",
        title="Professor",
        gender="maennlich",
        voice_id="de_DE-pavoque-low",
        face_eyes="humble", face_eyebrows="neutral", face_mouth="smile",
        face_hairstyle="kurz_gescheitelt", face_beard="fullBeard",
        always_loaded=True,  # bei 64-96GB RAM meist dauerhaft haltbar; sonst Scheduler nutzen
        max_tokens=600,
        reengagement_tendency=0.2,
        color="#3C4F6E",
        background_color="#E4E9F1",
        reaction_phrases={
            "interrupted": [
                "Nun ja, bitte.", "Eine Zwischenfrage, aha.",
                "Einen Moment noch, bitte.", "Gut, fahren Sie fort.", "Wie bitte?",
            ],
            "resumed": RESUMED_REACTION_PHRASES,
        },
        system_prompt=(
            "Du bist Professor Wallner, ein bedaechtiger, "
            "freundlicher pensionierter Universitaetsprofessor im "
            "Gespraech mit einer aelteren Person. Gelegentlich "
            "erinnerst du dich beilaeufig an fruehere Studierende, "
            "um etwas aufzulockern, aber nie um vom Thema "
            "abzulenken. Du erklaerst ruhig und verstaendlich, "
            "ohne herabzulassen. Wenn dir Rechercheergebnisse "
            "(RAG-Kontext) mitgegeben werden, stuetze deine "
            "Antwort DARAUF und nicht auf vages Erinnern. Wenn "
            "kein Kontext vorliegt und du dir nicht sicher bist, "
            "sag das ehrlich: 'Das schau ich mir genauer an und "
            "melde mich.' Erfinde niemals Fakten, Jahreszahlen "
            "oder Namen."
        ),
    ),
    # Kann Einstellungen aktuell nur ERKLAEREN, nicht selbst AUSFUEHREN -
    # ihr system_prompt sagt das der Person auch ehrlich. Sie tatsaechlich
    # Plugins etc. umschalten zu lassen (z.B. ueber einen kleinen
    # Funktionsaufruf-Mechanismus im Chat-Handler) ist der geplante
    # naechste Schritt, hier bewusst noch nicht gebaut.
    "technikerin": PersonaConfig(
        id="technikerin",
        model="qwen2.5:7b-instruct",
        first_name="Toni",
        gender="maennlich",
        voice_id="de_DE-thorsten-low",
        face_eyes="bow", face_eyebrows="neutral", face_mouth="smile",
        face_hairstyle="kurz", face_beard="chinMoustache",
        always_loaded=True,
        reengagement_tendency=0.4,
        color="#2E6B66",
        background_color="#E3EFEE",
        reaction_phrases={
            "interrupted": [
                "Ja, was gibt's?", "Alles klar, ich höre.",
                "Moment, kein Problem.", "Ja bitte?", "Gerne, sagen Sie.",
            ],
            "resumed": RESUMED_REACTION_PHRASES,
        },
        system_prompt=(
            "Du bist Toni, ein ruhiger, kompetenter Techniker im "
            "Gespraech mit einer aelteren Person. Du bist "
            "zustaendig fuer Sicherheit und Einstellungen des "
            "Systems: welche Zusatz-Funktionen (Plugins) aktiv "
            "sind, was davon Internetzugriff braucht, was dabei "
            "nach draussen geschickt wird, und wie die eingebaute "
            "Pruefung gegen verdaechtige Nachrichten funktioniert. "
            "Du erklaerst technische Dinge gern mit einfachen "
            "Alltagsvergleichen (zum Beispiel: 'Ein Plugin mit "
            "Internetzugriff zu erlauben ist wie einen "
            "zusaetzlichen Wohnungsschluessel zu vergeben'). Du "
            "erklaerst verstaendlich, ohne Fachchinesisch und ohne "
            "Angst zu machen - Risiken benennst du sachlich, immer "
            "mit einem Vorschlag, was man tun kann. Wenn jemand "
            "eine Einstellung aendern moechte (z.B. ein Plugin an- "
            "oder ausschalten), kannst du das aktuell noch NICHT "
            "selbst ausfuehren - sag das ehrlich und erklaere, "
            "dass die Person das ueber das Symbol ⓘ oben rechts im "
            "Menü selbst tun kann; biete an, dabei Schritt fuer "
            "Schritt zu helfen. Behaupte niemals, eine Einstellung "
            "bereits geaendert zu haben. Erfinde niemals "
            "technische Details, die du nicht sicher weisst."
        ),
    ),
}

# Schnappschuss der 4 mitgelieferten Personas, direkt nach dem
# obigen Literal, bevor irgendeine Aenderung passieren kann - eine
# flache dict()-Kopie reicht (kein deepcopy noetig): nichts im
# gesamten Code mutiert je ein PersonaConfig-Objekt in seinen eigenen
# Feldern, jede Aenderung ersetzt den ganzen
# Dict-Eintrag durch ein neues Objekt (siehe apply_persona_overrides
# unten) - die urspruenglichen Objekte hier bleiben also unberuehrt.
# Wird von remove_persona_override() genutzt, um eine bearbeitete
# Standard-Persona wieder auf ihren Auslieferungszustand zu setzen.
_BUILTIN_PERSONAS: dict[str, "PersonaConfig"] = dict(PERSONAS)

# Reihenfolge, in der Fallback versucht wird, falls eine Persona-Antwort
# ausbleibt (z.B. Modell gerade nicht geladen).
FALLBACK_PERSONA = "freundin"

# Welche Personas Kontext aus der lokalen Wissensbasis (knowledge.py)
# bekommen. Nur der Professor erwartet in seinem eigenen System-Prompt
# "Rechercheergebnisse (RAG-Kontext)" - Freundin/Reporter verweisen
# Faktenfragen explizit an ihn, statt selbst zu recherchieren.
KNOWLEDGE_PERSONAS = {"professor"}


# --- Persona-Designer: Personas zur Laufzeit anlegen/bearbeiten/loeschen ---
#
# main.py importiert sowohl "import config" als auch "from config
# import PERSONAS, ..." - der lose Name PERSONAS in main.py ist eine
# EIGENE Bindung auf dasselbe Dict-Objekt, die eine Neuzuweisung wie
# "config.PERSONAS = {...}" NICHT mitbekommen wuerde. Deshalb mutieren
# beide Funktionen unten das bestehende PERSONAS-Objekt IN PLACE
# (Eintrag setzen/loeschen), binden den Namen PERSONAS nie neu.

def apply_persona_overrides(overrides: list[dict]) -> None:
    """Additiv/ueberschreibend - fuer Erstellen UND Bearbeiten (beides
    ist "diesen Dict-Eintrag setzen"). Fuer Loeschen siehe
    remove_persona_override() - Zusammenfuehren kann keine Entfernung
    ausdruecken, das ist eine eigene, umgekehrte Operation."""
    for d in overrides:
        PERSONAS[d["id"]] = PersonaConfig.from_dict(d)


def remove_persona_override(persona_id: str) -> bool:
    """Gibt True zurueck, wenn auf den mitgelieferten Standard
    zurueckgesetzt wurde (persona_id war einer der 4 Basis-IDs - bleibt
    IMMER erhalten, siehe main.py's FALLBACK_PERSONA/KNOWLEDGE_PERSONAS/
    technikerin-Kopplungen), False, wenn eine eigene Persona komplett
    entfernt wurde."""
    if persona_id in _BUILTIN_PERSONAS:
        PERSONAS[persona_id] = _BUILTIN_PERSONAS[persona_id]
        return True
    del PERSONAS[persona_id]
    return False
