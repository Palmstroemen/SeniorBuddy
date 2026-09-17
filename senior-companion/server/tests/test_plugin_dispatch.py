"""
Tests fuer server/plugins/dispatch.py - den tatsaechlichen
Ausfuehrungspfad fuer Plugins waehrend eines Chat-Turns.

find_triggered_plugin() ist reine Logik ohne I/O und wird gegen
selbstgebaute PluginInfo-Objekte getestet, nicht gegen die echten
Plugins auf der Platte. run_plugin() bekommt ein Stub-Modul mit einer
eigenen Plugin-Klasse - kein echtes httpx noetig.
"""
from types import SimpleNamespace

from plugins.dispatch import find_triggered_plugin, run_plugin
from plugins.loader import PluginInfo


def _plugin(id="weather", enabled=True, allowed_personas=None, trigger_keywords=None,
            module=None):
    return PluginInfo(
        id=id,
        name=id,
        description="",
        needs_internet=True,
        internet_domains=[],
        allowed_personas=allowed_personas if allowed_personas is not None else ["freundin"],
        enabled=enabled,
        module=module,
        trigger_keywords=trigger_keywords if trigger_keywords is not None else ["wetter"],
    )


# --- find_triggered_plugin ---------------------------------------------

def test_triggers_on_case_insensitive_keyword_match():
    plugins = {"weather": _plugin()}
    result = find_triggered_plugin(plugins, "freundin", "Wie ist das WETTER heute?")
    assert result is not None
    assert result.id == "weather"


def test_does_not_trigger_when_disabled():
    plugins = {"weather": _plugin(enabled=False)}
    assert find_triggered_plugin(plugins, "freundin", "Wie ist das Wetter?") is None


def test_does_not_trigger_when_persona_not_allowed():
    plugins = {"weather": _plugin(allowed_personas=["professor"])}
    assert find_triggered_plugin(plugins, "freundin", "Wie ist das Wetter?") is None


def test_does_not_trigger_without_keyword_match():
    plugins = {"weather": _plugin()}
    assert find_triggered_plugin(plugins, "freundin", "Wie geht es dir heute?") is None


def test_returns_none_for_empty_plugins():
    assert find_triggered_plugin({}, "freundin", "Wie ist das Wetter?") is None


# --- run_plugin ----------------------------------------------------------

class _StubHandleOk:
    async def handle(self, query, user_id):
        return f"Antwort auf '{query}' fuer {user_id}"


class _StubHandleFails:
    async def handle(self, query, user_id):
        raise RuntimeError("Plugin ist kaputt")


async def test_run_plugin_returns_handle_result():
    module = SimpleNamespace(Plugin=_StubHandleOk)
    plugin = _plugin(module=module)
    result = await run_plugin(plugin, "Wie ist das Wetter?", "testnutzer_1")
    assert result == "Antwort auf 'Wie ist das Wetter?' fuer testnutzer_1"


async def test_run_plugin_returns_none_when_handle_raises():
    module = SimpleNamespace(Plugin=_StubHandleFails)
    plugin = _plugin(module=module)
    result = await run_plugin(plugin, "Wie ist das Wetter?", "testnutzer_1")
    assert result is None
