#!/usr/bin/env python3
"""
Messstand fuer Sprachausgabe-Modelle: dieselben Testsaetze durch
verschiedene TTS-Engines schicken, messen, wann der ERSTE Ton kommt
(das, was im Gespraech zaehlt) und wie lange die ganze Ausgabe dauert,
und alles auf einer Anhoer-Seite nebeneinanderstellen.

Gedacht fuer den Moment, in dem ein neues, vielversprechendes Modell
erscheint: eine neue Engine = eine neue _run_<engine>()-Funktion, der
Rest (Messung, Bericht, Anhoer-Seite) bleibt gleich.

Jede Engine laeuft in IHREM EIGENEN venv (Piper im speech-service-venv,
MOSS-TTS-Nano in seinem eigenen) - darum zwei Schritte:

  1. "run" einmal pro Engine/Stimme, jeweils mit dem passenden Python.
     Schreibt WAVs + results.json nach <out>/<label>/.
  2. "serve" (beliebiges Python, nur Standardbibliothek) baut daraus
     index.html und liefert sie im Tailnet aus. Der gmxtec hat keinen
     Lautsprecher (Session-Notiz 2026-09-24) - angehoert wird im
     Browser von Tablet/Laptop, wie bei rate_voices.py.

Beispiele (auf dem gmxtec):
  # Piper, so wie die App es heute nutzt
  .venv/bin/python tts_bench.py run --engine piper --voice de_DE-thorsten-low \\
      --label "Piper thorsten-low"

  # MOSS-TTS-Nano, Stimme aus einer Referenzaufnahme geklont
  ~/moss_tts_nano_test/venv/bin/python tts_bench.py run --engine moss \\
      --moss-dir ~/moss_tts_nano_test/MOSS-TTS-Nano \\
      --ref ~/tts_bench/ref_thorsten.wav --label "MOSS-Nano Thorsten"

  # Anhoer-Seite bauen und im Tailnet ausliefern
  python3 tts_bench.py serve

Gemessen pro Satz:
  erster Ton  - ab Aufruf bis die Engine das erste abspielbare Audio
                liefert. Piper: erster fertiger Satz (Piper arbeitet
                satzweise). MOSS: erster Streaming-Block.
  gesamt      - bis die ganze Ausgabe fertig ist. Bei Piper ist DAS
                heute die echte Wartezeit in der App, weil
                speech-service/main.py's /synthesize erst die fertige
                WAV-Datei zurueckgibt.
  RTF         - gesamt / Audiodauer (< 1 = schneller als Echtzeit).
"""
import argparse
import html
import http.server
import json
import re
import socketserver
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path

DEFAULT_OUT = Path.home() / "tts_bench"
DEFAULT_PORT = 8766

# Bewusst gemischt: kurze Rueckfrage, typischer Persona-Satz, langer
# Satz mit Nebensaetzen, Zahlen/Datum, Umlaute, eine emotionale Stelle.
# Formulierungen angelehnt an Beispieltexte/ (Repo-Root).
DEFAULT_TEXTS = [
    "Ach, wirklich?",
    "Guten Morgen! Haben Sie gut geschlafen?",
    "Ach, Sie gehen heute in die Stadt? Da fällt mir ein, ich weiß ja noch gar nicht, in welcher Stadt Sie wohnen.",
    "Wenn Sie heute Bürgermeister wären, was würden Sie wirklich von Herzen gerne ändern, und was würden Sie lieber erst einmal auf die lange Bank schieben?",
    "Am 3. Mai 1962 war ich zwölf Jahre alt, und wir sind mit dem Zug nach Salzburg gefahren.",
    "Oh, das tut mir aber leid. Das war bestimmt eine schwere Zeit für Sie.",
]


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_") or "engine"


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# ---------------------------------------------------------------------
# Engines: jede liefert pro Text (wav_bytes_writer, first_audio_s, total_s)
# ---------------------------------------------------------------------

def _run_piper(args, texts, out_dir):
    from piper.config import SynthesisConfig
    from piper.voice import PiperVoice

    voices_dir = Path(args.voices_dir or Path(__file__).resolve().parent / "voices")
    t0 = time.perf_counter()
    voice = PiperVoice.load(str(voices_dir / f"{args.voice}.onnx"))
    load_s = time.perf_counter() - t0
    syn_config = SynthesisConfig(speaker_id=args.speaker_id) if args.speaker_id is not None else None

    def synth(text, wav_path):
        start = time.perf_counter()
        first = None
        chunks = []
        rate = None
        for chunk in voice.synthesize(text, syn_config=syn_config):
            if first is None:
                first = time.perf_counter() - start
            chunks.append(chunk.audio_int16_bytes)
            rate = chunk.sample_rate
        total = time.perf_counter() - start
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(chunks))
        return first, total

    meta = {"engine": "piper", "voice": args.voice, "speaker_id": args.speaker_id}
    return load_s, synth, meta


def _run_moss(args, texts, out_dir):
    moss_dir = Path(args.moss_dir).expanduser().resolve()
    sys.path.insert(0, str(moss_dir))
    from onnx_tts_runtime import OnnxTtsRuntime

    t0 = time.perf_counter()
    runtime = OnnxTtsRuntime(
        model_dir=moss_dir / "models",
        thread_count=args.threads,
        output_dir=out_dir,
    )
    load_s = time.perf_counter() - t0

    # MOSS laedt die Referenz per torchaudio.load(), das in aktuellen
    # torchaudio-Versionen zwingend das Zusatzpaket torchcodec (+ System-
    # ffmpeg) braucht - ohne scheitert schon eine einfache WAV-Datei.
    # Statt das nachzuinstallieren: WAV mit der Standardbibliothek lesen,
    # nur das Resampling bleibt bei torchaudio (braucht kein torchcodec).
    import numpy as np
    import torch
    import torchaudio

    def load_reference_wav(path):
        with wave.open(str(Path(path).expanduser()), "rb") as w:
            rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError("Referenz muss 16-bit-WAV sein")
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        waveform = torch.from_numpy(samples.reshape(-1, channels).T.copy())
        target_rate = int(runtime.codec_meta["codec_config"]["sample_rate"])
        target_channels = int(runtime.codec_meta["codec_config"]["channels"])
        if rate != target_rate:
            waveform = torchaudio.functional.resample(waveform, rate, target_rate)
        if waveform.shape[0] == 1 and target_channels > 1:
            waveform = waveform.repeat(target_channels, 1)
        elif waveform.shape[0] > 1 and target_channels == 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        return waveform.unsqueeze(0).numpy().astype(np.float32, copy=False)

    runtime._load_reference_audio = load_reference_wav

    # Erster-Ton-Messung: MOSS meldet den Zeitpunkt seines ersten
    # Streaming-Blocks nicht nach aussen - darum den Decoder-Aufruf
    # umwickeln und den ersten Aufruf mit echtem Audio festhalten.
    session = runtime.codec_streaming_session
    original_run_frames = session.run_frames
    state = {"first": None}

    def timed_run_frames(frames):
        result = original_run_frames(frames)
        if state["first"] is None and result is not None and result[1] > 0:
            state["first"] = time.perf_counter()
        return result

    session.run_frames = timed_run_frames

    def synth(text, wav_path):
        state["first"] = None
        start = time.perf_counter()
        runtime.synthesize(
            text=text,
            voice=args.voice,
            prompt_audio_path=args.ref,
            output_audio_path=wav_path,
            streaming=True,
            enable_wetext=False,  # WeTextProcessing kennt nur zh/en
            seed=args.seed,
        )
        total = time.perf_counter() - start
        first = (state["first"] - start) if state["first"] else total
        return first, total

    meta = {
        "engine": "moss-tts-nano",
        "ref": str(args.ref) if args.ref else None,
        "voice": args.voice,
        "threads": args.threads,
    }
    return load_s, synth, meta


ENGINES = {"piper": _run_piper, "moss": _run_moss}


def cmd_run(args):
    texts = DEFAULT_TEXTS
    if args.texts:
        texts = [line.strip() for line in Path(args.texts).read_text(encoding="utf-8").splitlines() if line.strip()]

    out_dir = Path(args.out).expanduser() / _slug(args.label)
    out_dir.mkdir(parents=True, exist_ok=True)

    load_s, synth, meta = ENGINES[args.engine](args, texts, out_dir)
    print(f"[{args.label}] Modell geladen in {load_s:.2f}s")

    # Aufwaermen (nicht gemessen): erste Inferenz initialisiert bei
    # beiden Engines noch Puffer/Graphen und waere sonst unfair langsam.
    synth("Hallo.", out_dir / "_warmup.wav")
    (out_dir / "_warmup.wav").unlink(missing_ok=True)

    rows = []
    for index, text in enumerate(texts, start=1):
        wav_path = out_dir / f"{index:02d}.wav"
        firsts, totals = [], []
        for _ in range(args.repeat):
            first, total = synth(text, wav_path)
            firsts.append(first)
            totals.append(total)
        audio_s = _wav_duration(wav_path)
        row = {
            "index": index,
            "text": text,
            "wav": wav_path.name,
            "first_audio_s": round(statistics.median(firsts), 3),
            "total_s": round(statistics.median(totals), 3),
            "audio_s": round(audio_s, 3),
            "rtf": round(statistics.median(totals) / audio_s, 3) if audio_s else None,
        }
        rows.append(row)
        print(
            f"  {index:02d}  erster Ton {row['first_audio_s']:.2f}s  gesamt {row['total_s']:.2f}s"
            f"  Audio {audio_s:.2f}s  RTF {row['rtf']}  | {text[:50]}"
        )

    results = {
        "label": args.label,
        "meta": meta,
        "load_s": round(load_s, 3),
        "repeat": args.repeat,
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rows": rows,
    }
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{args.label}] gespeichert in {out_dir}")


# ---------------------------------------------------------------------
# Bericht + Anhoer-Seite (nur Standardbibliothek)
# ---------------------------------------------------------------------

PAGE_STYLE = """
body{font-family:system-ui,sans-serif;margin:16px;background:#faf8f5;color:#222}
h1{font-size:1.3em} table{border-collapse:collapse;width:100%;margin-bottom:28px}
td,th{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#eee8df} .n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.text{font-size:1.05em;margin:18px 0 6px} audio{width:240px;height:36px}
.good{color:#1a7f37;font-weight:600} .bad{color:#b42318;font-weight:600} small{color:#666}
"""


def _fmt(value, good=None, bad=None):
    css = ""
    if good is not None and value <= good:
        css = "good"
    elif bad is not None and value >= bad:
        css = "bad"
    return f'<td class="n {css}">{value:.2f}</td>'


def build_page(out_root: Path) -> str:
    runs = []
    for results_path in sorted(out_root.glob("*/results.json")):
        data = json.loads(results_path.read_text(encoding="utf-8"))
        data["dir"] = results_path.parent.name
        runs.append(data)

    parts = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>TTS-Messstand</title><style>{PAGE_STYLE}</style><h1>TTS-Messstand</h1>"]
    if not runs:
        parts.append("<p>Noch keine Messungen - erst <code>tts_bench.py run</code> ausfuehren.</p>")
        return "".join(parts)

    # Uebersicht: Mediane ueber alle Saetze
    parts.append("<table><tr><th>Engine</th><th class=n>erster Ton (Median)</th>"
                 "<th class=n>erster Ton (max)</th><th class=n>gesamt (Median)</th>"
                 "<th class=n>RTF (Median)</th><th class=n>Laden</th></tr>")
    for run in runs:
        rows = run["rows"]
        first = [r["first_audio_s"] for r in rows]
        parts.append(
            f"<tr><td>{html.escape(run['label'])}<br><small>{html.escape(json.dumps(run['meta'], ensure_ascii=False))}"
            f" · {html.escape(run['measured_at'])}</small></td>"
            + _fmt(statistics.median(first), good=0.3, bad=1.0)
            + _fmt(max(first), good=0.5, bad=1.5)
            + _fmt(statistics.median(r["total_s"] for r in rows), good=0.5, bad=2.0)
            + _fmt(statistics.median(r["rtf"] for r in rows), good=0.3, bad=1.0)
            + f'<td class="n">{run["load_s"]:.1f}s</td></tr>'
        )
    parts.append("</table><p><small>Sekunden. Grün/rot: grobe Richtwerte fürs Gespräch "
                 "(erster Ton unter 0,3 s fühlt sich sofort an, über 1 s wie eine Denkpause). "
                 "Bei Piper ist heute <b>gesamt</b> die echte Wartezeit in der App.</small></p>")

    # Pro Satz: alle Engines untereinander zum direkten Vergleichen
    texts = {}
    for run in runs:
        for row in run["rows"]:
            texts.setdefault(row["index"], row["text"])
    for index, text in sorted(texts.items()):
        parts.append(f"<p class=text><b>{index}.</b> {html.escape(text)}</p><table>"
                     "<tr><th>Engine</th><th>Anhören</th><th class=n>erster Ton</th>"
                     "<th class=n>gesamt</th><th class=n>Audio</th></tr>")
        for run in runs:
            row = next((r for r in run["rows"] if r["index"] == index), None)
            if row is None:
                continue
            src = f"{run['dir']}/{row['wav']}"
            parts.append(
                f"<tr><td>{html.escape(run['label'])}</td>"
                f"<td><audio controls preload=none src='{html.escape(src)}'></audio></td>"
                + _fmt(row["first_audio_s"], good=0.3, bad=1.0)
                + _fmt(row["total_s"], good=0.5, bad=2.0)
                + f'<td class="n">{row["audio_s"]:.2f}</td></tr>'
            )
        parts.append("</table>")
    return "".join(parts)


def _tailnet_ip() -> str | None:
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
        return out.stdout.split()[0] if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cmd_serve(args):
    out_root = Path(args.out).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "index.html").write_text(build_page(out_root), encoding="utf-8")
    if args.no_serve:
        print(f"Seite geschrieben: {out_root / 'index.html'}")
        return

    # Nur an die Tailnet-Adresse binden, nie an 0.0.0.0 - "kein offener
    # Port" gilt auch fuer Werkzeuge (siehe README, Fernzugriff).
    host = args.host or _tailnet_ip() or "127.0.0.1"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out_root), **kw)

        def log_message(self, format, *a):
            pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                # Bei jedem Neuladen frisch bauen - neue Messlaeufe
                # erscheinen ohne Neustart des Servers.
                (out_root / "index.html").write_text(build_page(out_root), encoding="utf-8")
            super().do_GET()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, args.port), Handler) as server:
        print(f"Anhoer-Seite: http://{host}:{args.port}/  (Strg+C beendet)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"Ergebnisordner (Standard: {DEFAULT_OUT})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="eine Engine/Stimme messen")
    run.add_argument("--engine", choices=sorted(ENGINES), required=True)
    run.add_argument("--label", required=True, help="Name in der Uebersicht, z.B. 'Piper thorsten-low'")
    run.add_argument("--texts", help="Textdatei, ein Satz pro Zeile (Standard: eingebaute Testsaetze)")
    run.add_argument("--repeat", type=int, default=3, help="Messungen pro Satz, Median zaehlt (Standard: 3)")
    run.add_argument("--voice", help="Piper: Stimmname. MOSS: eingebaute Stimme (statt --ref)")
    run.add_argument("--speaker-id", type=int, help="Piper: Sprecher bei Mehrsprecher-Stimmen")
    run.add_argument("--voices-dir", help="Piper: Stimmenordner (Standard: speech-service/voices)")
    run.add_argument("--moss-dir", help="MOSS: Pfad zum MOSS-TTS-Nano-Repo")
    run.add_argument("--ref", help="MOSS: Referenzaufnahme (wav) zum Stimmklonen")
    run.add_argument("--threads", type=int, default=4, help="MOSS: CPU-Threads (Standard: 4)")
    run.add_argument("--seed", type=int, default=1234, help="MOSS: fester Zufallswert fuer vergleichbare Laeufe")
    run.set_defaults(func=cmd_run)

    serve = sub.add_parser("serve", help="Anhoer-Seite bauen und im Tailnet ausliefern")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--host", help="Bind-Adresse (Standard: eigene Tailnet-IP)")
    serve.add_argument("--no-serve", action="store_true", help="nur index.html schreiben")
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    if getattr(args, "engine", None) == "moss" and not args.moss_dir:
        parser.error("--engine moss braucht --moss-dir")
    if getattr(args, "engine", None) == "piper" and not args.voice:
        parser.error("--engine piper braucht --voice")
    args.func(args)


if __name__ == "__main__":
    main()
