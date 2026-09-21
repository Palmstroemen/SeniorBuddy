"""
Tests fuer server/display.py - Registrierung offener TV-/Zweitbildschirm-
Verbindungen (in-memory, analog zu room.py's modulglobalem Zustand) und
Broadcast von Anzeige-Kommandos an sie.
"""
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import display
import main


@pytest.fixture(autouse=True)
def _reset_display_state():
    display._connections.clear()
    yield
    display._connections.clear()


def test_connection_count_zero_for_new_user():
    assert display.connection_count("disp_user1") == 0


def test_register_adds_connection():
    ws = AsyncMock()
    display.register("disp_user2", ws)
    assert display.connection_count("disp_user2") == 1


def test_unregister_removes_connection_and_cleans_up_empty_entry():
    ws = AsyncMock()
    display.register("disp_user3", ws)
    display.unregister("disp_user3", ws)
    assert display.connection_count("disp_user3") == 0
    assert "disp_user3" not in display._connections


async def test_broadcast_with_zero_connections_returns_zero():
    delivered = await display.broadcast("disp_user4", {"type": "display", "url": "x"})
    assert delivered == 0


async def test_broadcast_delivers_to_multiple_connections():
    ws1, ws2 = AsyncMock(), AsyncMock()
    display.register("disp_user5", ws1)
    display.register("disp_user5", ws2)
    message = {"type": "display", "url": "https://example.com/foto.jpg"}

    delivered = await display.broadcast("disp_user5", message)

    assert delivered == 2
    ws1.send_json.assert_awaited_once_with(message)
    ws2.send_json.assert_awaited_once_with(message)


async def test_broadcast_self_heals_on_dead_connection():
    healthy, dead = AsyncMock(), AsyncMock()
    dead.send_json.side_effect = RuntimeError("Verbindung schon zu")
    display.register("disp_user6", healthy)
    display.register("disp_user6", dead)

    delivered = await display.broadcast("disp_user6", {"type": "display", "url": "x"})
    assert delivered == 1
    assert display.connection_count("disp_user6") == 1

    # Zweiter Broadcast beweist: die tote Verbindung ist wirklich entfernt,
    # nicht nur beim ersten Mal uebersprungen worden.
    healthy.send_json.reset_mock()
    delivered_again = await display.broadcast("disp_user6", {"type": "display", "url": "y"})
    assert delivered_again == 1
    healthy.send_json.assert_awaited_once()


async def test_broadcast_is_per_user():
    ws_a, ws_b = AsyncMock(), AsyncMock()
    display.register("disp_user7a", ws_a)
    display.register("disp_user7b", ws_b)

    await display.broadcast("disp_user7a", {"type": "display", "url": "x"})

    ws_a.send_json.assert_awaited_once()
    ws_b.send_json.assert_not_called()


# --- Integration (echte WS-Verbindung + echter POST-Endpunkt) --------------


def test_display_websocket_receives_pushed_message():
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/display/disp_int1") as ws:
            res = client.post(
                "/api/display/disp_int1",
                json={"url": "https://example.com/foto.jpg"},
            )
            assert res.status_code == 200
            assert res.json() == {"delivered_to": 1}

            msg = ws.receive_json()
            assert msg == {"type": "display", "url": "https://example.com/foto.jpg"}


def test_display_post_with_no_open_connection_returns_zero():
    with TestClient(main.app) as client:
        res = client.post("/api/display/disp_int2", json={"url": "https://example.com/x.jpg"})
    assert res.json() == {"delivered_to": 0}


def test_display_websocket_disconnect_removes_from_registry():
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/display/disp_int3"):
            pass  # Verbindung wird beim Verlassen des with-Blocks getrennt

        # Das serverseitige finally/unregister laeuft im Hintergrund-
        # Thread des Test-Clients - kurz pollen statt eine feste Sleep-
        # Zeit zu raten (analog zu test_room_chat.py's
        # _receive_json_with_timeout-Gedanken, hier aber auf den
        # Registry-Zustand direkt statt auf eine Nachricht gepollt).
        deadline = time.time() + 2.0
        while time.time() < deadline and display.connection_count("disp_int3") != 0:
            time.sleep(0.02)

        res = client.post("/api/display/disp_int3", json={"url": "https://example.com/x.jpg"})
    assert res.json() == {"delivered_to": 0}
