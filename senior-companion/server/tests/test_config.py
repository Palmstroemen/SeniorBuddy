"""
Tests fuer die geschlechtsspezifischen Persona-Varianten (config.py).

Jede Persona hat einen festen Namen und drei Text-/Stimm-Varianten
(neutral/weiblich/maennlich); PERSONA_GENDER waehlt pro Installation,
welche aktiv ist. Default ist ueberall "neutral".
"""
import config


def test_all_personas_default_to_neutral():
    for persona_id, persona in config.PERSONAS.items():
        assert config.PERSONA_GENDER[persona_id] == "neutral"
        assert persona.display_name == persona.variants["neutral"].display_name
        assert persona.system_prompt == persona.variants["neutral"].system_prompt
        assert persona.voice_id == persona.variants["neutral"].voice_id


def test_all_personas_have_all_three_gender_variants():
    for persona in config.PERSONAS.values():
        assert set(persona.variants.keys()) == {"neutral", "weiblich", "maennlich"}


def test_persona_gender_is_configurable(monkeypatch):
    monkeypatch.setitem(config.PERSONA_GENDER, "freundin", "weiblich")
    persona = config.PERSONAS["freundin"]
    assert persona.display_name == persona.variants["weiblich"].display_name
    assert persona.display_name != persona.variants["neutral"].display_name


def test_each_variant_has_a_distinct_voice_id():
    for persona in config.PERSONAS.values():
        voice_ids = {v.voice_id for v in persona.variants.values()}
        assert len(voice_ids) == 3
