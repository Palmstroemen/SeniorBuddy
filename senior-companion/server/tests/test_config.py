"""
Tests fuer die Persona-Konfiguration (config.py). Eine Persona hat EIN
festes Geschlecht, EINEN Namen, EINE Stimme - kein zur Laufzeit
umschaltbares Varianten-System (siehe PersonaConfig-Docstring).
"""
import config


def test_display_name_combines_title_first_and_last_name():
    persona = config.PersonaConfig(
        id="test", model="m", first_name="Wallner", last_name="",
        title="Professor",
    )
    # first_name/title beide gesetzt - reines Beispiel fuer den
    # Space-Join, nicht die tatsaechliche Wallner-Persona.
    assert persona.display_name == "Professor Wallner"


def test_display_name_with_only_first_name_has_no_stray_whitespace():
    persona = config.PersonaConfig(id="test", model="m", first_name="Robin")
    assert persona.display_name == "Robin"


def test_display_name_with_title_and_last_name_no_first_name():
    persona = config.PersonaConfig(
        id="test", model="m", first_name="", last_name="Wallner", title="Professor",
    )
    assert persona.display_name == "Professor Wallner"


def test_all_personas_have_reengagement_tendency_between_0_and_1():
    for persona in config.PERSONAS.values():
        assert 0.0 <= persona.reengagement_tendency <= 1.0


def test_all_personas_default_anrede_to_sie():
    for persona in config.PERSONAS.values():
        assert persona.default_anrede == "sie"


def test_all_personas_have_a_display_name():
    for persona in config.PERSONAS.values():
        assert persona.display_name
