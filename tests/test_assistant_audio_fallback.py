"""Offline checks for Disha audio provider fallback (no real API keys/calls)."""
import asyncio
import base64
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from services import assistant_service  # noqa: E402


class FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

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

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if "api.openai.com" in url:
            return FakeResponse(429, text="quota exceeded")
        return FakeResponse(200, {
            "candidates": [{"content": {"parts": [{"text": "Hi Disha, blue kurta M mein 5 add karo"}]}}]
        })


async def main():
    FakeClient.requests = []
    with patch.dict(os.environ, {
        "AI_PROVIDER": "auto",
        "OPENAI_API_KEY": "offline-test-openai-key",
        "GEMINI_API_KEY": "offline-test-gemini-key",
        "GEMINI_MODEL": "offline-test-model",
    }), patch.object(assistant_service.httpx, "AsyncClient", FakeClient):
        transcript = await assistant_service.transcribe_audio(
            b"mobile mic sample", "disha-utterance.webm", "audio/webm;codecs=opus"
        )

    assert transcript == "Hi Disha, blue kurta M mein 5 add karo"
    assert len(FakeClient.requests) == 2
    gemini_url, gemini_request = FakeClient.requests[1]
    assert "offline-test-model:generateContent" in gemini_url
    audio_part = gemini_request["json"]["contents"][0]["parts"][1]["inlineData"]
    assert audio_part["mimeType"] == "audio/webm"
    assert base64.b64decode(audio_part["data"]) == b"mobile mic sample"
    print("ok: OpenAI 429 falls back to Gemini audio transcription in auto mode")
    print("ok: mobile WebM MIME type and audio bytes are passed correctly")


asyncio.run(main())
