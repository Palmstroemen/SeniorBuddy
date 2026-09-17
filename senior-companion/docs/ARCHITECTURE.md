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
4. **Faktentreue kommt aus Retrieval, nicht aus Modellgröße.**
   Zwei leichte, lokale Quellen (keine Embeddings, kein Download): kurze
   Personen-Fakten (`memory.py`, für alle Personas) und eine
   Stichwort-durchsuchte Wissensbasis aus `.md`/`.txt`-Dateien pro
   Nutzer:in (`knowledge.py`, nur für Personas in `KNOWLEDGE_PERSONAS`,
   aktuell nur der Professor). Beide werden vor dem
   `llm_client.stream()`-Aufruf in `main.py` als Kontext angehängt.
5. **Pro Nutzer:in eine eigene SQLite-Datei** (`server/data/<user_id>.sqlite3`).
   Das ist die Grundlage für Datentrennung und für den Gedanken, diese
   Datei später (z. B. externe Platte) an Hinterbliebene zu übergeben.
6. **Senior:innen warten nie auf Nebennutzung.** `priority.py` +
   `/ws/raw` geben einer zweiten Nutzergruppe (aktuell nur uns selbst,
   "Ausserordentlicher Nutzer") rohen, personalosen Zugriff auf das
   größte Modell – aber mit echter Präemption: eine laufende
   Low-Priority-Generierung wird aktiv abgebrochen, sobald eine
   Senior-Anfrage beginnt, statt nur neue Anfragen abzuweisen. Ollama
   kennt selbst keine Prioritäten und würde sonst intern seriell
   warten lassen.

## Bewusst nicht in Phase 1 enthalten

- **Slot-Management für den Professor bei vielen gleichzeitigen
  Senior:innen** – bei 2 Testnutzern nicht nötig. `scheduler.py` ist so
  gebaut, dass das später ergänzt werden kann, ohne die Struktur
  umzubauen. Nicht zu verwechseln mit `priority.py` (siehe unten): das
  regelt nur "Senior vs. Ausserordentlicher Nutzer", nicht "mehrere
  Senior:innen untereinander".
- **Fernwartungs-Backend** (Config remote ändern, Updates einspielen,
  Statistiken auslesen) – noch nicht gebaut. Ein HTTP-Endpoint, der
  selbst `git pull` + Neustart ausführt, ist bewusst *nicht* der Plan
  (Prozess, der sich selbst mitten im Request neu startet – Verlässlich-
  keits- und Sicherheitsrisiko); eher: Endpoint hinterlegt "Update
  angefordert", ein separater systemd-Timer führt es aus.
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

## Fernwartung & Netzwerk-Sicherheit

- Die App ist eine PWA (kein App-Store-Umweg): Updates sind sofort
  wirksam, sobald der Server neu deployed wird.
- **Kein offener Port, nie.** `uvicorn` bindet in `deploy/senior-companion.service`
  ausschließlich an `127.0.0.1`. Erreichbar wird der Dienst nur über
  `tailscale serve` – ein Rechner mit lokalem LLM/GPU ist ein
  attraktives Ziel für automatisierte Kryptominer-Botnetze, die gezielt
  nach offenen Ports/erreichbaren Inferenz-Endpunkten scannen. Server
  *und* jedes Tablet treten demselben Tailscale-Tailnet bei (WireGuard-
  VPN, geräteweise Authentifizierung) – siehe README.md, Abschnitt
  "Fernzugriff". Das gilt für Tablets im selben Raum genauso wie für
  Fernwartung; es gibt keinen separaten "vertrauten LAN"-Modus mehr.
- **systemd-Härtung** in `deploy/senior-companion.service`:
  `ProtectSystem=strict` (Dateisystem read-only außer
  `server/data/`), `NoNewPrivileges`, `CapabilityBoundingSet=` (keine
  Capabilities), `SystemCallFilter=@system-service` (Seccomp), u. a. –
  ein kompromittierter Prozess (z. B. durch eine Sicherheitslücke in
  einer Abhängigkeit) kann damit kaum etwas außerhalb seines eigenen
  Datenverzeichnisses anrichten. Mit `systemd-analyze verify` geprüft
  (Syntax korrekt; echte Pfade nur auf dem Zielsystem vorhanden, hier
  nicht end-to-end testbar mangels echtem Tailscale-Zweitgerät).
- Deployment: `deploy/setup.sh` für Ersteinrichtung (installiert u. a.
  Tailscale), `deploy/senior-companion.service` für Dauerbetrieb via
  systemd.
- **Honeypot als zusätzliche Erkennungsebene** (`server/honeypot.py`),
  falls VPN/Härtung doch umgangen werden (kompromittiertes
  Tailnet-Gerät, physischer Zugriff): eine per `inotify` überwachte
  Köder-Datei plus ein paar Köder-API-Routen, die kein legitimer Client
  je berührt. Alarm per Push über ntfy.sh, siehe README.md.
  `inotify` statt Zeitstempel-Polling, weil viele Systeme inzwischen
  mit `noatime`/`relatime` mounten, wo ein Lesezugriff die Zugriffszeit
  gar nicht mehr verändert.
