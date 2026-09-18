"""
Tests fuer server/analysis.py - aktuell nur die Erkennung eines
ausdruecklichen Du-Angebots (Anrede-Wechsel, siehe main.py). Gleiches
Muster wie test_security.py: jede Regel bekommt ein Treffer- und ein
Gegenbeispiel.
"""
import pytest

import analysis


DU_OFFER_CASES = [
    "Sag doch einfach du zu mir.",
    "Wir koennen uns ruhig duzen.",
    "Nenn mich einfach beim Vornamen.",
    "Wir sind doch schon laengst per du.",
    "Ach, duzen wir uns doch.",
]


@pytest.mark.parametrize("text", DU_OFFER_CASES)
def test_detect_du_offer_recognizes_explicit_offers(text):
    assert analysis.detect_du_offer(text) is True


DU_OFFER_ALLOW_CASES = [
    "Wie geht es Ihnen heute?",
    "Mein Enkel heisst Paul, sagen Sie ihm doch mal hallo.",
    "Koennen Sie mir sagen, wie spaet es ist?",
    "Ich habe gestern meine Nachbarin besucht.",
    "Duzt du eigentlich deine Kollegen?",
]


@pytest.mark.parametrize("text", DU_OFFER_ALLOW_CASES)
def test_detect_du_offer_ignores_unrelated_text(text):
    assert analysis.detect_du_offer(text) is False
