"""
Senior-Korrespondenz-System - Backend-Einstiegspunkt.

Start (Entwicklung):
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Voraussetzung: Ollama laeuft lokal und die in config.py referenzierten
Modelle sind gepullt (siehe README.md).
"""
import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import admin_auth
import admin_settings
import analysis
import config
import honeypot
import knowledge
import llm_client
import memory
import priority
import satisfaction
import security
import speech_client
from config import PERSONAS, PERSONA_GENDER, FALLBACK_PERSONA, KNOWLEDGE_PERSONAS
from plugins.dispatch import find_triggered_plugin, run_plugin, plugin_failure_count
from plugins.loader import discover_plugins
import scheduler as scheduler_module

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

plugins = discover_plugins()
guard = security.BasicGuard()
_started_at = time.time()

# System-Prompt fuer /ws/raw - bewusst ohne Persona, siehe dort.
RAW_SYSTEM_PROMPT = "Du bist ein hilfreicher Assistent."


def _apply_persisted_admin_settings():
    """Von lifespan() beim Start aufgerufen: persistierte Admin-
    Aenderungen (server/data/admin_settings.json) ueberschreiben die
    Standardwerte aus config.py, falls vorhanden - sonst waere eine per
    /admin/config/* geaenderte Einstellung nach einem Neustart wieder
    weg."""
    settings = admin_settings.load()
    if "persona_gender" in settings:
        PERSONA_GENDER.update(settings["persona_gender"])
    if "ntfy_topic" in settings:
        honeypot.NTFY_TOPIC = settings["ntfy_topic"]
    if "satisfaction_interval_days" in settings:
        satisfaction.CHECKIN_INTERVAL_DAYS = settings["satisfaction_interval_days"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    _apply_persisted_admin_settings()
    scheduler_module.setup_scheduler()
    honeypot_stop = threading.Event()
    honeypot.start_watching(honeypot_stop)
    log.info("Geladene Plugins: %s", list(plugins.keys()))
    yield
    scheduler_module.shutdown_scheduler()
    honeypot_stop.set()


app = FastAPI(title="Senior-Korrespondenz-System", lifespan=lifespan)

# Im lokalen Netz gedacht - CORS grosszuegig, da kein oeffentliches Internet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------
# Personas & Transparenz
# ---------------------------------------------------------------------

@app.get("/api/personas")
def list_personas():
    return [
        {"id": p.id, "display_name": p.display_name, "voice_id": p.voice_id}
        for p in PERSONAS.values()
    ]


@app.get("/api/transparency/{user_id}")
def transparency_log(user_id: str):
    """Liefert die Liste aller Internet-/Plugin-Zugriffe fuer die
    'Was wurde heute nach draussen geschickt?'-Anzeige im Client."""
    return memory.get_transparency_log(user_id)


@app.get("/api/plugins")
def list_plugins():
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "needs_internet": p.needs_internet,
            "internet_domains": p.internet_domains,
            "enabled": p.enabled,
        }
        for p in plugins.values()
    ]


class PluginToggle(BaseModel):
    enabled: bool


@app.post("/api/plugins/{plugin_id}/toggle")
def toggle_plugin(plugin_id: str, body: PluginToggle):
    if plugin_id not in plugins:
        raise HTTPException(404, "Plugin nicht gefunden")
    plugins[plugin_id].enabled = body.enabled
    return {"id": plugin_id, "enabled": body.enabled}


# ---------------------------------------------------------------------
# Story-Freigabe (Reporter-Ergebnisse)
# ---------------------------------------------------------------------

class ConsentUpdate(BaseModel):
    status: str  # 'kids' | 'adults' | 'private' | 'deleted'
    note: str = ""


@app.post("/api/stories/{user_id}/{story_id}/consent")
def update_story_consent(user_id: str, story_id: str, body: ConsentUpdate):
    memory.set_story_consent(user_id, story_id, body.status, body.note)
    return {"story_id": story_id, "status": body.status}


# ---------------------------------------------------------------------
# Personen-Fakten (RAG-Quelle 1: kurze Schluessel/Wert-Aussagen)
# ---------------------------------------------------------------------

class FactUpdate(BaseModel):
    key: str
    value: str
    source_persona: str | None = None


@app.post("/api/facts/{user_id}")
def add_fact(user_id: str, body: FactUpdate):
    # Einmalige Pruefung beim Schreiben - Fakten sind danach statisch,
    # eine erneute Pruefung bei jeder Injektion waere redundant (anders
    # als Plugin-/Wissensbasis-Kontext, der bei jedem Turn frisch
    # entsteht).
    check = guard.check_input(body.value)
    if not check["ok"]:
        raise HTTPException(400, f"Wert wurde vom Guard blockiert (Regel={check['rule']})")
    memory.add_fact(user_id, body.key, body.value, body.source_persona)
    return {"user_id": user_id, "key": body.key, "value": body.value}


# ---------------------------------------------------------------------
# Sprachdienst (optional, siehe speech-service/): Spracherkennung und
# -ausgabe koennen wahlweise auf dem Server laufen statt im Browser -
# fuer schwaechere Tablets. Kein zusaetzlicher Guard-Aufruf noetig:
# transkribierter Text durchlaeuft guard.check_input() ohnehin, sobald
# er als Chat-Nachricht gesendet wird; zu synthetisierender Text ist
# immer die bereits generierte Antwort einer Persona.
# ---------------------------------------------------------------------

@app.post("/api/stt")
async def speech_to_text(audio: UploadFile):
    text = await speech_client.transcribe(await audio.read(), audio.filename or "aufnahme.webm")
    return {"text": text}


class TTSRequest(BaseModel):
    text: str
    persona_id: str


@app.post("/api/tts")
async def text_to_speech(body: TTSRequest):
    persona = PERSONAS.get(body.persona_id) or PERSONAS[FALLBACK_PERSONA]
    audio_bytes = await speech_client.synthesize(body.text, persona.voice_id)
    return Response(content=audio_bytes, media_type="audio/wav")


# ---------------------------------------------------------------------
# Chat (WebSocket, damit Antworten Wort-fuer-Wort gestreamt werden koennen)
# ---------------------------------------------------------------------

@app.websocket("/ws/chat/{user_id}/{persona_id}")
async def chat(websocket: WebSocket, user_id: str, persona_id: str):
    await websocket.accept()

    persona = PERSONAS.get(persona_id) or PERSONAS[FALLBACK_PERSONA]

    try:
        while True:
            user_text = await websocket.receive_text()

            input_check = guard.check_input(user_text)
            if not input_check["ok"]:
                log.warning(
                    "Eingabe blockiert (Regel=%s) fuer %s/%s",
                    input_check["rule"], user_id, persona_id,
                )
                await websocket.send_json({
                    "type": "blocked", "reason": input_check["rule"],
                })
                continue

            memory.add_message(user_id, persona.id, "user", user_text)

            if persona.id == "technikerin":
                # Reiner Seiteneffekt: ordnet diese Nachricht einer
                # offenen Zufriedenheits-Nachfrage zu, falls es eine
                # gibt (No-Op sonst) - aendert den Gespraechsfluss nicht.
                memory.record_feedback_reply(user_id, persona.id, user_text)

            history = memory.recent_messages(user_id, persona.id, limit=20)
            chat_messages = [
                {"role": m["role"], "content": m["content"]} for m in history
            ]

            triggered = find_triggered_plugin(plugins, persona.id, user_text)
            if triggered:
                plugin_output = await run_plugin(triggered, user_text, user_id)
                if plugin_output:
                    context_check = guard.check_context(plugin_output)
                    if context_check["ok"]:
                        chat_messages.append({
                            "role": "system",
                            "content": f"[Rechercheergebnis von {triggered.name}]: {plugin_output}",
                        })
                    else:
                        log.warning(
                            "Plugin-Kontext von '%s' blockiert (Regel=%s)",
                            triggered.id, context_check["rule"],
                        )

            # Kontext-Reihenfolge ist bewusst Plugin -> Fakten ->
            # Wissensbasis, nicht zufaellig gewachsen.
            facts = memory.list_facts(user_id, limit=20)
            if facts:
                fact_lines = "; ".join(f"{f['key']}: {f['value']}" for f in facts)
                chat_messages.append({
                    "role": "system",
                    "content": f"Bekannte Fakten ueber {user_id}: {fact_lines}",
                })

            if persona.id in KNOWLEDGE_PERSONAS:
                for match in knowledge.search(user_id, user_text):
                    context_check = guard.check_context(match.text)
                    if context_check["ok"]:
                        chat_messages.append({
                            "role": "system",
                            "content": f"[Wissensbasis: {match.title}]: {match.text}",
                        })
                    else:
                        log.warning(
                            "Wissens-Kontext '%s' blockiert (Regel=%s)",
                            match.title, context_check["rule"],
                        )

            # Anrede (Du/Sie): ein ausdrueckliches Angebot schaltet
            # sofort um (siehe analysis.py), sonst bleibt "sie" der
            # Default. Wirkt fuer jede Persona, nicht nur Technikerin -
            # siehe config.py fuer den statischen Sie-Standard im
            # system_prompt, der das hier ueberschreibt.
            anrede_key = f"anrede:{persona.id}"
            if (
                analysis.detect_du_offer(user_text)
                and memory.get_fact(user_id, anrede_key) != "du"
            ):
                memory.add_fact(user_id, anrede_key, "du", source_persona=persona.id)
            anrede = memory.get_fact(user_id, anrede_key) or "sie"
            chat_messages.append({
                "role": "system",
                "content": (
                    f"Anrede-Form fuer diese Person bei dieser Persona: {anrede}. "
                    f"Sprich konsequent in der "
                    f"{'Du' if anrede == 'du' else 'Sie'}-Form."
                ),
            })

            if persona.id == "technikerin" and satisfaction.is_due(user_id, persona.id):
                chat_messages.append({
                    "role": "system", "content": satisfaction.CHECKIN_PROMPT,
                })
                memory.record_feedback_asked(user_id, persona.id)

            full_response = ""
            priority.senior_stream_started()
            try:
                async for token in llm_client.stream(
                    persona.model, persona.system_prompt, chat_messages,
                    max_tokens=persona.max_tokens,
                ):
                    full_response += token
                    await websocket.send_json({"type": "token", "content": token})
            finally:
                priority.senior_stream_finished()

            memory.add_message(user_id, persona.id, "assistant", full_response)
            await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        log.info("Verbindung getrennt: %s / %s", user_id, persona_id)


# ---------------------------------------------------------------------
# Ausserordentlicher Nutzer: roher Chat mit niedriger Prioritaet
#
# Kein Persona-System-Prompt, kein memory.py (keine Senior-Identitaet,
# fuer die gespeichert werden koennte), Verlauf nur fuer die Dauer der
# WebSocket-Verbindung im Speicher. Nutzt das groesste konfigurierte
# Modell (aktuell der Professor). Laeuft eine Generierung hier, wenn
# eine Senior-Anfrage beginnt, wird sie aktiv abgebrochen (priority.py)
# - Ollama kennt sonst keine Prioritaeten und wuerde die Senior-Antwort
# intern hinter dieser Anfrage einreihen.
# ---------------------------------------------------------------------

@app.websocket("/ws/raw")
async def raw_chat(websocket: WebSocket):
    await websocket.accept()
    model = PERSONAS["professor"].model  # groesstes konfiguriertes Modell
    chat_history: list[dict] = []

    try:
        while True:
            user_text = await websocket.receive_text()

            input_check = guard.check_input(user_text)
            if not input_check["ok"]:
                await websocket.send_json({
                    "type": "blocked", "reason": input_check["rule"],
                })
                continue

            chat_history.append({"role": "user", "content": user_text})

            if priority.senior_stream_active():
                await websocket.send_json({"type": "waiting"})
                await priority.wait_until_idle()

            tokens: list[str] = []

            async def collect():
                async for token in llm_client.stream(
                    model, RAW_SYSTEM_PROMPT, chat_history, max_tokens=1024,
                ):
                    tokens.append(token)
                    await websocket.send_json({"type": "token", "content": token})

            gen_task = asyncio.create_task(collect())
            priority.register_low_priority_task(gen_task)
            try:
                await gen_task
            except asyncio.CancelledError:
                # Nutzerfrage bleibt im Verlauf, die unfertige
                # Teilantwort wird verworfen - ein abgeschnittener
                # Kontext wuerde das Modell beim naechsten Turn nur
                # verwirren.
                await websocket.send_json({"type": "preempted"})
                continue
            finally:
                priority.unregister_low_priority_task(gen_task)

            full_response = "".join(tokens)
            chat_history.append({"role": "assistant", "content": full_response})
            await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        log.info("Roher Chat getrennt")


# ---------------------------------------------------------------------
# Honeypot: Koeder-Routen, die kein echter Client je aufruft. Antwort
# ist ein unauffaelliger 404 - verraet nicht, dass es eine Falle war.
# Zweite Falle (Honeyfile) laeuft ueber honeypot.start_watching() in
# lifespan().
# ---------------------------------------------------------------------

@app.get("/api/admin/backup")
@app.get("/api/admin/export")
@app.get("/api/facts/all")
@app.get("/.env")
async def _honeypot_route(request: Request):
    honeypot.alert(
        f"Koeder-Route aufgerufen: {request.url.path} "
        f"von {request.client.host if request.client else '?'}"
    )
    raise HTTPException(404, "Not Found")


# ---------------------------------------------------------------------
# Fernwartungs-API (/admin/*) - einzige Stelle mit echter
# Authentifizierung (admin_auth.require_admin), bewusst getrennt vom
# offenen /api/*-Namensraum. "Updates einspielen" fuehrt NICHT dieser
# Prozess selbst aus (Verlaesslichkeits-/Sicherheitsrisiko, ein
# Prozess, der sich selbst neu startet) - der Endpoint schreibt nur
# eine Marker-Datei, ein separates, privilegiertes systemd-.path-Unit
# fuehrt den eigentlichen Update-Lauf aus (deploy/run_update.sh).
# Plugin an/aus gehoert funktional auch zum Fernwartungsumfang, braucht
# aber keinen neuen Endpoint - /api/plugins/{id}/toggle ist schon da.
# ---------------------------------------------------------------------

class PersonaGenderUpdate(BaseModel):
    gender: str


@app.get("/admin/config/persona-gender", dependencies=[Depends(admin_auth.require_admin)])
def get_persona_gender():
    return PERSONA_GENDER


@app.post(
    "/admin/config/persona-gender/{persona_id}",
    dependencies=[Depends(admin_auth.require_admin)],
)
def set_persona_gender(persona_id: str, body: PersonaGenderUpdate):
    if persona_id not in PERSONAS:
        raise HTTPException(404, "Persona nicht gefunden")
    if body.gender not in {"neutral", "weiblich", "maennlich"}:
        raise HTTPException(400, "Ungueltiges Geschlecht")
    PERSONA_GENDER[persona_id] = body.gender  # sofort wirksam - PERSONAS liest denselben dict
    admin_settings.update("persona_gender", PERSONA_GENDER)
    return {"persona_id": persona_id, "gender": body.gender}


class NtfyTopicUpdate(BaseModel):
    topic: str


@app.post("/admin/config/ntfy-topic", dependencies=[Depends(admin_auth.require_admin)])
def set_ntfy_topic(body: NtfyTopicUpdate):
    honeypot.NTFY_TOPIC = body.topic
    admin_settings.update("ntfy_topic", body.topic)
    return {"topic": body.topic}


@app.get("/admin/stats", dependencies=[Depends(admin_auth.require_admin)])
def admin_stats():
    per_user = {}
    usage = {}
    sentiment = {}
    external_requests = {}
    story_consent = {}
    persona_usage = {}
    for db_path in sorted(config.DATA_DIR.glob("*.sqlite3")):
        user_id = db_path.stem
        with memory.get_db(user_id) as db:
            count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        per_user[user_id] = count
        usage[user_id] = memory.usage_stats(user_id)
        sentiment[user_id] = memory.sentiment_stats(user_id)
        external_requests[user_id] = memory.external_request_stats(user_id)
        story_consent[user_id] = memory.story_consent_stats(user_id)
        persona_usage[user_id] = memory.persona_usage_stats(user_id)

    return {
        "uptime_seconds": time.time() - _started_at,
        "users": per_user,
        "total_messages": sum(per_user.values()),
        "plugins_enabled": {pid: p.enabled for pid, p in plugins.items()},
        "priority": {
            "senior_stream_active": priority.senior_stream_active(),
            "preemptions": priority.preemption_count(),
        },
        "honeypot": {
            "trigger_count": honeypot.trigger_count(),
            "last_triggered_at": honeypot.last_triggered_at(),
        },
        # "Wie oft, wieviele Minuten am Tag" pro Nutzer:in.
        "usage": usage,
        # "Freundeskreis": wie oft/wie lange wird welche Persona
        # tatsaechlich genutzt - siehe memory.persona_usage_stats().
        "persona_usage": persona_usage,
        # Best-Effort-Signal aus dem naechtlichen LLM-Klassifikations-
        # Job (sentiment_job.py), nicht live - unclassified_pending
        # zeigt, wie viel der naechste Lauf noch vor sich hat.
        "sentiment": sentiment,
        "external_requests": external_requests,
        # Bewusst OHNE approved_by_user-Auswertung: die Spalte wird
        # nirgends gesetzt (kein Consent-Dialog existiert), ein
        # immer-0-Feld wuerde falsche Schluesse nahelegen.
        "story_consent": story_consent,
        "problems": {
            "guard_input_blocks": security.input_block_count(),
            "guard_context_blocks": security.context_block_count(),
            "plugin_failures": plugin_failure_count(),
        },
    }


class SatisfactionIntervalUpdate(BaseModel):
    days: int


@app.post(
    "/admin/config/satisfaction-interval",
    dependencies=[Depends(admin_auth.require_admin)],
)
def set_satisfaction_interval(body: SatisfactionIntervalUpdate):
    if body.days < 1:
        raise HTTPException(400, "Muss mindestens 1 Tag sein")
    satisfaction.CHECKIN_INTERVAL_DAYS = body.days
    admin_settings.update("satisfaction_interval_days", body.days)
    return {"days": body.days}


@app.get("/admin/feedback", dependencies=[Depends(admin_auth.require_admin)])
def admin_feedback(limit: int = 50):
    rows = []
    for db_path in config.DATA_DIR.glob("*.sqlite3"):
        user_id = db_path.stem
        for row in memory.list_feedback(user_id, limit=limit):
            rows.append({"user_id": user_id, **row})
    rows.sort(key=lambda r: r["asked_ts"], reverse=True)
    return rows[:limit]


@app.post("/admin/update", dependencies=[Depends(admin_auth.require_admin)])
def request_update():
    admin_settings.UPDATE_MARKER_FILE.write_text(str(time.time()), encoding="utf-8")
    return {"status": "angefordert"}


@app.get("/admin/update/status", dependencies=[Depends(admin_auth.require_admin)])
def update_status():
    if not admin_settings.UPDATE_LOG_FILE.exists():
        return {"log": None}
    return {"log": admin_settings.UPDATE_LOG_FILE.read_text(encoding="utf-8")[-2000:]}


# ---------------------------------------------------------------------
# Statisches Ausliefern der PWA (Client-Ordner)
# ---------------------------------------------------------------------
app.mount("/", StaticFiles(directory="../client", html=True), name="client")
