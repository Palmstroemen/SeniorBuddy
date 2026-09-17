"""
Beispiel-Plugin: Wetterabfrage.

Zeigt das Muster, dem jedes Internet-Plugin folgen sollte:
1. Nur ausfuehren, wenn `enabled` (Nutzer-Freigabe) gesetzt ist -
   das prueft schon der Aufrufer in main.py/dispatch.py, nicht das
   Plugin selbst.
2. VOR dem eigentlichen Request: log_external_request() aufrufen,
   damit es in der Transparenz-Anzeige auftaucht.
3. So wenig Daten wie moeglich rausschicken (hier: nur Koordinaten,
   kein Name, keine Adresse).

Standort kommt aus config.HOME_LOCATION - eine bewusste Vereinfachung,
solange es kein Nutzer-Profil-System gibt (siehe docs/ARCHITECTURE.md).
"""
import httpx

import memory
from config import HOME_LOCATION


class Plugin:
    async def handle(self, query: str, user_id: str) -> str:
        lat, lon = HOME_LOCATION["lat"], HOME_LOCATION["lon"]

        memory.log_external_request(
            user_id,
            "weather",
            "Wetterabfrage fuer den Wohnort",
            data_sent={"lat": lat, "lon": lon},
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,precipitation",
                },
            )
            data = resp.json().get("current", {})

        temp = data.get("temperature_2m")
        rain = data.get("precipitation", 0)
        if temp is None:
            return "Ich konnte das Wetter gerade nicht abrufen."
        if rain and rain > 0:
            return f"Es sind {temp:.0f} Grad und es regnet gerade."
        return f"Es sind {temp:.0f} Grad und es ist trocken."
