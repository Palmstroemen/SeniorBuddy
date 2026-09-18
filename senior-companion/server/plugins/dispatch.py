"""
Fuehrt Plugins tatsaechlich aus und liefert ihr Ergebnis, damit es der
Aufrufer (main.py) als Kontext fuer den aktuellen Chat-Turn injizieren
kann - siehe docs/ARCHITECTURE.md, Abschnitt zur RAG-Anbindung: "wird
als weiterer Kontext-Baustein vor den llm_client.stream()-Aufruf
gehaengt". Genau diese Stelle im Ablauf bedient dieses Modul.

Trigger-Logik ist bewusst simpel gehalten (Substring-Match auf
trigger_keywords aus dem Manifest) - kein NLP, keine Scores. Das passt
zum Umfang: es gibt heute ein Plugin.
"""
import logging

from plugins.loader import PluginInfo

log = logging.getLogger("plugins.dispatch")

# Fuer die "Probleme"-Statistik in /admin/stats.
_plugin_failure_count = 0


def plugin_failure_count() -> int:
    return _plugin_failure_count


def find_triggered_plugin(
    plugins: dict[str, PluginInfo], persona_id: str, query: str
) -> PluginInfo | None:
    """Reine Funktion, kein I/O - leicht ohne Mocks testbar.

    Liefert das erste passende Plugin (Einfuegereihenfolge von
    `plugins`) oder None. Bewusst nur eines: bei heute einem Plugin
    ist "mehrere gleichzeitig triggernde Plugins zusammenfuehren"
    verfruehte Komplexitaet - ein zweites, gleichzeitig triggerndes
    Plugin wuerde damit ignoriert. Wird relevant, sobald ein zweites
    Plugin dazukommt."""
    query_lower = query.lower()
    for plugin in plugins.values():
        if not plugin.enabled:
            continue
        if persona_id not in plugin.allowed_personas:
            continue
        if any(kw.lower() in query_lower for kw in plugin.trigger_keywords):
            return plugin
    return None


async def run_plugin(plugin: PluginInfo, query: str, user_id: str) -> str | None:
    """Fuehrt handle() aus und faengt JEDEN Fehler ab - ein kaputtes
    oder nicht erreichbares Plugin darf einen Chat-Turn nie zum
    Absturz bringen. None bedeutet: kein Kontext zum Injizieren.

    Loggt NICHT selbst nach memory.log_external_request() - das ist
    Aufgabe des jeweiligen Plugins (siehe
    plugins/example_weather/plugin.py: 'VOR dem eigentlichen Request:
    log_external_request() aufrufen'), da nur das Plugin selbst weiss,
    WAS tatsaechlich nach draussen ging. Ein zusaetzlicher Aufruf hier
    wuerde jeden Plugin-Trigger doppelt protokollieren."""
    global _plugin_failure_count
    try:
        instance = plugin.module.Plugin()
        return await instance.handle(query, user_id)
    except Exception:
        _plugin_failure_count += 1
        log.exception("Plugin '%s' ist bei der Ausfuehrung fehlgeschlagen", plugin.id)
        return None
