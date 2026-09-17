from plugins.loader import discover_plugins


def test_discovers_example_weather_plugin():
    plugins = discover_plugins()
    assert "weather" in plugins


def test_internet_plugins_are_disabled_by_default():
    """Sicherheitsprinzip: Plugins mit Internetzugriff sind PER DEFAULT
    AUS, muessen explizit vom Nutzer freigeschaltet werden."""
    plugins = discover_plugins()
    weather = plugins["weather"]
    assert weather.needs_internet is True
    assert weather.enabled is False


def test_manifest_domains_are_exposed_for_transparency_ui():
    plugins = discover_plugins()
    assert "api.open-meteo.com" in plugins["weather"].internet_domains


def test_plugin_module_is_loaded_and_usable():
    plugins = discover_plugins()
    plugin_instance = plugins["weather"].module.Plugin()
    assert hasattr(plugin_instance, "handle")


def test_manifest_exposes_trigger_keywords():
    plugins = discover_plugins()
    assert "wetter" in plugins["weather"].trigger_keywords


def test_manifest_without_trigger_keywords_defaults_to_empty_list(tmp_path, monkeypatch):
    """Ein Manifest ohne trigger_keywords darf nicht crashen - das
    Plugin triggert dann einfach nie automatisch."""
    import json
    import plugins.loader as loader

    plugin_dir = tmp_path / "no_keywords_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(
        json.dumps({
            "id": "no_keywords",
            "name": "Ohne Schluesselwoerter",
            "description": "Testplugin ohne trigger_keywords.",
        }),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        "class Plugin:\n"
        "    async def handle(self, query, user_id):\n"
        "        return 'ok'\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)
    discovered = loader.discover_plugins()
    assert discovered["no_keywords"].trigger_keywords == []
