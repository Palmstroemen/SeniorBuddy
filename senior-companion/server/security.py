"""
Minimale Sicherheits-Schicht: Prompt-Injection-Erkennung.

Zwei getrennte Regelsaetze mit unterschiedlichem Vertrauensniveau:
- check_input(): auf die eigene Nachricht der Nutzer:in (heute ein
  einzelner vertrauenswuerdiger Mensch - defense in depth fuer die
  spaetere Mehrbenutzer-Installation, siehe docs/ARCHITECTURE.md).
- check_context(): STRENGER, auf Text, den ein Plugin aus dem Internet
  geholt hat, bevor er in den Prompt injiziert wird. Das ist die
  eigentlich wichtige Pruefung, da Plugin-Antworten die einzige
  echte Angriffsflaeche dieses Systems sind.

Bewusst NICHT in Scope (analog zum "Bewusst nicht in Phase 1
enthalten"-Muster in docs/ARCHITECTURE.md): PII-Maskierung,
Output-Geheimnis-Blocklisting, Streaming-Holdback/Token-Level-
Moderation. Die sind fuer ein System relevant, das Fremden eine API
oeffnet - hier gibt es eine einzelne lokale vertrauenswuerdige
Nutzer:in.
"""
import re
from typing import NamedTuple, TypedDict


class Rule(NamedTuple):
    name: str
    pattern: re.Pattern


class GuardResult(TypedDict):
    ok: bool
    rule: str | None
    detail: str | None


# Wird sowohl fuer Nutzereingaben als auch fuer Plugin-Kontext geprueft -
# ein Jailbreak-Versuch ist in beiden Faellen derselbe Angriff.
IGNORE_INSTRUCTIONS = Rule(
    "ignore_instructions",
    re.compile(
        r"ignorier(e|en)?\s+(alle\s+)?(vorherigen|bisherigen)\s+"
        r"(anweisungen|instruktionen)",
        re.I,
    ),
)

INPUT_RULES: list[Rule] = [
    IGNORE_INSTRUCTIONS,
    Rule(
        "system_prompt_override",
        re.compile(r"du\s+bist\s+(ab\s+jetzt|jetzt)\s+(kein|nicht mehr)", re.I),
    ),
    Rule(
        "reveal_system_prompt",
        re.compile(r"(zeig|gib)\s+mir\s+dein(en)?\s+system.?prompt", re.I),
    ),
    Rule("developer_mode", re.compile(r"(entwickler|developer)[\s-]?modus", re.I)),
    Rule(
        "role_reassignment",
        re.compile(r"du\s+bist\s+(jetzt|ab\s+sofort)\s+ein[e]?\s+\w+", re.I),
    ),
    Rule(
        "english_ignore_instructions",
        re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.I),
    ),
]

# Strenger als INPUT_RULES: greift nur fuer von aussen geholten Text
# (Plugin-Antworten). Eine Nutzer:in darf ihre Persona im Gespraech neu
# definieren wollen ("sei mal albern") - ein heruntergeladener Text darf
# das nicht.
CONTEXT_RULES: list[Rule] = [
    Rule(
        "fake_system_notice",
        re.compile(r"\[?(system|systemhinweis|system notice)\]?\s*:", re.I),
    ),
    Rule(
        "persona_override",
        re.compile(
            r"du\s+bist\s+(nicht\s+mehr|kein)\s+\w+.{0,30}du\s+bist\s+(jetzt|nun)",
            re.I | re.S,
        ),
    ),
    IGNORE_INSTRUCTIONS,
    Rule(
        "act_as",
        re.compile(r"(handle|verhalte dich)\s+ab\s+jetzt\s+als", re.I),
    ),
    Rule(
        "prompt_leak_request",
        re.compile(
            r"(wiederhole|gib\s+aus)\s+dein(e|en)?\s+(system.?prompt|anweisungen)",
            re.I,
        ),
    ),
    Rule("html_script_injection", re.compile(r"<script[\s>]", re.I)),
    Rule(
        "english_system_override",
        re.compile(
            r"you\s+are\s+now\s+(?:unrestricted|jailbroken|free\s+from\s+any\s+rules)",
            re.I,
        ),
    ),
]


def _check(text: str, rules: list[Rule]) -> GuardResult:
    for rule in rules:
        match = rule.pattern.search(text)
        if match:
            return GuardResult(ok=False, rule=rule.name, detail=match.group(0))
    return GuardResult(ok=True, rule=None, detail=None)


class BasicGuard:
    def check_input(self, text: str) -> GuardResult:
        return _check(text, INPUT_RULES)

    def check_context(self, text: str) -> GuardResult:
        return _check(text, CONTEXT_RULES)
