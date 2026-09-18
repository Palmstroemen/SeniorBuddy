"""
Tests fuer server/admin_settings.py: persistierte Admin-Aenderungen,
die einen Neustart ueberleben muessen (reine In-Memory-Aenderung waere
nach systemctl restart wieder weg).
"""
import admin_settings


def test_load_returns_empty_dict_when_no_file_exists():
    assert admin_settings.load() == {}


def test_update_then_load_roundtrip():
    admin_settings.update("ntfy_topic", "mein-geheimes-topic")
    assert admin_settings.load() == {"ntfy_topic": "mein-geheimes-topic"}


def test_update_preserves_other_keys():
    admin_settings.update("ntfy_topic", "topic-a")
    admin_settings.update("persona_gender", {"freundin": "weiblich"})
    settings = admin_settings.load()
    assert settings["ntfy_topic"] == "topic-a"
    assert settings["persona_gender"] == {"freundin": "weiblich"}


def test_update_overwrites_existing_key():
    admin_settings.update("ntfy_topic", "alt")
    admin_settings.update("ntfy_topic", "neu")
    assert admin_settings.load()["ntfy_topic"] == "neu"
