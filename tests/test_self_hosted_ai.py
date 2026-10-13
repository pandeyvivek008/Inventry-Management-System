"""Offline adapter checks for Ollama, local Whisper and Piper; no API keys/network."""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from services import assistant_service  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, body=None, content=b"", text=""):
        self.status_code = status_code
        self._body = body or {}
        self.content = content
        self.text = text

    def json(self):
        return self._body


class FakeClient:
    requests = []
    responses = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


async def main():
    FakeClient.requests = []
    FakeClient.responses = [FakeResponse(body={"message": {"content": '{"intent":"stock_check","product_query":"नीली ड्रेस","size":"M","quantity":null,"operation":"add","sku":"","product_name":"","category":"","new_sku":"","new_name":"","new_category":"","sizes":"","order_id":null,"reply":""}'}})]
    env = {"AI_PROVIDER": "local", "LOCAL_AI_URL": "http://127.0.0.1:11434", "LOCAL_AI_MODEL": "qwen2.5:3b", "LOCAL_AI_API_KEY": "local-test-token"}
    with patch.dict(os.environ, env, clear=True), patch.object(assistant_service.httpx, "AsyncClient", FakeClient):
        assert assistant_service.active_provider() == "local"
        parsed = await assistant_service.parse_inventory_request("नीली ड्रेस के M साइज़ का स्टॉक बताओ", [])
    assert parsed["intent"] == "stock_check" and parsed["product_query"] == "नीली ड्रेस"
    url, request = FakeClient.requests[-1]
    assert url == "http://127.0.0.1:11434/api/chat"
    assert request["headers"]["Authorization"] == "Bearer local-test-token"
    assert request["json"]["format"] == assistant_service.INTENT_SCHEMA
    assert request["json"]["messages"][0]["role"] == "system"
    assert "Hindi-only" in request["json"]["messages"][0]["content"]
    print("ok: local Ollama uses Hindi inventory instructions and strict intent JSON schema")

    FakeClient.requests = []
    FakeClient.responses = [FakeResponse(body={"text": "नीली ड्रेस के एम साइज़ में पाँच जोड़ो"})]
    with patch.dict(os.environ, {"AI_PROVIDER": "local", "LOCAL_STT_URL": "http://whisper:8000/v1/audio/transcriptions"}, clear=True), patch.object(assistant_service.httpx, "AsyncClient", FakeClient):
        transcript = await assistant_service.transcribe_audio(b"sample", "voice.webm", "audio/webm")
    assert transcript.startswith("नीली ड्रेस")
    stt_url, stt_request = FakeClient.requests[-1]
    assert stt_url.endswith("/v1/audio/transcriptions")
    assert stt_request["data"]["language"] == "hi"
    assert stt_request["files"]["file"][1] == b"sample"
    print("ok: self-hosted Whisper transcription forces Hindi and forwards captured audio")

    FakeClient.requests = []
    FakeClient.responses = [FakeResponse(content=b"RIFF\x00\x00\x00\x00WAVEbytes")]
    with patch.dict(os.environ, {"AI_PROVIDER": "local", "LOCAL_TTS_URL": "http://piper:5000/synthesize", "LOCAL_TTS_API_KEY": "tts-test"}, clear=True), patch.object(assistant_service.httpx, "AsyncClient", FakeClient):
        audio = await assistant_service.synthesize_speech("नमस्ते, मैं दिशा हूँ।")
    assert audio.startswith(b"RIFF")
    tts_url, tts_request = FakeClient.requests[-1]
    assert tts_url.endswith("/synthesize")
    assert tts_request["headers"]["Authorization"] == "Bearer tts-test"
    assert tts_request["json"]["text"].startswith("नमस्ते")
    print("ok: self-hosted Piper returns WAV speech without calling Gemini")

    # A temporary Gemini 503 retries a bounded number of times, then the
    # configured local Hindi recognizer handles the same recording.
    FakeClient.requests = []
    FakeClient.responses = [
        FakeResponse(503, text="overloaded"), FakeResponse(503, text="overloaded"),
        FakeResponse(503, text="overloaded"), FakeResponse(body={"text": "स्टॉक बताओ"}),
    ]
    with patch.dict(os.environ, {
        "AI_PROVIDER": "gemini", "GEMINI_API_KEY": "gemini-test",
        "GEMINI_TRANSCRIBE_MODEL": "gemini-test-model",
        "LOCAL_STT_URL": "http://whisper:8000/v1/audio/transcriptions",
    }, clear=True), patch.object(assistant_service.httpx, "AsyncClient", FakeClient):
        # No wall-clock delay is needed in this mocked test.
        async def no_wait(_seconds):
            return None
        with patch.object(assistant_service.asyncio, "sleep", no_wait):
            transcript = await assistant_service.transcribe_audio(b"sample", "voice.webm", "audio/webm")
    assert transcript == "स्टॉक बताओ"
    assert len(FakeClient.requests) == 4
    assert FakeClient.requests[-1][0].endswith("/v1/audio/transcriptions")
    print("ok: Gemini HTTP 503 retries three times then falls back to configured local Whisper")


asyncio.run(main())
