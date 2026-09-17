"""
Senior-Korrespondenz-System - Backend-Einstiegspunkt.

Start (Entwicklung):
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Voraussetzung: Ollama laeuft lokal und die in config.py referenzierten
Modelle sind gepullt (siehe README.md).
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import knowledge
import llm_client
import memory
import security
from config import PERSONAS, FALLBACK_PERSONA, KNOWLEDGE_PERSONAS
from plugins.dispatch import find_triggered_plugin, run_plugin
from plugins.loader import discover_plugins
import scheduler as scheduler_module

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

plugins = discover_plugins()
guard = security.BasicGuard()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_module.setup_scheduler()
    log.info("Geladene Plugins: %s", list(plugins.keys()))
    yield
    scheduler_module.shutdown_scheduler()


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
            async for token in llm_client.stream(
                persona.model, persona.system_prompt, chat_messages,
                max_tokens=persona.max_tokens,
            ):
                full_response += token
                await websocket.send_json({"type": "token", "content": token})

            memory.add_message(user_id, persona.id, "assistant", full_response)
            await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        log.info("Verbindung getrennt: %s / %s", user_id, persona_id)


# ---------------------------------------------------------------------
# Statisches Ausliefern der PWA (Client-Ordner)
# ---------------------------------------------------------------------
app.mount("/", StaticFiles(directory="../client", html=True), name="client")
