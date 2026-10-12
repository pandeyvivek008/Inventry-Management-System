"""Disha service-flow checks using an isolated SQLite database."""
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
_test_db = os.path.join(tempfile.gettempdir(), f"disha_flow_{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db.replace(os.sep, '/')}"

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

client = TestClient(main.app)
main._assistant_rate_limit = lambda _client_ip: True
state = {}


async def fake_parser(_message, _history):
    defaults = {
        "intent": "conversation", "product_query": "", "size": "", "quantity": None,
        "operation": "add", "sku": "", "product_name": "", "category": "",
        "new_sku": "", "new_name": "", "new_category": "", "sizes": "",
        "order_id": None, "reply": "Namaste! Main Disha hoon.",
    }
    return {**defaults, **state}


async def fake_transcribe(audio, filename, content_type):
    check(audio == b"test audio" and filename == "voice.webm" and content_type == "audio/webm",
          "transcription endpoint forwards bounded browser audio")
    return "Blue kurta size M mein 3 add karo"


async def fake_speech(text):
    check("Disha" in text, "speech endpoint receives assistant text")
    return b"fake-mp3"


main.assistant_service.api_key_configured = lambda: True
main.assistant_service.parse_inventory_request = fake_parser
main.assistant_service.transcribe_audio = fake_transcribe
main.assistant_service.synthesize_speech = fake_speech


def check(ok, description):
    if not ok:
        raise AssertionError(description)
    print("  ok:", description)


def ask(message="test", **intent):
    state.clear()
    state.update(intent)
    response = client.post("/api/assistant/chat", json={"message": message})
    check(response.status_code == 200, f"chat `{message}` returned 200")
    return response.json()


print("Disha read-only and conversational intents")
transcribed = client.post("/api/assistant/transcribe", files={
    "file": ("voice.webm", b"test audio", "audio/webm")
})
check(transcribed.status_code == 200 and transcribed.json()["text"] == "Blue kurta size M mein 3 add karo",
      "Hindi/English voice transcript endpoint")
spoken = client.post("/api/assistant/speech", json={"text": "Hello, main Disha hoon"})
check(spoken.status_code == 200 and spoken.content == b"fake-mp3" and spoken.headers["content-type"].startswith("audio/mpeg"),
      "spoken response endpoint returns audio without exposing provider keys")
created = client.post("/api/products", json={
    "sku": "DISHA-TEST-01", "name": "Blue Kurta", "category": "Kurta Set",
    "variants": [{"size": "S", "initial_stock": 12}, {"size": "M", "initial_stock": 4}],
})
check(created.status_code == 200, "isolated Disha catalog created")
conversation = ask("hello")
check(conversation["status"] == "reply", "normal conversation reply")
result = ask("stock", intent="stock_check", product_query="Blue Kurta", size="S")
check(result["stock"]["current_stock"] == 12, "stock query reads the live catalog")
result = ask("design", intent="product_search", product_query="Blue Kurta")
check(len(result["products"][0]["sizes"]) == 2, "design query returns every size")
result = ask("all sizes", intent="product_search", product_query="Blue Kurta")
check(result["status"] == "product_search" and len(result["products"][0]["sizes"]) == 2,
      "all-size follow-up returns the full design")
for action in ("alerts_summary", "reorder_summary", "pending_orders", "recent_activity", "daily_sales", "app_status"):
    check(ask(action, intent=action)["status"] == action, f"{action} summary")

print("Disha confirmation and CRUD flows")
proposal = ask("add 3", intent="stock_add", product_query="Blue Kurta", size="M", quantity=3)
check(proposal["status"] == "confirm_stock" and proposal["action"]["proposed_stock"] == 7,
      "stock add proposes a total and awaits confirmation")
current = client.get("/api/products/DISHA-TEST-01").json()
check(next(v for v in current["variants"] if v["size"] == "M")["current_stock"] == 4,
      "stock unchanged before confirmation")
updated = client.post("/api/inventory/adjust", json={
    "variant_code": proposal["action"]["variant_code"], "new_count": proposal["action"]["proposed_stock"],
    "expected_current_stock": proposal["action"]["current_stock"], "note": "isolated Disha test",
})
check(updated.status_code == 200 and updated.json()["current_stock"] == 7, "confirmed size-only stock update")
proposal = ask("set 9", intent="stock_set", product_query="Blue Kurta", size="M", quantity=9)
check(proposal["status"] == "confirm_stock" and proposal["action"]["proposed_stock"] == 9,
      "stock set proposes the final physical count")
updated = client.post("/api/inventory/adjust", json={
    "variant_code": proposal["action"]["variant_code"], "new_count": proposal["action"]["proposed_stock"],
    "expected_current_stock": proposal["action"]["current_stock"], "note": "isolated Disha set test",
})
check(updated.status_code == 200 and updated.json()["current_stock"] == 9, "confirmed stock set succeeds")
stale = client.post("/api/inventory/adjust", json={
    "variant_code": proposal["action"]["variant_code"], "new_count": 8, "expected_current_stock": 4,
})
check(stale.status_code == 409, "stale stock confirmation rejected")
check(ask("missing", intent="stock_add", product_query="Blue Kurta", size="XL", quantity=2)["status"] == "missing_size",
      "missing size is safely explained")

proposal = ask("create", intent="product_create", sku="DISHA-NEW-02", product_name="Green Suit", sizes="S M")
check(proposal["status"] == "confirm_action", "product create requires confirmation")
created = client.post("/api/products", json={
    "sku": proposal["action"]["sku"], "name": proposal["action"]["name"],
    "variants": [{"size": size, "initial_stock": 0} for size in proposal["action"]["sizes"]],
})
check(created.status_code == 200, "confirmed product create succeeds")

proposal = ask("rename", intent="product_update", product_query="Blue Kurta", new_name="Blue Kurta Updated")
changed = client.patch(f"/api/products/{proposal['action']['sku']}", json=proposal["action"]["changes"])
check(proposal["status"] == "confirm_action" and changed.status_code == 200, "confirmed product metadata update")
proposal = ask("trash", intent="product_archive", product_query="Blue Kurta Updated")
archived = client.delete(f"/api/products/{proposal['action']['sku']}")
check(proposal["status"] == "confirm_action" and archived.status_code == 200, "confirmed product archive")
proposal = ask("restore", intent="product_restore", product_query="Blue Kurta Updated")
restored = client.post(f"/api/products/{proposal['action']['sku']}/restore")
check(proposal["status"] == "confirm_action" and restored.status_code == 200, "confirmed product restore")

order = client.post("/api/orders/upload", files={
    "file": ("disha_test.csv", b"SKU,Size,Quantity\nDISHA-TEST-01,S,2\n", "text/csv")
})
batch_id = order.json()["order_upload_id"]
proposal = ask("dispatch", intent="order_dispatch", order_id=batch_id)
dispatched = client.post(f"/api/orders/{batch_id}/dispatch")
check(proposal["status"] == "confirm_action" and dispatched.status_code == 200
      and dispatched.json()["dispatched_units"] == 2, "confirmed dispatch on isolated test data")
print("ALL DISHA FLOW CHECKS PASSED")
