const API = "/api";
const STANDARD_SIZES = ["S", "M", "L", "XL", "XXL"];
const PAGE_SIZE = 100;
const LIST_CAP = 500;

const $ = (id) => document.getElementById(id);
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function debounce(fn, ms = 300) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

// ---------------- Toast ----------------
let toastTimer;
function showToast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

// ---------------- Confirm modal ----------------
let confirmAction = null;
$("confirm-cancel").addEventListener("click", () => { $("confirm-modal").hidden = true; });
$("confirm-ok").addEventListener("click", async () => {
  $("confirm-modal").hidden = true;
  if (confirmAction) await confirmAction();
});
function askConfirm(title, body, action) {
  $("confirm-title").textContent = title;
  $("confirm-body").textContent = body;
  confirmAction = action;
  $("confirm-modal").hidden = false;
}

// ---------------- View switching ----------------
document.querySelectorAll(".rail-link[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

function switchView(name) {
  document.querySelectorAll(".rail-link").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view-${name}`));
  if (name === "dashboard") { loadSummary(); loadInventory(); }
  if (name === "reorder") loadReorderList();
  if (name === "alerts") loadAlerts();
  if (name !== "scan") stopCamera();
}

// ---------------- Dashboard: summary chips, filters, pagination ----------------
const inv = { q: "", brand: "", status: "", offset: 0, total: 0 };
const STATUS_LABEL = { UNCOUNTED: "Not counted", OUT: "Out of stock", LOW: "Low stock", OK: "In stock" };

async function loadSummary() {
  try {
    const s = await getJSON(`${API}/summary`);
    const chips = [
      { key: "", label: "All sizes", n: s.variants },
      { key: "UNCOUNTED", label: "Not counted", n: s.uncounted },
      { key: "OUT", label: "Out of stock", n: s.out },
      { key: "LOW", label: "Low stock", n: s.low },
      { key: "OK", label: "In stock", n: s.ok },
    ];
    const colorFor = { OUT: "text-red-600", LOW: "text-amber-600", OK: "text-emerald-600", UNCOUNTED: "text-slate-400", "": "text-slate-900" };
    $("summary-row").innerHTML = chips.map((c) => `
      <button type="button" class="chip ${inv.status === c.key ? "is-on" : ""}" data-status="${c.key}">
        <span class="chip-n ${colorFor[c.key]}">${c.n.toLocaleString()}</span><span class="chip-l">${c.label}</span>
      </button>`).join("") + `<div class="flex items-center text-xs text-slate-500 px-2">${s.products.toLocaleString()} designs</div>`;
    $("summary-row").querySelectorAll(".chip").forEach((btn) => btn.addEventListener("click", () => {
      inv.status = btn.dataset.status; $("status-filter").value = inv.status; inv.offset = 0;
      loadSummary(); loadInventory();
    }));
    setAlertBadge(s.open_alerts);
  } catch (e) { /* decorative - table still works without it */ }
}

function setAlertBadge(n) {
  const badge = $("alert-count");
  badge.textContent = n;
  badge.hidden = !(n > 0);
}

async function loadBrands() {
  try {
    const brands = await getJSON(`${API}/brands`);
    const opts = brands.map((b) => `<option value="${esc(b)}">${esc(b)}</option>`).join("");
    $("brand-filter").insertAdjacentHTML("beforeend", opts);
    $("bulk-brand").insertAdjacentHTML("beforeend", opts);
  } catch (e) { /* ignore */ }
}

async function loadInventory() {
  const params = new URLSearchParams({ limit: PAGE_SIZE, offset: inv.offset });
  if (inv.q) params.set("q", inv.q);
  if (inv.brand) params.set("brand", inv.brand);
  if (inv.status) params.set("status", inv.status);
  const body = $("inventory-body");
  try {
    const page = await getJSON(`${API}/variants?${params}`);
    inv.total = page.total;
    renderInventory(page.items);
    const from = page.total ? page.offset + 1 : 0;
    const to = Math.min(page.offset + page.limit, page.total);
    $("pg-info").textContent = `${from.toLocaleString()}–${to.toLocaleString()} of ${page.total.toLocaleString()}`;
    $("pg-prev").disabled = page.offset === 0;
    $("pg-next").disabled = page.offset + page.limit >= page.total;
    $("pg-prev").classList.toggle("opacity-30", page.offset === 0);
    $("pg-next").classList.toggle("opacity-30", page.offset + page.limit >= page.total);
  } catch (e) {
    body.innerHTML = `<tr><td colspan="8" class="text-center text-slate-400 py-8">Could not load inventory.</td></tr>`;
  }
}

function renderInventory(items) {
  const body = $("inventory-body");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="8" class="text-center text-slate-400 py-8">Nothing matches these filters.</td></tr>`;
    return;
  }
  body.innerHTML = items.map((v) => `
    <tr class="border-b border-slate-100 last:border-0 hover:bg-slate-50">
      <td class="pl-3.5">${v.image_path ? `<img class="thumb" src="${esc(v.image_path)}" alt="">` : `<div class="thumb-placeholder"></div>`}</td>
      <td class="sku-tag px-3.5 py-2.5">${esc(v.sku)}</td>
      <td class="px-3.5 py-2.5 max-w-[280px] truncate" title="${esc(v.product_name)}">${esc(v.product_name)}</td>
      <td class="px-3.5 py-2.5 text-slate-500">${esc(v.brand || "—")}</td>
      <td class="px-3.5 py-2.5"><span class="size-chip">${esc(v.size)}</span></td>
      <td class="px-3.5 py-2.5 text-right tabular-nums">${v.status === "UNCOUNTED" ? '<span class="text-slate-300">—</span>' : v.current_stock}</td>
      <td class="px-3.5 py-2.5 text-right text-slate-500 tabular-nums">${v.reorder_threshold}</td>
      <td class="px-3.5 py-2.5"><span class="status-pill status-${v.status}">${STATUS_LABEL[v.status] || v.status}</span></td>
    </tr>`).join("");
}

$("search-box").addEventListener("input", debounce((e) => { inv.q = e.target.value.trim(); inv.offset = 0; loadInventory(); }));
$("brand-filter").addEventListener("change", (e) => { inv.brand = e.target.value; inv.offset = 0; loadInventory(); });
$("status-filter").addEventListener("change", (e) => { inv.status = e.target.value; inv.offset = 0; loadSummary(); loadInventory(); });
$("pg-prev").addEventListener("click", () => { inv.offset = Math.max(0, inv.offset - PAGE_SIZE); loadInventory(); });
$("pg-next").addEventListener("click", () => { inv.offset += PAGE_SIZE; loadInventory(); });

// ================================================================ //
// PRODUCTS  (search a design -> see every size together, photo, delete)
// ================================================================ //
$("product-search").addEventListener("input", debounce((e) => searchProducts(e.target.value.trim()), 300));

async function searchProducts(q) {
  const grid = $("product-cards");
  const hint = $("product-search-hint");
  if (q.length < 2) { grid.innerHTML = ""; hint.hidden = false; hint.textContent = "Type at least 2 characters to search your catalog."; return; }
  hint.hidden = true;
  try {
    const products = await getJSON(`${API}/products?q=${encodeURIComponent(q)}&limit=30`);
    if (!products.length) {
      grid.innerHTML = "";
      hint.hidden = false;
      hint.textContent = `No design matches "${q}".`;
      return;
    }
    grid.innerHTML = products.map(productCardHTML).join("");
    products.forEach(wireProductCard);
  } catch (e) {
    grid.innerHTML = "";
    hint.hidden = false;
    hint.textContent = "Could not search right now.";
  }
}

function productCardHTML(p) {
  const sizeChips = p.variants.map((v) => `
    <span class="group relative inline-flex items-center gap-1 size-chip status-${v.status} !bg-opacity-100">
      ${esc(v.size)}: ${v.status === "UNCOUNTED" ? "—" : v.current_stock}
      <button class="del-size hidden group-hover:inline text-current/60 hover:text-red-600 font-bold" data-code="${esc(v.variant_code)}" title="Delete this size">×</button>
    </span>`).join("");
  return `
    <div class="card p-4 flex flex-col gap-3" data-sku="${esc(p.sku)}">
      <div class="flex items-start gap-3">
        <label class="shrink-0 cursor-pointer relative group/photo" title="Click to upload a photo">
          ${p.image_path
            ? `<img src="${esc(p.image_path)}" class="w-14 h-14 rounded-lg object-cover">`
            : `<div class="w-14 h-14 rounded-lg bg-slate-100 flex items-center justify-center text-slate-300 text-xl">📷</div>`}
          <input type="file" accept="image/*" class="photo-input hidden" data-sku="${esc(p.sku)}">
          <div class="absolute inset-0 bg-black/40 rounded-lg opacity-0 group-hover/photo:opacity-100 flex items-center justify-center text-white text-[10px] font-semibold transition-opacity">Upload</div>
        </label>
        <div class="min-w-0 flex-1">
          <div class="font-bold text-slate-900 text-sm leading-tight truncate" title="${esc(p.name)}">${esc(p.name)}</div>
          <div class="text-xs text-slate-500 mt-0.5">
            <span class="sku-tag">${esc(p.sku)}</span> · ${esc(p.brand || "—")}${p.category ? " · " + esc(p.category) : ""}
          </div>
        </div>
        <button class="del-product icon-btn danger shrink-0" title="Delete product">🗑</button>
      </div>
      <div class="flex flex-wrap gap-1.5">${sizeChips}</div>
    </div>`;
}

function wireProductCard(p) {
  const card = document.querySelector(`#product-cards [data-sku="${CSS.escape(p.sku)}"]`);
  if (!card) return;

  card.querySelector(".del-product").addEventListener("click", () => {
    askConfirm(
      `Delete ${p.name}?`,
      `This permanently removes ${p.sku} and all ${p.variants.length} of its sizes, including their full stock history. This cannot be undone.`,
      async () => {
        try {
          await apiDelete(`${API}/products/${encodeURIComponent(p.sku)}`);
          card.remove();
          showToast(`${p.sku} deleted`);
          loadSummary();
        } catch (err) { showToast("Could not delete: " + err.message, true); }
      }
    );
  });

  card.querySelectorAll(".del-size").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const code = btn.dataset.code;
      askConfirm(`Delete size ${code}?`, "This removes just this size and its history. Other sizes are untouched.", async () => {
        try {
          const r = await apiDelete(`${API}/variants/${encodeURIComponent(code)}`);
          if (r.product_also_deleted) { card.remove(); } else { searchProducts($("product-search").value.trim()); }
          showToast(`${code} deleted`);
          loadSummary();
        } catch (err) { showToast("Could not delete: " + err.message, true); }
      });
    });
  });

  const photoInput = card.querySelector(".photo-input");
  photoInput.addEventListener("change", async () => {
    const file = photoInput.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch(`${API}/products/${encodeURIComponent(p.sku)}/image`, { method: "POST", body: formData });
      if (!res.ok) throw new Error((await res.json()).detail || res.status);
      const data = await res.json();
      card.querySelector("label > img, label > div:first-child").outerHTML = `<img src="${esc(data.image_path)}" class="w-14 h-14 rounded-lg object-cover">`;
      showToast("Photo updated");
    } catch (err) { showToast("Upload failed: " + err.message, true); }
  });
}

async function apiDelete(url) {
  const res = await fetch(url, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

// ---------------- Add Product modal ----------------
$("open-add-product").addEventListener("click", () => { $("add-product-modal").hidden = false; });
$("close-add-product").addEventListener("click", () => { $("add-product-modal").hidden = true; });
$("add-product-modal").addEventListener("click", (e) => { if (e.target.id === "add-product-modal") $("add-product-modal").hidden = true; });

function addSizeRow(size = "") {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td class="pr-2 py-1"><input class="size-size input-base w-full" value="${esc(size)}" placeholder="M" required></td>
    <td class="pr-2 py-1"><input class="size-stock input-base w-full" type="number" value="0" min="0"></td>
    <td class="pr-2 py-1"><input class="size-threshold input-base w-full" type="number" value="5" min="0"></td>
    <td class="pr-2 py-1"><input class="size-target input-base w-full" type="number" value="20" min="0"></td>
    <td class="py-1"><button type="button" class="row-remove text-slate-400 hover:text-red-600 font-bold px-1" title="Remove">×</button></td>`;
  tr.querySelector(".row-remove").addEventListener("click", () => tr.remove());
  $("size-rows").appendChild(tr);
}
function resetSizeRows() { $("size-rows").innerHTML = ""; STANDARD_SIZES.forEach((s) => addSizeRow(s)); }
resetSizeRows();
$("add-size-row").addEventListener("click", () => addSizeRow());

$("add-product-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const msg = $("add-product-msg");
  const variants = Array.from(document.querySelectorAll("#size-rows tr")).map((tr) => ({
    size: tr.querySelector(".size-size").value.trim(),
    initial_stock: Number(tr.querySelector(".size-stock").value) || 0,
    reorder_threshold: Number(tr.querySelector(".size-threshold").value) || 5,
    target_stock_level: Number(tr.querySelector(".size-target").value) || 20,
  })).filter((v) => v.size);
  if (!variants.length) { msg.textContent = "Add at least one size."; msg.className = "msg error"; return; }

  const res = await fetch(`${API}/products`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sku: form.sku.value, name: form.name.value, brand: form.brand.value, category: form.category.value, variants }),
  });
  const data = await res.json();
  if (res.ok) {
    showToast(`Added ${data.sku} (${data.variants.length} sizes)`);
    form.reset();
    resetSizeRows();
    msg.textContent = "";
    $("add-product-modal").hidden = true;
    loadSummary();
    if ($("view-products").classList.contains("is-active") && $("product-search").value.trim()) searchProducts($("product-search").value.trim());
  } else {
    msg.textContent = typeof data.detail === "string" ? data.detail : "Please check the form.";
    msg.className = "msg error";
  }
});

// ================================================================ //
// SCAN & RECOUNT
// ================================================================ //
let scanStream = null;
let scanRafId = null;

$("scan-toggle").addEventListener("click", () => { scanStream ? stopCamera() : startCamera(); });

async function startCamera() {
  const video = $("scan-video");
  const msg = $("scan-msg");
  try {
    scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    video.srcObject = scanStream;
    await video.play();
    $("scan-toggle").textContent = "Stop Camera";
    msg.textContent = "";
    scanLoop();
  } catch (err) {
    msg.textContent = `Could not access the camera (${err.message}). Use "Type Instead" below.`;
    msg.className = "msg error";
  }
}

function stopCamera() {
  if (scanRafId) cancelAnimationFrame(scanRafId);
  if (scanStream) { scanStream.getTracks().forEach((t) => t.stop()); scanStream = null; }
  $("scan-toggle").textContent = "Start Camera";
}

function scanLoop() {
  const video = $("scan-video");
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const tick = () => {
    if (!scanStream) return;
    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      canvas.width = video.videoWidth; canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const code = jsQR(img.data, img.width, img.height);
      if (code && code.data) { stopCamera(); lookupVariant(code.data.trim()); return; }
    }
    scanRafId = requestAnimationFrame(tick);
  };
  scanRafId = requestAnimationFrame(tick);
}

$("manual-lookup-form").addEventListener("submit", (e) => {
  e.preventDefault();
  lookupVariant(`${e.target.sku.value.trim().toUpperCase()}-${e.target.size.value.trim().toUpperCase()}`);
});

async function lookupVariant(variantCode) {
  const msg = $("scan-msg");
  const res = await fetch(`${API}/variants/${encodeURIComponent(variantCode)}`);
  if (!res.ok) {
    msg.textContent = `"${variantCode}" not found — check the SKU and size.`;
    msg.className = "msg error";
    $("recount-result").hidden = true;
    return;
  }
  const v = await res.json();
  msg.textContent = "";
  $("recount-result").hidden = false;
  $("recount-img").src = v.image_path || "";
  $("recount-img").style.visibility = v.image_path ? "visible" : "hidden";
  $("recount-name").textContent = v.product_name;
  $("recount-code").textContent = `${v.variant_code} (size ${v.size})`;
  $("recount-current-stock").textContent = v.counted ? `${v.current_stock} pcs` : "not counted yet";
  $("recount-msg").textContent = "";
  const input = $("recount-new-count");
  input.value = "";
  input.dataset.variantCode = v.variant_code;
  input.focus();
}

$("recount-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("recount-new-count");
  const msg = $("recount-msg");
  const res = await fetch(`${API}/inventory/adjust`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ variant_code: input.dataset.variantCode, new_count: Number(input.value), note: $("recount-note").value || "manual recount" }),
  });
  const data = await res.json();
  if (res.ok) {
    msg.textContent = `${data.variant_code} set to ${data.current_stock}`;
    msg.className = "msg success";
    $("recount-current-stock").textContent = `${data.current_stock} pcs`;
    loadSummary();
  } else {
    msg.textContent = data.detail;
    msg.className = "msg error";
  }
});

// ================================================================ //
// BULK STOCK COUNT
// ================================================================ //
function updateBulkDownloadLink() {
  const params = new URLSearchParams();
  if ($("bulk-brand").value) params.set("brand", $("bulk-brand").value);
  if ($("bulk-only-uncounted").checked) params.set("only_uncounted", "true");
  $("bulk-download").href = `${API}/inventory/count-sheet?${params}`;
}
$("bulk-brand").addEventListener("change", updateBulkDownloadLink);
$("bulk-only-uncounted").addEventListener("change", updateBulkDownloadLink);

$("bulk-file-input").addEventListener("change", async () => {
  const input = $("bulk-file-input");
  const file = input.files[0];
  if (!file) return;
  const box = $("bulk-result");
  box.innerHTML = `<p class="text-sm text-slate-500">Loading ${esc(file.name)}…</p>`;
  const formData = new FormData();
  formData.append("file", file);
  try {
    const res = await fetch(`${API}/inventory/bulk-set`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) { box.innerHTML = `<div class="msg error">${esc(data.detail)}</div>`; return; }
    box.innerHTML = `
      <div class="flex flex-wrap gap-3 mb-3">
        <div class="stat-card"><div class="n text-emerald-600">${data.updated_count}</div><div class="l">Sizes updated</div></div>
        <div class="stat-card"><div class="n">${data.skipped_blank_count}</div><div class="l">Blank, skipped</div></div>
        <div class="stat-card"><div class="n text-red-600">${data.not_found_count}</div><div class="l">Code not found</div></div>
        <div class="stat-card"><div class="n text-amber-600">${data.duplicate_rows_ignored}</div><div class="l">Repeat rows ignored</div></div>
      </div>
      ${data.not_found_count ? `<div class="msg error">Not found: ${data.not_found_sample.map(esc).join(", ")}${data.not_found_count > data.not_found_sample.length ? " …" : ""}</div>` : ""}`;
    loadSummary();
    showToast(`${data.updated_count} sizes updated`);
  } catch (err) {
    box.innerHTML = `<div class="msg error">Upload failed: ${esc(err)}</div>`;
  } finally { input.value = ""; }
});

// ================================================================ //
// ORDERS UPLOAD
// ================================================================ //
const dropzone = $("dropzone");
const fileInput = $("order-file-input");
dropzone.addEventListener("dragover", (e) => e.preventDefault());
dropzone.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; handleOrderUpload(); } });
fileInput.addEventListener("change", handleOrderUpload);

async function handleOrderUpload() {
  const file = fileInput.files[0];
  if (!file) return;
  const box = $("upload-result");
  box.innerHTML = `<p class="text-sm text-slate-500">Processing ${esc(file.name)}…</p>`;
  const formData = new FormData();
  formData.append("file", file);
  try {
    const res = await fetch(`${API}/orders/upload`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) { box.innerHTML = `<div class="msg error text-sm">${esc(data.detail)}</div>`; return; }
    renderUploadResult(data);
    loadSummary();
  } catch (err) {
    box.innerHTML = `<div class="msg error">Upload failed: ${esc(err)}</div>`;
  } finally { fileInput.value = ""; }
}

function renderUploadResult(data) {
  const rows = data.lines.map((l) => `
    <tr class="border-b border-slate-100 last:border-0">
      <td class="sku-tag px-3.5 py-2">${esc(l.sku)}</td>
      <td class="px-3.5 py-2"><span class="size-chip">${esc(l.size || "—")}</span></td>
      <td class="px-3.5 py-2 max-w-[240px] truncate" title="${esc(l.product_name || "")}">${esc(l.product_name || "—")}</td>
      <td class="px-3.5 py-2 text-right tabular-nums">${l.qty_ordered}</td>
      <td class="px-3.5 py-2 text-right tabular-nums">${l.remaining_stock ?? "—"}</td>
      <td class="px-3.5 py-2"><span class="status-pill ${l.status === "FULFILLED" ? "status-OK" : l.status === "INSUFFICIENT_STOCK" ? "status-OUT" : "status-UNCOUNTED"}">${esc(l.status.replace(/_/g, " "))}</span>
        ${l.note ? `<div class="text-[11px] text-slate-500 mt-0.5 max-w-[220px]">${esc(l.note)}</div>` : ""}</td>
    </tr>`).join("");

  $("upload-result").innerHTML = `
    ${data.file_note ? `<div class="msg note mb-3">${esc(data.file_note)}</div>` : ""}
    <div class="flex flex-wrap gap-3 mb-4">
      <div class="stat-card"><div class="n">${data.unique_skus}</div><div class="l">Sizes in file</div></div>
      <div class="stat-card"><div class="n text-emerald-600">${data.fulfilled_count}</div><div class="l">Fulfilled</div></div>
      <div class="stat-card"><div class="n text-amber-600">${data.insufficient_count}</div><div class="l">Insufficient stock</div></div>
      <div class="stat-card"><div class="n text-red-600">${data.not_found_count}</div><div class="l">Not found</div></div>
    </div>
    ${data.new_alerts.length ? `<div class="msg error mb-3">New low-stock alerts: ${data.new_alerts.map(esc).join(", ")}</div>` : ""}
    <div class="card overflow-hidden">
      <table class="w-full text-sm">
        <thead><tr class="bg-slate-50 text-slate-500 text-xs"><th class="th">SKU</th><th class="th">Size</th><th class="th">Product</th><th class="th text-right">Ordered</th><th class="th text-right">Remaining</th><th class="th">Status</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

// ================================================================ //
// REORDER LIST
// ================================================================ //
async function loadReorderList() {
  const body = $("reorder-body");
  try {
    const items = await getJSON(`${API}/reorder-list`);
    if (!items.length) {
      body.innerHTML = `<tr><td colspan="6" class="text-center text-slate-400 py-8">Nothing counted is below its threshold — no reorder needed.</td></tr>`;
      $("reorder-note").textContent = "";
      return;
    }
    const shown = items.slice(0, LIST_CAP);
    $("reorder-note").textContent = items.length > LIST_CAP
      ? `Showing the ${LIST_CAP} most urgent of ${items.length.toLocaleString()} lines. Export to Excel for the full list.`
      : `${items.length} line${items.length === 1 ? "" : "s"}.`;
    body.innerHTML = shown.map((i) => `
      <tr class="border-b border-slate-100 last:border-0 hover:bg-slate-50">
        <td class="sku-tag px-3.5 py-2.5">${esc(i.sku)}</td>
        <td class="px-3.5 py-2.5 max-w-[260px] truncate" title="${esc(i.name)}">${esc(i.name)}</td>
        <td class="px-3.5 py-2.5"><span class="size-chip">${esc(i.size)}</span></td>
        <td class="px-3.5 py-2.5 text-slate-500">${esc(i.vendor || "—")}</td>
        <td class="px-3.5 py-2.5 text-right tabular-nums">${i.current_stock}</td>
        <td class="px-3.5 py-2.5 text-right tabular-nums font-semibold">${i.required_qty}</td>
      </tr>`).join("");
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6" class="text-center text-slate-400 py-8">Could not load the reorder list.</td></tr>`;
  }
}

// ================================================================ //
// ALERTS
// ================================================================ //
async function loadAlerts() {
  const list = $("alerts-list");
  try {
    const [alerts, s] = await Promise.all([getJSON(`${API}/alerts?limit=300`), getJSON(`${API}/summary`)]);
    setAlertBadge(s.open_alerts);
    $("alerts-note").textContent = s.open_alerts > alerts.length ? `Showing the ${alerts.length} lowest of ${s.open_alerts.toLocaleString()} open alerts.` : "";
    if (!alerts.length) { list.innerHTML = `<p class="text-sm text-slate-500">No open alerts — every counted size is above its threshold.</p>`; return; }
    list.innerHTML = alerts.map((a) => `
      <div class="card px-4 py-3 flex items-center justify-between border-l-4 ${a.alert_type === "OUT_OF_STOCK" ? "border-l-red-500" : "border-l-amber-500"}">
        <div>
          <div class="font-semibold text-slate-900 text-sm">${esc(a.product_name)} <span class="sku-tag">(${esc(a.variant_code)})</span></div>
          <div class="text-xs text-slate-500 mt-0.5">${a.alert_type === "OUT_OF_STOCK" ? "Out of stock" : "Low stock"} — ${a.stock_at_alert} pcs left</div>
        </div>
      </div>`).join("");
  } catch (e) { list.innerHTML = `<p class="text-sm text-slate-500">Could not load alerts.</p>`; }
}

// ================================================================ //
// PRINT LABELS
// ================================================================ //
$("label-search").addEventListener("input", debounce(async (e) => {
  const q = e.target.value.trim();
  const box = $("label-results");
  if (q.length < 2) { box.innerHTML = ""; return; }
  try {
    const products = await getJSON(`${API}/products?q=${encodeURIComponent(q)}&limit=15`);
    box.innerHTML = products.length
      ? products.map((p) => `<button type="button" class="result-row" data-sku="${esc(p.sku)}"><span class="sku-tag">${esc(p.sku)}</span> ${esc(p.name)}</button>`).join("")
      : `<p class="text-sm text-slate-500">No design matches "${esc(q)}".</p>`;
    box.querySelectorAll(".result-row").forEach((btn, i) => btn.addEventListener("click", () => selectLabelProduct(products[i])));
  } catch (err) { box.innerHTML = ""; }
}));

function selectLabelProduct(product) {
  $("label-results").innerHTML = "";
  $("label-selected").hidden = false;
  $("label-selected-name").textContent = `${product.sku} — ${product.name}`;
  $("label-variant-checks").innerHTML = product.variants.map((v) => `
    <label class="flex items-center gap-2 text-sm"><input type="checkbox" value="${esc(v.variant_code)}" checked class="accent-brand-500"> ${esc(v.variant_code)}</label>`).join("");
  $("label-variant-checks").querySelectorAll("input").forEach((cb) => cb.addEventListener("change", updateLabelDownloadLink));
  updateLabelDownloadLink();
}

function updateLabelDownloadLink() {
  const codes = Array.from(document.querySelectorAll("#label-variant-checks input:checked")).map((cb) => cb.value);
  $("label-download").href = codes.length ? `${API}/labels/sheet?variant_codes=${encodeURIComponent(codes.join(","))}` : "#";
}

// ---------------- Init ----------------
loadBrands();
loadSummary();
loadInventory();
