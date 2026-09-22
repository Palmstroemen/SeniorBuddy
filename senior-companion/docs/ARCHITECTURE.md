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
- **Kontinuierliches, vorausschauendes Sprechen** (spekulative
  Satz-für-Satz-Generierung mit Vorrats-Puffer, verzweigte
  Vorausberechnung, Antwort-Bausteine mit Lücken) – ausführlich unter
  "Vision: Kontinuierliches, vorausschauendes Sprechen" weiter unten.

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

## Vision: Kontinuierliches, vorausschauendes Sprechen (noch nicht gebaut)

Aus einer langen Gesprächs-Session (2026-09-22) entstanden, ausgelöst
durch reale Wiederholungs-/Latenzprobleme bei den Auto-Turns (siehe
`server/main.py::_too_similar_to_own_recent`, `_first_sentence` –
symptomatische Fixes, die zum Auslöser dieser Vision wurden). Noch
nicht geplant oder gebaut – hier bewusst als zusammenhängender Entwurf
festgehalten, damit er griffbereit ist, sobald er angegangen wird.
Konkrete Beispiel-Dialoge, die die gewünschte Tonalität zeigen, liegen
unter `/Beispieltexte/` (Repo-Root, ausserhalb von `senior-companion/`)
– `Wohnort.md`, `Beziehungsstatus.md`, `Politik.md`, wachsend.

**Das Grundproblem mit dem heutigen Modell:** `room_chat()` generiert
reaktiv – entweder auf eine echte Nutzer-Nachricht hin, oder alle
`autoturn.SHORT_PAUSE_SECONDS` per Tick-Check. Zwischen "die Person
schweigt" und "die Persona sagt etwas" liegt darum immer eine spürbare
Lücke, und jede Generierung startet bei Null, ohne Vorlauf.

**Die Idee: Generierung und Zustellung sind zwei getrennte Schichten.**
Wie beim Schach, wo man mehrere Züge vorausdenkt: der Textgenerator
läuft praktisch durchgehend und geht erst dann in einen Ruhezustand,
wenn ein Vorrat (Richtwert: ~5 Sätze) an vorbereiteten Sätzen aufgebaut
ist – nicht getriggert durch Stille, sondern durch einen vollen
Vorrats-Speicher. Ein separater "Regisseur" entscheidet, WANN aus
diesem Vorrat tatsächlich gesprochen wird (z. B. eine kurze
Atempause nach einer Frage, damit die Person antworten kann) – die
Generierung selbst wartet darauf nie.

**Verzweigte Vorausberechnung statt linearer Vorbereitung:**
- Bei einer Ja/Nein-Frage werden **beide** möglichen Fortsetzungen
  vorbereitet, bevor die Frage überhaupt zu Ende gesprochen ist.
- Bei offenen Fragen (z. B. einem Namen) wird ein **Antwort-Textbaustein
  mit Lücke** vorbereitet und größtenteils schon vorab vertont; nur das
  fehlende Stück (der Name) muss nach dem Hören noch synthetisiert und
  eingefügt werden. Beispiel (Wortlaut aus dem Gespräch):
  - Frage: "Wie heißt denn das Enkelchen?"
  - Vorbereiteter Baustein: "`<name>`! Was für ein netter Name! Habe
    ich es richtig erfasst? `<name>` heißt das Kind?"
  - Sogar `<name>` selbst wird vorsorglich in zwei Betonungs-Varianten
    vorgerendert (einmal freudig erregt, einmal nüchterner), damit das
    zusammengesetzte Ergebnis trotzdem stimmig klingt, ganz ohne
    Synthese-Umweg im kritischen Moment.
  - Parallel läuft die Generierung schon weiter voraus: die nächste
    Frage ("Ist das ein Bub oder ein Mädchen? ... Ah ein
    `<Bub/Mädchen>`! ...") wird bereits vorbereitet, inklusive beider
    aufzählbarer Werte für die Lücke.
- **Regional-/Dialekt-bewusstes Vokabular:** "Bub" (Österreich) vs.
  "Junge" (Deutschland) wurde als konkretes Beispiel genannt – die
  Bausteine/Lücken-Füllungen müssen wissen, in welcher
  deutschsprachigen Region die Person lebt, statt ein festes
  Standarddeutsch anzunehmen. Wie diese Information erfasst wird
  (neuer Fakt? aus der Sprachverwendung abgeleitet?) ist noch offen.

**Gesprächsführung als Ermöglicher, nicht als Bevormundung.** Eine
wirklich offene Frage wie "Worüber möchtest du als nächstes sprechen?"
lässt sich nicht sinnvoll verzweigt vorausberechnen – die Antwort kann
alles sein. Löst sich, indem die Persona die Frage selbst eingrenzt:
"Worüber möchtest du als nächstes sprechen? Lieber über Politik oder
über Musik?" Damit gibt es nur noch wenige, konkret vorbereitbare
Antwortzweige – einen pro angebotener Option, plus **einen generischen
Auffangzweig** für "etwas ganz anderes" (den die Person sich jederzeit
nehmen darf, das Angebot ist eine Einladung, keine Einschränkung –
"Der User kann es ja ohnehin über den Haufen werfen"). Beispiel
(Wortlaut aus dem Gespräch):
- "Politik! Sehr gut. Habe ich heute in der Zeitung gelesen ..."
- "Musik! Wunderbar. Wir hatten zuletzt ja über Bach gesprochen ..."
- (Auffangzweig) "`<anderes Thema>` auch gut. Du möchtest also lieber
  über `<anderes Thema>` sprechen. Soll mir recht sein. Über
  `<anderes Thema>` hatten wir ja zuletzt schon gesprochen. Wenn ich
  mich recht erinnere, hattest du gemeint, dass ..."

Der Auffangzweig zeigt auch, warum das nicht nur ein Trick für
weniger Verzweigungen ist, sondern echten Wert hat: er greift auf die
ohnehin schon bestehende Historie/Fakten-Ablage zurück (`memory.
list_facts()`/`recent_messages()`), damit selbst eine unerwartete
Antwort persönlich und aufmerksam wirkt, nicht generisch. Diese
Eingrenzungs-Technik ist damit keine Nebensache, sondern die
Voraussetzung dafür, dass die verzweigte Vorausberechnung oben
überhaupt für echte (nicht nur Ja/Nein-)Themenwahl funktioniert.

**Verwerfen ist ein akzeptierter Preis, kein Problem.** Antwortet die
Person und lenkt das Gespräch in eine andere Richtung, wird ein
relevanter Teil des bereits generierten (und teils schon vertonten)
Vorrats einfach verworfen. Das ist im Modell so vorgesehen, nicht ein
Zeichen für Verschwendung, die es zu vermeiden gilt.

**Messbarkeit von Anfang an mitbauen, nicht nachträglich.** Damit sich
die Vorrats-Tiefe (Richtwert 5 Sätze) tatsächlich begründen lässt statt
geraten zu werden: mitloggen, wie viel vorausberechnet und wie viel
davon verworfen wird, und vor allem, wie oft der 2., 3., 4., 5.
vorausberechnete Satz tatsächlich noch zum Einsatz kommt, bevor eine
echte Antwort dazwischenkommt. Kommt der 5.-Satz-Vorrat z. B. nur in
~5 % der Fälle tatsächlich zum Einsatz, lohnt sich diese Tiefe nicht;
liegt die Quote eher bei 20–30 %, schon. Diese Kennzahl entscheidet
die Tuning-Frage empirisch statt aus dem Bauch heraus.

**Charakter/Ton-Prinzip, das hier mit hineinspielt** (siehe
`Beispieltexte/`): Personas dürfen herausfordernd, frech oder kantig
sein – ausdrücklich gewünscht, nicht nur toleriert, solange die Person
die Persona trotzdem sympathisch findet. Wird später vermutlich zu
einstellbaren Parametern pro Persona (ähnlich `reengagement_tendency`),
nicht zu einem einheitlichen "immer bravem" Standardton.

**Einordnung:** das ist ein System-Umbau, kein Prompt-Tweak – ein vom
Sende-/Tick-Zyklus entkoppelter Hintergrund-Generierungs-Loop,
abbrechbare In-Flight-Arbeit, eine klare Trennung zwischen
Generierungs- und Zustellungs-Pipeline, plus das Baustein-/Lücken-
System für Antwortvorlagen. Entsprechend als eigene, größere
Planungsrunde zu behandeln, nicht nebenbei mitzuerledigen.

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

## Sicheres Löschen auf Wunsch + Todesfall (`secrecy.py`)

Personen können im Gespräch mit jeder Persona vertrauliche Dinge
preisgeben und verlangen, dass sie gelöscht werden — sofort oder erst
im Todesfall ("Im Falle meines Todes, bitte lösche alles was ich dir
zu Heinrich erzählt habe"). Zwei Design-Entscheidungen, beide bewusst
gegen die naheliegendere, einfachere Alternative getroffen:

1. **Ein Thema wird aktiv benannt, nicht aus dem Verlauf erraten.**
   Weder Stichwortsuche noch LLM-Klassifikation entscheiden, welche
   Nachrichten "zu Heinrich" gehören — bei einer unwiderruflichen
   Aktion sind falsch-negative (Geheimnis bleibt stehen) und
   falsch-positive Treffer (Falsches wird gelöscht) beide inakzeptabel.
   Stattdessen erkennt `secrecy.py` ein Vertraulichkeits-Signal ("das
   bleibt unter uns" u.ä., `CONFIDENTIAL_RULES`), die Persona fragt
   einmal nach einem kurzen Namen dafür, und die Antwort im nächsten
   Zug wird eingefangen — exakt das Zeitfenster-Capture-Muster aus
   `satisfaction.py` (`memory.capture_topic_label()`,
   `max_age_seconds`). Ab dann werden alle Nachrichten dieses
   Austauschs (beide Seiten) mit dem Thema getaggt
   (`messages.topic`, neue Spalte). Höchstens ein offenes Thema pro
   Persona gleichzeitig (v1-Vereinfachung) — ein zweites
   Vertraulichkeits-Signal, während eins bereits offen ist, öffnet
   kein zweites.
2. **Eine erkannte "Jetzt löschen"-Anfrage wird nicht sofort
   ausgeführt.** Die Persona fragt einmal nach ("Soll ich wirklich
   alles zum Thema 'Heinrich' unwiderruflich löschen?"), erst eine
   klare Ja-Antwort (`AFFIRMATIVE_RULES`) löst die tatsächliche
   Löschung aus (`memory.confirm_pending_deletion()`). Anders als bei
   der (reversiblen) Du/Sie-Umschaltung in `analysis.py`, die bewusst
   ohne Rückfrage sofort umschaltet — eine falsch erkannte Regel hier
   würde eine permanente, nicht rückgängig machbare Aktion auslösen.

**Echte, sichere Löschung statt eines Status-Flags:** Das Projekt
kannte bisher nur "weiches" Löschen (`story_fragments.consent_status
= 'deleted'` ist eine reine UPDATE-Markierung, der Inhalt bleibt in
der Datenbankdatei). Für dieses Feature reicht das nicht — `memory.py`
führt zum ersten Mal ein echtes `DELETE FROM messages ...` aus, gefolgt
von `memory.vacuum(user_id)`: eine frische, transaktionslose
`sqlite3.connect()`-Verbindung (VACUUM kann nicht innerhalb einer
offenen Transaktion laufen, `get_db()`s Context-Manager scheidet dafür
aus), die die freigegebenen Seiten wirklich aus der Datei entfernt,
nicht nur aus zukünftigen Abfrageergebnissen. Ein echter Test
(`test_vacuum_shrinks_file_and_removes_deleted_content_from_disk`)
schreibt substantiellen Inhalt, löscht ihn, vacuumt, und durchsucht
danach die **rohen Bytes der .sqlite3-Datei** nach dem gelöschten
Text — beweist, dass er wirklich weg ist, nicht nur unsichtbar.

**Todesfall-Bestätigung** (`POST /admin/confirm-death/{user_id}`,
gleiche Bearer-Auth wie alle `/admin/*`-Routen): das System kann einen
Todesfall nicht selbst erkennen, nur ein vertrauenswürdiger Admin
(Familienmitglied) bestätigt ihn von außen. `confirm_user_id` im
Body muss den Pfad-Parameter spiegeln — eine billige, aber wirksame
Absicherung gegen versehentliches Auslösen (bewusst kein mehrstufiger
Bestätigungsdialog). Führt `memory.execute_death_directives()` aus:
löscht NUR die Nachrichten, die zu Themen mit einer offenen
`on_death`-Löschanweisung gehören, vacuumt einmal am Ende, ist
idempotent (ein zweiter Aufruf ohne neue offene Anweisungen löscht
nichts mehr). **Alles andere bleibt unangetastet** — das ist Absicht,
nicht Zufall: die Pro-Nutzer-SQLite-Datei ist bewusst so gebaut, dass
sie später an Hinterbliebene übergeben werden kann (siehe
Grundprinzip 5 oben), der Todesfall darf also nicht pauschal alles
löschen. `/admin/stats`s `pending_deletion_directives` zeigt pro
Nutzer:in nur die **Anzahl** offener Todesfall-Anweisungen, nie deren
Thema oder Inhalt — sonst wäre der Admin-Zugang selbst ein Leck für
ein Geheimnis, das erst im Todesfall gelöscht werden soll.

**Bewusst v1-Scope:** nur `messages` sind themen-taggbar/löschbar.
`facts` (kurze Schlüssel/Wert-Fakten) und `story_fragments` (eigener,
bereits bestehender Consent-Mechanismus) bleiben unberührt — deckt das
geschilderte Szenario ("was ich dir erzählt habe" = Gesprächsinhalt)
ab, ohne unnötige Komplexität.

**Das System bewertet den Inhalt eines Themas nicht — bewusste
Entscheidung.** Ein offenes Thema kann in einer einzigen Sitzung sowohl
unbedenkliche als auch besonders sensible Gesprächsanteile enthalten
(Beispiel aus dem Konzeptgespräch: eine Person erzählt sowohl liebevoll
von einem Onkel als auch von erlittenem Missbrauch durch dieselbe
Person). Das System kann und soll nicht selbst beurteilen, was "gut"
oder "schlimm" ist, oder einzelne Sätze innerhalb eines Themas gezielt
herauslöschen — das wäre eine Anmaßung, die dem Willen der Person nicht
gerecht würde. Stattdessen gilt: **ein getaggtes Thema ist eine
Einheit** — wird es gelöscht, wird alles gelöscht, was während dieses
offenen Themas gesagt wurde, ohne Ausnahme. Möchte die Person einen
unbedenklichen Teil davon behalten, kann sie ihn jederzeit in einem
NICHT getaggten Gespräch (außerhalb eines offenen vertraulichen
Themas) erneut erzählen — dieser Teil bekommt dann kein Topic-Tag und
bleibt unabhängig von einer späteren Löschung erhalten. Das ist bewusst
grob, aber einfach zu verstehen und ohne stille inhaltliche Bewertung
durch das System — echt getestet
(`test_confirm_pending_deletion_leaves_untagged_mentions_of_the_same_name_alone`):
eine Erwähnung derselben Person AUSSERHALB eines offenen Themas bleibt
von einer Löschung unberührt, weil sie nie getaggt wurde — nicht weil
das System ihren Inhalt als "unbedenklich" eingestuft hätte.

**Ein zweites Leck wurde beim echten Rauchtest gefunden und behoben:**
die Bestätigungs-Nachricht nach einer Löschung enthielt den Themen-
Namen im an die KI übergebenen System-Kontext
(`DELETION_DONE_PROMPT_TEMPLATE.format(label=...)`), damit die Persona
die Löschung bestätigen kann. Ein kleines lokales Testmodell
wiederholte den Namen daraufhin in seiner eigenen Antwort — und diese
Antwort wird (ungetaggt) neu gespeichert, was den Namen direkt nach
dem Löschen wieder in die Datenbank zurückschreiben würde. Behoben,
indem `DELETION_DONE_PROMPT` den Namen gar nicht mehr enthält und die
Persona stattdessen explizit angewiesen wird, ihn NICHT zu wiederholen.
Eine grundsätzliche Restunsicherheit bleibt: kein Prompt-Text kann zu
100 % garantieren, dass ein Sprachmodell niemals versehentlich etwas
Sensibles in einer Antwort erwähnt — das ist ein inhärentes Risiko
jedes Systems, das ein generatives Modell über ein gerade gelöschtes
Thema sprechen lässt, keine vollständig lösbare Garantie.

**Ein drittes Leck, ebenfalls per echtem Rauchtest gefunden:** die
Nachricht, mit der eine Todesfall-Anweisung überhaupt erst erteilt
wird ("Im Falle meines Todes, bitte lösche alles was ich dir zu X
erzählt habe" — enthält den Namen selbst), wurde ungetaggt (`topic
= NULL`) gespeichert, weil `handle_turn()` das Thema im selben
Gesprächsschritt bereits schloss, *bevor* es das aktuelle Thema für
die Nachrichten-Taggierung dieses Turns ermittelte. `execute_
death_directives()` löscht aber nur getaggte Nachrichten — die
auslösende Anweisung selbst wäre damit nie erfasst worden und hätte
den Namen dauerhaft im Klartext stehen lassen, selbst nach
erfolgreicher Ausführung. Behoben durch Umstellen der Reihenfolge:
das aktive Thema wird VOR einer möglichen Schließung ermittelt und
für die Taggierung dieses gesamten Turns verwendet (Anfrage der
Person UND Antwort der Persona) — beide werden dadurch korrekt Teil
dessen, was später (sofort oder im Todesfall) mitgelöscht wird.
Beide Lecks zusammen zeigen, warum der echte Byte-Test
(`test_vacuum_shrinks_file_and_removes_deleted_content_from_disk`
und der manuelle End-to-End-Rauchtest mit echtem Ollama) hier nicht
optional war: beide Fehler wären mit rein gemockten Unit-Tests nicht
aufgefallen, weil dort nie eine echte, vom Modell generierte Antwort
oder eine vollständige Turn-Reihenfolge über mehrere echte
Gesprächsschritte hinweg durchlief.
