"""
Tests fuer die leichte lokale Wissensbasis (server/knowledge.py):
reines Stichwort-Overlap-Scoring ueber *.md/*.txt-Dateien pro
Nutzer:in - keine Embeddings, kein Modell.
"""
import knowledge


def _write(user_id, filename, content):
    user_dir = knowledge.KNOWLEDGE_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / filename).write_text(content, encoding="utf-8")


def test_search_returns_empty_when_no_knowledge_dir_exists():
    assert knowledge.search("niemand", "Wo wohnt die Familie?") == []


def test_search_returns_empty_when_query_has_no_content_tokens():
    _write("dave", "familie.md", "Die Familie kommt urspruenglich aus Graz.")
    assert knowledge.search("dave", "Wie ist das?") == []


def test_search_finds_matching_paragraph_by_keyword_overlap():
    _write(
        "dave",
        "familie.md",
        "Die Familie kommt urspruenglich aus Graz und zog spaeter um.\n\n"
        "Der alte Bauernhof stand am Waldrand und hatte drei Kuehe.\n\n"
        "Im Sommer gab es immer ein grosses Erntefest im Dorf.",
    )
    # "bauernhof" und "waldrand" sind wortgleich mit dem Absatz - das
    # Scoring stemmt nicht, Flexionsformen (z.B. "Kuehen" vs. "Kuehe")
    # zaehlen bewusst nicht als Treffer.
    results = knowledge.search("dave", "Erzaehl mir vom Bauernhof am Waldrand")
    assert len(results) == 1
    assert "Bauernhof" in results[0].text
    assert results[0].title == "familie"


def test_search_respects_top_k_and_orders_by_score():
    _write(
        "dave",
        "familie.md",
        "Der Bauernhof hatte Kuehe und Huehner und einen grossen Garten.\n\n"
        "Der Bauernhof am Waldrand war schon alt.\n\n"
        "Das Wetter im Dorf war meistens schoen.",
    )
    results = knowledge.search(
        "dave", "Erzaehl vom Bauernhof mit Garten und Waldrand", top_k=2
    )
    assert len(results) == 2
    assert results[0].score >= results[1].score


def test_search_skips_files_that_cannot_be_read(tmp_path):
    _write("dave", "familie.md", "Der Bauernhof hatte drei Kuehe im Stall.")
    user_dir = knowledge.KNOWLEDGE_DIR / "dave"
    (user_dir / "kaputt.md").write_bytes(b"\xff\xfe\x00\xff\xff\xfe")

    results = knowledge.search("dave", "Wie viele Kuehe waren im Stall?")
    assert len(results) == 1
    assert "Kuehe" in results[0].text


def test_search_ignores_non_md_txt_files():
    _write("dave", "notizen.json", '{"kuehe": "drei im Stall am Bauernhof"}')
    results = knowledge.search("dave", "Wie viele Kuehe waren im Stall am Bauernhof?")
    assert results == []


def test_search_below_min_overlap_returns_nothing():
    _write("dave", "familie.md", "Das Wetter war heute schoen und sonnig.")
    results = knowledge.search("dave", "Kuehe Bauernhof Stall", min_overlap=2)
    assert results == []
