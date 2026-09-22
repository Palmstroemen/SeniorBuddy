"""
Tests fuer server/admin_auth.py - die einzige Stelle im Projekt mit
echter Authentifizierung. Bewusst NICHT fail-closed: ohne konfiguriertes
Token bleibt die Admin-API waehrend der Entwicklung offen (Session-Notiz
2026-09-22), erst ein gesetztes Token aktiviert die Pruefung.
"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import admin_auth


@pytest.fixture
def app_with_protected_route(monkeypatch):
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(admin_auth.require_admin)])
    def protected():
        return {"ok": True}

    return app


def test_no_token_configured_allows_access(app_with_protected_route, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_TOKEN", "")
    with TestClient(app_with_protected_route) as client:
        r = client.get("/protected")
    assert r.status_code == 200


def test_no_token_configured_allows_access_even_with_garbage_header(app_with_protected_route, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_TOKEN", "")
    with TestClient(app_with_protected_route) as client:
        r = client.get("/protected", headers={"Authorization": "Bearer irgendwas"})
    assert r.status_code == 200


def test_missing_authorization_header_returns_401(app_with_protected_route, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_TOKEN", "geheim123")
    with TestClient(app_with_protected_route) as client:
        r = client.get("/protected")
    assert r.status_code == 401


def test_wrong_token_returns_401(app_with_protected_route, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_TOKEN", "geheim123")
    with TestClient(app_with_protected_route) as client:
        r = client.get("/protected", headers={"Authorization": "Bearer falsch"})
    assert r.status_code == 401


def test_wrong_scheme_returns_401(app_with_protected_route, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_TOKEN", "geheim123")
    with TestClient(app_with_protected_route) as client:
        r = client.get("/protected", headers={"Authorization": "Basic geheim123"})
    assert r.status_code == 401


def test_correct_token_is_let_through(app_with_protected_route, monkeypatch):
    monkeypatch.setattr(admin_auth, "ADMIN_TOKEN", "geheim123")
    with TestClient(app_with_protected_route) as client:
        r = client.get("/protected", headers={"Authorization": "Bearer geheim123"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
