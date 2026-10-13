"""Small regression test for the local visual-product scanner endpoint."""
import os
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///./test_visual_match.db"
BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
import main  # noqa: E402


def _photo(colour):
    image = Image.new("RGB", (180, 240), colour)
    # Keep a stable shape so image hashing also has useful signal.
    for x in range(55, 125):
        for y in range(45, 205):
            image.putpixel((x, y), (245, 245, 245))
    output = BytesIO()
    image.save(output, "JPEG")
    return output.getvalue()


def test_visual_match_returns_ranked_product_with_live_stock():
    client = TestClient(main.app)
    client.post("/api/products", json={"sku": "VISUAL-RED", "name": "Red Test Kurta", "variants": [{"size": "M", "initial_stock": 7}]})
    client.post("/api/products", json={"sku": "VISUAL-BLUE", "name": "Blue Test Kurta", "variants": [{"size": "M", "initial_stock": 3}]})
    red = _photo((220, 30, 35))
    blue = _photo((30, 60, 220))
    assert client.post("/api/products/VISUAL-RED/image", files={"file": ("red.jpg", red, "image/jpeg")}).status_code == 200
    assert client.post("/api/products/VISUAL-BLUE/image", files={"file": ("blue.jpg", blue, "image/jpeg")}).status_code == 200
    result = client.post("/api/products/visual-match", files={"file": ("scan.jpg", red, "image/jpeg")})
    assert result.status_code == 200
    first = result.json()["matches"][0]
    assert first["product"]["sku"] == "VISUAL-RED"
    assert first["product"]["variants"][0]["current_stock"] == 7
    assert first["similarity"] >= 95

    # Index outages must not discard an image that was already committed to
    # the product record; photo search can be rebuilt after the DB extension is fixed.
    with patch.object(main.visual_search_service, "index_product_image", side_effect=RuntimeError("index unavailable")):
        uploaded = client.post("/api/products/VISUAL-RED/image", files={"file": ("replacement.jpg", red, "image/jpeg")})
    assert uploaded.status_code == 200
    image_path = client.get("/api/products/VISUAL-RED").json()["image_path"]
    assert image_path == uploaded.json()["image_path"]
    assert client.get(image_path).status_code == 200


if __name__ == "__main__":
    test_visual_match_returns_ranked_product_with_live_stock()
