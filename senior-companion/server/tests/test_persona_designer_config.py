"""
Tests fuer die Persona-Designer-Grundlage in config.py: verlustfreies
to_dict()/from_dict(), sowie apply_persona_overrides()/remove_persona_
override() - das In-Place-Mutations-Fundament, auf dem main.py's
/admin/personas-Routen aufbauen. Flaches Schema (siehe
PersonaConfig-Docstring) - eine Persona hat EIN Geschlecht, EINEN Namen,
EINE Stimme, kein Varianten-Dict mehr.

config.PERSONAS ist ein echtes, sitzungsweites Python-Singleton (kein
Test-Fixture setzt es automatisch zurueck) - jeder Test hier raeumt
seine eigenen Aenderungen ueber die autouse-Fixture unten wieder auf.
"""
import pytest

import config


@pytest.fixture(autouse=True)
def _reset_persona_state():
    ids_before = set(config.PERSONAS.keys())
    yield
    for extra_id in set(config.PERSONAS.keys()) - ids_before:
        del config.PERSONAS[extra_id]
    for persona_id in ids_before:
        config.PERSONAS[persona_id] = config._BUILTIN_PERSONAS.get(
            persona_id, config.PERSONAS[persona_id]
        )


def _sample_persona_dict(persona_id="testpersona"):
    return {
        "id": persona_id,
        "model": "qwen2.5:7b-instruct",
        "first_name": "Test",
        "last_name": "",
        "title": "",
        "gender": "weiblich",
        "default_anrede": "sie",
        "voice_id": "v1",
        "system_prompt": "Ein Prompt.",
        "always_loaded": True,
        "max_tokens": 400,
        "reengagement_tendency": 0.5,
        "color": "#7A5C99",
        "background_color": "#EEE9F2",
    }


def test_persona_config_to_dict_from_dict_roundtrip():
    original = config.PERSONAS["freundin"]
    restored = config.PersonaConfig.from_dict(original.to_dict())
    assert restored == original


def test_persona_config_display_name_survives_roundtrip_via_source_fields():
    """display_name selbst ist keine Konstruktor-Feld (berechnete
    Property) - der Roundtrip funktioniert trotzdem, weil title/
    first_name/last_name (die eigentlichen Quellfelder) mitkommen."""
    original = config.PERSONAS["professor"]
    restored = config.PersonaConfig.from_dict(original.to_dict())
    assert restored.display_name == original.display_name == "Professor Wallner"


def test_persona_config_from_dict_ignores_stray_display_name_key():
    """Defensiv: from_dict() darf nicht mit TypeError abstuerzen, wenn
    (z.B. durch einen ausgehenden Payload, der es versehentlich
    unveraendert zurueckschickt) ein display_name-Schluessel im Dict
    landet - er ist kein Konstruktor-Feld."""
    d = _sample_persona_dict("displaynametest")
    d["display_name"] = "Sollte ignoriert werden"
    restored = config.PersonaConfig.from_dict(d)
    assert restored.display_name == "Test"  # aus first_name, nicht aus dem gestreuten Schluessel


def test_apply_persona_overrides_adds_new_persona():
    config.apply_persona_overrides([_sample_persona_dict("neuepersona")])
    assert "neuepersona" in config.PERSONAS
    assert config.PERSONAS["neuepersona"].model == "qwen2.5:7b-instruct"
    assert config.PERSONAS["neuepersona"].gender == "weiblich"


def test_apply_persona_overrides_replaces_existing_builtin():
    d = _sample_persona_dict("freundin")
    d["system_prompt"] = "Neuer Prompt."
    config.apply_persona_overrides([d])
    assert config.PERSONAS["freundin"].system_prompt == "Neuer Prompt."


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


def test_persona_config_from_dict_without_face_fields_uses_defaults():
    """Rueckwaertskompatibilitaet: ein Dict ohne face_*-Schluessel darf
    nicht mit einem fehlenden Pflichtargument abstuerzen, sondern muss
    auf die Dataclass-Defaults zurueckfallen (gleiches Prinzip wie das
    schon laenger bestehende voice_id: str = "")."""
    d = _sample_persona_dict("altepersona")
    restored = config.PersonaConfig.from_dict(d)
    assert restored.face_eyebrows == "neutral"
    assert restored.face_eyes == "happy"
    assert restored.face_mouth == "smile"
    assert restored.face_hairstyle == "kurz"
    assert restored.face_beard == ""


def test_persona_config_from_dict_without_reaction_phrases_uses_empty_default():
    """Rueckwaertskompatibilitaet analog zu den face_*-Feldern: alte
    Dicts ohne reaction_phrases-Schluessel duerfen nicht abstuerzen."""
    old_shaped = _sample_persona_dict("altepersona2")
    old_shaped.pop("reaction_phrases", None)
    config.apply_persona_overrides([old_shaped])
    assert config.PERSONAS["altepersona2"].reaction_phrases == {}


def test_persona_config_reaction_phrases_roundtrip():
    d = _sample_persona_dict("reaktionstest")
    d["reaction_phrases"] = {"interrupted": ["Äh?", "Moment mal!"]}
    config.apply_persona_overrides([d])
    assert config.PERSONAS["reaktionstest"].reaction_phrases == {
        "interrupted": ["Äh?", "Moment mal!"],
    }


def test_persona_config_narrative_fields_default_to_empty():
    d = _sample_persona_dict("narrativtest")
    config.apply_persona_overrides([d])
    persona = config.PERSONAS["narrativtest"]
    assert persona.long_term_agenda == ""
    assert persona.daily_agenda == ""
    assert persona.own_backstory == ""
    assert persona.own_interests == ""
    assert persona.family_relations == ""


def test_persona_config_mutate_in_place_object_identity_preserved():
    """Sicherheitskritisch: apply_persona_overrides() darf PERSONAS
    NIEMALS neu binden (config.PERSONAS = {...}), nur mutieren -
    main.py haelt eine eigene from-config-import-Referenz auf
    dasselbe Objekt, die eine Neuzuweisung nicht mitbekommen wuerde."""
    dict_id_before = id(config.PERSONAS)
    config.apply_persona_overrides([_sample_persona_dict("identitaetstest")])
    assert id(config.PERSONAS) == dict_id_before
