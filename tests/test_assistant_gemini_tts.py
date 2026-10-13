"""Gemini Hindi female-voice TTS checks; all network responses are mocked."""
import asyncio
import base64
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from services import assistant_service  # noqa: E402


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class FakeClient:
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return FakeResponse({"voices": [{"id": "hi-IN-female-warm-01", "gender": "female"}]})

    async def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        wav = b"RIFF\x00\x00\x00\x00WAVE"
        return FakeResponse({"steps": [{"type": "model_output", "content": [{
            "type": "audio", "data": base64.b64encode(wav).decode("ascii")
        }]}]})


async def main():
    FakeClient.requests = []
    assistant_service._female_voice_cache = None
    with patch.dict(os.environ, {
        "AI_PROVIDER": "gemini", "GEMINI_API_KEY": "test-only-gemini-key",
        "GEMINI_TTS_MODEL": "test-gemini-tts", "GEMINI_TTS_VOICE": "",
    }, clear=True), patch.object(assistant_service.httpx, "AsyncClient", FakeClient):
        assert assistant_service.active_provider() == "gemini"
        result = await assistant_service.synthesize_speech("नमस्ते, स्टॉक अपडेट कर दूँ?")

    assert result.startswith(b"RIFF")
    assert [request[0] for request in FakeClient.requests] == ["GET", "POST"]
    voice_lookup = FakeClient.requests[0][2]
    assert voice_lookup["params"]["language_code"] == "hi-IN"
    assert voice_lookup["params"]["gender"] == "female"
    tts = FakeClient.requests[1]
    assert tts[1].endswith("/interactions")
    payload = tts[2]["json"]
    assert payload["model"] == "test-gemini-tts"
    assert payload["generation_config"]["speech_config"][0]["voice"] == "hi-IN-female-warm-01"
    assert payload["input"][0]["content"][0]["text"].startswith("नमस्ते")
    print("ok: Gemini selects a Hindi female conversational voice and returns WAV")
    print("ok: Hindi TTS request stays server-side and uses configured model/key")


asyncio.run(main())
