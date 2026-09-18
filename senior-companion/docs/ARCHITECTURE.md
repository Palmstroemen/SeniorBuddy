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
- **Story-Verdichtung / Formatierung fürs Hinterbliebenen-Erinnerungsbuch**
  – das Datenmodell (`story_fragments`, `consent_status`) existiert
  schon in `memory.py`, aber der Verdichtungs-/Exportprozess selbst
  ist noch zu bauen.
- **Offline-STT/TTS direkt im Browser (WASM)** – ursprünglich als
  whisper.cpp-WASM-Umstieg angedacht; stattdessen umgesetzt als
  **serverseitige Alternative** (`speech-service/`, `faster-whisper` +
  `Piper`, siehe unten), weil zwei ältere Tablets im Testbetrieb
  schwächer als aktuelle Modelle sind – Rechenlast lieber auf den
  Server verlagern als im Browser stemmen. Web Speech API bleibt
  Standard-Fallback, beides ist pro Tablet umschaltbar. Ein echter
  WASM-Weg direkt im Browser bleibt eine Option, falls beide Server
  irgendwann nicht mehr ausreichen.

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

## Sprachdienst (STT/TTS)

`speech-service/` ist ein **eigener Prozess mit eigenem venv**, genau
wie Ollama nicht Teil von `server/` ist – `server/speech_client.py`
spricht per HTTP damit, wie `llm_client.py` mit Ollama. Bewusste
Abweichung vom Referenzprojekt YulYens_AI: dort laufen `faster-whisper`
und `Piper` direkt im Haupt-Prozess importiert (geprüft: kein
separater Dienst, "Einfachheit vor Ressourcen-Isolation" war dort die
bewusste Wahl). Hier ist die Isolation aber genau der Punkt – zwei
ältere Tablets im Testbetrieb sollen nicht durch Serverlast ausgebremst
werden, und der Server (mehrkerniger Mini-PC) soll dem Sprachdienst bei
Bedarf gezielt einzelne Kerne zuweisen können (`AllowedCPUs=` in
`speech-service/deploy/speech-service.service`, vorbereitet, aber
bewusst nicht aktiviert – erst nach echter Messung, nicht vorab).

**Keine Integration mit `priority.py`** – bewusst geprüft und
verworfen: `priority.py` regelt Konkurrenz um *Ollama*-Generierungen
(die Ollama intern serialisiert). Der Sprachdienst ist ein komplett
separater Prozess mit eigenem Ressourcenpool und konkurriert nicht auf
dieselbe Weise; eine Vermischung der beiden Mechanismen würde nur
Verwirrung stiften.

Persona → Stimme läuft über das schon bestehende `voice_id`-Feld in
`PersonaVariant` (`server/config.py`) – bis hierher ein toter
Platzhalter seit der ersten Persona-Runde, jetzt ein echter
Piper-Stimmenname. Wie bei Ollama-Modellnamen muss die Stimmdatei
separat geladen werden (`speech-service/setup.sh` lädt eine, weitere
Stimmen sind im Quellcode schon korrekt benannt, aber nicht
mitgeliefert – Download-Hinweis in README.md).

Umschaltbar pro Tablet (`localStorage`, wie die `?user=`-Geräte-Identität),
nicht pro Installation – die Tablets sind unterschiedlich leistungsstark,
die Entscheidung ist also wirklich pro Gerät sinnvoll, nicht global.

## Fernwartungs-API (`/admin/*`)

Erste und einzige Stelle im Projekt mit echter Authentifizierung
(`server/admin_auth.py`, Bearer-Token via `SENIOR_COMPANION_ADMIN_TOKEN`,
konstant-zeitiger Vergleich wie YulYens_AIs `hmac.compare_digest`).
Fail-closed: ohne gesetztes Token antwortet die ganze `/admin/*`-API mit
503, nie offen. Bewusst getrennt vom offenen `/api/*`-Namensraum, weil
Konfiguration ändern und Updates anstoßen privilegierte Operationen
sind, anders als der Rest der API (offen fürs Tailnet).

Config-Änderungen (`PERSONA_GENDER`, `NTFY_TOPIC`) wirken sofort (dieselben
Objekte, die auch `config.py`/`honeypot.py` zur Laufzeit lesen, werden
direkt mutiert) **und** überstehen einen Neustart – `server/admin_settings.py`
persistiert sie in `server/data/admin_settings.json`, `main.py`s
`_apply_persisted_admin_settings()` wendet sie beim Start wieder an,
bevor der Server Anfragen annimmt.

**"Updates einspielen" läuft nicht im App-Prozess.** `/admin/update`
schreibt nur eine Marker-Datei (`admin_settings.UPDATE_MARKER_FILE`);
`deploy/senior-companion-updater.path` (ein systemd-`.path`-Unit)
bemerkt die Änderung und startet `deploy/run_update.sh` — läuft
bewusst **ohne** `User=` (also als root), weil `git pull` +
`systemctl restart` privilegierte Operationen sind, aber nie direkt
vom Netz erreichbar ist: der FastAPI-Prozess (unprivilegiert, `User=
senior-companion`) kann ausschließlich die Marker-Datei schreiben,
sonst nichts. Getrennt getestet: die Git-Pull-Mechanik real gegen ein
lokales Fake-Origin-Repository (Fast-Forward-Pull funktioniert), das
komplette Skript real mit einem Fake-`systemctl` (Kontrollfluss,
Logging, Exit-Code) — nur die echten `systemctl`-Aufrufe selbst sind
hier mangels root/systemd nicht end-to-end testbar.

Plugin an/aus läuft weiter über das bestehende `/api/plugins/{id}/toggle`
(bewusst ohne eigenes Auth, Teil der offenen Senior-Oberfläche) — kein
zusätzlicher Admin-Endpoint nötig, nur dieselbe Route auch als Teil des
Fernwartungsumfangs verstanden.

### Statistik, Zufriedenheit & Anrede

`/admin/stats` liefert zusätzlich zu den bestehenden Feldern:

- **`usage`** (pro Nutzer:in): Sitzungen/aktive Minuten/aktive Tage,
  rein aus vorhandenen Nachrichten-Zeitstempeln berechnet
  (`memory.usage_stats()`), keine zusätzliche Instrumentierung nötig.
- **`sentiment`**: Stimmung (positiv/neutral/negativ) und
  Zustimmung/Widerspruch pro Nutzer:in. Bewusst **nicht** per
  Stichwort-Heuristik (anders als `security.py`/`analysis.py`),
  sondern per lokalem Modell in einem nächtlichen Hintergrund-Job
  (`server/sentiment_job.py`, 03:00 Uhr via `scheduler.py`) —
  genauer bei natürlicher Sprache, dafür ohne Latenz-Einfluss auf den
  Live-Chat. Läuft als Low-Priority-Task (`priority.py`): eine
  Senior-Anfrage bricht ihn jederzeit ab, der nächste Lauf macht
  weiter (jede Nachricht wird einzeln klassifiziert und committet).
  `unclassified_pending` zeigt ehrlich, wie viel der nächste Lauf noch
  vor sich hat.
- **`external_requests`**: Internet-/Plugin-Anfragen pro Nutzer:in,
  aggregiert aus der bestehenden `external_requests`-Tabelle. Jedes
  Plugin protokolliert dort selbst (Vertrag aus
  `plugins/example_weather/plugin.py`s Docstring: "VOR dem
  eigentlichen Request: log_external_request() aufrufen") — beim
  Bauen wurde bewusst geprüft, ob `plugins/dispatch.py`s `run_plugin()`
  das zusätzlich tun sollte, und wieder verworfen: das hätte jeden
  echten Plugin-Trigger doppelt protokolliert (per Regressionstest
  `test_run_plugin_does_not_double_log_a_plugin_that_logs_itself`
  nachgewiesen). `run_plugin()` selbst loggt daher nichts.
- **`story_consent`**: Verteilung der Geschichten-Freigaben
  (kids/adults/private/deleted/undecided). Bewusst **ohne**
  Auswertung von `external_requests.approved_by_user` — die Spalte
  wird nirgends gesetzt (es gibt keinen Consent-Dialog vor einem
  Plugin-Aufruf, nur die globale Plugin-Freischaltung), ein
  immer-0-Feld würde falsche Schlüsse nahelegen.
- **`problems`**: Guard-Blocks (Eingabe/Kontext getrennt gezählt,
  `security.py`) und Plugin-Fehler (`plugins/dispatch.py`) — gleiches
  Zähler-Muster wie `priority.preemption_count()`/
  `honeypot.trigger_count()`.
- **`persona_usage`** ("Freundeskreis"): pro Nutzer:in aufgeschlüsselt
  nach Persona, wie oft (Nachrichten, Sitzungen) und wie lange
  (aktive Minuten) tatsächlich mit ihr gesprochen wird —
  `memory.persona_usage_stats()` ruft dafür `memory.usage_stats()`
  je Persona auf (dort jetzt per `persona`-Parameter filterbar, statt
  wie bisher immer über alle Personas gepoolt). Gedacht, um sichtbar
  zu machen, dass unterschiedliche Personen unterschiedliche Personas
  bevorzugen (z. B. Professor vs. Freundin) — relevant, sobald weitere
  Personas dazukommen.

**Anrede (Du/Sie):** wird bewusst nicht dem Sprachmodell überlassen,
sondern explizit gespeichert — kleine lokale Modelle halten Konsistenz
über eine lange Unterhaltung oder mehrere Sitzungen hinweg nicht
zuverlässig durch, und ohne diese Änderung stand in keinem
System-Prompt überhaupt etwas zur Anredeform. Default ist überall
"Sie" (jetzt auch statisch in jedem `system_prompt`, `config.py`).
Ein ausdrückliches Du-Angebot (`server/analysis.py`, Regex-Muster wie
`security.py`) schaltet sofort um — keine Rückfrage, das war eine
bewusste Abwägung: einfacher Mechanismus, ein falsch erkanntes Angebot
lässt sich jederzeit über `POST /api/facts/{user_id}`
(`key=anrede:<persona_id>`) manuell korrigieren. Gespeichert wird das
in der ohnehin vorhandenen `facts`-Tabelle (`key=f"anrede:{persona_id}"`),
keine neue Tabelle. Pro Turn wird der aktuell geltende Stand als
System-Kontext-Nachricht injiziert (`main.py`, gleiches Muster wie
Fakten/Wissensbasis).

**Technikerin-Zufriedenheitsabfrage:** fragt in eigenen Worten hin und
wieder nach Zufriedenheit/Verbesserungswünschen (`satisfaction.py`,
Standard-Intervall 7 Tage, admin-konfigurierbar über
`POST /admin/config/satisfaction-interval`). Läuft rein im
Chat-Handler (`main.py`) mit, kein Scheduler-Job nötig — ein
Server-initiierter Push in eine offene, aber gerade inaktive
WebSocket-Verbindung wäre ein deutlich größeres Feature gewesen als
verlangt. Die nächste Nutzer-Nachricht nach der Frage wird als Antwort
erfasst, aber nur innerhalb eines kurzen Zeitfensters (Standard 10
Minuten, `memory.record_feedback_reply()`) — sonst könnte eine
spätere, thematisch unabhängige Nachricht fälschlich als Antwort
gelten. Aggregiert über `GET /admin/feedback`.

Erste Schema-Änderung des Projekts (`messages` bekommt `sentiment`/
`stance`-Spalten, `feedback` ist eine neue Tabelle): `memory._migrate()`
ergänzt fehlende Spalten idempotent per `ALTER TABLE` bei jedem
`get_db()`-Aufruf, da `CREATE TABLE IF NOT EXISTS` bei bereits
existierenden Tabellen nichts mehr bewirkt.
