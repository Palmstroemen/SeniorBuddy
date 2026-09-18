"""
Erkennung von Anrede-relevanten Signalen in Nutzer-Nachrichten.

Aktuell nur ein Zweck: ein ausdrueckliches Angebot, vom Sie zum Du zu
wechseln (siehe main.py, Anrede-Handling). Gleiches Rule/regex-Muster
wie security.py - deterministisch, offline testbar, kein LLM-Aufruf im
Live-Pfad noetig.
"""
import re
from typing import NamedTuple


class Rule(NamedTuple):
    name: str
    pattern: re.Pattern


DU_OFFER_RULES: list[Rule] = [
    Rule("du_zu_mir", re.compile(r"du\s+zu\s+mir", re.I)),
    Rule("duzen", re.compile(r"duzen", re.I)),
    Rule("beim_vornamen", re.compile(r"beim\s+vornamen", re.I)),
    Rule("per_du", re.compile(r"per\s+du\b", re.I)),
]


def detect_du_offer(text: str) -> bool:
    return any(rule.pattern.search(text) for rule in DU_OFFER_RULES)
