"""
Tests fuer die Persona-Designer-Grundlage in config.py: verlustfreies
to_dict()/from_dict(), sowie apply_persona_overrides()/remove_persona_
override() - das In-Place-Mutations-Fundament, auf dem main.py's
/admin/personas-Routen aufbauen.

config.PERSONAS/PERSONA_GENDER sind echte, sitzungsweite Python-
Singletons (kein Test-Fixture setzt sie automatisch zurueck) - jeder
Test hier raeumt seine eigenen Aenderungen ueber die autouse-Fixture
unten wieder auf, analog zu test_admin.py's manuellen "aufraeumen"-
Kommentaren.
"""
import pytest

import config


@pytest.fixture(autouse=True)
def _reset_persona_state():
    ids_before = set(config.PERSONAS.keys())
    genders_before = dict(config.PERSONA_GENDER)
    yield
    for extra_id in set(config.PERSONAS.keys()) - ids_before:
        del config.PERSONAS[extra_id]
        config.PERSONA_GENDER.pop(extra_id, None)
    for persona_id in ids_before:
        config.PERSONAS[persona_id] = config._BUILTIN_PERSONAS.get(
            persona_id, config.PERSONAS[persona_id]
        )
    config.PERSONA_GENDER.clear()
    config.PERSONA_GENDER.update(genders_before)


def _sample_persona_dict(persona_id="testpersona"):
    return {
        "id": persona_id,
        "model": "qwen2.5:7b-instruct",
        "always_loaded": True,
        "max_tokens": 400,
        "reengagement_tendency": 0.5,
        "color": "#7A5C99",
        "background_color": "#EEE9F2",
        "variants": {
            "neutral": {"display_name": "Test", "system_prompt": "Sie-Form bitte.", "voice_id": "v1"},
            "weiblich": {"display_name": "Test (w)", "system_prompt": "Sie-Form bitte.", "voice_id": "v2"},
            "maennlich": {"display_name": "Test (m)", "system_prompt": "Sie-Form bitte.", "voice_id": "v3"},
        },
    }


def test_persona_variant_to_dict_from_dict_roundtrip():
    variant = config.PersonaVariant(display_name="X", system_prompt="Y", voice_id="z1")
    restored = config.PersonaVariant.from_dict(variant.to_dict())
    assert restored == variant


def test_persona_config_to_dict_from_dict_roundtrip():
    original = config.PERSONAS["freundin"]
    restored = config.PersonaConfig.from_dict(original.to_dict())
    assert restored == original


def test_apply_persona_overrides_adds_new_persona():
    config.apply_persona_overrides([_sample_persona_dict("neuepersona")])
    assert "neuepersona" in config.PERSONAS
    assert config.PERSONAS["neuepersona"].model == "qwen2.5:7b-instruct"
    assert config.PERSONA_GENDER["neuepersona"] == "neutral"


def test_apply_persona_overrides_replaces_existing_builtin():
    d = _sample_persona_dict("freundin")
    d["variants"]["neutral"]["system_prompt"] = "Neuer Prompt, Sie-Form."
    config.apply_persona_overrides([d])
    assert config.PERSONAS["freundin"].system_prompt == "Neuer Prompt, Sie-Form."


def test_apply_persona_overrides_preserves_existing_persona_gender_via_setdefault():
    config.PERSONA_GENDER["freundin"] = "weiblich"
    config.apply_persona_overrides([_sample_persona_dict("freundin")])
    assert config.PERSONA_GENDER["freundin"] == "weiblich"


def test_remove_persona_override_reverts_builtin_to_shipped_default():
    original_prompt = config.PERSONAS["freundin"].system_prompt
    config.apply_persona_overrides([_sample_persona_dict("freundin")])
    assert config.PERSONAS["freundin"].system_prompt != original_prompt

    reverted = config.remove_persona_override("freundin")
    assert reverted is True
    assert config.PERSONAS["freundin"].system_prompt == original_prompt


def test_remove_persona_override_deletes_custom_persona_entirely():
    config.apply_persona_overrides([_sample_persona_dict("wegwerfpersona")])
    removed = config.remove_persona_override("wegwerfpersona")
    assert removed is False
    assert "wegwerfpersona" not in config.PERSONAS
    assert "wegwerfpersona" not in config.PERSONA_GENDER


def test_persona_variant_from_dict_without_face_fields_uses_defaults():
    """Rueckwaertskompatibilitaet: Varianten-Dicts, die vor dieser Runde
    gespeichert wurden, haben keine face_*-Schluessel - from_dict()
    darf dabei nicht mit einem fehlenden Pflichtargument abstuerzen,
    sondern muss auf die Dataclass-Defaults zurueckfallen (gleiches
    Prinzip wie das schon laenger bestehende voice_id: str = "")."""
    old_shaped = {"display_name": "Alt", "system_prompt": "Sie-Form."}
    restored = config.PersonaVariant.from_dict(old_shaped)
    assert restored.face_eyebrows == "neutral"
    assert restored.face_eyes == "happy"
    assert restored.face_mouth == "smile"
    assert restored.face_hairstyle == "kurz"
    assert restored.face_beard == ""


def test_apply_persona_overrides_with_old_shaped_dict_still_works():
    """Derselbe Rueckwaertskompatibilitaets-Fall, aber ueber den
    tatsaechlichen Lade-Pfad (apply_persona_overrides -> PersonaConfig.
    from_dict), mit einem Dict im alten Format wie es vor dieser Runde
    in server/data/admin_settings.json gelegen haben koennte."""
    config.apply_persona_overrides([_sample_persona_dict("altepersona")])
    restored = config.PERSONAS["altepersona"]
    assert restored.variants["neutral"].face_eyebrows == "neutral"
    assert restored.variants["neutral"].face_beard == ""


def test_persona_config_mutate_in_place_object_identity_preserved():
    """Sicherheitskritisch: apply_persona_overrides() darf PERSONAS
    NIEMALS neu binden (config.PERSONAS = {...}), nur mutieren -
    main.py haelt eine eigene from-config-import-Referenz auf
    dasselbe Objekt, die eine Neuzuweisung nicht mitbekommen wuerde."""
    dict_id_before = id(config.PERSONAS)
    config.apply_persona_overrides([_sample_persona_dict("identitaetstest")])
    assert id(config.PERSONAS) == dict_id_before
