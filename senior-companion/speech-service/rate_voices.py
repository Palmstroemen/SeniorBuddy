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
statt per HTTP) - kein laufender Sprachdienst noetig, nur dieses
venv. Ergebnisse werden fortlaufend nach jeder Bewertung gespeichert
(speaker_ratings_<stimme>.json neben diesem Skript) - ein Durchlauf
kann jederzeit mit 'q' abgebrochen und spaeter (einfach erneut
aufrufen) beim ersten noch unbewerteten Sprecher fortgesetzt werden.

Beispiele:
  # Erster Durchlauf, alle Sprecher, Standard-Demosatz
  python3 rate_voices.py --voice de_DE-mls-medium

  # Eigener Demosatz
  python3 rate_voices.py --voice de_DE-mls-medium --text "Wie war Ihr Tag heute?"

  # Zweiter Durchlauf: nur die bisherige Shortlist (Bewertung 1 oder 2)
  # nochmal durchgehen
  python3 rate_voices.py --voice de_DE-mls-medium --only-rating 1,2

  # Steuerung ohne echte Audiowiedergabe testen (kein Player noetig)
  python3 rate_voices.py --voice de_DE-mls-medium --dry-run

WAEHREND der Wiedergabe (Casting-Prinzip - kein Enter noetig):
  s        SOFORT abbrechen, automatisch mit 5 bewertet, naechster
           Sprecher - eine erkennbar unbrauchbare Stimme muss den
           Demosatz nicht zu Ende sprechen

Steuerung NACH jeder Wiedergabe (Eingabe + Enter):
  1-5      Bewertung abgeben (1=sehr gut, 5=unbrauchbar), optional
           direkt gefolgt von einem Kommentar, z.B. "2 klingt nasal"
  s        wie 1-5, aber fest auf 5 (gleichbedeutend mit dem
           Sofort-Abbruch waehrend der Wiedergabe, nur nachtraeglich)
  r        aktuellen Sprecher nochmal abspielen
  n        weiter ohne Bewertung (bleibt beim naechsten Aufruf offen)
  p        zurueck zum vorherigen Sprecher
  q        beenden - Fortschritt ist schon gespeichert
"""
import argparse
import io
import json
import select
import shutil
import subprocess
import sys
import termios
import tty
import wave
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VOICES_DIR = SCRIPT_DIR / "voices"
DEFAULT_TEXT = "Guten Tag, wie geht es Ihnen heute? Ich hoffe, Sie hatten einen schoenen Tag."

# In dieser Reihenfolge probiert - aplay (ALSA) ist auf den meisten
# Linux-Systemen ohnehin vorhanden, ffplay/paplay als Alternativen.
_PLAYER_CANDIDATES = ["aplay", "paplay", "ffplay"]


def _find_player():
    for name in _PLAYER_CANDIDATES:
        if shutil.which(name):
            return name
    return None


def _play_wav_bytes(player_name, wav_bytes, tmp_path):
    """Spielt ab und bricht SOFORT ab, wenn waehrend der Wiedergabe 's'
    gedrueckt wird (kein Enter noetig - Terminal kurzzeitig in den
    cbreak-Modus versetzt, siehe termios/tty). Gibt True zurueck, wenn
    per 's' uebersprungen wurde, sonst False."""
    tmp_path.write_bytes(wav_bytes)
    if player_name == "ffplay":
        cmd = ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", str(tmp_path)]
    else:
        cmd = [player_name, str(tmp_path)]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not sys.stdin.isatty():
        # Kein echtes Terminal (z.B. automatisierter Aufruf) - kein
        # Raw-Modus moeglich, einfach normal zu Ende abwarten.
        proc.wait()
        return False

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    skipped = False
    try:
        tty.setcbreak(fd)
        while proc.poll() is None:
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if ready and sys.stdin.read(1).lower() == "s":
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                skipped = True
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return skipped


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
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Keine echte Audiowiedergabe/kein Piper noetig - nur die Steuerung testen",
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

    player_name = None
    voice = None
    if not args.dry_run:
        player_name = _find_player()
        if player_name is None:
            print(
                "Kein Audio-Player gefunden (aplay/paplay/ffplay). "
                "Z.B. 'sudo apt install alsa-utils' installieren, "
                "oder --dry-run zum Testen der Steuerung ohne Ton."
            )
            sys.exit(1)
        from piper.voice import PiperVoice
        from piper.config import SynthesisConfig

        model_path = VOICES_DIR / f"{args.voice}.onnx"
        print(f"Lade {model_path.name} ...")
        voice = PiperVoice.load(str(model_path))

    tmp_path = SCRIPT_DIR / "_rate_voices_tmp.wav"
    i = find_start_index(speaker_ids, ratings, args.start_at)

    print(f"{len(speaker_ids)} Sprecher in dieser Runde. Ergebnisse: {results_path.name}")
    print("Waehrend der Wiedergabe: s = sofort abbrechen + mit 5 bewerten (kein Enter noetig).")
    print("Danach: 1-5 = Bewertung (+ optionaler Kommentar), s = wie 5, r = wiederholen, "
          "n = weiter ohne Bewertung, p = zurueck, q = beenden.\n")

    try:
        while 0 <= i < len(speaker_ids):
            sid = speaker_ids[i]
            corpus_key = id_to_corpus_key.get(sid, "?")
            existing = ratings.get(str(sid))
            status = f" (bisher: {existing['rating']} - {existing.get('comment', '')})" if existing else ""
            print(f"[{i + 1}/{len(speaker_ids)}] Sprecher-ID {sid} ({corpus_key}){status}")

            skipped = False
            if args.dry_run:
                print("  (dry-run: keine Wiedergabe)")
            else:
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as wav_file:
                    voice.synthesize_wav(args.text, wav_file, syn_config=SynthesisConfig(speaker_id=sid))
                skipped = _play_wav_bytes(player_name, buffer.getvalue(), tmp_path)

            if skipped:
                ratings[str(sid)] = {
                    "rating": 5, "comment": "per Skip (S) waehrend der Wiedergabe aussortiert",
                    "corpus_key": corpus_key,
                }
                save_ratings(results_path, ratings)
                print("  -> per Skip sofort mit 5 bewertet.")
                i += 1
                continue

            cmd = input("  > ").strip()
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
            elif key == "s":
                ratings[str(sid)] = {
                    "rating": 5, "comment": cmd[1:].strip() or "per Skip (S) aussortiert",
                    "corpus_key": corpus_key,
                }
                save_ratings(results_path, ratings)
                i += 1
            elif key in "12345":
                ratings[str(sid)] = {
                    "rating": int(key), "comment": cmd[1:].strip(), "corpus_key": corpus_key,
                }
                save_ratings(results_path, ratings)
                i += 1
            else:
                print("  Unbekannter Befehl. 1-5, s, r, n, p oder q.")
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    print(f"\nFertig (oder mit 'q' beendet). {len(ratings)} Sprecher insgesamt bewertet. "
          f"Ergebnisse: {results_path}")


if __name__ == "__main__":
    main()
