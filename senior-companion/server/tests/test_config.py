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


def test_weiblich_and_maennlich_have_distinct_real_piper_voices():
    for persona in config.PERSONAS.values():
        weiblich = persona.variants["weiblich"].voice_id
        maennlich = persona.variants["maennlich"].voice_id
        assert weiblich and maennlich
        assert weiblich != maennlich


def test_neutral_reuses_the_maennlich_voice():
    """Piper hat keine geschlechtsneutrale Stimme - 'neutral' bekommt
    bewusst dieselbe Stimme wie 'maennlich' (nur der Text bleibt
    neutral formuliert), statt eine vierte, nicht existierende Stimme
    vorzutaeuschen."""
    for persona in config.PERSONAS.values():
        assert persona.variants["neutral"].voice_id == persona.variants["maennlich"].voice_id


def test_all_personas_have_reengagement_tendency_between_0_and_1():
    for persona in config.PERSONAS.values():
        assert 0.0 <= persona.reengagement_tendency <= 1.0


def test_every_variant_defaults_to_sie_form():
    """Ohne diesen Hinweis wuerde das Sprachmodell die Anrede-Form
    selbst improvisieren - siehe main.py, das den tatsaechlichen
    Anrede-Stand (facts-Tabelle) zusaetzlich pro Turn injiziert. Dieser
    Satz ist nur das statische Sicherheitsnetz/der Standard."""
    for persona in config.PERSONAS.values():
        for variant in persona.variants.values():
            assert "Sie-Form" in variant.system_prompt
