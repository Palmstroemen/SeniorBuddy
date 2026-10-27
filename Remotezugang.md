# Remotezugang

Fernzugriff auf den gmxtec (SeniorBuddy-Server) von unterwegs – per
Tailscale, ohne offene Ports. Eingerichtet und getestet am 2026-10-27.

## Geräte im Tailnet (Konto palmstroem177@…)

| Gerät | Rolle |
|---|---|
| `gmxtec` | Server: App, Sprachdienst, Ollama |
| `dell-laptop` | Wartung/Entwicklung per SSH |
| `tab-s7-fe-von-oliver` | Test-Tablet für die App |

## Zugriff

- **App (Tablet/Browser):** `https://gmxtec.tail83779a.ts.net/?user=testnutzer_1`
- **SSH (Laptop):** `ssh gmxtec` – Projekt liegt unter `/opt/SeniorBuddy`
- **Läuft der richtige Code?** `https://gmxtec.tail83779a.ts.net/api/version`

Voraussetzung immer: Tailscale auf dem jeweiligen Gerät **aktiv**
(Android: Schlüssel-Symbol in der Statusleiste).

## Szenarien

**Zuhause im eigenen WLAN** – funktioniert einfach, gleiche Adresse.

**Persönlicher Hotspot / mobile Daten** – funktioniert einfach, gleiche
Adresse. Achtung Datenverbrauch (Sprachausgabe kommt als Audio).

**Fremdes/öffentliches WLAN** (Hotel, Café, Bahn) – funktioniert in der
Regel auch. Verkehr ist durchgehend verschlüsselt, das fremde WLAN sieht
keine Inhalte. Ggf. vorher die WLAN-Anmeldeseite erledigen (siehe unten).

## Vor dem Wegfahren

- gmxtec eingeschaltet lassen (nicht herunterfahren, kein Standby)
- Tablet: Einstellungen → Apps → Tailscale → Akku → **Nicht eingeschränkt**
- optional Tablet: VPN-Einstellungen → Tailscale → **Immer aktives VPN**

## Wenn's nicht klappt

1. **Tailscale aktiv?** Tailscale-App öffnen, verbinden. `gmxtec` muss
   in der Geräteliste als online erscheinen.
2. **Internet überhaupt da?** Irgendeine normale Webseite öffnen.
3. **Öffentliches WLAN, keine Anmeldeseite erscheint?** Tailscale kurz
   ausschalten (bei „Immer aktives VPN“ dieses kurz deaktivieren), im
   WLAN anmelden, Tailscale wieder einschalten.
4. **WLAN sperrt VPNs** (manche Firmen-/Schul-/Gästenetze)? Auf den
   Hotspot ausweichen.
5. **gmxtec offline in der Liste, Laptop/Tablet aber online?** Dann ist
   der Server selbst nicht erreichbar (Stromausfall, Router zuhause
   aus). Aus der Ferne nicht lösbar – jemand vor Ort muss ihn
   einschalten. (Tipp für später: im BIOS „Restore on AC Power Loss“
   auf „Power On“ stellen, dann startet er nach Stromausfall selbst.)
6. **gmxtec online, aber App lädt nicht?** Per `ssh gmxtec` prüfen:
   ```
   systemctl status senior-companion
   sudo systemctl restart senior-companion
   ```
   Sprachdienst analog: `speech-service`, Modelle: `ollama`.
7. **Langsame Antworten?** Meist das WLAN bzw. ein Tailscale-Relay
   statt Direktverbindung – kein Fehler im Aufbau.
