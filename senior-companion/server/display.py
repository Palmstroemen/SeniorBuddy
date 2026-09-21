"""
Anzeige-Registrierung fuer TV-/Zweitbildschirme: haelt offene
/ws/display/{user_id}-Verbindungen fest und erlaubt es, ihnen per
Broadcast ein Bild/Video-Kommando zu schicken.

Reiner Prozessspeicher (keine SQLite), analog zu room.py's _present.
Ein Serverneustart trennt alle TV-Verbindungen; der TV-Client baut die
Verbindung per Auto-Reconnect selbst wieder auf (siehe tv.js).
"""
import logging

from fastapi import WebSocket

log = logging.getLogger("display")

# user_id -> Menge offener Display-Websockets (mehrere gleichzeitig
# erlaubt, z.B. zwei Fernseher oder ein Fernseher + ein Test-Tab)
_connections: dict[str, set[WebSocket]] = {}


def register(user_id: str, websocket: WebSocket) -> None:
    _connections.setdefault(user_id, set()).add(websocket)


def unregister(user_id: str, websocket: WebSocket) -> None:
    conns = _connections.get(user_id)
    if not conns:
        return
    conns.discard(websocket)
    if not conns:
        del _connections[user_id]


async def broadcast(user_id: str, message: dict) -> int:
    """Schickt `message` an alle offenen Display-Verbindungen von
    user_id. Tote Verbindungen (send schlaegt fehl) werden dabei still
    aus der Registrierung entfernt, statt den ganzen Broadcast
    abzubrechen - eine kaputte Fernseher-Verbindung darf eine zweite
    nicht stoeren. Gibt zurueck, an wie viele Verbindungen erfolgreich
    gesendet wurde."""
    conns = list(_connections.get(user_id, ()))
    delivered = 0
    for ws in conns:
        try:
            await ws.send_json(message)
            delivered += 1
        except Exception:
            log.info("Tote Display-Verbindung entfernt (user_id=%s)", user_id)
            unregister(user_id, ws)
    return delivered


def connection_count(user_id: str) -> int:
    return len(_connections.get(user_id, ()))
