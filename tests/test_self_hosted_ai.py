"""Offline adapter checks for Ollama, local Whisper and Piper; no API keys/network."""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    assert "Hindi" in request["json"]["messages"][0]["content"] and "English" in request["json"]["messages"][0]["content"]
    print("ok: local Ollama uses multilingual inventory instructions and strict intent JSON schema")

    FakeClient.requests = []
    FakeClient.responses = [FakeResponse(body={"choices": [{"message": {"content": '{"intent":"stock_check","product_query":"नीली ड्रेस","size":"M","quantity":null,"operation":"add","sku":"","product_name":"","category":"","new_sku":"","new_name":"","new_category":"","sizes":"","order_id":null,"reply":""}'}}]})]
    env = {
        "AI_PROVIDER": "compatible",
        "OPENAI_COMPATIBLE_BASE_URL": "https://api.groq.com/openai/v1",
        "OPENAI_COMPATIBLE_MODEL": "openai/gpt-oss-20b",
        "OPENAI_COMPATIBLE_API_KEY": "provider-test-token",
    }
    with patch.dict(os.environ, env, clear=True), patch.object(assistant_service.httpx, "AsyncClient", FakeClient):
        assert assistant_service.active_provider() == "compatible"
        parsed = await assistant_service.parse_inventory_request("नीली ड्रेस के M साइज़ का स्टॉक बताओ", [])
    assert parsed["intent"] == "stock_check" and parsed["size"] == "M"
    url, request = FakeClient.requests[-1]
    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer provider-test-token"
    assert request["json"]["response_format"] == {"type": "json_object"}
    assert request["json"]["model"] == "openai/gpt-oss-20b"
    print("ok: hosted OpenAI-compatible API returns a validated Disha intent without exposing its key")

    FakeClient.requests = []
    FakeClient.responses = [FakeResponse(body={"choices": [{"message": {"content": '{"intent":"conversation","product_query":"","size":"","quantity":null,"operation":"add","sku":"","product_name":"","category":"","new_sku":"","new_name":"","new_category":"","sizes":"","order_id":null,"reply":"नमस्ते।"}'}}]})]
    env = {
        "AI_PROVIDER": "strata",
        "OPENAI_COMPATIBLE_BASE_URL": "http://strata:8080/v1",
        "OPENAI_COMPATIBLE_MODEL": "strata",
    }
    with patch.dict(os.environ, env, clear=True), patch.object(assistant_service.httpx, "AsyncClient", FakeClient):
        assert assistant_service.active_provider() == "compatible"
        parsed = await assistant_service.parse_inventory_request("नमस्ते", [])
    url, request = FakeClient.requests[-1]
    assert url == "http://strata:8080/v1/chat/completions"
    assert "Authorization" not in request["headers"]
    assert parsed["intent"] == "conversation"
    print("ok: Strata-compatible endpoint works without a cloud API key")

    with patch.dict(os.environ, {
        "DISHA_AI_ENABLED": "true", "DISHA_AI_PROVIDER": "strata",
        "DISHA_AI_BASE_URL": "https://gpu.example.com/v1", "DISHA_AI_MODEL": "strata-model",
        "DISHA_AI_ALLOW_EXTERNAL_INFERENCE": "false",
    }, clear=True):
        assert assistant_service.active_provider() is None
    with patch.dict(os.environ, {
        "DISHA_AI_ENABLED": "true", "DISHA_AI_PROVIDER": "strata",
        "DISHA_AI_BASE_URL": "https://gpu.example.com/v1", "DISHA_AI_MODEL": "strata-model",
        "DISHA_AI_API_KEY": "disha-test-token", "DISHA_AI_ALLOW_EXTERNAL_INFERENCE": "true",
        "DISHA_AI_MAX_OUTPUT_TOKENS": "2500", "DISHA_AI_TIMEOUT_SECONDS": "35", "DISHA_AI_TEMPERATURE": "0.4",
    }, clear=True):
        assert assistant_service.active_provider() == "compatible"
        assert assistant_service._compatible_chat_url() == "https://gpu.example.com/v1/chat/completions"
        assert assistant_service._compatible_generation_limits() == (2500, 35.0)
        assert assistant_service._compatible_temperature() == 0.4
    with patch.dict(os.environ, {
        "DISHA_AI_ENABLED": "false", "DISHA_AI_PROVIDER": "strata",
        "DISHA_AI_BASE_URL": "http://strata:8080/v1", "DISHA_AI_MODEL": "strata-model",
    }, clear=True):
        assert assistant_service.active_provider() is None
    with patch.dict(os.environ, {
        "DISHA_AI_PROVIDER": "strata", "DISHA_AI_BASE_URL": "https://user:pass@gpu.example.com/v1",
        "DISHA_AI_MODEL": "strata-model", "DISHA_AI_ALLOW_EXTERNAL_INFERENCE": "true",
    }, clear=True):
        assert assistant_service.active_provider() is None
    with patch.dict(os.environ, {
        "DISHA_AI_ENABLED": "false", "DISHA_AI_PROVIDER": "strata", "GEMINI_API_KEY": "must-not-be-used",
    }, clear=True):
        try:
            await assistant_service.transcribe_audio(b"sample", "voice.webm", "audio/webm")
        except assistant_service.AssistantProviderError:
            pass
        else:
            raise AssertionError("disabled Disha must not call a speech provider")
    with patch.dict(os.environ, {
        "DISHA_AI_PROVIDER": "strata", "DISHA_AI_BASE_URL": "http://strata:8080/v1",
        "DISHA_AI_MODEL": "strata", "DISHA_AI_ALLOW_EXTERNAL_INFERENCE": "false",
        "GEMINI_API_KEY": "must-not-be-used",
    }, clear=True), patch.object(assistant_service, "_transcribe_with_embedded_whisper", new=AsyncMock(return_value="local transcript")) as local_stt:
        transcript = await assistant_service.transcribe_audio(b"sample", "voice.webm", "audio/webm")
        assert transcript == "local transcript"
        local_stt.assert_awaited_once_with(b"sample")
    print("ok: Disha provider config validates limits, disabled state, safe endpoint defaults, and remote-inference opt-in")

    model_options = {}
    class FakeWhisperModel:
        def __init__(self, name, **kwargs):
            model_options.update(name=name, **kwargs)

        def transcribe(self, audio, **kwargs):
            assert audio.read() == b"sample"
            model_options.update(transcribe=kwargs)
            return iter([SimpleNamespace(text=" स्टॉक पाँच जोड़ो ")]), SimpleNamespace(language="hi")

    assistant_service._whisper_model = None
    assistant_service._whisper_model_key = None
    with patch.dict(os.environ, {
        "DISHA_AI_PROVIDER": "strata", "LOCAL_STT_MODEL": "tiny", "LOCAL_STT_DEVICE": "cpu",
        "LOCAL_STT_COMPUTE_TYPE": "int8", "LOCAL_STT_LANGUAGE": "auto",
    }, clear=True), patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=FakeWhisperModel)}):
        transcript = await assistant_service.transcribe_audio(b"sample", "voice.webm", "audio/webm")
    assert transcript == "स्टॉक पाँच जोड़ो"
    assert model_options["name"] == "tiny" and model_options["device"] == "cpu" and model_options["compute_type"] == "int8"
    assert model_options["transcribe"]["language"] is None
    print("ok: embedded Whisper transcribes audio locally with CPU/int8 and auto language detection")

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
