"""
Authentifizierung fuer die Fernwartungs-API (/admin/*) - die einzige
Stelle im Projekt mit echter Auth. Der Rest der API ist bewusst offen
(vertrautes Tailnet, siehe docs/ARCHITECTURE.md), aber Konfiguration
aendern und Updates anstossen sind privilegierte Operationen und
verdienen eine zusaetzliche Schranke.

Ohne gesetztes SENIOR_COMPANION_ADMIN_TOKEN bleibt die Admin-API bewusst
OFFEN statt zu blockieren - waehrend der aktiven Entwicklung (Session-
Notiz 2026-09-22: "das muessen wir nicht weiss Gott wie absichern, noch
sind wir in der Entwicklung") ist ein fehlendes Token der Normalfall,
nicht ein Fehlerzustand. Sobald spaeter ein echtes Token gesetzt wird,
greift die Pruefung unten automatisch wieder ganz normal - kein
Code-Wechsel noetig, nur das Setzen der Umgebungsvariable (siehe README).
"""
import os
import secrets

from fastapi import Header, HTTPException

ADMIN_TOKEN = os.environ.get("SENIOR_COMPANION_ADMIN_TOKEN", "")


async def require_admin(authorization: str = Header(default="")):
    if not ADMIN_TOKEN:
        return
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(401, "Ungueltiges oder fehlendes Admin-Token")
