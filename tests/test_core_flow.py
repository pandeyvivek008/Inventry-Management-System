"""
Run from the project root:   python tests/test_core_flow.py
Real API, real database (a throw-away SQLite file), no mocking.
"""
import os
import sys
import tempfile
from io import BytesIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ["DATABASE_URL"] = "sqlite:///./test_inventory.db"
if os.path.exists("test_inventory.db"):
    os.remove("test_inventory.db")

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402

import main  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import ReorderBatch  # noqa: E402
from scripts.import_seller_listings import import_csv  # noqa: E402

client = TestClient(main.app)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def make_xlsx(headers, rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(list(r))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def post_orders(content: bytes, filename: str):
    mime = "text/csv" if filename.endswith(".csv") else XLSX
    return client.post("/api/orders/upload", files={"file": (filename, content, mime)})


def expect(condition, message):
    if not condition:
        raise AssertionError("FAIL: " + message)
    print("  ok:", message)


def reorder_batches() -> int:
    db = SessionLocal()
    try:
        return db.query(ReorderBatch).count()
    finally:
        db.close()


# ====================================================================== #
print("1. Create a product with several size variants")
r = client.post("/api/products", json={
    "sku": "T1", "name": "Test Kurta A",
    "variants": [
        {"size": "S", "initial_stock": 10, "reorder_threshold": 5},
        {"size": "M", "initial_stock": 6, "reorder_threshold": 5},
        {"size": "L", "initial_stock": 2, "reorder_threshold": 5},
    ],
})
expect(r.status_code == 200, "create product T1 with 3 size variants")
body = r.json()
expect(len(body["variants"]) == 3 and body["total_stock"] == 18, "3 variants, total_stock 10+6+2 = 18")
by_size = {v["size"]: v for v in body["variants"]}
expect(by_size["S"]["variant_code"] == "T1-S", "variant_code is SKU-Size")
expect(by_size["S"]["status"] == "OK", "T1-S status OK (10 > 5)")
expect(by_size["L"]["status"] == "LOW", "T1-L status LOW (2 <= 5): typed-in starting stock counts as counted")

r = client.post("/api/products", json={"sku": "T2", "name": "Test Suit B",
                                        "variants": [{"size": "M", "initial_stock": 6, "reorder_threshold": 5}]})
expect(r.status_code == 200, "create product T2")
r = client.post("/api/products", json={"sku": "T1", "name": "dup", "variants": [{"size": "S"}]})
expect(r.status_code == 400, "duplicate product SKU rejected")

# ====================================================================== #
print("2. Order upload deducts SIZE-WISE stock (repeated rows aggregated, sizes never cross-contaminate)")
excel = make_xlsx(["SKU", "Size", "Quantity"], [
    ("t1", "s", 3), ("T2", "M", 4), ("t1", "S", 4), ("t1", "M", 2), ("UNKNOWN99", "M", 1),
])
r = post_orders(excel, "orders.xlsx")
expect(r.status_code == 200, "order upload succeeds")
body = r.json()
expect(body["unique_skus"] == 4, f"4 unique variants/codes from 5 rows, got {body['unique_skus']}")
expect(body["fulfilled_count"] == 3 and body["not_found_count"] == 1, "3 fulfilled, UNKNOWN99 not found")
t1s = next(l for l in body["lines"] if l["sku"] == "T1" and l["size"] == "S")
expect(t1s["qty_ordered"] == 7 and t1s["remaining_stock"] == 3, "T1-S: rows 3+4 aggregated to 7, 10-7 = 3 left")
t1m = next(l for l in body["lines"] if l["sku"] == "T1" and l["size"] == "M")
expect(t1m["qty_ordered"] == 2 and t1m["remaining_stock"] == 4, "T1-M: 6-2 = 4, unaffected by T1-S")

# ====================================================================== #
print("3. Alerts fire per VARIANT (size-specific)")
codes = [a["variant_code"] for a in client.get("/api/alerts").json()]
expect({"T1-S", "T1-L", "T1-M", "T2-M"} <= set(codes), f"T1-S, T1-L, T1-M, T2-M all alerted, got {codes}")

# ====================================================================== #
print("4. Insufficient stock is reported, never negative")
r = post_orders(make_xlsx(["SKU", "Size", "Quantity"], [("T1", "S", 999)]), "orders2.xlsx")
expect(r.json()["insufficient_count"] == 1, "over-large order flagged INSUFFICIENT_STOCK")
t1 = next(p for p in client.get("/api/products").json() if p["sku"] == "T1")
expect(next(v for v in t1["variants"] if v["size"] == "S")["current_stock"] == 3, "stock unchanged (3) after failed deduction")

# ====================================================================== #
print("5. Same file twice is rejected, not double-deducted")
same = make_xlsx(["SKU", "Size", "Quantity"], [("T2", "M", 1)])
expect(post_orders(same, "same.xlsx").status_code == 200, "first upload succeeds")
expect(post_orders(same, "same.xlsx").status_code == 409, "identical re-upload rejected (409)")

# ====================================================================== #
print("6. Manual recount by variant_code updates stock and resolves the alert")
r = client.post("/api/inventory/adjust", json={"variant_code": "T1-S", "new_count": 20, "note": "physical recount"})
expect(r.json()["current_stock"] == 20 and r.json()["status"] == "OK", "T1-S recounted to 20, status OK")
codes = [a["variant_code"] for a in client.get("/api/alerts").json()]
expect("T1-S" not in codes and "T1-M" in codes, "T1-S alert resolved; T1-M untouched (size isolation)")
expect(client.post("/api/inventory/adjust", json={"variant_code": "T1-S", "new_count": -1}).status_code == 400,
       "negative count rejected")

# ====================================================================== #
print("7. Reorder list: only counted variants at/below threshold, size shown")
reorder = {i["variant_code"]: i for i in client.get("/api/reorder-list").json()}
expect("T1-S" not in reorder and "T1-L" in reorder and reorder["T1-L"]["size"] == "L", "T1-L listed with its size, T1-S not")

# ====================================================================== #
print("8. Variant lookup (scan screen)")
r = client.get("/api/variants/t1-l")
expect(r.status_code == 200 and r.json()["sku"] == "T1" and r.json()["counted"] is True, "lookup is case-insensitive")
expect(client.get("/api/variants/NOPE-X").status_code == 404, "unknown code -> 404")

print("9. Audit trail per variant")
expect(len(client.get("/api/variants/T1-S/transactions").json()) >= 3, "T1-S ledger: initial, deduction, recount")

print("10. Reorder export is a real file")
r = client.get("/api/reorder-list/export")
expect(r.status_code == 200 and len(r.content) > 1000, "export xlsx has content")

print("11. QR labels")
r = client.get("/api/variants/T1-L/label")
expect(r.status_code == 200 and r.headers["content-type"] == "image/png" and len(r.content) > 500, "single label PNG")
r = client.get("/api/labels/sheet", params={"variant_codes": "T1-S,T1-M,T1-L"})
expect(r.status_code == 200 and len(r.content) > 1000, "label sheet PNG")

# ====================================================================== #
print("12. Real-catalog import: mixed-case codes normalised, sizes start NOT COUNTED")
csv_rows = [
    # style, name, size, brand, article, van, seller sku code, sku code, status
    (1001, "TWINS LADY Kurta Set Pink", "M", "TWINS LADY", "Kurta Sets", "Twins_Test_Pink", "Twins_Test_Pink_M", "TWLAKASS900001", "A"),
    (1001, "TWINS LADY Kurta Set Pink", "L", "TWINS LADY", "Kurta Sets", "Twins_Test_Pink", "Twins_Test_Pink_L", "TWLAKASS900002", "A"),
    (1002, "GIRL CNTRL Dress", "M", "GIRL CNTRL", "Dresses", "SW999_M", "SW999_M", "TWLAKASS900003", "A"),   # van repeats the size
    (1002, "GIRL CNTRL Dress", "L", "GIRL CNTRL", "Dresses", "SW999_L", "SW999_L", "TWLAKASS900004", "A"),
    (1003, "TWINS LADY Dup", "M", "TWINS LADY", "Kurtas", "DUP", "DUP_M", "TWLAKASS900005", "A"),
    (1003, "TWINS LADY Dup", "M", "TWINS LADY", "Kurtas", "DUP", "DUP_L", "TWLAKASS900006", "A"),            # same size twice
    (1004, "TWINS LADY Old", "S", "TWINS LADY", "Kurtas", "OLD", "OLD_S", "TWLAKASS900007", "DA"),            # deactive
]
csv_df = pd.DataFrame(csv_rows, columns=["style id", "style name", "size", "brand", "article type",
                                          "van", "seller sku code", "sku code", "listing status"])
csv_path = os.path.join(tempfile.mkdtemp(), "listings.csv")
csv_df.to_csv(csv_path, index=False)
res = import_csv(csv_path)
expect(res["products_created"] == 3 and res["variants_created"] == 5, f"3 active products / 5 sizes imported, got {res['products_created']}/{res['variants_created']}")
expect(len(res["duplicate_size_rows"]) == 1, "duplicate-size row in the source is flagged, not a crash")
expect(client.get("/api/variants/twins_test_pink-m").status_code == 200, "mixed-case source SKU is found via lowercase lookup")
lk = client.get("/api/variants/TWINS_TEST_PINK-M").json()
expect(lk["counted"] is False and lk["current_stock"] == 0, "imported size reports counted = False")
expect(client.get("/api/variants/SW999-M").status_code == 200, "size-suffixed van collapsed to base SKU SW999")
expect(client.get("/api/variants/OLD-S").status_code == 404, "deactive listing skipped by default")

print("13. Not-counted sizes never raise alerts or reorder lines")
codes = {a["variant_code"] for a in client.get("/api/alerts").json()}
expect(not any(c.startswith(("TWINS_TEST_PINK", "SW999", "DUP")) for c in codes), "no alerts for never-counted sizes")
reorder_codes = {i["variant_code"] for i in client.get("/api/reorder-list").json()}
expect(not any(c.startswith(("TWINS_TEST_PINK", "SW999", "DUP")) for c in reorder_codes), "no reorder lines for never-counted sizes")
s = client.get("/api/summary").json()
expect(s["uncounted"] == 5 and s["variants"] == 9 and s["products"] == 5, f"summary: 5 uncounted of 9 variants, 5 products, got {s}")

print("14. Filtering, search and pagination")
page = client.get("/api/variants", params={"status": "UNCOUNTED", "limit": 2, "offset": 1}).json()
expect(page["total"] == 5 and len(page["items"]) == 2 and page["offset"] == 1, "UNCOUNTED page: total 5, 2 items from offset 1")
expect(client.get("/api/variants", params={"q": "twins_test"}).json()["total"] == 2, "search by SKU text")
expect(client.get("/api/variants", params={"brand": "GIRL CNTRL"}).json()["total"] == 2, "brand filter")
expect("GIRL CNTRL" in client.get("/api/brands").json(), "brand list")
sizes = [i["size"] for i in client.get("/api/variants", params={"q": "T1-"}).json()["items"]]
expect(sizes == ["S", "M", "L"], f"sizes sorted S, M, L (not alphabetical), got {sizes}")
expect(client.get("/api/variants", params={"status": "BOGUS"}).status_code == 400, "bad status -> 400")

# ====================================================================== #
print("15. Order sheet matched on REAL marketplace codes (no Size column), never-counted -> clear note")
orders = make_xlsx(["Seller SKU Code", "Quantity"], [
    ("Twins_Test_Pink_M", 2),      # seller code, mixed case
    ("TWLAKASS900002", 1),        # marketplace's internal code for Twins_Test_Pink_L
    ("SW999_M", 1),
])
r = post_orders(orders, "myntra_orders.xlsx")
expect(r.status_code == 200, "order sheet with only a seller-sku column accepted")
body = r.json()
expect(body["not_found_count"] == 0 and body["insufficient_count"] == 3, "all 3 codes resolved to a size (insufficient because never counted)")
expect(all("never counted" in (l["note"] or "") for l in body["lines"]), "each line says the stock was never counted")
expect(body["new_alerts"] == [], "no alerts from never-counted sizes")

print("16. CSV order file, base SKU + Size columns")
csv_orders = b"SKU,Size,Qty\ntwins_test_pink,m,1\n"
r = post_orders(csv_orders, "orders.csv")
expect(r.status_code == 200 and r.json()["lines"][0]["product_name"] == "TWINS LADY Kurta Set Pink", "CSV order matched by SKU + Size")

print("17. Unreadable file is a clean 400, not a crash")
r = post_orders(b"this is not an excel file", "bad.xlsx")
expect(r.status_code == 400 and "Could not read" in r.json()["detail"], "garbage upload -> 400 with a readable message")

print("17b. No Quantity column: an order/shipment manifest where every row IS one piece")
manifest = make_xlsx(["Seller SKU Code"], [("Twins_Test_Pink_M",), ("Twins_Test_Pink_M",), ("SW999_L",)])
r = post_orders(manifest, "manifest.xlsx")
expect(r.status_code == 200, "file with no quantity column is accepted, not rejected")
mbody = r.json()
expect(mbody["file_note"] and "1 piece" in mbody["file_note"], f"response says qty defaulted to 1, got {mbody['file_note']!r}")
pink_line = next(l for l in mbody["lines"] if l["sku"] == "TWINS_TEST_PINK")
expect(pink_line["qty_ordered"] == 2, f"two repeated manifest rows for the same size = qty 2, got {pink_line['qty_ordered']}")

print("17c. A real quantity column still overrides the 1-per-row default (no regression)")
with_qty = make_xlsx(["SKU", "Size", "Qty"], [("T2", "M", 3)])
r = post_orders(with_qty, "with_qty.xlsx")
expect(r.json()["file_note"] is None, "file_note absent when a quantity column exists")
expect(r.json()["lines"][0]["qty_ordered"] == 3, "real quantity column value used, not defaulted to 1")

print("17d. Legacy .xls (old binary Excel format) is readable")
import xlwt
xls_buf = BytesIO()
xls_wb = xlwt.Workbook()
xls_ws = xls_wb.add_sheet("Orders")
for c, h in enumerate(["SKU", "Size", "Quantity"]):
    xls_ws.write(0, c, h)
xls_ws.write(1, 0, "T2"); xls_ws.write(1, 1, "M"); xls_ws.write(1, 2, 2)
xls_wb.save(xls_buf)
r = client.post("/api/orders/upload", files={"file": ("orders.xls", xls_buf.getvalue(), "application/vnd.ms-excel")})
expect(r.status_code == 200, f"legacy .xls file is read correctly, got {r.status_code}: {r.text[:200]}")

print("17e. A real .xlsx uploaded with a misleading/missing extension is still sniffed correctly")
mislabeled = make_xlsx(["SKU", "Size", "Quantity"], [("T2", "M", 777)])  # distinct content - must not hash-collide with test 5's file
r = client.post("/api/orders/upload", files={"file": ("export", mislabeled, "application/octet-stream")})
expect(r.status_code == 200, f"content is sniffed by its real bytes when the extension is missing/wrong, got {r.status_code}: {r.text[:200]}")

# ====================================================================== #
print("18. Bulk stock count: set, skip blanks, report unknown codes, ignore duplicate rows")
counts = make_xlsx(["Variant Code", "Count"], [
    ("TWINS_TEST_PINK-M", 10),
    ("TWINS_TEST_PINK-L", 3),
    ("SW999-M", None),               # blank -> skipped
    ("NOPE-X", 4),                   # unknown -> reported
    ("TWINS_TEST_PINK-M", 99),       # duplicate row -> first wins
])
r = client.post("/api/inventory/bulk-set", files={"file": ("counts.xlsx", counts, XLSX)})
expect(r.status_code == 200, "bulk-set accepted")
b = r.json()
expect(b["updated_count"] == 2 and b["not_found_count"] == 1 and b["skipped_blank_count"] == 1 and b["duplicate_rows_ignored"] == 1,
       f"2 updated, 1 unknown, 1 blank, 1 duplicate ignored, got {b}")
lk = client.get("/api/variants/TWINS_TEST_PINK-M").json()
expect(lk["counted"] is True and lk["current_stock"] == 10, "TWINS_TEST_PINK-M counted, stock 10 (duplicate 99 ignored)")
expect(client.get("/api/variants/SW999-M").json()["counted"] is False, "blank row left SW999-M not counted")
codes = {a["variant_code"] for a in client.get("/api/alerts").json()}
expect("TWINS_TEST_PINK-L" in codes and "TWINS_TEST_PINK-M" not in codes, "counted low size alerts (3 <= 5); size with 10 does not")
reorder = {i["variant_code"]: i for i in client.get("/api/reorder-list").json()}
expect(reorder["TWINS_TEST_PINK-L"]["required_qty"] == 17, "reorder qty = target 20 - stock 3 = 17")
expect(client.get("/api/summary").json()["uncounted"] == 3, "summary: 3 sizes still not counted")

print("19. After counting, an order sheet deducts from the real count")
r = post_orders(make_xlsx(["Seller SKU Code", "Quantity"], [("TWINS_TEST_PINK_M", 4)]), "orders_after_count.xlsx")
line = r.json()["lines"][0]
expect(line["status"] == "FULFILLED" and line["remaining_stock"] == 6, "10 - 4 = 6 remaining")

print("20. Count sheet export -> fill -> upload round trip")
r = client.get("/api/inventory/count-sheet", params={"only_uncounted": "true"})
expect(r.status_code == 200, "count sheet downloads")
wb = load_workbook(BytesIO(r.content))
ws = wb.active
rows = list(ws.iter_rows(values_only=True))
expect(rows[0][0] == "Variant Code" and rows[0][6] == "Count", "header row as documented")
expect(len(rows) - 1 == 3 and all(row[5] is None for row in rows[1:]), "only the 3 uncounted sizes, System Stock blank")
for i in range(2, ws.max_row + 1):
    ws.cell(row=i, column=7, value=i)     # "count" each size
buf = BytesIO()
wb.save(buf)
r = client.post("/api/inventory/bulk-set", files={"file": ("filled.xlsx", buf.getvalue(), XLSX)})
expect(r.json()["updated_count"] == 3, "filled sheet uploads back: 3 updated")
expect(client.get("/api/summary").json()["uncounted"] == 0, "nothing left uncounted")

print("21. Viewing the reorder list is read-only; exporting saves one snapshot")
before = reorder_batches()
client.get("/api/reorder-list")
client.get("/api/reorder-list")
expect(reorder_batches() == before, "opening the list twice created no snapshots")
client.get("/api/reorder-list/export")
expect(reorder_batches() == before + 1, "export saved exactly one snapshot")

print("22. Delete a whole product: sizes, ledger, alerts all cleaned up, nothing orphaned")
client.post("/api/products", json={"sku": "DEL1", "name": "Delete Me", "variants": [
    {"size": "S", "initial_stock": 3, "reorder_threshold": 5}, {"size": "M", "initial_stock": 20},
]})
expect({a["variant_code"] for a in client.get("/api/alerts").json()} >= {"DEL1-S"}, "DEL1-S alerted before delete")
r = client.delete("/api/products/DEL1")
expect(r.status_code == 200 and r.json()["sizes_removed"] == 2, f"delete reports 2 sizes removed, got {r.json()}")
expect(client.get("/api/variants/DEL1-S").status_code == 404, "DEL1-S gone")
expect(not any(a["sku"] == "DEL1" for a in client.get("/api/alerts").json()), "DEL1's alert cleaned up, not orphaned")
expect(not any(p["sku"] == "DEL1" for p in client.get("/api/products").json()), "DEL1 gone from product list")
expect(client.delete("/api/products/DEL1").status_code == 404, "deleting again -> 404, not a crash")

print("23. Delete a single size: the rest of the product is untouched")
client.post("/api/products", json={"sku": "DEL2", "name": "Partial Delete", "variants": [
    {"size": "S", "initial_stock": 5}, {"size": "M", "initial_stock": 7},
]})
r = client.delete("/api/variants/DEL2-S")
expect(r.status_code == 200 and r.json()["product_also_deleted"] is False, "DEL2-S removed, product kept (M remains)")
expect(client.get("/api/variants/DEL2-M").status_code == 200, "DEL2-M still there")
r = client.delete("/api/variants/DEL2-M")
expect(r.json()["product_also_deleted"] is True, "removing the LAST size also removes the now-empty product")
expect(not any(p["sku"] == "DEL2" for p in client.get("/api/products").json()), "DEL2 gone entirely")

print("\nALL CHECKS PASSED")
