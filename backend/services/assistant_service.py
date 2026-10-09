"""Small Hugging Face intent parser for safe, size-specific inventory actions.

The model only extracts what the user asked for. Catalog lookup, current stock,
authorization to mutate, and all inventory writes remain in the application DB.
"""
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


# Railway variables take precedence; this only fills missing values locally.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


HF_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-20b:fastest"

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


def token_configured() -> bool:
    return bool(os.getenv("HF_TOKEN", "").strip())


async def parse_inventory_request(message: str, history: list[dict]) -> dict:
    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        raise AssistantProviderError("Hugging Face assistant is not configured.")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history[-10:]:
        role = item.get("role")
        content = str(item.get("content", ""))[:1000]
        if role in ("user", "assistant") and content.strip():
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message[:500]})

    payload = {
        "model": os.getenv("HF_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "temperature": 0,
        "max_tokens": 220,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "inventory_intent", "strict": True, "schema": INTENT_SCHEMA},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(18.0, connect=5.0)) as client:
            response = await client.post(
                HF_CHAT_URL,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise AssistantProviderError(f"Inference provider returned HTTP {response.status_code}.")
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
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
    except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AssistantProviderError("Inference provider unavailable or returned invalid JSON.") from exc
