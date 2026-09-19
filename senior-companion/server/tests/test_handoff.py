"""
Tests fuer server/handoff.py: Erkennung einer Zusammenfassungs-
Uebergabe-Bitte ("Robin, erzaehl das mal Wallner"). Reine Funktion,
kein Zeit-/DB-/LLM-Bezug (analog zu test_room.py).
"""
import handoff

CANDIDATES = {"freundin": "Robin", "professor": "Wallner", "technikerin": "Toni"}


def test_detect_handoff_target_matches_erzaehl_plus_name():
    target = handoff.detect_handoff_target(
        "Robin, erzaehl das mal Wallner", CANDIDATES, exclude_persona_id="freundin",
    )
    assert target == "professor"


def test_detect_handoff_target_matches_berichte_variant():
    target = handoff.detect_handoff_target(
        "Kannst du Toni davon berichten?", CANDIDATES, exclude_persona_id="freundin",
    )
    assert target == "technikerin"


def test_detect_handoff_target_excludes_source_persona():
    # Nur der Name der Quell-Persona selbst kommt vor - ohne den
    # exclude-Parameter wuerde das faelschlich als Ziel durchgehen.
    target = handoff.detect_handoff_target(
        "Robin, erzaehl doch Robin davon", CANDIDATES, exclude_persona_id="freundin",
    )
    assert target is None


def test_detect_handoff_target_returns_none_when_no_match():
    target = handoff.detect_handoff_target(
        "Wie war dein Tag?", CANDIDATES, exclude_persona_id="freundin",
    )
    assert target is None


def test_detect_handoff_target_ignores_name_only_mention_without_verb():
    target = handoff.detect_handoff_target(
        "Ich glaube, Wallner hat recht", CANDIDATES, exclude_persona_id="freundin",
    )
    assert target is None


def test_detect_handoff_target_only_matches_given_candidates():
    target = handoff.detect_handoff_target(
        "Erzaehl das mal Heinrich", CANDIDATES, exclude_persona_id="freundin",
    )
    assert target is None


def test_detect_handoff_target_matches_mid_sentence_not_only_sentence_initial():
    # Bewusster Unterschied zu room.py's detect_addressed_persona (dort
    # nur Satzanfang) - eine Uebergabe-Bitte muss nicht vorne stehen.
    target = handoff.detect_handoff_target(
        "Kannst du das mal Wallner erzaehlen", CANDIDATES, exclude_persona_id="freundin",
    )
    assert target == "professor"


def test_sag_verb_deliberately_not_recognized_as_handoff_trigger():
    """'sag' ist bewusst NICHT als Uebergabe-Verb aufgenommen - zu
    breit, wuerde z.B. beilaeufige Erwaehnungen faelschlich triggern."""
    target = handoff.detect_handoff_target(
        "Sag mal, was hat Wallner dazu gesagt?", CANDIDATES, exclude_persona_id="freundin",
    )
    assert target is None


def test_ack_handoff_prompt_is_a_nonempty_string():
    assert isinstance(handoff.ACK_HANDOFF_PROMPT, str)
    assert len(handoff.ACK_HANDOFF_PROMPT) > 0


def test_cannot_share_prompt_is_a_nonempty_string():
    assert isinstance(handoff.CANNOT_SHARE_PROMPT, str)
    assert len(handoff.CANNOT_SHARE_PROMPT) > 0


def test_summary_system_prompt_is_a_nonempty_string():
    assert isinstance(handoff.SUMMARY_SYSTEM_PROMPT, str)
    assert len(handoff.SUMMARY_SYSTEM_PROMPT) > 0
