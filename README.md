# Twins Lady — Hub Inventory System

A production-structured inventory system for the Twins Lady / Kashi hub: live stock
tracked **size-wise** (every size of every design counted separately), a real-catalog
importer for your marketplace seller-listings export, Excel/CSV order deduction that
matches on your actual marketplace SKU codes, QR-code scan-to-recount, bulk physical
stock counting, automatic low-stock alerts, a daily reorder list, a searchable
**Products** view with photo upload and delete, a modern Tailwind CSS interface, and a
designed (not-yet-trained) path to photo-based stock updates.

## 1. Architecture

```
BROWSER  frontend/  (HTML/CSS/JS + Tailwind CSS via CDN, no build step, no framework)
  Live Inventory (filters, search, pagination) - Products (search a design, see every
  size together, upload its photo, delete it) - Scan & Recount (camera QR) -
  Bulk Stock Count - Process Orders - Reorder List - Alerts - Print Labels
        |  fetch() - same-origin REST calls
API LAYER  backend/main.py  (FastAPI, also serves the frontend + /docs)
        |
SERVICE LAYER  backend/services/
  inventory_service   atomic per-variant stock changes - the one place that
                       touches current_stock; every other service calls this
  table_reader         one shared .xlsx / .xls / .csv reader (all-text dtype,
                       so "3202" never becomes 3202.0); used by every uploader
  excel_service         orders -> deduction. Matches each row on whichever
                       code your marketplace export uses (seller SKU code,
                       marketplace SKU code, or your own SKU + Size)
  bulk_stock_service    a full physical count, loaded in one upload -
                       set-based SQL, ~1,900 sizes/second
  alert_service          opens/resolves low-stock alerts (skips sizes never
                       counted - see "not counted" below)
  reorder_service       low-stock sizes -> a vendor-ready reorder list
  barcode_service        QR labels, one per size, to print and stick on a
                       shelf or bin
  ai_inventory_service   Phase 2 - designed, not yet trained (see #6)
        |  SQLAlchemy ORM
DATA LAYER  backend/models.py
  SQLite file today (inventory.db) -> PostgreSQL later via one env var

  Product (a design/style)
    - sku, name, brand, category, image_path, vendor
    - variants: one per size
        Variant (the actual stockable unit)
          - size, variant_code ("392-M"), current_stock
          - reorder_threshold, target_stock_level
          - last_counted_at  (NULL = never counted - see #3)
          - seller_sku_code, marketplace_sku_code  (marketplace aliases)
          -> InventoryTransaction (append-only ledger, never edited)
          -> Alert
          -> OrderLine
          -> ReorderBatchItem
```

**Why this shape:** "SKU 392" alone was never a stockable thing - "392, size M" is.
The schema splits **Product** (the design: name, image, brand - shared across sizes)
from **Variant** (one row per size: the count, the threshold, the QR code). Every
transaction, alert, order line and reorder line points at a Variant, so a low-stock
signal always means one specific size, never a whole design smeared across five sizes.

**Why not Next.js:** Next.js needs its own Node.js server running alongside the
Python one - a second service to deploy, keep in sync, and pay for, with a CORS
boundary between them. For an internal tool with no public pages and no SEO need,
that buys nothing a plain page doesn't already have. Tailwind (via CDN, no build
step) gets the same modern look without any of that - one Python process, one
deployment, matching what's already connected on Railway.

The service layer is the only part of the system that knows business rules. `main.py`
never touches the database directly - it only calls services - so the same logic could
later sit behind a scheduled job or a direct marketplace order-API pull without being
rewritten, only the layer that calls it.

## 2. Setup

```bash
cd inventory-system
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd backend
```

Then pick one starting point:

```bash
# A) A handful of sample products, to try the system immediately
python seed_data.py

# B) Your real catalog, from a marketplace seller-listings export
#    (a copy of the one you sent is already in sample_data/)
python scripts/import_seller_listings.py ../sample_data/seller_listings_export.csv
```

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open **http://localhost:8000/app/** for the dashboard, **http://localhost:8000/docs**
for the auto-generated API reference. `--host 0.0.0.0` means any PC on the same office
network can reach it at your machine's LAN IP (e.g. `http://192.168.1.20:8000/app/`).

**Moving to PostgreSQL later** (multi-user / networked / cloud): set one environment
variable before starting the server -
`export DATABASE_URL="postgresql://user:password@host:5432/inventory"` - no code
changes; `database.py` reads it automatically. This is required for #10 below.

## 3. Products: search a design, see every size, photo, delete

The *Products* tab is the catalog-management view - separate from *Live Inventory*,
which stays focused on flat, filterable stock monitoring across every size. Type a
SKU or product name (2+ characters) and every matching **design** appears as a card:
its photo, every size with its current count in one place, an upload control for the
photo, a delete button for the whole design, and a small × on each size chip to
remove just that one size. Deleting is permanent - it removes the design/size and its
full ledger history, not just the stock number - the delete button asks for
confirmation before doing it (`DELETE /api/products/{sku}` and
`DELETE /api/variants/{variant_code}` under the hood; see `main.py`).

## 3.1 "Not counted" is not the same as "zero"

A marketplace listings export tells you *which* SKUs and sizes exist - it carries no
stock numbers. So every size the importer creates starts as **NOT COUNTED**, not as
zero: its `last_counted_at` is NULL. A never-counted size:
- shows as a distinct grey "Not counted" status on the dashboard (not "Out of stock")
- raises **no alert**
- never appears on the reorder list

A size becomes counted the moment a real number is entered for it - a manual recount,
a Scan & Recount, or a **Bulk Stock Count** upload - and only then does it start
taking part in alerts and reordering. This matters: without it, importing a catalog
of 1,561 designs would instantly raise ~7,800 false "out of stock" alerts and put
every single size on the reorder list at its full restock target, on day one, before
anyone has counted anything.

## 4. Getting from "just imported" to "fully counted"

**Bulk Stock Count** (for the initial count, or a full recount):
1. Dashboard -> *Bulk Stock Count* -> download the count sheet (optionally filtered
   to one brand, or to only the sizes not yet counted).
2. Walk the hub, type each size's real count into the **Count** column. Leave a row
   blank to skip it for now; enter `0` if you counted and found none.
3. Upload the filled sheet. Every filled row sets that size's stock, logs it to the
   ledger, marks it counted, and opens/resolves its alert - in one pass
   (~1,900 sizes/second; the full 7,862-size catalog took 1.4s in testing).

**Scan & Recount** (for one size at a time, day to day): open *Scan & Recount*, point
the camera at a size's printed QR label (or type its SKU + size), see what the system
currently shows, type the real count. *Print Labels* generates and prints those QR
codes, one per size.

## 5. Processing orders

*Process Orders* accepts an Excel **or CSV** file. It matches each row on whichever
code your export actually has - your own `variant_code`, the marketplace's
**seller SKU code**, the marketplace's internal **SKU code**, or a plain **SKU + Size**
pair - so a raw marketplace order export can usually be uploaded as-is, with no
reformatting. If a size hasn't been counted yet, the order line reports insufficient
stock with a clear note ("never counted - count it first") rather than silently
pretending there's unlimited stock.

## 6. Data structures & algorithms - the decisions that matter at ~8,000 rows

- **Batch resolution, not one query per row.** Every order/count upload first collects
  every distinct code in the file, resolves them in a handful of chunked `IN (...)`
  queries into hash maps, then aggregates quantities per resolved variant in one linear
  pass. A 500-row order file with repeated SKUs becomes tens of database writes, not
  hundreds - and a 7,862-row bulk count runs as one set-based `INSERT`/`UPDATE` pair
  instead of 7,862 individual ORM calls (an early version took 140s doing it the naive
  way; the set-based version takes about 1.4s).
- **Stock deduction is atomic at the database level** (`inventory_service.py`), not
  "read in Python, check, write back" - so two staff processing an order and a recount
  at the same moment can't both succeed against the same last few pieces.
- **Idempotent uploads.** Every uploaded order file's SHA-256 hash is stored; the same
  file uploaded twice is rejected instead of silently double-deducting.
- **Alerts are idempotent by rule**, not by remembered state: "should an alert be open
  right now?" is re-derived from current stock vs. threshold every time, so it can
  never drift out of sync with reality.
- **Append-only ledger + cached running balance.** `Variant.current_stock` is a
  fast-read cache; `InventoryTransaction` is the permanent, never-edited audit trail -
  if a number is ever in doubt, it can be rebuilt exactly by summing that variant's
  transactions.
- **Reorder list is an `O(n log n)` sort** by current stock (most urgent first) -
  plenty at this catalog size; no need for a fancier structure.
- **The catalog importer groups by `style id`**, not by the marketplace's own "base
  SKU" column, because on the real export that base-SKU column was constant per style
  only ~72% of the time (verified against the 8,313-row file before writing the
  importer) - `style id` was the one column that was reliably constant every time.
  Two genuine data problems the importer catches rather than crashes on or silently
  mis-imports: two different designs that happened to share a derived SKU (resolved by
  appending the style id), and one design with two rows both claiming the same size
  (kept the first, flagged the rest for you to check in your seller panel).

## 7. Phase 2 - AI photo-to-inventory (designed, not yet trained)

The full design, a **working and tested** nearest-neighbor matching algorithm, and
exact activation steps are in `backend/services/ai_inventory_service.py`. Short
version, confirmed against real photos of your rack during development:

- **Identifying which design is in a photo** - reliable with today's models
  (CLIP-style image embeddings compared against one reference embedding per design).
- **Counting exact pieces in a stack of folded, identical garments from one photo** -
  not reliably solved by computer vision alone; there's no visible boundary between
  piece 3 and piece 4 in a folded stack, and a model-worn "listing" photo looks very
  different from a folded, poly-wrapped warehouse photo of the same design.
- **The workflow this is built for:** camera identifies the design (reliable) -> system
  shows a best-effort suggested count -> one tap confirms or corrects it before it's
  saved. A more reliable alternative worth considering: the QR labels already built for
  Scan & Recount are a more dependable route to an exact count than image-based
  counting ever will be.
- Not wired into a live endpoint: it needs model weights downloaded on a machine with
  internet access (this build environment didn't have that) and 100-200 of your own
  rack photos to fine-tune detection. The matching *algorithm itself* is implemented
  and unit-tested with synthetic data; only the vision model connection is left.

## 8. What's tested

`tests/test_core_flow.py` runs the real API (FastAPI's TestClient, no mocking) through
21 checks: size-wise deduction and alert isolation, insufficient-stock handling (stock
never goes negative), duplicate-file rejection, manual recount, catalog import
(mixed-case normalisation, base-SKU derivation, duplicate-size and SKU-collision
handling, DA/PDL filtering), "not counted" semantics (no false alerts or reorder
lines), search/filter/pagination, order matching on real marketplace codes, CSV
uploads, unreadable-file handling, bulk stock count (including the count-sheet
export -> fill -> upload round trip), and the reorder list's read-only-until-exported
behaviour. Run it yourself:

```bash
pip install -r requirements.txt
python tests/test_core_flow.py
```

## 9. Natural next steps (not built, in priority order)

1. **Login/auth** - currently anyone on the network can use it. Fine for a small
   trusted office LAN; add before exposing this beyond that.
2. **AMU_NAVADIYA.xlsx** (your vendor-costing working sheet) wasn't auto-imported -
   its structure (multiple vendors' data side-by-side per sheet, multi-SKU cells,
   free-text status notes) is too irregular to import reliably without risking wrong
   data. It's a plausible source for a `Vendor` mapping per product later if wanted.
3. **Vendor-grouped reorder export** - one sheet per vendor instead of one combined
   list, if you want to send each party only their own items.
4. **Marketplace order-API integration** - replace the manual export/upload step with
   a direct pull; the service layer underneath doesn't need to change, only how order
   rows arrive.
5. **WhatsApp/SMS alert delivery** - push low-stock alerts out instead of requiring
   someone to open the Alerts tab.
6. **Phase 2 AI activation** - see #7.

## 10. Putting it online (GitHub + Railway or Render, not Vercel)

**Why not Vercel:** Vercel runs everything as short-lived serverless functions with no
persistent local disk. `inventory.db` would be wiped on every cold start - your real
catalog would not survive between requests. This isn't a configuration problem to work
around; it's what "serverless" means. **Railway** and **Render** are the natural fit
instead: both run this app exactly like `uvicorn` does on your PC right now, both add a
managed PostgreSQL database in a couple of clicks, and both redeploy automatically
every time you push to GitHub. Either works the same way; Railway's free tier is
slightly more generous as of writing.

**1. Push this to GitHub** (a git repository, with a first commit, is already prepared
in this folder):
```bash
# create a new EMPTY repository at https://github.com/new first, then:
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

**2. On Railway** ([railway.app](https://railway.app), sign in with GitHub):
1. *New Project -> Deploy from GitHub repo* -> pick this repo.
2. *New -> Database -> Add PostgreSQL* in the same project. Railway sets a
   `DATABASE_URL` variable on it automatically - open the web service's *Variables*
   tab and reference that same value there too (Railway can do this with a variable
   reference, or just copy the connection string across).
3. On the web service, set the **Start Command** explicitly (don't rely on
   auto-detection): `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Deploy. Then run the catalog import once against the live database - easiest from
   your own machine, pointed at the live DB:
   ```bash
   export DATABASE_URL="<the same postgres URL from Railway>"
   cd backend && python scripts/import_seller_listings.py ../sample_data/seller_listings_export.csv
   ```
   (`seed_data.py` instead, if you'd rather start with sample data.)

**Render** ([render.com](https://render.com)) is nearly identical: *New -> Web
Service* from the GitHub repo, add a *PostgreSQL* instance from the dashboard, set the
same Start Command, add `DATABASE_URL` as an environment variable, deploy, then run
the import the same way.

**What doesn't move over automatically:** anything already sitting in your **local**
`inventory.db` (if you've already run a bulk count on your PC) stays on your PC -
only the *code* goes through git. If you've already counted real stock locally before
going live, say so and the count-sheet export/import (#4) is also the easiest way to
carry that data across: export a count sheet from the local app, upload it to the
live one. Product images saved via the image-upload endpoint also live on local disk
today (`backend/static/product_images/`) - fine for now, but on Railway/Render that
folder resets on redeploy unless you attach a persistent volume or move image storage
to something like S3/Cloudinary - a reasonable next step once real product photos are
in the system.
