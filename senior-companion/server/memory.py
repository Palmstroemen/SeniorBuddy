"""
Lokale Speicherschicht (SQLite, eine Datei pro Nutzer/in).

Bewusst so gebaut, dass Nutzer-Trennung von Anfang an sauber ist:
jede Person hat ihre eigene Datenbankdatei. Das entspricht dem
"externe Festplatte pro Person"-Gedanken aus dem Architekturgespraech
und macht spaeteres Kopieren/Uebergeben trivial (einfach die Datei).
"""
import sqlite3
import time
import json
from pathlib import Path
from contextlib import contextmanager

from config import DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona TEXT NOT NULL,
    role TEXT NOT NULL,              -- 'user' oder 'assistant'
    content TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,               -- z.B. 'enkel_name'
    value TEXT NOT NULL,
    source_persona TEXT,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS story_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL,          -- gruppiert Fragmente derselben Geschichte
    content TEXT NOT NULL,
    ts REAL NOT NULL,
    consent_status TEXT DEFAULT 'undecided',  -- undecided|kids|adults|private|deleted
    consent_note TEXT DEFAULT ''     -- z.B. "nicht fuer Tante Grete"
);

-- Transparenz-Log: jede Aktion, die Daten nach draussen schickt
-- (Internet-Abfrage, Plugin-Aufruf). Wird im UI als "Was wurde
-- heute nach draussen geschickt?" angezeigt.
CREATE TABLE IF NOT EXISTS external_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin TEXT NOT NULL,
    purpose TEXT NOT NULL,
    data_sent TEXT NOT NULL,         -- Klartext, was rausging
    ts REAL NOT NULL,
    approved_by_user INTEGER DEFAULT 0
);

-- Technikerin-Zufriedenheitsabfrage: wann gefragt, was geantwortet
-- wurde (falls die naechste Nachricht rechtzeitig als Antwort erkannt
-- wurde, siehe record_feedback_reply()).
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona TEXT NOT NULL,
    asked_ts REAL NOT NULL,
    reply TEXT,
    reply_ts REAL
);

-- Vertrauliches Thema pro Persona: "Wie sollen wir das nennen?" -
-- label bleibt NULL, bis die naechste Antwort ihn einfaengt (siehe
-- capture_topic_label()). Hoechstens ein offenes Thema pro Persona
-- gleichzeitig (v1-Vereinfachung).
CREATE TABLE IF NOT EXISTS active_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona TEXT NOT NULL,
    label TEXT,
    opened_ts REAL NOT NULL,
    closed_ts REAL
);

-- Loeschanweisung zu einem Thema: sofort (nach Bestaetigung) oder erst
-- im Todesfall. trigger_phrase wird nur fuer Nachvollziehbarkeit
-- gespeichert, NIE der eigentliche vertrauliche Inhalt.
CREATE TABLE IF NOT EXISTS deletion_directives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona TEXT NOT NULL,
    topic_label TEXT NOT NULL,
    trigger_phrase TEXT NOT NULL,
    mode TEXT NOT NULL,              -- pending_confirmation|immediate|on_death
    created_ts REAL NOT NULL,
    executed_ts REAL
);
"""

# Wird bei jedem get_db()-Aufruf ausgefuehrt - siehe _migrate().
MIN_SESSION_MINUTES = 1.0


def db_path_for(user_id: str) -> Path:
    return DATA_DIR / f"{user_id}.sqlite3"


def _migrate(conn: sqlite3.Connection):
    """Idempotente Spalten-Ergaenzung fuer bereits existierende
    Datenbankdateien - CREATE TABLE IF NOT EXISTS greift nicht mehr,
    sobald eine Tabelle schon existiert. Erste Schema-Aenderung des
    Projekts, daher bewusst simpel gehalten (kein Migrations-Framework
    fuer eine Handvoll ALTER-Statements)."""
    for stmt in (
        "ALTER TABLE messages ADD COLUMN sentiment TEXT",
        "ALTER TABLE messages ADD COLUMN stance TEXT",
        "ALTER TABLE messages ADD COLUMN topic TEXT",
        "ALTER TABLE messages ADD COLUMN links_to_id INTEGER",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise


@contextmanager
def get_db(user_id: str):
    conn = sqlite3.connect(db_path_for(user_id))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_message(
    user_id: str, persona: str, role: str, content: str, topic: str | None = None
) -> int:
    with get_db(user_id) as db:
        cur = db.execute(
            "INSERT INTO messages (persona, role, content, ts, topic) VALUES (?,?,?,?,?)",
            (persona, role, content, time.time(), topic),
        )
        return cur.lastrowid


def add_linked_message(
    user_id: str, persona: str, role: str, links_to_id: int, topic: str | None = None
) -> int:
    """Legt eine leichte 'mitgehoert'-Zeile an (Gruppenchat-Fan-out): ihr
    eigentlicher Inhalt kommt ueber links_to_id von der Master-Zeile
    (siehe recent_messages()). content bleibt Leerstring, NICHT NULL -
    die Spalte ist NOT NULL; ob eine Verlinkung tatsaechlich aufgeloest
    werden konnte, wird beim Lesen anhand des JOIN-Ergebnisses erkannt,
    nicht anhand von content."""
    with get_db(user_id) as db:
        cur = db.execute(
            "INSERT INTO messages (persona, role, content, ts, topic, links_to_id) "
            "VALUES (?,?,?,?,?,?)",
            (persona, role, "", time.time(), topic, links_to_id),
        )
        return cur.lastrowid


def recent_messages(user_id: str, persona: str, limit: int = 20) -> list[dict]:
    """Verlinkte Zeilen (Gruppenchat-Fan-out, siehe add_linked_message())
    werden ueber die Master-Zeile aufgeloest. Eine Zeile mit links_to_id,
    deren Master nicht (mehr) existiert, wuerde durch den LEFT JOIN kein
    Gegenstueck finden (orig_id bleibt NULL) - so eine kaputte
    Verlinkung wird herausgefiltert statt als leere Nachricht an das
    Sprachmodell weitergereicht."""
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT m.role, m.links_to_id, orig.id AS orig_id, "
            "COALESCE(orig.content, m.content) AS content, m.ts "
            "FROM messages m LEFT JOIN messages orig ON m.links_to_id = orig.id "
            "WHERE m.persona=? ORDER BY m.id DESC LIMIT ?",
            (persona, limit),
        ).fetchall()
    return [
        {"role": r["role"], "content": r["content"], "ts": r["ts"]}
        for r in reversed(rows)
        if not (r["links_to_id"] is not None and r["orig_id"] is None)
    ]


def usage_stats(
    user_id: str, session_gap_minutes: int = 20, persona: str | None = None
) -> dict:
    """Nutzungs-Statistik aus den vorhandenen Nachrichten-Zeitstempeln -
    braucht keine zusaetzliche Instrumentierung. Eine "Sitzung" endet,
    sobald zwischen zwei Nutzer-Nachrichten mehr als session_gap_minutes
    vergehen. Nur role='user'-Zeitstempel zaehlen fuer die Gruppierung -
    eine Antwort allein soll keine Sitzung verlaengern. persona=None
    (Default) poolt ueber alle Personas - fuer eine Aufschluesselung pro
    Persona siehe persona_usage_stats()."""
    query = "SELECT ts FROM messages WHERE role='user'"
    params: tuple = ()
    if persona is not None:
        query += " AND persona=?"
        params = (persona,)
    query += " ORDER BY ts"
    with get_db(user_id) as db:
        rows = db.execute(query, params).fetchall()
    timestamps = [r["ts"] for r in rows]

    if not timestamps:
        return {
            "total_sessions": 0,
            "total_active_minutes": 0,
            "distinct_active_days": 0,
            "avg_minutes_per_active_day": 0,
            "first_message_ts": None,
            "last_message_ts": None,
        }

    gap_seconds = session_gap_minutes * 60
    sessions = [[timestamps[0]]]
    for ts in timestamps[1:]:
        if ts - sessions[-1][-1] > gap_seconds:
            sessions.append([ts])
        else:
            sessions[-1].append(ts)

    total_active_minutes = sum(
        max((s[-1] - s[0]) / 60, MIN_SESSION_MINUTES) for s in sessions
    )
    distinct_active_days = len({
        time.strftime("%Y-%m-%d", time.gmtime(ts)) for ts in timestamps
    })

    return {
        "total_sessions": len(sessions),
        "total_active_minutes": round(total_active_minutes, 1),
        "distinct_active_days": distinct_active_days,
        "avg_minutes_per_active_day": round(
            total_active_minutes / distinct_active_days, 1
        ) if distinct_active_days else 0,
        "first_message_ts": timestamps[0],
        "last_message_ts": timestamps[-1],
    }


def persona_usage_stats(user_id: str, session_gap_minutes: int = 20) -> dict:
    """Wie oft/wie lange wird welche Persona tatsaechlich genutzt -
    Grundlage fuer den "Freundeskreis"-Gedanken: manche Personen reden
    lieber mit dem Professor, andere kaum. message_count zaehlt nur
    role='user'-Nachrichten (eine Antwort allein ist keine "Ansprache")."""
    with get_db(user_id) as db:
        personas = [
            r["persona"] for r in db.execute(
                "SELECT DISTINCT persona FROM messages WHERE role='user'"
            ).fetchall()
        ]
        counts = {
            persona: db.execute(
                "SELECT COUNT(*) FROM messages WHERE role='user' AND persona=?",
                (persona,),
            ).fetchone()[0]
            for persona in personas
        }

    return {
        persona: {
            "message_count": counts[persona],
            **usage_stats(user_id, session_gap_minutes, persona=persona),
        }
        for persona in personas
    }


def log_external_request(user_id: str, plugin: str, purpose: str, data_sent: dict):
    """Jede Internet-/Plugin-Abfrage MUSS hier durchlaufen, bevor sie passiert.
    Das ist die technische Grundlage der Transparenz-Anzeige."""
    with get_db(user_id) as db:
        db.execute(
            "INSERT INTO external_requests (plugin, purpose, data_sent, ts) "
            "VALUES (?,?,?,?)",
            (plugin, purpose, json.dumps(data_sent, ensure_ascii=False), time.time()),
        )


def get_transparency_log(user_id: str, limit: int = 50) -> list[dict]:
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT plugin, purpose, data_sent, ts FROM external_requests "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def external_request_stats(user_id: str) -> dict:
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT plugin, COUNT(*) AS n FROM external_requests GROUP BY plugin"
        ).fetchall()
    by_plugin = {r["plugin"]: r["n"] for r in rows}
    return {"total": sum(by_plugin.values()), "by_plugin": by_plugin}


def unclassified_messages(user_id: str, limit: int = 200) -> list[dict]:
    """Fuer den naechtlichen Sentiment-Job (sentiment_job.py): noch
    nicht klassifizierte Nutzer-Nachrichten, aelteste zuerst."""
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT id, content FROM messages WHERE role='user' AND sentiment IS NULL "
            "ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_message_classification(
    user_id: str, message_id: int, sentiment: str, stance: str | None
):
    with get_db(user_id) as db:
        db.execute(
            "UPDATE messages SET sentiment=?, stance=? WHERE id=?",
            (sentiment, stance, message_id),
        )


def sentiment_stats(user_id: str) -> dict:
    with get_db(user_id) as db:
        sentiment_rows = db.execute(
            "SELECT sentiment, COUNT(*) AS n FROM messages "
            "WHERE role='user' AND sentiment IS NOT NULL GROUP BY sentiment"
        ).fetchall()
        stance_rows = db.execute(
            "SELECT stance, COUNT(*) AS n FROM messages "
            "WHERE role='user' AND stance IS NOT NULL GROUP BY stance"
        ).fetchall()
        pending = db.execute(
            "SELECT COUNT(*) FROM messages WHERE role='user' AND sentiment IS NULL"
        ).fetchone()[0]
    return {
        "sentiment": {r["sentiment"]: r["n"] for r in sentiment_rows},
        "stance": {r["stance"]: r["n"] for r in stance_rows},
        "unclassified_pending": pending,
    }


def add_story_fragment(user_id: str, story_id: str, content: str) -> int:
    with get_db(user_id) as db:
        cur = db.execute(
            "INSERT INTO story_fragments (story_id, content, ts) VALUES (?,?,?)",
            (story_id, content, time.time()),
        )
        return cur.lastrowid


def add_fact(user_id: str, key: str, value: str, source_persona: str | None = None):
    with get_db(user_id) as db:
        db.execute(
            "INSERT INTO facts (key, value, source_persona, ts) VALUES (?,?,?,?)",
            (key, value, source_persona, time.time()),
        )


def list_facts(user_id: str, limit: int = 20) -> list[dict]:
    """Ein Eintrag pro key, jeweils der neueste Wert - eine korrigierte
    Tatsache (z.B. richtig geschriebener Enkelname) darf nicht neben der
    alten im Kontext auftauchen. Nutzt SQLites dokumentiertes
    Spezialverhalten: bei genau einem MAX()/MIN() in einer
    GROUP-BY-Aggregatabfrage stammen die uebrigen nackten Spalten
    garantiert aus DERSELBEN Zeile wie das Maximum."""
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT key, value, source_persona, MAX(ts) AS ts FROM facts "
            "GROUP BY key ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_fact(user_id: str, key: str) -> str | None:
    """Gezielter Einzel-Lookup des neuesten Werts zu einem Schluessel -
    z.B. fuer die Anrede-Praeferenz (key=f"anrede:{persona_id}")."""
    with get_db(user_id) as db:
        row = db.execute(
            "SELECT value FROM facts WHERE key=? ORDER BY ts DESC LIMIT 1", (key,)
        ).fetchone()
    return row["value"] if row else None


def set_story_consent(user_id: str, story_id: str, status: str, note: str = ""):
    """status: 'kids' | 'adults' | 'private' | 'deleted'"""
    with get_db(user_id) as db:
        db.execute(
            "UPDATE story_fragments SET consent_status=?, consent_note=? "
            "WHERE story_id=?",
            (status, note, story_id),
        )


def story_consent_stats(user_id: str) -> dict:
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT consent_status, COUNT(*) AS n FROM story_fragments "
            "GROUP BY consent_status"
        ).fetchall()
    return {r["consent_status"]: r["n"] for r in rows}


# --- Technikerin-Zufriedenheitsabfrage -----------------------------------

def record_feedback_asked(user_id: str, persona: str) -> int:
    with get_db(user_id) as db:
        cur = db.execute(
            "INSERT INTO feedback (persona, asked_ts) VALUES (?,?)",
            (persona, time.time()),
        )
        return cur.lastrowid


def record_feedback_reply(
    user_id: str, persona: str, reply: str, max_age_seconds: float = 600
) -> bool:
    """Ordnet `reply` der juengsten offenen Nachfrage (reply IS NULL) fuer
    diese Persona zu, aber nur innerhalb eines kurzen Zeitfensters nach
    dem Fragen - sonst koennte eine spaetere, thematisch unabhaengige
    Nachricht faelschlich als Antwort gelten. Liefert False, wenn nichts
    Offenes/Rechtzeitiges gefunden wurde (reiner No-Op)."""
    cutoff = time.time() - max_age_seconds
    with get_db(user_id) as db:
        row = db.execute(
            "SELECT id FROM feedback WHERE persona=? AND reply IS NULL "
            "AND asked_ts >= ? ORDER BY asked_ts DESC LIMIT 1",
            (persona, cutoff),
        ).fetchone()
        if row is None:
            return False
        db.execute(
            "UPDATE feedback SET reply=?, reply_ts=? WHERE id=?",
            (reply, time.time(), row["id"]),
        )
        return True


def last_feedback_asked_ts(user_id: str, persona: str) -> float | None:
    with get_db(user_id) as db:
        row = db.execute(
            "SELECT asked_ts FROM feedback WHERE persona=? "
            "ORDER BY asked_ts DESC LIMIT 1",
            (persona,),
        ).fetchone()
    return row["asked_ts"] if row else None


def list_feedback(user_id: str, limit: int = 50) -> list[dict]:
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT persona, asked_ts, reply, reply_ts FROM feedback "
            "ORDER BY asked_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Vertrauliche Themen ("sicheres Loeschen auf Wunsch") ----------------
#
# Ein Thema wird NICHT aus dem Gespraechsverlauf erraten (weder per
# Stichwortsuche noch per LLM) - zu riskant bei einer unwiderruflichen
# Aktion. Stattdessen wird es aktiv benannt: die Persona fragt einmal
# nach ("Wie sollen wir das nennen?"), die Antwort im naechsten Zug
# wird eingefangen (gleiches Zeitfenster-Muster wie
# record_feedback_reply()). Hoechstens ein offenes/unbenanntes Thema
# pro Persona gleichzeitig - v1-Vereinfachung.

def open_pending_topic(user_id: str, persona: str) -> int:
    with get_db(user_id) as db:
        existing = db.execute(
            "SELECT id FROM active_topics WHERE persona=? AND label IS NULL "
            "AND closed_ts IS NULL",
            (persona,),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = db.execute(
            "INSERT INTO active_topics (persona, label, opened_ts) VALUES (?,?,?)",
            (persona, None, time.time()),
        )
        return cur.lastrowid


def capture_topic_label(
    user_id: str, persona: str, label: str, max_age_seconds: float = 600
) -> bool:
    cutoff = time.time() - max_age_seconds
    with get_db(user_id) as db:
        row = db.execute(
            "SELECT id FROM active_topics WHERE persona=? AND label IS NULL "
            "AND closed_ts IS NULL AND opened_ts >= ? ORDER BY opened_ts DESC LIMIT 1",
            (persona, cutoff),
        ).fetchone()
        if row is None:
            return False
        db.execute(
            "UPDATE active_topics SET label=? WHERE id=?", (label, row["id"])
        )
        return True


def active_topic(user_id: str, persona: str, idle_seconds: float = 86400) -> str | None:
    """Juengstes offenes, benanntes Thema - aber nur, wenn zuletzt
    tatsaechlich etwas damit getaggt wurde (oder es gerade erst
    eroeffnet wurde), nicht laenger als idle_seconds her. Sicherheitsnetz
    gegen ein wochenaltes, vergessenes Thema, das eine unabhaengige
    spaetere Erwaehnung desselben Namens wieder einfangen wuerde."""
    with get_db(user_id) as db:
        row = db.execute(
            "SELECT label, opened_ts FROM active_topics WHERE persona=? "
            "AND label IS NOT NULL AND closed_ts IS NULL "
            "ORDER BY opened_ts DESC LIMIT 1",
            (persona,),
        ).fetchone()
        if row is None:
            return None
        last_activity = db.execute(
            "SELECT MAX(ts) FROM messages WHERE persona=? AND topic=?",
            (persona, row["label"]),
        ).fetchone()[0]
    reference_ts = last_activity if last_activity is not None else row["opened_ts"]
    if time.time() - reference_ts > idle_seconds:
        return None
    return row["label"]


def close_topic(user_id: str, persona: str, label: str):
    with get_db(user_id) as db:
        db.execute(
            "UPDATE active_topics SET closed_ts=? WHERE persona=? AND label=? "
            "AND closed_ts IS NULL",
            (time.time(), persona, label),
        )


def list_topics(user_id: str, persona: str) -> list[str]:
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT label FROM active_topics WHERE persona=? AND label IS NOT NULL "
            "AND closed_ts IS NULL ORDER BY opened_ts",
            (persona,),
        ).fetchall()
    return [r["label"] for r in rows]


def has_pending_secrecy_interaction(
    user_id: str, persona: str, max_age_seconds: float = 600
) -> bool:
    """True, wenn diese Persona gerade mitten in einem vertraulichen
    Wortwechsel mit der Person steckt: entweder wartet sie auf einen
    Themen-Namen (open_pending_topic) oder auf eine Ja/Nein-Bestaetigung
    zum Loeschen (record_deletion_directive mode='pending_confirmation').
    Wird im Gruppenchat gebraucht (siehe main.py room_chat/director.py):
    eine unadressierte Folgenachricht wie ein bloss genannter Themenname
    darf NICHT vom Regisseur an eine andere anwesende Persona geroutet
    werden, sonst landet die Antwort ungetaggt in deren Sicht - echtes
    Leck trotz secrecy.py's eigentlich korrekter Tagging-Logik."""
    cutoff = time.time() - max_age_seconds
    with get_db(user_id) as db:
        pending_topic = db.execute(
            "SELECT 1 FROM active_topics WHERE persona=? AND label IS NULL "
            "AND closed_ts IS NULL AND opened_ts >= ?",
            (persona, cutoff),
        ).fetchone()
        if pending_topic:
            return True
        pending_deletion = db.execute(
            "SELECT 1 FROM deletion_directives WHERE persona=? "
            "AND mode='pending_confirmation' AND executed_ts IS NULL "
            "AND created_ts >= ?",
            (persona, cutoff),
        ).fetchone()
        return pending_deletion is not None


# --- Loeschanweisungen -----------------------------------------------------
#
# Nach der Ausfuehrung wird nicht nur der Gespraechsinhalt geloescht,
# sondern auch der Themen-NAME selbst aus active_topics/
# deletion_directives entfernt bzw. anonymisiert - sonst wuerde "Heinrich"
# als Audit-Spur permanent im Klartext stehen bleiben und das
# "sicher"-Versprechen unterlaufen (per echtem Rauchtest gefunden: die
# Bytes der .sqlite3-Datei enthielten den Namen noch, obwohl die
# Nachrichten selbst laengst weg waren). executed_ts/mode bleiben fuer
# die Nachvollziehbarkeit erhalten - nur WAS geloescht wurde, nicht WANN.
SCRUBBED_LABEL = "[geloescht]"


def record_deletion_directive(
    user_id: str, persona: str, topic_label: str, trigger_phrase: str, mode: str
) -> int:
    """mode: 'pending_confirmation' | 'immediate' | 'on_death'"""
    with get_db(user_id) as db:
        cur = db.execute(
            "INSERT INTO deletion_directives "
            "(persona, topic_label, trigger_phrase, mode, created_ts) "
            "VALUES (?,?,?,?,?)",
            (persona, topic_label, trigger_phrase, mode, time.time()),
        )
        return cur.lastrowid


def confirm_pending_deletion(
    user_id: str, persona: str, max_age_seconds: float = 600
) -> str | None:
    """Fuehrt eine bereits bestaetigte 'jetzt loeschen'-Anfrage aus (der
    Aufrufer hat die Ja-Antwort schon erkannt, siehe secrecy.py). Findet
    die juengste offene pending_confirmation-Anweisung innerhalb des
    Zeitfensters, loescht die getaggten Nachrichten wirklich, entfernt
    auch den Themen-Namen selbst (active_topics-Zeile, Anonymisierung
    in deletion_directives) und vacuumt die Datei. Gibt das Thema
    zurueck (fuer die Bestaetigungs-Antwort DIESES Turns - der Name
    selbst existiert danach in der Datenbank nicht mehr), oder None,
    wenn nichts Offenes/Rechtzeitiges gefunden wurde."""
    cutoff = time.time() - max_age_seconds
    with get_db(user_id) as db:
        row = db.execute(
            "SELECT id, topic_label FROM deletion_directives WHERE persona=? "
            "AND mode='pending_confirmation' AND executed_ts IS NULL "
            "AND created_ts >= ? ORDER BY created_ts DESC LIMIT 1",
            (persona, cutoff),
        ).fetchone()
        if row is None:
            return None
        topic_label = row["topic_label"]
        db.execute(
            "DELETE FROM messages WHERE persona=? AND topic=?",
            (persona, topic_label),
        )
        db.execute(
            "UPDATE deletion_directives SET executed_ts=?, topic_label=?, "
            "trigger_phrase=? WHERE id=?",
            (time.time(), SCRUBBED_LABEL, SCRUBBED_LABEL, row["id"]),
        )
        db.execute(
            "DELETE FROM active_topics WHERE persona=? AND label=?",
            (persona, topic_label),
        )
    vacuum(user_id)
    return topic_label


def vacuum(user_id: str):
    """VACUUM kann nicht innerhalb einer offenen Transaktion laufen -
    deshalb eine eigene, frische Verbindung statt get_db()'s Context-
    Manager. Entfernt geloeschte Inhalte wirklich aus der Datei
    (freigegebene Seiten), nicht nur aus zukuenftigen Abfrageergebnissen."""
    conn = sqlite3.connect(db_path_for(user_id))
    conn.execute("VACUUM")
    conn.close()


def pending_directives(user_id: str) -> list[dict]:
    """Offene Todesfall-Anweisungen - fuer die Admin-Statistik NUR die
    Anzahl relevant, nie der Inhalt (sonst waere der Admin-Zugang selbst
    ein Leck fuer ein Geheimnis, das erst im Todesfall gelöscht werden soll)."""
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT topic_label, created_ts FROM deletion_directives "
            "WHERE mode='on_death' AND executed_ts IS NULL"
        ).fetchall()
    return [dict(r) for r in rows]


def execute_death_directives(user_id: str) -> int:
    """Fuehrt alle offenen Todesfall-Loeschanweisungen aus (aufgerufen
    von main.py's /admin/confirm-death, nachdem ein Admin den Todesfall
    bestaetigt hat). Entfernt wie confirm_pending_deletion() auch den
    Themen-Namen selbst, nicht nur die Nachrichten. Vacuumt am Ende
    genau EINMAL, nicht pro Anweisung - bei mehreren Themen reicht ein
    Durchlauf. Idempotent: ein zweiter Aufruf ohne neue offene
    Anweisungen loescht nichts mehr."""
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT id, persona, topic_label FROM deletion_directives "
            "WHERE mode='on_death' AND executed_ts IS NULL"
        ).fetchall()
        for row in rows:
            db.execute(
                "DELETE FROM messages WHERE persona=? AND topic=?",
                (row["persona"], row["topic_label"]),
            )
            db.execute(
                "UPDATE deletion_directives SET executed_ts=?, topic_label=?, "
                "trigger_phrase=? WHERE id=?",
                (time.time(), SCRUBBED_LABEL, SCRUBBED_LABEL, row["id"]),
            )
            db.execute(
                "DELETE FROM active_topics WHERE persona=? AND label=?",
                (row["persona"], row["topic_label"]),
            )
    if rows:
        vacuum(user_id)
    return len(rows)


# --- Gruppenchat: geteiltes Gedaechtnis (links_to_id) ---------------------
#
# Ein Master haelt den eigentlichen Inhalt, andere anwesende Personas
# bekommen nur eine leichte, darauf verweisende Zeile (add_linked_message
# oben). Drei unterschiedliche Loeschabsichten:
# - delete_own_view: nur eine einzelne Persona "vergisst" es, der Rest
#   bleibt unberuehrt.
# - delete_master_with_handoff: der Master selbst wird geloescht, aber es
#   gibt noch Personas, die sich erinnern - die Master-Rolle wird an eine
#   von ihnen weitergereicht, damit deren Inhalt nicht verwaist.
# - delete_utterance_entirely: niemand soll sich mehr erinnern - Master
#   UND alle Verlinkungen verschwinden zusammen.

def delete_own_view(user_id: str, message_id: int):
    """Loescht NUR eine verlinkte (nicht-Master) Zeile. Auf eine
    Master-Zeile angewendet passiert bewusst nichts - dafuer gibt es
    delete_master_with_handoff()/delete_utterance_entirely()."""
    with get_db(user_id) as db:
        db.execute(
            "DELETE FROM messages WHERE id=? AND links_to_id IS NOT NULL",
            (message_id,),
        )


def delete_master_with_handoff(user_id: str, master_id: int):
    """Loescht eine Master-Zeile. Gibt es noch verlinkte Zeilen, wird die
    aelteste davon zur neuen Master-Zeile befoerdert (Inhalt kopiert,
    eigenes links_to_id geleert), alle uebrigen werden auf die neue
    Master-Zeile umgehaengt. Ohne verbleibende Links wird einfach
    geloescht."""
    with get_db(user_id) as db:
        master = db.execute(
            "SELECT content FROM messages WHERE id=?", (master_id,)
        ).fetchone()
        if master is None:
            return
        links = db.execute(
            "SELECT id FROM messages WHERE links_to_id=? ORDER BY id", (master_id,)
        ).fetchall()
        if links:
            new_master_id = links[0]["id"]
            db.execute(
                "UPDATE messages SET content=?, links_to_id=NULL WHERE id=?",
                (master["content"], new_master_id),
            )
            remaining_ids = [r["id"] for r in links[1:]]
            if remaining_ids:
                db.executemany(
                    "UPDATE messages SET links_to_id=? WHERE id=?",
                    [(new_master_id, rid) for rid in remaining_ids],
                )
        db.execute("DELETE FROM messages WHERE id=?", (master_id,))


def delete_utterance_entirely(user_id: str, master_id: int):
    """'Niemand erinnert sich mehr' - loescht die Master-Zeile UND jede
    Zeile, die auf sie verweist, bewusst OHNE Befoerderung."""
    with get_db(user_id) as db:
        db.execute("DELETE FROM messages WHERE links_to_id=?", (master_id,))
        db.execute("DELETE FROM messages WHERE id=?", (master_id,))
