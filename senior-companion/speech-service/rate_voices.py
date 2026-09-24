#!/usr/bin/env python3
"""
Interaktives CLI-Werkzeug zum schnellen Durchhoeren/Bewerten aller
Sprecher einer Mehrsprecher-Piper-Stimme (z.B. de_DE-mls-medium, 236
Sprecher in EINER Datei) - baut aus vielen Kandidaten eine Shortlist,
bevor man sich fuer eine bestimmte speaker_id in einer Persona
entscheidet (siehe server/config.py's PersonaConfig.voice_speaker_id,
server/main.py's GET /admin/voices/{voice}/speakers).

Laeuft DIREKT ueber die Piper-Bibliothek (dasselbe Vorgehen wie
speech-service/main.py's _get_voice()/synthesize_wav(), nur in-process
statt per HTTP) - kein laufender Sprachdienst noetig, nur dieses venv.

WIEDERGABE LAEUFT UEBER DEN BROWSER, NICHT LOKAL AUF DIESER MASCHINE
(Session-Notiz 2026-09-24): ein Server ohne angeschlossenen Lautsprecher
kann nichts lokal abspielen - stattdessen ein winziger eingebetteter
Webserver, den man von einem beliebigen Geraet im selben Tailnet
(Tablet, Laptop, Handy) im Browser oeffnet. Genau derselbe Weg, ueber
den die eigentliche Anwendung ohnehin schon Sprachausgabe an Tablets
ausliefert (HTTP + <audio>), nur hier fuers Durchhoeren statt fuer
echte Gespraeche.

Ergebnisse werden fortlaufend nach jeder Bewertung gespeichert
(speaker_ratings_<stimme>.json neben diesem Skript) - ein Durchlauf
kann jederzeit mit 'q' abgebrochen und spaeter (einfach erneut
aufrufen) beim ersten noch unbewerteten Sprecher fortgesetzt werden.

Beispiele:
  # Erster Durchlauf, alle Sprecher, Standard-Demosatz
  python3 rate_voices.py --voice de_DE-mls-medium

  # Eigener Demosatz, anderer Port
  python3 rate_voices.py --voice de_DE-mls-medium --text "Wie war Ihr Tag heute?" --port 8765

  # Zweiter Durchlauf: nur die bisherige Shortlist (Bewertung 1 oder 2)
  # nochmal durchgehen
  python3 rate_voices.py --voice de_DE-mls-medium --only-rating 1,2

  # Steuerung ohne Piper/echte Synthese testen
  python3 rate_voices.py --voice de_DE-mls-medium --dry-run

Steuerung (Eingabe im Terminal, waehrend im Browser gehoert wird):
  s        SOFORT (kein Enter noetig) mit 5 bewerten und weiter -
           Casting-Prinzip: eine erkennbar unbrauchbare Stimme muss
           nicht zu Ende gehoert werden. Sobald der naechste Sprecher
           bereitsteht, wechselt der Browser automatisch zu ihm.
  1-5      Bewertung abgeben (1=sehr gut, 5=unbrauchbar), optional
           direkt gefolgt von einem Kommentar, z.B. "2 klingt nasal"
  t        neuen Demosatz eingeben (Enter danach) - wird ab jetzt fuer
           alle weiteren Sprecher verwendet, spielt den aktuellen
           Sprecher sofort mit dem neuen Satz erneut ab
  r        aktuellen Sprecher nochmal abspielen
  n        weiter ohne Bewertung (bleibt beim naechsten Aufruf offen)
  p        zurueck zum vorherigen Sprecher
  q        beenden - Fortschritt ist schon gespeichert
"""
import argparse
import http.server
import io
import json
import socketserver
import sys
import termios
import threading
import time
import tty
import wave
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VOICES_DIR = SCRIPT_DIR / "voices"
DEFAULT_TEXT = "Guten Tag, wie geht es Ihnen heute? Ich hoffe, Sie hatten einen schoenen Tag."
DEFAULT_PORT = 8765

# Sentinel statt eines echten Kommando-Strings - siehe read_command().
_SKIP = object()


class _SharedAudio:
    """Zustand, den der Webserver-Thread liest und der Haupt-Thread
    (die interaktive Schleife) schreibt - ein einfacher Lock reicht,
    da beide Seiten den Zustand jeweils nur kurz halten."""

    def __init__(self):
        self._lock = threading.Lock()
        self.version = 0
        self.wav_bytes = b""
        self.label = "Warte auf ersten Sprecher ..."

    def update(self, wav_bytes: bytes, label: str) -> None:
        with self._lock:
            self.version += 1
            self.wav_bytes = wav_bytes
            self.label = label

    def snapshot(self):
        with self._lock:
            return self.version, self.wav_bytes, self.label


_AUDIO = _SharedAudio()

_PAGE_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Stimmen-Casting</title>
<style>
  body { font-family: sans-serif; background: #222; color: #eee; text-align: center; padding-top: 15vh; }
  h1 { font-size: 1.4em; padding: 0 1em; }
  audio { width: 80%; margin-top: 2em; }
</style>
</head>
<body>
  <h1 id="label">Warte auf ersten Sprecher ...</h1>
  <audio id="player" autoplay></audio>
<script>
let currentVersion = 0;
async function poll() {
  try {
    const res = await fetch("/status");
    const data = await res.json();
    if (data.version !== currentVersion) {
      currentVersion = data.version;
      document.getElementById("label").textContent = data.label;
      const player = document.getElementById("player");
      player.src = "/current.wav?v=" + currentVersion;
      player.play().catch(() => {});
    }
  } catch (err) {
    // still scheitern - naechster Poll-Versuch kommt in Kuerze
  }
  setTimeout(poll, 300);
}
poll();
</script>
</body>
</html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # kein Konsolen-Spam waehrend der interaktiven Bewertung

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(200, "text/html; charset=utf-8", _PAGE_HTML.encode("utf-8"))
        elif self.path == "/status":
            version, _, label = _AUDIO.snapshot()
            body = json.dumps({"version": version, "label": label}).encode("utf-8")
            self._send(200, "application/json", body)
        elif self.path.startswith("/current.wav"):
            _, wav_bytes, _ = _AUDIO.snapshot()
            self._send(200, "audio/wav", wav_bytes, cache=False)
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, status, content_type, body, cache=True):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def start_http_server(port: int):
    server = socketserver.ThreadingTCPServer(("0.0.0.0", port), _Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def read_command():
    """Wie input(), reagiert aber auf ein einzelnes 's' SOFORT (ohne
    Enter) mit dem _SKIP-Sentinel - Casting-Prinzip. Alles andere wird
    wie gewohnt zeilenweise eingelesen (Terminal-Echo bleibt im
    cbreak-Modus erhalten, siehe tty.setcbreak). Ohne echtes Terminal
    (z.B. automatisierter Aufruf) faellt das auf ein normales input()
    zurueck, da kein Raw-Modus moeglich ist."""
    if not sys.stdin.isatty():
        return input("  > ").strip()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    buf = []
    try:
        tty.setcbreak(fd)
        sys.stdout.write("  > ")
        sys.stdout.flush()
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\n", "\r"):
                sys.stdout.write("\n")
                return "".join(buf).strip()
            if ch.lower() == "s" and not buf:
                sys.stdout.write("s\n")
                return _SKIP
            buf.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def load_speaker_info(voice_name):
    """num_speakers/speaker_id_map direkt aus der .onnx.json - exakt
    dieselbe Quelle wie server/main.py's GET /admin/voices/{voice}/speakers,
    hier nur lokal gelesen statt ueber HTTP."""
    config_path = VOICES_DIR / f"{voice_name}.onnx.json"
    if not config_path.exists():
        print(f"Stimme '{voice_name}' nicht gefunden ({config_path}).")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        voice_config = json.load(f)
    num_speakers = voice_config.get("num_speakers", 1) or 1
    id_to_corpus_key = {}
    for key, sid in (voice_config.get("speaker_id_map") or {}).items():
        id_to_corpus_key[sid] = key
    return num_speakers, id_to_corpus_key


def load_ratings(results_path):
    if results_path.exists():
        with open(results_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ratings(results_path, ratings):
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(ratings, f, ensure_ascii=False, indent=2, sort_keys=True)


def build_speaker_list(num_speakers, only_ratings, ratings):
    speaker_ids = list(range(num_speakers))
    if only_ratings is not None:
        speaker_ids = [
            sid for sid in speaker_ids
            if str(sid) in ratings and ratings[str(sid)].get("rating") in only_ratings
        ]
    return speaker_ids


def find_start_index(speaker_ids, ratings, start_at):
    if start_at is not None and start_at in speaker_ids:
        return speaker_ids.index(start_at)
    for i, sid in enumerate(speaker_ids):
        if str(sid) not in ratings:
            return i
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--voice", required=True, help="Stimmenname ohne .onnx, z.B. de_DE-mls-medium")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Demosatz fuer jeden Sprecher")
    parser.add_argument(
        "--only-rating", default=None,
        help="Nur bereits so bewertete Sprecher erneut durchgehen, z.B. '1,2'",
    )
    parser.add_argument(
        "--start-at", type=int, default=None,
        help="Bei dieser Sprecher-ID beginnen statt beim ersten unbewerteten",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port fuer den Browser-Player (Standard {DEFAULT_PORT})")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Keine echte Synthese/kein Piper noetig - nur die Steuerung testen",
    )
    args = parser.parse_args()

    only_ratings = {int(x) for x in args.only_rating.split(",")} if args.only_rating else None

    results_path = SCRIPT_DIR / f"speaker_ratings_{args.voice}.json"
    ratings = load_ratings(results_path)

    num_speakers, id_to_corpus_key = load_speaker_info(args.voice)
    speaker_ids = build_speaker_list(num_speakers, only_ratings, ratings)
    if not speaker_ids:
        print("Keine passenden Sprecher gefunden (Filter zu eng, oder diese Stimme hat nur einen Sprecher).")
        return

    voice = None
    if not args.dry_run:
        from piper.voice import PiperVoice
        from piper.config import SynthesisConfig

        model_path = VOICES_DIR / f"{args.voice}.onnx"
        print(f"Lade {model_path.name} ...")
        voice = PiperVoice.load(str(model_path))

    start_http_server(args.port)
    print(f"\nBrowser oeffnen (im selben Tailnet, z.B. Tablet/Laptop): "
          f"http://gmxtec.tail83779a.ts.net:{args.port}/")
    print(f"(Falls dieser Rechner anders heisst: http://<hostname-oder-IP>:{args.port}/)\n")

    i = find_start_index(speaker_ids, ratings, args.start_at)
    demo_text = args.text  # per 't' waehrend des Laufs aenderbar, siehe unten

    print(f"{len(speaker_ids)} Sprecher in dieser Runde. Ergebnisse: {results_path.name}")
    print("s = sofort mit 5 bewerten (kein Enter noetig), 1-5 = Bewertung (+ Kommentar), "
          "t = Demosatz aendern, r = wiederholen, n = weiter ohne Bewertung, p = zurueck, q = beenden.\n")

    while 0 <= i < len(speaker_ids):
        sid = speaker_ids[i]
        corpus_key = id_to_corpus_key.get(sid, "?")
        existing = ratings.get(str(sid))
        status = f" (bisher: {existing['rating']} - {existing.get('comment', '')})" if existing else ""
        label = f"[{i + 1}/{len(speaker_ids)}] Sprecher-ID {sid} ({corpus_key}){status}"
        print(label)
        print(f"  Demosatz: {demo_text}")

        if args.dry_run:
            print("  (dry-run: keine Synthese)")
        else:
            buffer = io.BytesIO()
            render_start = time.perf_counter()
            with wave.open(buffer, "wb") as wav_file:
                voice.synthesize_wav(demo_text, wav_file, syn_config=SynthesisConfig(speaker_id=sid))
            render_seconds = time.perf_counter() - render_start
            print(f"  Rendering: {render_seconds:.2f}s")
            _AUDIO.update(buffer.getvalue(), label)

        cmd = read_command()
        if cmd is _SKIP:
            ratings[str(sid)] = {
                "rating": 5, "comment": "per Skip (S) sofort aussortiert", "corpus_key": corpus_key,
            }
            save_ratings(results_path, ratings)
            print("  -> per Skip sofort mit 5 bewertet.")
            i += 1
            continue

        if not cmd:
            continue
        key = cmd[0].lower()
        if key == "q":
            break
        if key == "n":
            i += 1
        elif key == "p":
            i = max(0, i - 1)
        elif key == "r":
            continue  # gleiches i - naechster Schleifendurchlauf spielt erneut ab
        elif key == "t":
            new_text = input("  Neuer Demosatz: ").strip()
            if new_text:
                demo_text = new_text
            continue  # gleiches i - spielt sofort mit dem neuen Satz erneut ab
        elif key in "12345":
            ratings[str(sid)] = {
                "rating": int(key), "comment": cmd[1:].strip(), "corpus_key": corpus_key,
            }
            save_ratings(results_path, ratings)
            i += 1
        else:
            print("  Unbekannter Befehl. 1-5, s, t, r, n, p oder q.")

    print(f"\nFertig (oder mit 'q' beendet). {len(ratings)} Sprecher insgesamt bewertet. "
          f"Ergebnisse: {results_path}")


if __name__ == "__main__":
    main()
