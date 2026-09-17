"""
Leichte lokale Wissensbasis: einfache Stichwort-Suche ueber
Textdateien pro Nutzer:in - keine Embeddings, kein Download, kein
externer Prozess. Ergaenzt die Personen-Fakten in memory.facts: Fakten
sind kurze, strukturierte Aussagen ("Enkel heisst Max"), die
Wissensbasis sind laengere Freitext-Notizen (z.B. "Die
Familiengeschichte", "Der alte Bauernhof"), von Angehoerigen von Hand
als *.md/*.txt unter KNOWLEDGE_DIR/<user_id>/ gepflegt.
"""
import logging
import re
from dataclasses import dataclass

from config import DATA_DIR

log = logging.getLogger("knowledge")

KNOWLEDGE_DIR = DATA_DIR / "knowledge"

# Deutsche Funktions-/Fragewoerter ohne Themensignal. Kein Stemming -
# nur Flexionsformen, die tatsaechlich vorkommen, gehoeren hier rein.
STOPWORDS = {
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einer", "eines", "einem", "einen",
    "und", "oder", "aber", "auch", "als", "dass", "weil", "wenn",
    "mit", "von", "zu", "zum", "zur", "auf", "für", "in", "im",
    "an", "am", "bei", "über",
    "ich", "du", "er", "sie", "es", "wir", "ihr",
    "mein", "meine", "dein", "deine", "sein", "seine",
    "ist", "sind", "war", "waren", "hat", "haben", "wird", "kann", "können",
    "wer", "was", "wo", "wann", "wie", "warum", "weshalb",
    "welche", "welcher", "welches",
}

_WORD_RE = re.compile(r"[a-zäöüß]+")
_MAX_SNIPPET_CHARS = 800


@dataclass
class KnowledgeMatch:
    title: str  # Dateiname ohne Endung
    text: str  # passender Absatz, gekuerzt
    score: int


def _tokenize(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in STOPWORDS}


def _paragraphs(path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def search(
    user_id: str, query: str, top_k: int = 2, min_overlap: int = 2
) -> list[KnowledgeMatch]:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    user_dir = KNOWLEDGE_DIR / user_id
    matches: list[KnowledgeMatch] = []
    for path in sorted(user_dir.glob("*.md")) + sorted(user_dir.glob("*.txt")):
        try:
            paragraphs = _paragraphs(path)
        except (OSError, UnicodeDecodeError):
            log.warning("Wissensdatei konnte nicht gelesen werden: %s", path)
            continue

        for paragraph in paragraphs:
            score = len(query_tokens & _tokenize(paragraph))
            if score >= min_overlap:
                matches.append(
                    KnowledgeMatch(
                        title=path.stem,
                        text=paragraph[:_MAX_SNIPPET_CHARS],
                        score=score,
                    )
                )

    matches.sort(key=lambda m: -m.score)
    return matches[:top_k]
