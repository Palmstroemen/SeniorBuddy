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
# Auf dem Server (macht deploy/setup.sh bereits mit):
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Dienst im Tailnet freigeben, ohne dass uvicorn selbst nach aussen bindet:
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

## Mehrere Personen (ein gemeinsamer Server, mehrere Tablets)

Jedes Tablet bekommt beim Einrichten eine eigene URL mit
`?user=<name>`, z. B. `https://senior-pc.<tailnet>.ts.net/?user=maria`.
Vor "Zum Startbildschirm hinzufügen" diese URL im Browser öffnen – das
Tablet merkt sich damit dauerhaft, zu wem es gehört, ohne
Login-Bildschirm. Im Transparenz-Panel (ⓘ) steht das aktive Profil zur
Kontrolle.

## Sprache auf dem Server (optional)

Standardmäßig laufen Spracherkennung und -ausgabe im Browser (Web
Speech API) – bequem, aber auf älteren/schwächeren Tablets spürbar
langsamer, und die Erkennung läuft in Chrome über Googles Server statt
lokal. Alternative: ein eigener, lokaler Sprachdienst
(`speech-service/`, separater Prozess mit eigenem venv – wie Ollama
nicht Teil von `server/`), der `faster-whisper` (Erkennung) und
`Piper` (Ausgabe) nutzt. Umschaltbar pro Tablet im Transparenz-Panel
(ⓘ → "Sprache") – nach jeder Aufnahme/Ausgabe erscheint kurz die
gebrauchte Zeit, zum Vergleichen zwischen Geräte- und Server-Modus.

```bash
cd speech-service
./setup.sh          # venv, Abhaengigkeiten, eine Test-Stimme (~60 MB)
source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8100
```

Für Dauerbetrieb: `speech-service/deploy/speech-service.service` wie
`senior-companion.service` einrichten (siehe unten). Jede Persona hat
in `server/config.py` eine eigene Piper-Stimme (`voice_id`) – weitere
Stimmen liegen unter
[huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE),
werden nach `speech-service/voices/` gelegt (genau wie Ollama-Modelle
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
für den Ausbaupfad (Story-Export, Fernwartungs-Backend, Slot-Management
bei vielen gleichzeitigen Senior:innen, u. a.).
