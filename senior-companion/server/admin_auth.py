"""
Authentifizierung fuer die Fernwartungs-API (/admin/*) - die einzige
Stelle im Projekt mit echter Auth. Der Rest der API ist bewusst offen
(vertrautes Tailnet, siehe docs/ARCHITECTURE.md), aber Konfiguration
aendern und Updates anstossen sind privilegierte Operationen und
verdienen eine zusaetzliche Schranke.
"""
import os
import secrets

from fastapi import Header, HTTPException

ADMIN_TOKEN = os.environ.get("SENIOR_COMPANION_ADMIN_TOKEN", "")


async def require_admin(authorization: str = Header(default="")):
    if not ADMIN_TOKEN:
        raise HTTPException(
            503, "Admin-API ist nicht konfiguriert (SENIOR_COMPANION_ADMIN_TOKEN fehlt)"
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(401, "Ungueltiges oder fehlendes Admin-Token")
