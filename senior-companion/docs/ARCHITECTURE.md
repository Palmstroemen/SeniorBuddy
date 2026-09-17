# Architektur

Kurzreferenz der Entscheidungen aus der Konzeptphase, damit sie nicht
nur im Chat-Verlauf existieren.

## Grundprinzipien

1. **Lokal zuerst.** Alle Personas laufen auf lokalen Modellen (Ollama).
   Internetzugriff ist ausschließlich über Plugins mit explizitem
   Manifest möglich (`server/plugins/*/manifest.json`), standardmäßig
   deaktiviert, und wird protokolliert (`memory.log_external_request`).
2. **Transparenz ist ein UI-Feature, kein Logfile.** Das Panel in der
   App zeigt Nutzer:innen jederzeit, was nach draußen ging.
3. **Vier Personas, zwei Modellgrößen.** Freundin, Reporter und
   Technikerin: kleine, immer geladene Modelle (7B-Klasse). Professor:
   größeres Modell (aktuell 32B), aber bewusst kein Riesenmodell –
   siehe unten. Die Technikerin ist für Sicherheit/Einstellungen
   zuständig, kann sie aber (Stand jetzt) nur erklären, nicht selbst
   ausführen – siehe Kommentar bei ihrer Persona in `config.py`.
4. **Faktentreue kommt aus Retrieval, nicht aus Modellgröße.** Für
   Phase 1 ist noch keine RAG-Anbindung eingebaut (Platzhalter in
   `personas.py`/`main.py`) – das ist der nächste sinnvolle Ausbauschritt,
   sobald das Grundsystem läuft.
5. **Pro Nutzer:in eine eigene SQLite-Datei** (`server/data/<user_id>.sqlite3`).
   Das ist die Grundlage für Datentrennung und für den Gedanken, diese
   Datei später (z. B. externe Platte) an Hinterbliebene zu übergeben.

## Bewusst nicht in Phase 1 enthalten

- **Prioritäts-Warteschlange / Slot-Management für den Professor bei
  vielen gleichzeitigen Nutzer:innen** – bei 2 Testnutzern nicht nötig.
  `scheduler.py` ist so gebaut, dass das später ergänzt werden kann,
  ohne die Struktur umzubauen.
- **RAG / lokale Wissensbasis** – noch nicht angebunden. Sobald sie
  existiert, wird sie als weiterer Kontext-Baustein vor den
  `llm_client.stream()`-Aufruf in `main.py` gehängt.
- **Story-Verdichtung / Formatierung fürs Hinterbliebenen-Erinnerungsbuch**
  – das Datenmodell (`story_fragments`, `consent_status`) existiert
  schon in `memory.py`, aber der Verdichtungs-/Exportprozess selbst
  ist noch zu bauen.
- **Offline-STT/TTS auf dem Tablet** – Phase 1 nutzt die Web Speech API
  des Browsers (online, geräteabhängig). Der Wechsel zu whisper.cpp
  (WASM) betrifft ausschließlich `client/js/app.js`.

## Warum Ollama statt direkt llama.cpp?

Einfachere Modellverwaltung (`ollama pull`), stabile HTTP-API, leichter
Wechsel zwischen Modellen. Der komplette Zugriff läuft über
`server/llm_client.py` – ein Wechsel auf einen rohen llama.cpp-Server
würde nur diese eine Datei betreffen.

## Testphilosophie

Ab jetzt TDD: neue Funktionalität bekommt zuerst einen fehlschlagenden
Test in `server/tests/`, dann die Implementierung dazu. Die
Retrofit-Suite für den bestehenden Code deckt bereits zwei reale Bugs
als Regressionstests ab:

- `test_scheduler.py::test_prewarm_time_at_full_hour` – die ursprüngliche
  Vorwärm-Zeitberechnung ergab bei voller Stunde `minute=-10` und ließ
  den Scheduler beim Start crashen.
- `test_scheduler.py::test_setup_scheduler_is_idempotent` – ohne
  Shutdown-Handler blieb der Scheduler über App-Neustarts hinweg aktiv
  und ein zweiter Start warf `SchedulerAlreadyRunningError`.

Externe Aufrufe (`llm_client`) werden in Tests ausschließlich gemockt
(`pytest-httpx`) – das passt zum Grundprinzip "vorsichtig mit
Internet": auch Tests dürfen nichts unbeabsichtigt nach draußen
schicken.

## Fernwartung

- Die App ist eine PWA (kein App-Store-Umweg): Updates sind sofort
  wirksam, sobald der Server neu deployed wird.
- Empfohlener Fernzugriff: Tailscale (oder vergleichbares VPN) statt
  offener Ports.
- Deployment: `deploy/setup.sh` für Ersteinrichtung,
  `deploy/senior-companion.service` für Dauerbetrieb via systemd.
