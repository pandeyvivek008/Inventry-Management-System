"""OpenAI/Gemini intent parser for safe, size-specific inventory actions.

The model only extracts what the user asked for. Catalog lookup, current stock,
authorization to mutate, and all inventory writes remain in the application DB.
"""
import base64
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv


# Railway variables take precedence; this only fills missing values locally.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


logger = logging.getLogger(__name__)
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_VOICES_URL = "https://generativelanguage.googleapis.com/v1beta/voices"
_female_voice_cache: tuple[float, str] | None = None

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["stock_add", "stock_set", "stock_check", "product_search", "alerts_summary", "reorder_summary", "pending_orders", "recent_activity", "product_create", "product_update", "product_archive", "product_restore", "order_dispatch", "daily_sales", "app_status", "conversation", "help", "other"]},
        "product_query": {"type": "string"},
        "size": {"type": "string"},
        "quantity": {"type": ["integer", "null"]},
        "operation": {"type": "string", "enum": ["add", "set"]},
        "sku": {"type": "string"},
        "product_name": {"type": "string"},
        "category": {"type": "string"},
        "new_sku": {"type": "string"},
        "new_name": {"type": "string"},
        "new_category": {"type": "string"},
        "sizes": {"type": "string"},
        "order_id": {"type": ["integer", "null"]},
        "reply": {"type": "string"},
    },
    "required": ["intent", "product_query", "size", "quantity", "operation", "sku", "product_name", "category", "new_sku", "new_name", "new_category", "sizes", "order_id", "reply"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are Disha, a warm, capable Hindi-only conversational assistant. Understand natural spoken Hindi, Devanagari Hindi, and Romanized Hindi/Hinglish, but always answer in natural Hindi using Devanagari script. Do not answer in English. Keep actual catalog product names and SKU identifiers exactly as stored when needed for an unambiguous inventory result.
Return only the required JSON object. Never invent a product, SKU, size, or stock value.
Allowed intent: stock_add, stock_set, stock_check, product_search, alerts_summary, reorder_summary, pending_orders, recent_activity, product_create, product_update, product_archive, product_restore, order_dispatch, daily_sales, app_status, conversation, help, other.
Examples:
- 'black wali M size mein 5 piece add karo' => stock_add, product_query='black', size='M', quantity=5, operation='add'.
- 'ZIA mustard XL size ka stock 12 set kar do' => stock_set, product_query='ZIA mustard', size='XL', quantity=12, operation='set'.
- 'KIDS NK 101 ke medium size mein kitne pieces hain?' / 'किड्स एनके 101 के मध्यम साइज़ में कितना स्टॉक है?' => stock_check, product_query='KIDS NK 101', size='M'.
- 'KIDS NK 101 mein 5 add karo' / 'किड्स एनके 101 में पाँच पीस जोड़ो' => stock_add, product_query='KIDS NK 101', quantity=5; ask which size if it is missing.
- 'mustard wali dress ki photo dikhao' / 'मस्टर्ड वाली ड्रेस दिखाओ' => product_search; search the live catalog and return its saved product image, never invent an image.
- 'blue wale kurte ka S size batao' / 'नीले कुर्ते का एस साइज़ बताओ' => stock_check; product_query='blue kurta', size='S'.
- 'medium size mein kitna stock hai' => stock_check; leave product_query empty if none was named.
- 'aaj kitni sale hui' or 'daily sales report' => daily_sales.
- 'app ka status batao', 'overall inventory kaisi hai' => app_status. Use only when the user asks for an overall dashboard/inventory summary, not one product's stock.
- 'product search karo', 'is design ko dikhao/photo dikhao', 'is design ke sizes dikhao', 'black wale products dhoondo' => product_search; put the product/SKU/color in product_query. If similar colours/designs match, ask the employee to choose the right one instead of guessing.
- 'sab sizes ka stock', 'all sizes dikhao', or a short answer like 'sab' after you asked which size => product_search for the same design, showing every size and its current stock. Do not ask the size again.
- 'alerts batao', 'kaunse designs low stock hain' => alerts_summary; product_query can narrow the alert search.
- 'kya restock karna hai', 'reorder list dikhao' => reorder_summary; product_query can narrow it.
- 'pending orders kitne hain', 'orders ka status batao' => pending_orders.
- 'recent stock changes/history dikhao' => recent_activity; product_query can narrow it.
- Product creation => product_create. Extract only explicitly provided sku, product_name, category, and comma-separated sizes. Never invent missing SKU/name/sizes.
- Hindi voice product creation example: 'नया प्रोडक्ट बनाओ, SKU DRESS-21, नाम नीली ड्रेस, साइज़ एस एम एल' or 'DRESS-21 नीली ड्रेस के एस एम एल साइज़ जोड़ो' => product_create, sku='DRESS-21', product_name='नीली ड्रेस', sizes='S M L'. If any required field is unclear, ask for it; do not save until the app asks for confirmation.
- Product metadata changes => product_update; product_query identifies the existing design; fill only explicitly requested new_sku, new_name, or new_category.
- 'move design to trash/delete design' => product_archive; product_query identifies the design. 'restore design' => product_restore.
- 'dispatch batch 123' => order_dispatch and order_id=123. Never dispatch without an explicitly identified batch ID.
- Greetings, thanks, 'who are you', casual chat, and general questions not about inventory => conversation.
- If the user is asking an ordinary question or having a general conversation, choose conversation even when it is unrelated to inventory. Answer the actual question directly and do not redirect them to app features. Use only live app queries for app-specific facts; do not invent account or inventory data.
- Questions about how to use Disha or the app => help.
- Requests outside inventory that are not covered above => other.
- If user says only '5 aur add karo', use recent chat context for product/size; do not guess missing fields.
Understand Hindi in Devanagari and Romanized Hindi/Hinglish, including common number words (ek/एक, do/दो, paanch/पाँच) and apparel terms. Preserve a spoken product code as a search query, but prefer the actual readable product name/color when it is clear. Normalize spoken sizes such as small/chhota to S, medium/madhyam to M, large/bada to L, double XL to XXL, and free size to FREE SIZE. If product, size, or quantity is ambiguous, ask a short clarification instead of guessing.
In user-facing replies, prefer the readable product name and color. Never include a SKU unless the user explicitly asks for it. When a user asks to show a photo, keep the spoken answer to the product name; do not read the SKU or its size list aloud.
For stock_add, quantity means units to add. For stock_set, it means the final physical count.
Do not put the product's size, quantity, action words, or generic words like 'design', 'product', 'stock' in product_query.

Also return `reply`:
- For conversation, help, or other, write a natural, useful answer as Disha in Hindi (Devanagari) only, whatever language/script the user used. Use recent chat history so follow-up conversation makes sense. Be friendly and conversational like a general chat assistant, not a fixed FAQ. Answer ordinary non-inventory questions directly when you can; be honest when unsure. Keep most replies to 1–3 short sentences and ask one clear follow-up only when it helps. Do not repeat an earlier greeting, recap, or answer unless the user asks; don't steer every topic back to inventory.
- For app_status and data/action intents (stock_add, stock_set, stock_check, product_search, alerts_summary, reorder_summary, pending_orders, recent_activity, product_create, product_update, product_archive, product_restore, order_dispatch, daily_sales), leave reply empty; the application will validate against live data and present a confirmation proposal.
- Never claim you changed stock. The application handles stock actions separately and requires confirmation.
- Disha can answer from the live app catalog, size-level inventory, alerts, reorder shortages, pending orders, stock history, daily dispatched sales, and overall dashboard status. Stock changes, product create/edit/archive/restore, and order dispatch are performed only through an app-provided action card after explicit user confirmation. Never say an action succeeded before the app confirms it. Do not claim arbitrary database, file-upload, settings, or code access.
"""


class AssistantProviderError(Exception):
    """The configured inference provider could not safely parse a request."""

    def __init__(self, message: str, *, upstream_status: int | None = None, user_message: str | None = None):
        super().__init__(message)
        self.upstream_status = upstream_status
        self.user_message = user_message or message


def _audio_provider_error(status: int, feature: str) -> AssistantProviderError:
    if status in (401, 403):
        advice = "OpenAI key ya project permission check karein."
    elif status == 429:
        advice = "OpenAI API credits/quota check karein."
    else:
        advice = "OpenAI audio service abhi response nahi de paayi."
    return AssistantProviderError(
        f"OpenAI {feature} returned HTTP {status}.", upstream_status=status,
        user_message=f"{advice} ({feature} HTTP {status})",
    )


async def transcribe_audio(audio: bytes, filename: str, content_type: str) -> str:
    """Transcribe a short Hindi utterance using the configured provider."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    provider = os.getenv("AI_PROVIDER", "gemini").strip().lower()
    if provider == "gemini" and not gemini_key:
        raise AssistantProviderError(
            "GEMINI_API_KEY is required for Hindi voice transcription.",
            user_message="हिंदी वॉइस के लिए Railway Variables में GEMINI_API_KEY जोड़ें।",
        )
    if provider == "gemini":
        return await _transcribe_with_gemini(audio, content_type, gemini_key)
    if not api_key and gemini_key and provider == "auto":
        return await _transcribe_with_gemini(audio, content_type, gemini_key)
    if not api_key:
        raise AssistantProviderError(
            "No audio transcription provider key is configured.",
            user_message="हिंदी वॉइस के लिए Railway Variables में GEMINI_API_KEY जोड़ें। तब तक टाइप करके बात कर सकती हैं।",
        )
    prompt = (
        "Transcribe spoken Hindi only. Write Hindi words in Devanagari, even when the speaker uses casual Hindi pronunciation. "
        "Keep product names, colours, sizes, quantities, and SKU identifiers as spoken. Do not translate or answer. "
        "Inventory vocabulary: size S M L XL XXL, stock, pieces, add, set, delete, restore, dispatch."
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=7.0)) as client:
            response = await client.post(
                OPENAI_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-transcribe"), "response_format": "json", "prompt": prompt},
                files={"file": (filename, audio, content_type)},
            )
        if response.status_code >= 400:
            logger.warning("OpenAI transcription returned HTTP %s: %s", response.status_code, response.text[:300])
            raise _audio_provider_error(response.status_code, "transcription")
        body = response.json()
        transcript = str(body.get("text") or "").strip()[:500]
        if transcript:
            return transcript
        raise AssistantProviderError("OpenAI transcription returned no speech text.", user_message="Awaaz clear nahi mili. Dobara boliye.")
    except AssistantProviderError as openai_error:
        if provider != "auto" or not gemini_key:
            raise
        logger.warning("OpenAI transcription failed; trying configured Gemini transcription fallback.")
        try:
            return await _transcribe_with_gemini(audio, content_type, gemini_key)
        except AssistantProviderError as gemini_error:
            logger.warning("Both configured audio transcription providers failed.")
            raise AssistantProviderError(
                "OpenAI and Gemini transcription both failed.",
                upstream_status=gemini_error.upstream_status,
                user_message=f"OpenAI voice service unavailable ({openai_error.user_message}); Gemini fallback bhi fail hua ({gemini_error.user_message}). Type karke baat jaari rakhein.",
            ) from gemini_error
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("OpenAI transcription failed (%s).", type(exc).__name__)
        openai_error = AssistantProviderError(
            "OpenAI transcription request failed.", user_message="Voice ko samajhne mein connection error aaya. Dobara boliye ya type karein."
        )
        if provider == "auto" and gemini_key:
            try:
                return await _transcribe_with_gemini(audio, content_type, gemini_key)
            except AssistantProviderError as gemini_error:
                raise AssistantProviderError(
                    "OpenAI and Gemini transcription both failed.",
                    upstream_status=gemini_error.upstream_status,
                    user_message=f"OpenAI voice connection nahi hua; Gemini fallback bhi fail hua ({gemini_error.user_message}). Type karke baat jaari rakhein.",
                ) from gemini_error
        raise openai_error from exc


async def _transcribe_with_gemini(audio: bytes, content_type: str, api_key: str) -> str:
    """Use Gemini's audio input in auto mode when OpenAI transcription is unavailable."""
    model = os.getenv("GEMINI_TRANSCRIBE_MODEL", os.getenv("GEMINI_MODEL", DEFAULT_MODEL)).strip() or DEFAULT_MODEL
    mime_type = (content_type or "audio/webm").split(";", 1)[0].strip().lower()
    payload = {
        "contents": [{"role": "user", "parts": [
            {"text": (
                "Transcribe this speech in Hindi only. Write Hindi words in Devanagari, but preserve actual product names, SKU identifiers, size labels, and numbers as spoken. "
                "Do not translate, answer, or add commentary. "
                "Return only the transcript."
            )},
            {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(audio).decode("ascii")}},
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 256},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=7.0)) as client:
            response = await client.post(
                GEMINI_API_URL.format(model=quote(model, safe="-._")),
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            logger.warning("Gemini transcription returned HTTP %s: %s", response.status_code, response.text[:300])
            status = response.status_code
            advice = "Gemini API key/project access check karein." if status in (401, 403) else "Gemini quota/rate limit check karein." if status == 429 else "Gemini audio service abhi response nahi de paayi."
            raise AssistantProviderError(
                f"Gemini transcription returned HTTP {status}.", upstream_status=status,
                user_message=f"{advice} (Gemini transcription HTTP {status})",
            )
        body = response.json()
        transcript = "".join(
            str(part.get("text", ""))
            for part in body["candidates"][0]["content"]["parts"]
            if isinstance(part, dict) and part.get("text")
        ).strip()
        if not transcript:
            raise AssistantProviderError("Gemini transcription returned no speech text.", user_message="Awaaz clear nahi mili. Dobara boliye.")
        return transcript[:500]
    except AssistantProviderError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("Gemini transcription failed (%s).", type(exc).__name__)
        raise AssistantProviderError(
            "Gemini transcription request failed.", user_message="Gemini se awaaz samajhne mein connection error aaya. Type karke baat jaari rakhein."
        ) from exc


async def _gemini_female_hindi_voice(api_key: str) -> str:
    """Cache the first Hindi female conversational voice returned by Gemini."""
    global _female_voice_cache
    configured_voice = os.getenv("GEMINI_TTS_VOICE", "").strip()
    if configured_voice:
        return configured_voice
    now = time.monotonic()
    if _female_voice_cache and now - _female_voice_cache[0] < 86400:
        return _female_voice_cache[1]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=7.0)) as client:
            response = await client.get(
                GEMINI_VOICES_URL,
                headers={"x-goog-api-key": api_key},
                params={"language_code": "hi-IN", "gender": "female", "context": "Conversational", "type": "prebuilt", "search": "warm", "page_size": 50},
            )
        if response.status_code >= 400:
            logger.warning("Gemini voice library returned HTTP %s; using configured fallback voice.", response.status_code)
            return os.getenv("GEMINI_TTS_FALLBACK_VOICE", "Kore").strip() or "Kore"
        voices = response.json().get("voices", [])
        match = next((voice for voice in voices if voice.get("id")), None)
        voice_id = str(match["id"]) if match else (os.getenv("GEMINI_TTS_FALLBACK_VOICE", "Kore").strip() or "Kore")
        _female_voice_cache = (now, voice_id)
        return voice_id
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("Gemini voice library lookup failed (%s); using configured fallback voice.", type(exc).__name__)
        return os.getenv("GEMINI_TTS_FALLBACK_VOICE", "Kore").strip() or "Kore"


async def synthesize_speech(text: str) -> bytes:
    """Render Hindi speech with Gemini TTS and a Hindi female voice."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AssistantProviderError("GEMINI_API_KEY is required for generated Hindi speech.", user_message="हिंदी आवाज़ के लिए Railway Variables में GEMINI_API_KEY जोड़ें।")
    voice = await _gemini_female_hindi_voice(api_key)
    payload = {
        "model": os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts").strip() or "gemini-3.8-flash-lite-tts",
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": text[:1200],
            "annotations": [{"type": "speech_metadata", "style": "warm, friendly, gently cheerful Hindi conversation, natural Indian pronunciation, clear moderate pace, pleasant feminine voice"}],
        }]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=7.0)) as client:
            response = await client.post(
                GEMINI_INTERACTIONS_URL,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            logger.warning("Gemini speech returned HTTP %s: %s", response.status_code, response.text[:300])
            status = response.status_code
            advice = "Gemini API key/model access check karein." if status in (401, 403, 404) else "Gemini quota/rate limit check karein." if status == 429 else "Gemini voice service abhi response nahi de paayi."
            raise AssistantProviderError(f"Gemini speech returned HTTP {status}.", upstream_status=status, user_message=f"{advice} (Gemini TTS HTTP {status})")
        body = response.json()
        audio_data = body.get("output_audio", {}).get("data")
        if not audio_data:
            audio_items = [part for step in body.get("steps", []) if step.get("type") == "model_output" for part in step.get("content", []) if part.get("type") == "audio" and part.get("data")]
            audio_data = audio_items[-1]["data"] if audio_items else None
        if not audio_data:
            raise AssistantProviderError("Gemini TTS returned no audio.", user_message="Gemini se awaaz nahi bani. Text mein jawab dikhaya hai.")
        return base64.b64decode(audio_data, validate=True)
    except AssistantProviderError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("Gemini speech failed (%s).", type(exc).__name__)
        raise AssistantProviderError(
            "Gemini speech generation failed.", user_message="हिंदी आवाज़ अभी नहीं बन पाई। जवाब टेक्स्ट में उपलब्ध है।"
        ) from exc


def active_provider() -> str | None:
    requested = os.getenv("AI_PROVIDER", "gemini").strip().lower()
    has_openai = bool(os.getenv("OPENAI_API_KEY", "").strip())
    has_gemini = bool(os.getenv("GEMINI_API_KEY", "").strip())
    if requested == "openai":
        return "openai" if has_openai else None
    if requested == "gemini":
        return "gemini" if has_gemini else None
    return "openai" if has_openai else ("gemini" if has_gemini else None)


def api_key_configured() -> bool:
    return active_provider() is not None


def _openai_json_schema(schema: dict) -> dict:
    """Convert nullable JSON Schema type arrays to strict-schema anyOf forms."""
    if isinstance(schema, list):
        return [_openai_json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result = {key: _openai_json_schema(value) for key, value in schema.items() if key != "type"}
    value_type = schema.get("type")
    if isinstance(value_type, list):
        result["anyOf"] = [{"type": item} for item in value_type]
    elif value_type is not None:
        result["type"] = value_type
    return result


async def parse_inventory_request(message: str, history: list[dict], *, _provider_override: str | None = None) -> dict:
    provider = _provider_override or active_provider()
    if not provider:
        raise AssistantProviderError("No Gemini API key is configured. Set GEMINI_API_KEY in Railway Variables.")

    conversation = []
    for item in history[-10:]:
        role = item.get("role")
        content = str(item.get("content", ""))[:1000]
        if role in ("user", "assistant") and content.strip():
            conversation.append({"role": "user" if role == "user" else "assistant", "content": content})
    conversation.append({"role": "user", "content": message[:500]})

    # Build the Gemini body without provider-specific conversation data here;
    # Gemini contents are assembled below. Keeping this provider-neutral also
    # prevents a NameError before the request's error handling can run.
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "generationConfig": {
            "temperature": 0.35,
            "maxOutputTokens": 320,
            "thinkingConfig": {"thinkingLevel": "low"},
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "intent": {"type": "STRING", "enum": INTENT_SCHEMA["properties"]["intent"]["enum"]},
                    "product_query": {"type": "STRING"},
                    "size": {"type": "STRING"},
                    "quantity": {"type": "INTEGER", "nullable": True},
                    "operation": {"type": "STRING", "enum": INTENT_SCHEMA["properties"]["operation"]["enum"]},
                    "sku": {"type": "STRING"},
                    "product_name": {"type": "STRING"},
                    "category": {"type": "STRING"},
                    "new_sku": {"type": "STRING"},
                    "new_name": {"type": "STRING"},
                    "new_category": {"type": "STRING"},
                    "sizes": {"type": "STRING"},
                    "order_id": {"type": "INTEGER", "nullable": True},
                    "reply": {"type": "STRING"},
                },
                "required": INTENT_SCHEMA["required"],
            },
        },
    }
    try:
        # Keep the response latency bounded; full app work is performed by the
        # database-backed routes after the model returns a structured intent.
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=7.0)) as client:
            if provider == "openai":
                api_key = os.getenv("OPENAI_API_KEY", "").strip()
                model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
                openai_payload = {
                    "model": model,
                    "instructions": SYSTEM_PROMPT,
                    "input": conversation,
                    "max_output_tokens": 420,
                    "reasoning": {"effort": "low"},
                    "text": {"format": {
                        "type": "json_schema", "name": "inventory_intent", "strict": True,
                        "schema": _openai_json_schema(INTENT_SCHEMA),
                    }},
                }
                response = await client.post(
                    OPENAI_API_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=openai_payload,
                )
            else:
                api_key = os.getenv("GEMINI_API_KEY", "").strip()
                model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
                gemini_contents = [
                    {"role": "user" if item["role"] == "user" else "model", "parts": [{"text": item["content"]}]}
                    for item in conversation
                ]
                payload["contents"] = gemini_contents
                payload["systemInstruction"] = {"parts": [{"text": SYSTEM_PROMPT}]}
                response = await client.post(
                    GEMINI_API_URL.format(model=quote(model, safe="-._")),
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload,
                )
        if response.status_code >= 400:
            # Keep provider keys private; only log the upstream status and safe error body.
            logger.warning("%s assistant request returned HTTP %s: %s", provider, response.status_code, response.text[:500])
            status = response.status_code
            model_variable = "OPENAI_MODEL" if provider == "openai" else "GEMINI_MODEL"
            key_label = "OpenAI API key" if provider == "openai" else "Gemini API key"
            if status in (401, 403):
                advice = f"{key_label} reject hui ya is project ko API access nahi mila. Railway Variables mein {provider.upper()} key check karein."
            elif status == 404:
                default_model = DEFAULT_OPENAI_MODEL if provider == "openai" else DEFAULT_MODEL
                advice = f"{provider.title()} model nahi mila. Railway mein {model_variable} ko {default_model} set karein."
            elif status == 429:
                advice = f"{provider.title()} quota/rate limit poori hui. Provider dashboard mein isi project ka usage aur billing check karein, phir retry karein."
            elif status == 400:
                advice = f"{provider.title()} request reject hui. Railway ka {model_variable} aur structured-output/API configuration check karein."
            else:
                advice = f"{provider.title()} provider abhi request serve nahi kar raha. Thodi der baad retry karein."
            raise AssistantProviderError(
                f"{provider} API returned HTTP {status}.", upstream_status=status,
                user_message=f"{advice} ({provider.title()} HTTP {status})",
            )
        body = response.json()
        if provider == "openai":
            content = str(body.get("output_text") or "")
            if not content:
                content = "".join(
                    str(part.get("text", ""))
                    for item in body.get("output", []) if item.get("type") == "message"
                    for part in item.get("content", []) if part.get("type") == "output_text"
                )
            if body.get("status") not in (None, "completed"):
                raise AssistantProviderError(f"OpenAI response status: {body.get('status')}.")
        else:
            content = "".join(
                str(part.get("text", ""))
                for part in body["candidates"][0]["content"]["parts"]
                if isinstance(part, dict)
            )
        parsed = json.loads(content)
        intent = str(parsed.get("intent", "other"))
        operation = str(parsed.get("operation", "add"))
        quantity = parsed.get("quantity")
        order_id = int(parsed["order_id"]) if parsed.get("order_id") is not None else None
        if order_id is not None and (order_id < 1 or order_id > 2_147_483_647):
            order_id = None
        if intent not in {"stock_add", "stock_set", "stock_check", "product_search", "alerts_summary", "reorder_summary", "pending_orders", "recent_activity", "product_create", "product_update", "product_archive", "product_restore", "order_dispatch", "daily_sales", "app_status", "conversation", "help", "other"}:
            intent = "other"
        if operation not in {"add", "set"}:
            operation = "add"
        if quantity is not None:
            quantity = int(quantity)
            if quantity < 0 or quantity > 100000:
                quantity = None
        return {
            "intent": intent,
            "product_query": str(parsed.get("product_query") or "").strip()[:160],
            "size": str(parsed.get("size") or "").strip()[:20].upper(),
            "quantity": quantity,
            "operation": operation,
            "sku": str(parsed.get("sku") or "").strip()[:64].upper(),
            "product_name": str(parsed.get("product_name") or "").strip()[:300],
            "category": str(parsed.get("category") or "").strip()[:100],
            "new_sku": str(parsed.get("new_sku") or "").strip()[:64].upper(),
            "new_name": str(parsed.get("new_name") or "").strip()[:300],
            "new_category": str(parsed.get("new_category") or "").strip()[:100],
            "sizes": str(parsed.get("sizes") or "").strip()[:300],
            "order_id": order_id,
            "reply": str(parsed.get("reply") or "").strip()[:1000],
        }
    except AssistantProviderError as exc:
        return await _fallback_or_raise(message, history, provider, _provider_override, exc)
    except httpx.TimeoutException as exc:
        logger.warning("%s request timed out (%s).", provider, type(exc).__name__)
        error = AssistantProviderError(f"{provider.title()} request timed out. Please retry.", user_message=f"{provider.title()} se response aane mein timeout hua.")
        return await _fallback_or_raise(message, history, provider, _provider_override, error)
    except httpx.HTTPError as exc:
        logger.warning("%s network request failed (%s).", provider, type(exc).__name__)
        error = AssistantProviderError(f"{provider.title()} network request failed.", user_message=f"{provider.title()} se connection nahi ho paaya.")
        return await _fallback_or_raise(message, history, provider, _provider_override, error)
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("%s assistant response was invalid (%s).", provider, type(exc).__name__)
        error = AssistantProviderError("Inference provider returned invalid JSON.", user_message=f"{provider.title()} ka response samajh nahi aaya.")
        return await _fallback_or_raise(message, history, provider, _provider_override, error)


async def _fallback_or_raise(message: str, history: list[dict], provider: str, override: str | None, error: AssistantProviderError) -> dict:
    """Use the secondary configured API in auto mode, then report both failures."""
    if not override and os.getenv("AI_PROVIDER", "gemini").strip().lower() == "auto":
        fallback = "gemini" if provider == "openai" else "openai"
        fallback_key = "GEMINI_API_KEY" if fallback == "gemini" else "OPENAI_API_KEY"
        if os.getenv(fallback_key, "").strip():
            logger.warning("%s assistant failed; retrying with configured %s provider.", provider, fallback)
            try:
                return await parse_inventory_request(message, history, _provider_override=fallback)
            except AssistantProviderError as fallback_exc:
                logger.warning("Both configured assistant providers failed (%s, %s).", provider, fallback)
                raise AssistantProviderError(
                    f"Both configured AI providers failed ({provider} and {fallback}).",
                    upstream_status=fallback_exc.upstream_status,
                    user_message=(
                        f"OpenAI aur Gemini dono se abhi response nahi mila. "
                        f"{error.user_message} Doosri API bhi fail hui: {fallback_exc.user_message}"
                    ),
                ) from fallback_exc
    raise error
