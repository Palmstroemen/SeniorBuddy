# Senior-Korrespondenz-System

Lokales, transparentes Gesprächssystem mit mehreren Personas
(Freundin, Lebensreporter, Professor) für ältere Menschen.
Läuft primär offline auf lokaler Hardware; Internetzugriff nur über
explizit freigegebene, protokollierte Plugins.

Siehe [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) für die
Hintergründe zu den Architekturentscheidungen.

## Schnellstart (Entwicklung/Test)

Voraussetzungen:
- Python 3.11+
- [Ollama](https://ollama.com) installiert und lauffähig

```bash
git clone <dein-repo-url>
cd senior-companion
./deploy/setup.sh
cd server
source .venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Danach im Browser (auf dem Server-Gerät oder von einem Tablet im
selben Netzwerk) öffnen:

```
http://<server-ip>:8000
```

Auf dem Tablet: über das Browser-Menü "Zum Startbildschirm hinzufügen"
wählen, damit die Web-App wie eine normale App aussieht.

## Mehrere Personen (ein gemeinsamer Server, mehrere Tablets)

Jedes Tablet bekommt beim Einrichten eine eigene URL mit
`?user=<name>`, z. B. `http://<server-ip>:8000/?user=maria`. Vor
"Zum Startbildschirm hinzufügen" diese URL im Browser öffnen – das
Tablet merkt sich damit dauerhaft, zu wem es gehört, ohne
Login-Bildschirm. Im Transparenz-Panel (ⓘ) steht das aktive Profil zur
Kontrolle.

## Tests (TDD)

Für neue Funktionen gilt: Test zuerst schreiben (rot sehen), dann
Code, bis der Test grün ist. Die bestehende Testsuite lebt unter
`server/tests/` und läuft komplett offline/gemockt (kein echter
Ollama-Server nötig, kein echter Internetzugriff in Tests).

```bash
cd server
source .venv/bin/activate
pytest -v
```

`deploy/setup.sh` installiert die Test-Abhängigkeiten mit.

## Struktur

```
server/     FastAPI-Backend, Personas, Speicher, Plugins, Scheduler
client/     PWA (Chat-Oberfläche, läuft im Browser des Tablets/Handys)
plugins/    liegt unter server/plugins/ – jedes Plugin: manifest.json + plugin.py
deploy/     systemd-Service + Setup-Skript für den Dauerbetrieb
docs/       Architekturentscheidungen
```

## Eigene Modelle konfigurieren

Persona-Modelle stehen in [`server/config.py`](server/config.py).
Modellnamen müssen mit `ollama list` übereinstimmen bzw. vorher per
`ollama pull <name>` geladen werden.

Jede Persona hat einen Namen und drei Text-/Stimm-Varianten
(`neutral`/`weiblich`/`maennlich`); welche aktiv ist, steht pro
Installation in `PERSONA_GENDER` in `server/config.py` (Default:
`neutral`). Ändern = Wert eintragen, Server neu starten.

## Neues Plugin hinzufügen

1. Ordner unter `server/plugins/<mein_plugin>/` anlegen
2. `manifest.json` mit `id`, `name`, `description`, `needs_internet`,
   `internet_domains`, `allowed_personas` anlegen (siehe
   `server/plugins/example_weather/manifest.json` als Vorlage)
3. `plugin.py` mit einer Klasse `Plugin` und einer `async def handle(...)`
   Methode schreiben
4. Server neu starten – das Plugin erscheint automatisch im
   Transparenz-Panel der App, standardmäßig deaktiviert, wenn es
   Internet braucht

## Stand / nächste Schritte

Dies ist das Grundgerüst für Phase 1 (Test mit 1–2 Personen). Siehe
`docs/ARCHITECTURE.md`, Abschnitt "Bewusst nicht in Phase 1 enthalten"
für den Ausbaupfad (RAG, Mehrbenutzer-Scheduling, Story-Export, u. a.).
