"""
Tests fuer die Persona-Designer-Admin-API (/admin/personas/*) in
main.py: Personas anlegen/bearbeiten/loeschen ohne Code anzufassen -
wirkt sofort, uebersteht einen simulierten Neustart, respektiert dass
die 4 mitgelieferten Personas nie ganz verschwinden duerfen (nur
zuruecksetzbar sind).
"""
import config
import main
import pytest
from fastapi.testclient import TestClient

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setattr(main.admin_auth, "ADMIN_TOKEN", "test-admin-token")


@pytest.fixture(autouse=True)
def _reset_persona_state():
    ids_before = set(config.PERSONAS.keys())
    genders_before = dict(config.PERSONA_GENDER)
    yield
    for extra_id in set(config.PERSONAS.keys()) - ids_before:
        del config.PERSONAS[extra_id]
        config.PERSONA_GENDER.pop(extra_id, None)
    for persona_id in ids_before:
        if persona_id in config._BUILTIN_PERSONAS:
            config.PERSONAS[persona_id] = config._BUILTIN_PERSONAS[persona_id]
    config.PERSONA_GENDER.clear()
    config.PERSONA_GENDER.update(genders_before)


def _persona_body(persona_id="nachbarin", **overrides):
    """persona_id=None laesst das 'id'-Feld ganz weg (fuer PUT-Bodies,
    die 'id' bewusst nicht enthalten - die Ziel-ID kommt eindeutig aus
    dem Pfad, siehe PersonaFieldsIn vs. PersonaCreateIn im Plan)."""
    body = {
        "model": "qwen2.5:7b-instruct",
        "always_loaded": True,
        "max_tokens": 400,
        "reengagement_tendency": 0.5,
        "color": "#7A5C99",
        "background_color": "#EEE9F2",
        "variants": {
            "neutral": {"display_name": "Erna", "system_prompt": "Sie-Form bitte.", "voice_id": "v1"},
            "weiblich": {"display_name": "Erna (Nachbarin)", "system_prompt": "Sie-Form bitte.", "voice_id": "v2"},
            "maennlich": {"display_name": "Erwin (Nachbar)", "system_prompt": "Sie-Form bitte.", "voice_id": "v3"},
        },
    }
    if persona_id is not None:
        body["id"] = persona_id
    body.update(overrides)
    return body


def test_admin_personas_require_auth():
    with TestClient(main.app) as client:
        r = client.get("/admin/personas")
    assert r.status_code == 401


def test_list_personas_includes_builtins():
    with TestClient(main.app) as client:
        r = client.get("/admin/personas", headers=ADMIN_HEADERS)
    ids = {p["id"] for p in r.json()}
    assert {"freundin", "reporter", "professor", "technikerin"} <= ids


def test_create_persona_success():
    with TestClient(main.app) as client:
        r = client.post("/admin/personas", json=_persona_body(), headers=ADMIN_HEADERS)
        assert r.status_code == 201
        r2 = client.get("/admin/personas/nachbarin", headers=ADMIN_HEADERS)
    assert r2.status_code == 200
    assert r2.json()["model"] == "qwen2.5:7b-instruct"


def test_list_personas_marks_builtin_vs_custom():
    with TestClient(main.app) as client:
        client.post("/admin/personas", json=_persona_body(), headers=ADMIN_HEADERS)
        r = client.get("/admin/personas", headers=ADMIN_HEADERS)
    by_id = {p["id"]: p for p in r.json()}
    assert by_id["freundin"]["is_builtin"] is True
    assert by_id["nachbarin"]["is_builtin"] is False


def test_get_single_persona_also_includes_is_builtin():
    with TestClient(main.app) as client:
        r = client.get("/admin/personas/freundin", headers=ADMIN_HEADERS)
    assert r.json()["is_builtin"] is True


def test_create_persona_rejects_duplicate_id():
    with TestClient(main.app) as client:
        r = client.post("/admin/personas", json=_persona_body("freundin"), headers=ADMIN_HEADERS)
    assert r.status_code == 409


def test_create_persona_rejects_bad_id_format():
    with TestClient(main.app) as client:
        r = client.post("/admin/personas", json=_persona_body("Not Valid!"), headers=ADMIN_HEADERS)
    assert r.status_code == 422


def test_create_persona_rejects_missing_gender_variant():
    body = _persona_body()
    del body["variants"]["maennlich"]
    with TestClient(main.app) as client:
        r = client.post("/admin/personas", json=body, headers=ADMIN_HEADERS)
    assert r.status_code == 422


def test_create_persona_rejects_reengagement_tendency_out_of_range():
    with TestClient(main.app) as client:
        r = client.post(
            "/admin/personas", json=_persona_body(reengagement_tendency=1.5), headers=ADMIN_HEADERS,
        )
    assert r.status_code == 422


def test_create_persona_rejects_bad_color_format():
    with TestClient(main.app) as client:
        r = client.post("/admin/personas", json=_persona_body(color="blue"), headers=ADMIN_HEADERS)
    assert r.status_code == 422


def test_update_persona_takes_effect_immediately():
    body = _persona_body(None)
    body["variants"]["neutral"]["system_prompt"] = "Neuer Prompt, Sie-Form."
    with TestClient(main.app) as client:
        r = client.put("/admin/personas/reporter", json=body, headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert main.PERSONAS["reporter"].system_prompt == "Neuer Prompt, Sie-Form."


def test_update_persona_rejects_unknown_id():
    with TestClient(main.app) as client:
        r = client.put(
            "/admin/personas/does_not_exist", json=_persona_body(None), headers=ADMIN_HEADERS,
        )
    assert r.status_code == 404


def test_create_persona_takes_effect_immediately_in_persona_lookup():
    with TestClient(main.app) as client:
        client.post("/admin/personas", json=_persona_body(), headers=ADMIN_HEADERS)
    assert main.PERSONAS["nachbarin"].display_name == "Erna"
    assert main.PERSONAS["nachbarin"].voice_id == "v1"


def test_update_persona_survives_simulated_restart():
    body = _persona_body(None)
    body["variants"]["neutral"]["system_prompt"] = "Ueberlebt Neustart, Sie-Form."
    with TestClient(main.app) as client:
        r = client.put("/admin/personas/reporter", json=body, headers=ADMIN_HEADERS)
    assert r.status_code == 200

    main.PERSONAS["reporter"] = config._BUILTIN_PERSONAS["reporter"]
    main._apply_persisted_admin_settings()
    assert main.PERSONAS["reporter"].system_prompt == "Ueberlebt Neustart, Sie-Form."


def test_delete_custom_persona_removes_it_entirely():
    with TestClient(main.app) as client:
        client.post("/admin/personas", json=_persona_body(), headers=ADMIN_HEADERS)
        r = client.delete("/admin/personas/nachbarin", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        assert r.json()["reverted_to_default"] is False
        r2 = client.get("/admin/personas/nachbarin", headers=ADMIN_HEADERS)
    assert r2.status_code == 404
    assert "nachbarin" not in main.PERSONAS


def test_delete_builtin_persona_reverts_instead_of_removing():
    original_prompt = config._BUILTIN_PERSONAS["freundin"].system_prompt
    body = _persona_body(None)
    body["variants"]["neutral"]["system_prompt"] = "Veraendert, Sie-Form."
    with TestClient(main.app) as client:
        client.put("/admin/personas/freundin", json=body, headers=ADMIN_HEADERS)
        r = client.delete("/admin/personas/freundin", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json()["reverted_to_default"] is True
    assert main.PERSONAS["freundin"].system_prompt == original_prompt


def test_delete_unknown_persona_404():
    with TestClient(main.app) as client:
        r = client.delete("/admin/personas/does_not_exist", headers=ADMIN_HEADERS)
    assert r.status_code == 404


def test_create_persona_with_unavailable_model_returns_warning_not_error(monkeypatch):
    async def fake_unavailable(model):
        return False

    monkeypatch.setattr(main.llm_client, "is_model_available", fake_unavailable)
    with TestClient(main.app) as client:
        r = client.post("/admin/personas", json=_persona_body(), headers=ADMIN_HEADERS)
    assert r.status_code == 201
    assert len(r.json().get("warnings", [])) > 0


def test_admin_persona_mutation_preserves_persona_dict_identity():
    """Sicherheitskritischster Test dieser Runde: beweist Objekt-
    Identitaet, nicht nur Werte. Wuerde eine kuenftige Aenderung
    versehentlich 'config.PERSONAS = {...}' statt 'PERSONAS[id] = ...'
    schreiben, bliebe main.PERSONAS (die beim Start gebundene, dann
    veraltete Referenz) die neue Persona NICHT sehen, obwohl
    config.PERSONAS sie enthielte - genau das deckt dieser Test auf."""
    from config import PERSONAS as personas_ref_held_separately

    dict_id_before = id(main.PERSONAS)
    assert id(personas_ref_held_separately) == dict_id_before

    with TestClient(main.app) as client:
        client.post("/admin/personas", json=_persona_body(), headers=ADMIN_HEADERS)

    assert id(main.PERSONAS) == dict_id_before
    assert id(personas_ref_held_separately) == dict_id_before
    assert "nachbarin" in main.PERSONAS
    assert "nachbarin" in personas_ref_held_separately
