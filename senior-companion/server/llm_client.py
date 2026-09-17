"""
Duenner Client fuer die lokale Modell-Runtime.

Zielt auf Ollamas HTTP-API, weil sie der einfachste Einstieg ist
(ein `ollama pull <modell>` genuegt). Wer stattdessen den
llama.cpp-Server direkt nutzen will, muss nur diese Datei anpassen -
der Rest des Systems kennt nur `generate()` / `stream()`.
"""
import httpx
import json
from typing import AsyncIterator

from config import OLLAMA_URL


async def stream(model: str, system_prompt: str, messages: list[dict],
                  max_tokens: int = 400) -> AsyncIterator[str]:
    """Streamt Antwort-Token fuer Token. `messages` sind bereits im
    Format [{"role": "user"/"assistant", "content": "..."}]."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "stream": True,
        "options": {"num_predict": max_tokens},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST", f"{OLLAMA_URL}/api/chat", json=payload
        ) as response:
            async for line in response.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if "message" in chunk and chunk["message"].get("content"):
                    yield chunk["message"]["content"]
                if chunk.get("done"):
                    break


async def generate(model: str, system_prompt: str, messages: list[dict],
                    max_tokens: int = 400) -> str:
    """Nicht-gestreamte Variante, z.B. fuer Hintergrund-Jobs (Scheduler,
    Story-Verdichtung), wo kein Live-Tippen im Chat noetig ist."""
    parts = []
    async for token in stream(model, system_prompt, messages, max_tokens):
        parts.append(token)
    return "".join(parts)


async def is_model_available(model: str) -> bool:
    """Prueft, ob ein Modell aktuell in Ollama geladen/gepullt ist -
    relevant fuer die 'Professor schlaeft gerade'-Logik."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            tags = [m["name"] for m in resp.json().get("models", [])]
            return any(model in t for t in tags)
        except httpx.HTTPError:
            return False
