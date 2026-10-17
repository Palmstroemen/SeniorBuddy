"""
Gemeinsame Test-Fixtures.

Zwei Dinge muessen fuer jeden Test stimmen:
1. Das Arbeitsverzeichnis ist server/, weil main.py den Client-Ordner
   relativ dazu einbindet ("../client").
2. Jeder Test bekommt sein eigenes, leeres Datenverzeichnis - Tests
   duerfen NIE echte Nutzerdaten aus server/data/ lesen oder dort
   hineinschreiben.
"""
import os
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))


@pytest.fixture(autouse=True)
def _run_from_server_dir():
    old_cwd = os.getcwd()
    os.chdir(SERVER_DIR)
    try:
        yield
    finally:
        os.chdir(old_cwd)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    import config
    import memory
    import knowledge
    import honeypot
    import admin_settings
    # config.DATA_DIR wird an mehreren Stellen direkt (nicht ueber
    # memory.DATA_DIR) fuer die Aggregation ueber alle Nutzer:innen
    # verwendet (main.py's admin_stats()/admin_feedback(),
    # sentiment_job.py) - ohne diese Zeile wuerden solche Tests
    # unbemerkt echte Daten aus server/data/ mitlesen.
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path)
    monkeypatch.setattr(knowledge, "KNOWLEDGE_DIR", tmp_path / "knowledge")
    monkeypatch.setattr(honeypot, "HONEYPOT_DIR", tmp_path / ".honeypot")
    monkeypatch.setattr(honeypot, "HONEYFILE", tmp_path / ".honeypot" / "zugangsdaten.txt")
    monkeypatch.setattr(admin_settings, "SETTINGS_FILE", tmp_path / "admin_settings.json")
    monkeypatch.setattr(admin_settings, "UPDATE_MARKER_FILE", tmp_path / ".update_requested")
    monkeypatch.setattr(admin_settings, "UPDATE_LOG_FILE", tmp_path / ".update_log")
    yield tmp_path


@pytest.fixture(autouse=True)
def _reset_reaction_audio_cache():
    """reaction_audio._cache ist absichtlich Modul-globaler Zustand
    (soll ueber die gesamte Prozesslaufzeit wirken) - zwischen Tests
    aber isoliert zuruecksetzen, sonst kann ein frueherer Test (in
    IRGENDEINER Datei) denselben (Stimme, Satz)-Cache-Eintrag
    hinterlassen und einen spaeteren Test verfaelschen. War zuvor nur
    lokal in test_reaction_audio.py autouse (wirkt dort NUR innerhalb
    dieser einen Datei) - echte Ueberschneidung live beobachtet
    (Session-Notiz 2026-10-17): RESUMED_REACTION_PHRASES' "sie"/"du"-
    Listen teilen sich mehrere anrede-neutrale Saetze woertlich, zwei
    benachbarte Tests in test_api.py konnten per random.choice()
    zufaellig denselben Satz ziehen und sich so unbemerkt den Cache
    teilen - abhaengig vom globalen random-Zustand, also je nachdem, was
    vorher sonst noch im Testlauf passiert ist."""
    import reaction_audio
    reaction_audio._cache.clear()
    yield
    reaction_audio._cache.clear()
