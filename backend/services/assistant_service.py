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

SYSTEM_PROMPT = """You are Disha, a warm, capable conversational assistant. Understand Hindi, Hinglish, and English.
Return only the required JSON object. Never invent a product, SKU, size, or stock value.
Allowed intent: stock_add, stock_set, stock_check, product_search, alerts_summary, reorder_summary, pending_orders, recent_activity, product_create, product_update, product_archive, product_restore, order_dispatch, daily_sales, app_status, conversation, help, other.
Examples:
- 'black wali M size mein 5 piece add karo' => stock_add, product_query='black', size='M', quantity=5, operation='add'.
- 'ZIA mustard XL size ka stock 12 set kar do' => stock_set, product_query='ZIA mustard', size='XL', quantity=12, operation='set'.
- 'medium size mein kitna stock hai' => stock_check; leave product_query empty if none was named.
- 'aaj kitni sale hui' or 'daily sales report' => daily_sales.
- 'app ka status batao', 'overall inventory kaisi hai' => app_status. Use only when the user asks for an overall dashboard/inventory summary, not one product's stock.
- 'product search karo', 'is design ke sizes dikhao', 'black wale products dhoondo' => product_search; put the product/SKU/color in product_query.
- 'alerts batao', 'kaunse designs low stock hain' => alerts_summary; product_query can narrow the alert search.
- 'kya restock karna hai', 'reorder list dikhao' => reorder_summary; product_query can narrow it.
- 'pending orders kitne hain', 'orders ka status batao' => pending_orders.
- 'recent stock changes/history dikhao' => recent_activity; product_query can narrow it.
- Product creation => product_create. Extract only explicitly provided sku, product_name, category, and comma-separated sizes. Never invent missing SKU/name/sizes.
- Product metadata changes => product_update; product_query identifies the existing design; fill only explicitly requested new_sku, new_name, or new_category.
- 'move design to trash/delete design' => product_archive; product_query identifies the design. 'restore design' => product_restore.
- 'dispatch batch 123' => order_dispatch and order_id=123. Never dispatch without an explicitly identified batch ID.
- Greetings, thanks, 'who are you', casual chat, and general questions not about inventory => conversation.
- Questions about how to use Disha or the app => help.
- Requests outside inventory that are not covered above => other.
- If user says only '5 aur add karo', use recent chat context for product/size; do not guess missing fields.
Use product_query only for the design/SKU/color/name. Normalize spoken sizes such as medium to M, double XL to XXL, and free size to FREE SIZE.
For stock_add, quantity means units to add. For stock_set, it means the final physical count.
Do not put the product's size, quantity, action words, or generic words like 'design', 'product', 'stock' in product_query.

Also return `reply`:
- For conversation, help, or other, write a natural, useful answer as Disha. Match the user's language and tone; use recent chat history so follow-up conversation makes sense. Be friendly and conversational like a general chat assistant, not a fixed FAQ. Answer ordinary non-inventory questions directly when you can; be honest when unsure. Keep most replies to 1–3 short sentences and ask one clear follow-up only when it helps. Do not repeat an earlier greeting, recap, or answer unless the user asks; don't steer every topic back to inventory.
- For app_status and data/action intents (stock_add, stock_set, stock_check, product_search, alerts_summary, reorder_summary, pending_orders, recent_activity, product_create, product_update, product_archive, product_restore, order_dispatch, daily_sales), leave reply empty; the application will validate against live data and present a confirmation proposal.
- Never claim you changed stock. The application handles stock actions separately and requires confirmation.
- Disha can answer from the live app catalog, size-level inventory, alerts, reorder shortages, pending orders, stock history, daily dispatched sales, and overall dashboard status. Stock changes, product create/edit/archive/restore, and order dispatch are performed only through an app-provided action card after explicit user confirmation. Never say an action succeeded before the app confirms it. Do not claim arbitrary database, file-upload, settings, or code access.
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
