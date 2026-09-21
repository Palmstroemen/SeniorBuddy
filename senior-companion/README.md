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

`./deploy/setup.sh` installiert bei Bedarf automatisch das `python3-venv`-
Systempaket sowie [Ollama](https://ollama.com), falls beides noch fehlt.

Empfehlung für einen frischen Server: zuerst `./deploy/setup_tailscale.sh`
ausführen (kurz, braucht eine einmalige Anmeldung im Browser) – danach ist
der Server bereits per Tailscale erreichbar, auch während der folgenden,
teils langen Ollama-Modell-Downloads in `setup.sh` (unabhängig von
lokalem WLAN/Router).

```bash
git clone <dein-repo-url>
cd senior-companion
./deploy/setup_tailscale.sh
./deploy/setup.sh
cd server
source .venv/bin/activate
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Danach im Browser auf demselben Rechner öffnen: `http://127.0.0.1:8000`.
Für den Zugriff von einem Tablet (egal ob im selben Raum oder von
unterwegs) siehe **Fernzugriff** unten – der Server bindet bewusst nur
an `127.0.0.1` und ist ohne diesen Schritt von keinem anderen Gerät aus
erreichbar.

## Fernzugriff (Tailscale, keine offenen Ports)

Der Dienst horcht nie auf einer öffentlich oder im LAN erreichbaren
Adresse – weder für Wartung noch für Tablets. Grund: ein Rechner mit
lokalem LLM/GPU ist ein attraktives Ziel für automatisierte
Kryptominer-Botnetze, die gezielt nach offenen Ports scannen. Statt
Ports zu öffnen, treten Server **und** jedes Tablet demselben
[Tailscale](https://tailscale.com)-Tailnet bei (WireGuard-VPN,
geräteweise Authentifizierung):

```bash
# Auf dem Server (macht deploy/setup_tailscale.sh bereits mit):
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Dienst im Tailnet freigeben, ohne dass uvicorn selbst nach aussen bindet
# (erst NACH dem App-Setup, deploy/setup.sh, sinnvoll):
sudo tailscale serve --bg 8000
```

`tailscale serve` liefert dabei automatisch ein gültiges HTTPS-Zertifikat
für den Tailnet-Hostnamen (z. B. `https://senior-pc.<tailnet>.ts.net`)
– Chat-Nachrichten laufen damit durchgehend verschlüsselt, nie im
Klartext über irgendein Netzwerksegment. Genaue Befehle können sich je
nach Tailscale-Version leicht unterscheiden; `tailscale serve --help`
zeigt die für die installierte Version passende Syntax.

Jedes Tablet installiert ebenfalls Tailscale (App aus dem jeweiligen
Store) und tritt demselben Tailnet bei – danach ist die Tailnet-URL von
dort aus erreichbar, exakt wie eine normale Internetadresse, aber für
niemanden ausserhalb des Tailnets sichtbar oder erreichbar.

## Honeypot (Alarm bei unbefugtem Zugriff)

Falls die Härtung oben doch umgangen wird (z. B. ein kompromittiertes
Tailnet-Gerät oder physischer Zugriff auf den Rechner), gibt es zwei
Fallen, die kein legitimer Teil der App je berührt – jede Berührung
löst sofort eine Push-Benachrichtigung über [ntfy.sh](https://ntfy.sh)
aus (kein Account nötig):

1. Eine Honeyfile (`server/data/.honeypot/zugangsdaten.txt`, wird beim
   Start automatisch angelegt), überwacht per `inotify`.
2. Köder-API-Routen (`/api/admin/backup`, `/.env` u. a.), die nach
   typischen Angriffszielen klingen.

Einrichtung:

```bash
# Eigenen, geheimen Topic-Namen erzeugen (nicht erraten koennen):
openssl rand -hex 16
```

Diesen Wert als Umgebungsvariable `SENIOR_COMPANION_NTFY_TOPIC` setzen
(z. B. `sudo systemctl edit senior-companion` → im Editor unter
`[Service]` die Zeile `Environment=SENIOR_COMPANION_NTFY_TOPIC=<wert>`
einfügen) und denselben Topic-Namen in der ntfy-App oder unter
`https://ntfy.sh/<topic>` im Browser abonnieren. Ohne gesetzten Topic
bleibt der Alarm nur im systemd-Journal sichtbar (`journalctl -u
senior-companion`), es wird aber nichts nach außen geschickt.

**Bekannte Fehlalarm-Quelle:** Backup-/Sync-Werkzeuge, die den ganzen
`server/data/`-Ordner erfassen, lösen beim Lesen selbst einen Alarm
aus – `server/data/.honeypot/` sollte deshalb von jedem Backup-Job
ausgeschlossen werden.

## Fernwartungs-API (`/admin/*`)

Einzige Stelle im Projekt mit echter Authentifizierung – der Rest der
API ist bewusst offen für Geräte im selben Tailnet (siehe
`docs/ARCHITECTURE.md`), aber Konfiguration ändern und ein Update
anstoßen sind privilegierte Operationen.

```bash
# Eigenes, starkes Token erzeugen:
openssl rand -hex 32
```

Als `SENIOR_COMPANION_ADMIN_TOKEN` setzen (`sudo systemctl edit
senior-companion` → `Environment=SENIOR_COMPANION_ADMIN_TOKEN=<wert>`).
Ohne gesetztes Token antwortet die gesamte `/admin/*`-API mit 503 –
nie offen.

```bash
TOKEN="<dein-token>"
BASE="https://senior-pc.<tailnet>.ts.net"   # oder http://127.0.0.1:8000 lokal

# Geschlechts-Variante einer Persona aendern (wirkt sofort, uebersteht Neustart)
curl -X POST "$BASE/admin/config/persona-gender/freundin" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"gender": "weiblich"}'

# Honeypot-Alarm-Topic aendern
curl -X POST "$BASE/admin/config/ntfy-topic" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"topic": "neues-topic"}'

# Statistiken (Nachrichten/Nutzung/Stimmung/Internet-Anfragen/
# Datenfreigaben/Probleme/Persona-Nutzung ("Freundeskreis") pro
# Nutzer:in, Plugin-Status, Praeemptionen, Honeypot-Ausloesungen, Uptime)
curl "$BASE/admin/stats" -H "Authorization: Bearer $TOKEN"

# Zufriedenheits-Antworten der Technikerin-Nachfrage (neueste zuerst)
curl "$BASE/admin/feedback" -H "Authorization: Bearer $TOKEN"

# Wie oft die Technikerin nach Zufriedenheit fragt (Tage, Standard: 7)
curl -X POST "$BASE/admin/config/satisfaction-interval" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"days": 7}'

# Auto-Turns (Personas melden sich bei Stille von sich aus, siehe
# "Gruppenchat & Auto-Turns" oben) global an-/abschalten
curl -X POST "$BASE/admin/config/auto-turns" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Personas anlegen/bearbeiten/loeschen (siehe "Persona-Designer" oben)
curl "$BASE/admin/personas" -H "Authorization: Bearer $TOKEN"

# Update anstossen (siehe unten, was dabei passiert) + Status abfragen
curl -X POST "$BASE/admin/update" -H "Authorization: Bearer $TOKEN"
curl "$BASE/admin/update/status" -H "Authorization: Bearer $TOKEN"

# Welcher Commit laeuft gerade? (kein Token noetig, oeffentlich) - der
# Wert wird beim Prozessstart einmal ermittelt, aendert sich also erst
# nach einem echten Neustart, nicht schon durch ein blosses "git pull"
# auf der Platte - nuetzlich um nach einem Update zu pruefen, ob der
# neue Code auch wirklich laeuft, bevor man auf Browser-Cache tippt.
curl "$BASE/api/version"

# Todesfall bestaetigen: fuehrt alle offenen "im Todesfall loeschen"-
# Anweisungen dieser Person aus (siehe unten). confirm_user_id muss
# der Pfad-Parameter sein - Schutz gegen versehentliches Ausloesen.
curl -X POST "$BASE/admin/confirm-death/maria" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"confirm_user_id": "maria"}'
```

**Statistik & Zufriedenheit:** `sentiment` in `/admin/stats` kommt aus
einem nächtlichen Hintergrund-Job (lokales Modell, kein Live-Latenz-
Einfluss, siehe `docs/ARCHITECTURE.md`), nicht aus einer Live-Analyse –
`unclassified_pending` zeigt, wie viel der nächste Lauf noch vor sich
hat. Die Technikerin fragt von sich aus hin und wieder nach
Zufriedenheit/Wünschen; die nächste Nachricht danach wird (innerhalb
weniger Minuten) als Antwort erfasst und landet in `/admin/feedback`.
Anrede (Du/Sie) startet immer bei "Sie" und schaltet bei einem
ausdrücklichen Du-Angebot automatisch um – bei einer Fehlerkennung per
`POST /api/facts/{user_id}` (`key=anrede:<persona_id>`, `value=sie`)
manuell korrigierbar.

**Plugin an/aus** läuft weiter über das bestehende, absichtlich
unauthentifizierte `/api/plugins/{id}/toggle` (Teil der offenen
Senior-Oberfläche) – genauso über das Tailnet fernsteuerbar, kein
eigener Endpoint nötig.

**Sicheres Löschen auf Wunsch + Todesfall:** In jedem Gespräch kann
die Person ein vertrauliches Thema markieren ("Das bleibt unter uns")
– die Persona fragt einmal nach einem kurzen Namen dafür (z. B.
"Heinrich"), danach kann jederzeit "Bitte lösche alles, was ich dir
dazu erzählt habe" verlangt werden (löscht nach einer einmaligen
Rückfrage/Bestätigung wirklich, inkl. `VACUUM` der Datenbankdatei –
kein bloßes Verstecken) oder "Im Falle meines Todes, lösche alles zu
Heinrich" (merkt sich das nur, löscht nichts sofort). Ein Todesfall
wird ausschließlich über den `/admin/confirm-death/{user_id}`-Aufruf
oben ausgelöst – das System erkennt ihn nicht selbst. Dabei wird
NUR das markierte Thema gelöscht; Lebensgeschichten, Fakten und alle
nicht markierten Gespräche bleiben erhalten (die Pro-Person-Datenbank­
datei ist bewusst so gebaut, dass sie später an Hinterbliebene
übergeben werden kann, siehe `docs/ARCHITECTURE.md`).

**"Updates einspielen" läuft nicht im App-Prozess selbst.** Ein
FastAPI-Prozess, der sich mitten im Request neu startet, ist ein
Verlässlichkeits- und Sicherheitsrisiko. `/admin/update` schreibt
stattdessen nur eine Marker-Datei; ein separates, privilegiert
laufendes systemd-`.path`-Unit bemerkt die Änderung und führt den
eigentlichen `git pull` + Neustart aus (`deploy/run_update.sh`) – der
netz-erreichbare Prozess bekommt dadurch nie Rechte für `git pull`
oder `systemctl restart`, nur die Fähigkeit, eine Datei zu schreiben.
Einrichtung:

```bash
sudo cp deploy/senior-companion-updater.{path,service} /etc/systemd/system/
sudo systemctl enable --now senior-companion-updater.path
```

## Gruppenchat & Auto-Turns

Mehrere Personas können gleichzeitig "anwesend" sein und abwechselnd
antworten – passiert automatisch, sobald jemand mehr als eine Persona
in derselben Sitzung anspricht (z. B. "Wallner, was meinst du dazu?",
während gerade mit Robin gesprochen wurde). Ein vertrauliches Thema
(siehe oben, "Sicheres Löschen auf Wunsch + Todesfall") wird dabei nie
an eine andere anwesende Persona weitergegeben – das ist die
sicherheitskritischste Eigenschaft des ganzen Features und
entsprechend gründlich getestet.

Bleibt die Person länger still, kann eine anwesende Persona von sich
aus weiterreden statt nur zu antworten ("Auto-Turns") – wie stark das
passiert, steuert die "Neigung" jeder Persona (siehe Persona-Designer
unten: eine geschwätzige Persona fragt öfter nach, eine zurückhaltende
lässt der Person mehr Ruhe). Nach längerer Stille verabschiedet sich
die zuletzt aktive Persona einmal warm und wird dann still, bis wieder
etwas gesagt wird. Global abschaltbar über `/admin/config/auto-turns`
(Beispiel siehe Abschnitt "Fernwartungs-API" oben).

Auf Wunsch gibt eine Persona eine kurze Zusammenfassung des bisherigen
Gesprächs an eine andere weiter, ganz ohne dass beide gleichzeitig
anwesend sein müssen ("Robin, erzähl das mal Wallner") – läuft im
Hintergrund nach der sichtbaren Antwort, die laufende Unterhaltung
kommt dadurch nicht ins Stocken. Auch hier gilt: ein als vertraulich
markiertes Thema wird nie in eine solche Zusammenfassung aufgenommen.

## Personas anlegen/bearbeiten (Persona-Designer)

Über `admin.html` (im selben Tailnet erreichbar, z. B.
`https://senior-pc.<tailnet>.ts.net/admin.html`) lassen sich Personas
komplett ohne Code-Änderung anlegen, bearbeiten und löschen – gesichert
mit demselben `SENIOR_COMPANION_ADMIN_TOKEN` wie die übrige
Fernwartungs-API (einmal eingeben, bleibt im Browser gespeichert, bis
"Abmelden" gedrückt wird).

Bearbeitbar je Persona: Name und Systemprompt für jede der drei
Geschlechts-Varianten (neutral/weiblich/männlich), Modell, ob sie
dauerhaft geladen bleibt, maximale Antwortlänge, die oben erwähnte
"Neigung" fürs Gruppenchat-Verhalten, Avatar-Farbe. Auch die 4
mitgelieferten Personas (Robin/Alex/Wallner/Toni) lassen sich so
bearbeiten – "Zurücksetzen" stellt dabei den Auslieferungszustand
wieder her, statt die Persona ganz zu entfernen (mehrere Stellen im
Code setzen voraus, dass es sie gibt).

Dieselbe Funktionalität steht auch als reine JSON-API zur Verfügung,
z. B. für eigene Skripte oder ohne Browser:

```bash
curl "$BASE/admin/personas" -H "Authorization: Bearer $TOKEN"

curl -X POST "$BASE/admin/personas" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "id": "nachbarin", "model": "qwen2.5:7b-instruct",
    "always_loaded": true, "max_tokens": 400,
    "reengagement_tendency": 0.5,
    "color": "#7A5C99", "background_color": "#EEE9F2",
    "variants": {
      "neutral":   {"display_name": "Erna", "system_prompt": "...", "voice_id": "de_DE-thorsten-low"},
      "weiblich":  {"display_name": "Erna", "system_prompt": "...", "voice_id": "de_DE-kerstin-low"},
      "maennlich": {"display_name": "Erwin", "system_prompt": "...", "voice_id": "de_DE-thorsten-low"}
    }
  }'
```

Änderungen wirken sofort (kein Neustart nötig) und überstehen einen
echten Neustart (gespeichert in `server/data/admin_settings.json`,
dieselbe Datei wie alle anderen Fernwartungs-Einstellungen).

## Mehrere Personen (ein gemeinsamer Server, mehrere Tablets)

Jedes Tablet bekommt beim Einrichten eine eigene URL mit
`?user=<name>`, z. B. `https://senior-pc.<tailnet>.ts.net/?user=maria`.
Vor "Zum Startbildschirm hinzufügen" diese URL im Browser öffnen – das
Tablet merkt sich damit dauerhaft, zu wem es gehört, ohne
Login-Bildschirm. Im Transparenz-Panel (ⓘ) steht das aktive Profil zur
Kontrolle.

## Sprache auf dem Server

Ein eigener, lokaler Sprachdienst (`speech-service/`, separater
Prozess mit eigenem venv – wie Ollama nicht Teil von `server/`), der
`faster-whisper` (Erkennung) und `Piper` (Ausgabe) nutzt. Die
Sprach**ausgabe** läuft standardmäßig darüber (browsereigene Stimmen
klingen je nach Gerät/Betriebssystem sehr unterschiedlich – auf Linux
z. B. deutlich roboterhafter – und die wirklich guten Browser-Stimmen
laufen selbst über einen fremden Cloud-Dienst, was dem
"alles bleibt im Haus"-Versprechen widerspricht). Die Spracher**kennung**
läuft weiterhin standardmäßig im Browser (Web Speech API), da sie auf
schwächeren Tablets meist schon schnell genug ist. Beides ist pro
Tablet im Transparenz-Panel (ⓘ → "Sprache") umschaltbar – nach jeder
Aufnahme/Ausgabe erscheint kurz die gebrauchte Zeit, zum Vergleichen
zwischen Geräte- und Server-Modus. Ist der Sprachdienst nicht
erreichbar, fällt die Ausgabe automatisch still auf die Geräte-Stimme
zurück, statt stumm zu bleiben.

```bash
cd speech-service
./setup.sh                # venv, Abhaengigkeiten, eine Test-Stimme (~60 MB)
./fetch_all_voices.sh     # alle 5 von den mitgelieferten Personas gebrauchten Stimmen
source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8100
```

Für Dauerbetrieb: `speech-service/deploy/speech-service.service` wie
`senior-companion.service` einrichten (siehe unten). Jede Persona hat
in `server/config.py` eine eigene Piper-Stimme (`voice_id`) –
`fetch_all_voices.sh` laedt alle aktuell gebrauchten auf einmal nach
(ueberspringt schon vorhandene, kann also gefahrlos erneut laufen);
fuer eine NEUE, dort noch nicht gelistete Stimme siehe
[huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE),
manuell nach `speech-service/voices/` legen (genau wie Ollama-Modelle
erst per `ollama pull` geladen werden müssen).

**CPU-Isolation:** falls der Sprachdienst spürbar mit den Personas um
Rechenzeit konkurriert, kann `speech-service/deploy/speech-service.service`
über die dort vorbereitete (auskommentierte) `AllowedCPUs=`-Zeile auf
bestimmte Kerne beschränkt werden – bewusst erst nach einer echten
Messung aktivieren, nicht vorsorglich.

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
server/         FastAPI-Backend, Personas, Speicher, Plugins, Scheduler
speech-service/ optionaler Sprachdienst (STT/Piper-TTS), eigener Prozess
client/         PWA (Chat-Oberfläche, läuft im Browser des Tablets/Handys)
plugins/        liegt unter server/plugins/ – jedes Plugin: manifest.json + plugin.py
deploy/         systemd-Service + Setup-Skript für den Dauerbetrieb
docs/           Architekturentscheidungen
```

## Eigene Modelle konfigurieren

Modell, Prompts, Stimmen und mehr lassen sich mittlerweile ohne
Code-Änderung und ohne Neustart über den **Persona-Designer** ändern
(siehe oben, `admin.html` bzw. `/admin/personas`-API) – der
empfohlene Weg für alles außer der Ersteinrichtung.

Nur für den Bootstrap bzw. wer lieber direkt im Code arbeitet: die 4
mitgelieferten Personas stehen als Ausgangswerte in
[`server/config.py`](server/config.py). Modellnamen müssen mit
`ollama list` übereinstimmen bzw. vorher per `ollama pull <name>`
geladen werden. Jede Persona hat einen Namen und drei Text-/Stimm-
Varianten (`neutral`/`weiblich`/`maennlich`); welche aktiv ist, lässt
sich ebenfalls per Fernwartungs-API ändern (Beispiel siehe Abschnitt
"Fernwartungs-API" oben, `persona-gender`).

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

## Avatare (Strichgesichter)

Die Gesichts-Avatare basieren auf dem Stil "toon-head" aus der
Open-Source-Bibliothek [DiceBear](https://www.dicebear.com)
(https://github.com/dicebear/dicebear), lizenziert unter
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) ©
DiceBear-Mitwirkende. Die Bauteile (Augen, Augenbrauen, Mund, Frisuren,
Bart) wurden als statische Daten übernommen
(`client/assets/toon-head-faces.json`, ohne Körper/Kleidung); Nase und
Zusammensetzung sind eigene Ergänzungen.

## Stand / nächste Schritte

Dies ist das Grundgerüst für Phase 1 (Test mit 1–2 Personen). Siehe
`docs/ARCHITECTURE.md`, Abschnitt "Bewusst nicht in Phase 1 enthalten"
für den Ausbaupfad (Story-Export, Fernwartungs-Backend, Slot-Management
bei vielen gleichzeitigen Senior:innen, u. a.).
