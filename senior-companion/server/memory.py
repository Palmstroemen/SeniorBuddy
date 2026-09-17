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
"""


def db_path_for(user_id: str) -> Path:
    return DATA_DIR / f"{user_id}.sqlite3"


@contextmanager
def get_db(user_id: str):
    conn = sqlite3.connect(db_path_for(user_id))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_message(user_id: str, persona: str, role: str, content: str):
    with get_db(user_id) as db:
        db.execute(
            "INSERT INTO messages (persona, role, content, ts) VALUES (?,?,?,?)",
            (persona, role, content, time.time()),
        )


def recent_messages(user_id: str, persona: str, limit: int = 20) -> list[dict]:
    with get_db(user_id) as db:
        rows = db.execute(
            "SELECT role, content, ts FROM messages WHERE persona=? "
            "ORDER BY id DESC LIMIT ?",
            (persona, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


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


def set_story_consent(user_id: str, story_id: str, status: str, note: str = ""):
    """status: 'kids' | 'adults' | 'private' | 'deleted'"""
    with get_db(user_id) as db:
        db.execute(
            "UPDATE story_fragments SET consent_status=?, consent_note=? "
            "WHERE story_id=?",
            (status, note, story_id),
        )
