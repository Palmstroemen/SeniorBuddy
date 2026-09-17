"""
Tests fuer das Beispiel-Plugin server/plugins/example_weather.

Zwei Dinge muessen stimmen, bevor ein Plugin als Vorlage fuer weitere
taugt: die Signatur passt zum dokumentierten Vertrag
(handle(query, user_id)), und die Transparenz-Zusage aus der eigenen
Docstring wird eingehalten (log_external_request VOR dem Request,
nur Koordinaten, keine weiteren Daten).
"""
import json

import memory
from plugins.loader import discover_plugins


def _weather_plugin():
    return discover_plugins()["weather"].module.Plugin()


async def test_handle_accepts_query_and_user_id(httpx_mock):
    httpx_mock.add_response(
        json={"current": {"temperature_2m": 18.4, "precipitation": 0}}
    )
    result = await _weather_plugin().handle("Wie wird das Wetter?", "testnutzer_1")
    assert "18" in result


async def test_handle_logs_transparency_before_the_request(httpx_mock):
    httpx_mock.add_response(
        json={"current": {"temperature_2m": 10.0, "precipitation": 2.5}}
    )
    await _weather_plugin().handle("Regnet es?", "testnutzer_1")

    log = memory.get_transparency_log("testnutzer_1")
    assert len(log) == 1
    assert log[0]["plugin"] == "weather"
    data_sent = json.loads(log[0]["data_sent"])
    assert set(data_sent.keys()) == {"lat", "lon"}


async def test_handle_reports_rain(httpx_mock):
    httpx_mock.add_response(
        json={"current": {"temperature_2m": 9.0, "precipitation": 3.0}}
    )
    result = await _weather_plugin().handle("Regnet es?", "testnutzer_1")
    assert "regnet" in result.lower()


async def test_handle_returns_fallback_when_data_missing(httpx_mock):
    httpx_mock.add_response(json={"current": {}})
    result = await _weather_plugin().handle("Wie wird das Wetter?", "testnutzer_1")
    assert "konnte" in result.lower()
