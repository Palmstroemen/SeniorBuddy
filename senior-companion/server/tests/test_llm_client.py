"""
Tests fuer llm_client, ausschliesslich mit gemocktem HTTP-Verkehr
(pytest-httpx) - es wird nie ein echter Ollama-Server gebraucht und
NIE eine echte Netzwerkverbindung aufgebaut. Das passt zum
"lokal, vorsichtig mit Internet"-Grundprinzip: auch in Tests soll
nichts unbeabsichtigt nach draussen gehen.
"""
import httpx

import llm_client


async def test_is_model_available_true_when_model_present(httpx_mock):
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/tags",
        json={"models": [{"name": "qwen2.5:7b-instruct"}]},
    )
    assert await llm_client.is_model_available("qwen2.5:7b-instruct") is True


async def test_is_model_available_false_when_not_pulled(httpx_mock):
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/tags",
        json={"models": []},
    )
    assert await llm_client.is_model_available("qwen2.5:32b-instruct") is False


async def test_is_model_available_false_when_server_unreachable(httpx_mock):
    # Simuliert: Ollama laeuft (noch) nicht - darf nicht crashen,
    # sondern soll sauber False liefern (relevant fuer die
    # "Professor schlaeft gerade"-Logik).
    httpx_mock.add_exception(httpx.ConnectError("Verbindung fehlgeschlagen"))
    assert await llm_client.is_model_available("irgendein-modell") is False


async def test_generate_concatenates_streamed_tokens(httpx_mock):
    httpx_mock.add_response(
        url=f"{llm_client.OLLAMA_URL}/api/chat",
        stream=httpx.ByteStream(
            b'{"message": {"content": "Hallo"}, "done": false}\n'
            b'{"message": {"content": ", Welt!"}, "done": false}\n'
            b'{"message": {"content": ""}, "done": true}\n'
        ),
    )
    result = await llm_client.generate(
        model="qwen2.5:7b-instruct",
        system_prompt="Testprompt",
        messages=[{"role": "user", "content": "Hi"}],
    )
    assert result == "Hallo, Welt!"
