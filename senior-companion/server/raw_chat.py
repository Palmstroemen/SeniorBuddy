#!/usr/bin/env python3
"""
Terminal-Client fuer /ws/raw ("Ausserordentlicher Nutzer", siehe
main.py) - ein Hintergrund-Chat mit dem groessten konfigurierten
Modell (aktuell Professor Wallners qwen2.5:32b-instruct), der niemals
ein Senior-Gespraech verzoegert: sobald irgendein Senior-Stream
beginnt, bricht priority.py eine laufende Antwort hier aktiv ab
(Nachricht "preempted"), statt sie nur zu verzoegern (Session-Notiz
2026-10-16). Kein eigener Zugriffsschutz (Session-Notiz 2026-10-16,
"erstmal offen lassen") - erreichbar fuer jeden im Tailnet.

Interaktiver Modus: eine Zeile = eine Nachricht, Verlauf bleibt fuer
die Dauer der Verbindung serverseitig im Speicher (main.py's
chat_history), geht beim Beenden verloren. Bei "preempted" wird die
unfertige Teilantwort verworfen (so main.py es schon handhabt) - die
Frage bleibt im Verlauf stehen und kann unveraendert erneut
abgeschickt werden.

--file haengt den Inhalt einer Tabellen-/Textdatei (.xlsx/.csv/.txt/.md/
.json) vor die ERSTE Nachricht - der Server haelt den Verlauf pro
Verbindung im Speicher, die Datei bleibt also fuer alle weiteren
Nachfragen derselben Sitzung im Kontext, ohne erneut mitgeschickt zu
werden (Session-Notiz 2026-10-16: "Files wie Excel kann ich damit
nicht analysieren"). Alte .xls-Dateien (vor Excel 2007) werden nicht
unterstuetzt - vorher als .xlsx oder .csv speichern.

Beispiele:
  # Interaktiv gegen gmxtec (Vorgabe)
  python3 raw_chat.py

  # Einzelne Frage, sofort beenden
  python3 raw_chat.py --prompt "Fasse mir in drei Saetzen zusammen, was REST bedeutet."

  # Excel-Datei mitschicken, danach interaktiv weiterfragen
  python3 raw_chat.py --file ~/Dokumente/haushaltsbuch.xlsx --prompt "Was faellt dir an den Ausgaben auf?"

  # Gegen einen lokalen Dev-Server statt gmxtec
  python3 raw_chat.py --url ws://127.0.0.1:8000/ws/raw
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import websockets

DEFAULT_URL = "wss://gmxtec.tail83779a.ts.net/ws/raw"

# Grobe Faustregel ~4 Zeichen/Token: 20000 Zeichen sind rund 5000 Token -
# genug fuer eine ordentliche Tabelle, laesst aber noch Platz im
# Kontextfenster fuer die eigentliche Frage und den weiteren
# Gespraechsverlauf derselben Sitzung. Ueberschreitungen werden sichtbar
# abgeschnitten, nicht stillschweigend verworfen.
MAX_FILE_CHARS = 20_000

SUPPORTED_SUFFIXES = {".xlsx", ".csv", ".txt", ".md", ".json"}


def _xlsx_to_text(path: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    parts = []
    for sheet in wb.worksheets:
        parts.append(f"### Blatt: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if c is None else str(c) for c in row]
            parts.append(" | ".join(cells))
    return "\n".join(parts)


def load_file_as_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Dateityp '{suffix}' wird nicht unterstuetzt (nur "
            f"{', '.join(sorted(SUPPORTED_SUFFIXES))}). Alte .xls-Dateien "
            "vorher als .xlsx oder .csv speichern."
        )
    text = _xlsx_to_text(path) if suffix == ".xlsx" else path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_FILE_CHARS:
        cut = len(text) - MAX_FILE_CHARS
        text = text[:MAX_FILE_CHARS] + f"\n[... gekuerzt, {cut} weitere Zeichen abgeschnitten ...]"
    return text


def build_file_prefix(path: Path) -> str:
    content = load_file_as_text(path)
    return f'[Inhalt der Datei "{path.name}"]\n{content}\n[Ende Dateiinhalt]\n\n'


async def _send_and_receive(ws, text: str) -> None:
    await ws.send(text)
    while True:
        try:
            msg = await ws.recv()
        except websockets.exceptions.ConnectionClosed:
            # z.B. wenn das konfigurierte Modell auf der Gegenseite nicht
            # verfuegbar ist (main.py's raw_chat() faengt das selbst nicht
            # ab) - ohne diesen Fang wuerde das Skript hier lautlos
            # haengen bleiben statt einen Fehler zu zeigen.
            print("\n[Verbindung wurde unerwartet beendet]")
            return
        data = json.loads(msg)
        msg_type = data.get("type")
        if msg_type == "token":
            print(data["content"], end="", flush=True)
        elif msg_type == "waiting":
            print("[wartet - ein Senior-Gespraech laeuft gerade] ", end="", flush=True)
        elif msg_type == "preempted":
            print("\n[von einem Senior-Gespraech unterbrochen - Teilantwort verworfen, Frage erneut stellen]")
            return
        elif msg_type == "blocked":
            print(f"\n[abgelehnt: {data.get('reason')}]")
            return
        elif msg_type == "done":
            print()
            return


async def run_interactive(url: str, file_prefix: str | None = None) -> None:
    print(f"Verbunden mit {url} - leere Zeile oder Strg-D zum Beenden.\n")
    async with websockets.connect(url) as ws:
        while True:
            try:
                text = await asyncio.get_event_loop().run_in_executor(None, input, "> ")
            except EOFError:
                print()
                return
            text = text.strip()
            if not text:
                return
            if file_prefix:
                text = file_prefix + text
                file_prefix = None  # nur die ERSTE Nachricht traegt die Datei
            await _send_and_receive(ws, text)


async def run_single_prompt(url: str, prompt: str, file_prefix: str | None = None) -> None:
    async with websockets.connect(url) as ws:
        await _send_and_receive(ws, (file_prefix or "") + prompt)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"WebSocket-Adresse von /ws/raw (Vorgabe: {DEFAULT_URL})")
    parser.add_argument("--prompt", default=None, help="Einzelne Frage statt interaktivem Modus")
    parser.add_argument("--file", default=None, help="Datei (.xlsx/.csv/.txt/.md/.json), die der ersten Nachricht vorangestellt wird")
    args = parser.parse_args()

    file_prefix = None
    if args.file:
        path = Path(args.file).expanduser()
        if not path.exists():
            print(f"Datei nicht gefunden: {path}", file=sys.stderr)
            sys.exit(1)
        try:
            file_prefix = build_file_prefix(path)
        except ValueError as err:
            print(str(err), file=sys.stderr)
            sys.exit(1)
        print(f"Datei geladen: {path.name} ({len(file_prefix)} Zeichen)")

    try:
        if args.prompt:
            asyncio.run(run_single_prompt(args.url, args.prompt, file_prefix))
        else:
            asyncio.run(run_interactive(args.url, file_prefix))
    except (ConnectionRefusedError, OSError) as err:
        print(f"Verbindung zu {args.url} fehlgeschlagen: {err}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
