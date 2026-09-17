"""
Plugin-System.

Jedes Plugin liegt in einem eigenen Ordner mit:
  - manifest.json  (Pflichtfelder: name, description, needs_internet,
                     internet_domains, allowed_personas)
  - plugin.py       (Klasse `Plugin` mit Methode `handle(query, user_id)`)

Der Loader liest ALLE Manifeste ein, bevor irgendein Plugin aufgerufen
werden kann - das ist die Grundlage der Transparenz-Anzeige: das UI
kann jederzeit zeigen, welches Plugin welchen Internetzugriff braucht,
ganz ohne ein einziges Plugin auszufuehren.
"""
import json
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

PLUGINS_DIR = Path(__file__).resolve().parent


@dataclass
class PluginInfo:
    id: str
    name: str
    description: str
    needs_internet: bool
    internet_domains: list[str]
    allowed_personas: list[str]
    enabled: bool  # Nutzer-Freigabe, standardmaessig False bei needs_internet=True
    module: object  # geladenes Python-Modul mit Plugin-Klasse
    # Case-insensitive Substring-Trigger fuer main.py's Chat-Handler.
    # Fehlt das Manifest-Feld, triggert das Plugin einfach nie automatisch.
    trigger_keywords: list[str] = field(default_factory=list)


def discover_plugins() -> dict[str, PluginInfo]:
    plugins = {}
    for folder in PLUGINS_DIR.iterdir():
        manifest_path = folder / "manifest.json"
        plugin_path = folder / "plugin.py"
        if not (manifest_path.exists() and plugin_path.exists()):
            continue

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        spec = importlib.util.spec_from_file_location(
            f"plugins.{folder.name}.plugin", plugin_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        plugins[manifest["id"]] = PluginInfo(
            id=manifest["id"],
            name=manifest["name"],
            description=manifest["description"],
            needs_internet=manifest.get("needs_internet", False),
            internet_domains=manifest.get("internet_domains", []),
            allowed_personas=manifest.get("allowed_personas", []),
            # Sicherheitsprinzip: Internet-Plugins sind PER DEFAULT AUS.
            # Muss ueber die App explizit vom Nutzer freigeschaltet werden.
            enabled=not manifest.get("needs_internet", False),
            module=module,
            trigger_keywords=manifest.get("trigger_keywords", []),
        )
    return plugins
