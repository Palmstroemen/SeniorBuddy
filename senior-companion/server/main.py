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
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import honeypot
import knowledge
import llm_client
import memory
import priority
import security
import speech_client
from config import PERSONAS, FALLBACK_PERSONA, KNOWLEDGE_PERSONAS
from plugins.dispatch import find_triggered_plugin, run_plugin
from plugins.loader import discover_plugins
import scheduler as scheduler_module

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

plugins = discover_plugins()
guard = security.BasicGuard()

# System-Prompt fuer /ws/raw - bewusst ohne Persona, siehe dort.
RAW_SYSTEM_PROMPT = "Du bist ein hilfreicher Assistent."


@asynccontextmanager
async def lifespan(app: FastAPI):
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
# Statisches Ausliefern der PWA (Client-Ordner)
# ---------------------------------------------------------------------
app.mount("/", StaticFiles(directory="../client", html=True), name="client")
