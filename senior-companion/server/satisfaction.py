"""
Technikerin fragt hin und wieder von sich aus nach Zufriedenheit und
Verbesserungswuenschen (siehe main.py, Chat-Handler). Dieses Modul
entscheidet nur WANN das faellig ist - das eigentliche Erfassen der
Antwort laeuft ueber memory.record_feedback_reply().
"""
import time

import memory

# Admin-konfigurierbar ueber /admin/config/satisfaction-interval
# (main.py) - Persistenz ueber admin_settings.py.
CHECKIN_INTERVAL_DAYS = 7

CHECKIN_PROMPT = (
    "Baue in deine Antwort in diesem Gespraechsschritt natuerlich eine "
    "Frage ein, wie die Person die Gespraeche mit dir findet oder ob "
    "sie sich etwas wuenschen wuerde - in deinen eigenen Worten, "
    "sinngemaess z.B.: 'Was mich mal interessieren wuerde: wie "
    "gefallen Ihnen eigentlich unsere Gespraeche? Gibt es etwas, das "
    "Sie sich wuenschen wuerden?' Nicht aufdringlich, nur einmal in "
    "dieser Antwort."
)


def is_due(user_id: str, persona: str) -> bool:
    last = memory.last_feedback_asked_ts(user_id, persona)
    if last is None:
        return True
    return (time.time() - last) >= CHECKIN_INTERVAL_DAYS * 86400
