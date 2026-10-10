"""Gemini intent parser for safe, size-specific inventory actions.

The model only extracts what the user asked for. Catalog lookup, current stock,
authorization to mutate, and all inventory writes remain in the application DB.
"""
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv


# Railway variables take precedence; this only fills missing values locally.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


logger = logging.getLogger(__name__)
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.5-flash-lite"

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["stock_add", "stock_set", "stock_check", "daily_sales", "conversation", "help", "other"]},
        "product_query": {"type": "string"},
        "size": {"type": "string"},
        "quantity": {"type": ["integer", "null"]},
        "operation": {"type": "string", "enum": ["add", "set"]},
    },
    "required": ["intent", "product_query", "size", "quantity", "operation"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You extract inventory requests from Hindi, Hinglish, and English.
Return only the required JSON object. Never invent a product, SKU, size, or stock value.
Allowed intent: stock_add, stock_set, stock_check, daily_sales, conversation, help, other.
Examples:
- 'black wali M size mein 5 piece add karo' => stock_add, product_query='black', size='M', quantity=5, operation='add'.
- 'ZIA mustard XL size ka stock 12 set kar do' => stock_set, product_query='ZIA mustard', size='XL', quantity=12, operation='set'.
- 'medium size mein kitna stock hai' => stock_check; leave product_query empty if none was named.
- 'aaj kitni sale hui' or 'daily sales report' => daily_sales.
- Greetings, thanks, or questions such as 'who are you' => conversation.
- If user says only '5 aur add karo', use recent chat context for product/size; do not guess missing fields.
Use product_query only for the design/SKU/color/name. Normalize spoken sizes such as medium to M, double XL to XXL, and free size to FREE SIZE.
For stock_add, quantity means units to add. For stock_set, it means the final physical count.
Do not put the product's size, quantity, action words, or generic words like 'design', 'product', 'stock' in product_query.
"""


class AssistantProviderError(Exception):
    """The configured inference provider could not safely parse a request."""


def api_key_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


async def parse_inventory_request(message: str, history: list[dict]) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AssistantProviderError("Gemini API is not configured.")

    contents = []
    for item in history[-10:]:
        role = item.get("role")
        content = str(item.get("content", ""))[:1000]
        if role in ("user", "assistant") and content.strip():
            contents.append({"role": "user" if role == "user" else "model", "parts": [{"text": content}]})
    contents.append({"role": "user", "parts": [{"text": message[:500]}]})

    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 220,
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
                },
                "required": INTENT_SCHEMA["required"],
            },
        },
    }
    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    try:
        # Leave room for cold starts and transient provider latency.
        async with httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=8.0)) as client:
            response = await client.post(
                GEMINI_API_URL.format(model=quote(model, safe="-._")),
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            # Keep the API key and prompt private: log only Google's short error body and status.
            logger.warning("Gemini generateContent returned HTTP %s: %s", response.status_code, response.text[:500])
            raise AssistantProviderError(f"Gemini API returned HTTP {response.status_code}.")
        body = response.json()
        content = "".join(
            str(part.get("text", ""))
            for part in body["candidates"][0]["content"]["parts"]
            if isinstance(part, dict)
        )
        parsed = json.loads(content)
        intent = str(parsed.get("intent", "other"))
        operation = str(parsed.get("operation", "add"))
        quantity = parsed.get("quantity")
        if intent not in {"stock_add", "stock_set", "stock_check", "daily_sales", "conversation", "help", "other"}:
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
        }
    except AssistantProviderError:
        raise
    except httpx.TimeoutException as exc:
        logger.warning("Gemini request timed out (%s).", type(exc).__name__)
        raise AssistantProviderError("Gemini request timed out.") from exc
    except httpx.HTTPError as exc:
        logger.warning("Gemini network request failed (%s).", type(exc).__name__)
        raise AssistantProviderError("Gemini provider request failed.") from exc
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Gemini assistant response was invalid (%s).", type(exc).__name__)
        raise AssistantProviderError("Inference provider unavailable or returned invalid JSON.") from exc
