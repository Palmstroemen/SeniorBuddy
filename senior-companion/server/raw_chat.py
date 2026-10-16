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

Beispiele:
  # Interaktiv gegen gmxtec (Vorgabe)
  python3 raw_chat.py

  # Einzelne Frage, sofort beenden
  python3 raw_chat.py --prompt "Fasse mir in drei Saetzen zusammen, was REST bedeutet."

  # Gegen einen lokalen Dev-Server statt gmxtec
  python3 raw_chat.py --url ws://127.0.0.1:8000/ws/raw
"""
import argparse
import asyncio
import json
import sys

import websockets

DEFAULT_URL = "wss://gmxtec.tail83779a.ts.net/ws/raw"


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


async def run_interactive(url: str) -> None:
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
            await _send_and_receive(ws, text)


async def run_single_prompt(url: str, prompt: str) -> None:
    async with websockets.connect(url) as ws:
        await _send_and_receive(ws, prompt)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"WebSocket-Adresse von /ws/raw (Vorgabe: {DEFAULT_URL})")
    parser.add_argument("--prompt", default=None, help="Einzelne Frage statt interaktivem Modus")
    args = parser.parse_args()

    try:
        if args.prompt:
            asyncio.run(run_single_prompt(args.url, args.prompt))
        else:
            asyncio.run(run_interactive(args.url))
    except (ConnectionRefusedError, OSError) as err:
        print(f"Verbindung zu {args.url} fehlgeschlagen: {err}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
