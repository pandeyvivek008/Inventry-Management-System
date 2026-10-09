const API = "/api";
const STANDARD_SIZES = ["S", "M", "L", "XL", "XXL"];
const PAGE_SIZE = 100;
const LIST_CAP = 500;

const $ = (id) => document.getElementById(id);
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function debounce(fn, ms = 300) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(String(r.status)); return r.json(); }
async function apiDelete(url) { const r = await fetch(url, { method: "DELETE" }); const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`); return d; }
async function apiPost(url, body) {
  const r = await fetch(url, { method: "POST", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `HTTP ${r.status}`);
  return d;
}

// ---------------- Toast ----------------
let toastTimer;
function showToast(message, type = "success", duration = 1400) {
  // Backward compatible: showToast("msg", true)
  if (type === true) type = "error";
  const el = $("toast");
  el.textContent = message;
  el.className = `toast ${type}`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, duration);
}

// ---------------- Sound + vibration for alerts ----------------
let audioCtx = null;
function playAlertSound() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    const now = audioCtx.currentTime;
    [0, 0.18, 0.36].forEach((offset, i) => {
      const o = audioCtx.createOscillator();
      const g = audioCtx.createGain();
      o.type = "sine";
      o.frequency.value = i === 1 ? 1046 : 880;
      o.connect(g); g.connect(audioCtx.destination);
      g.gain.setValueAtTime(0.0001, now + offset);
      g.gain.exponentialRampToValueAtTime(0.12, now + offset + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.14);
      o.start(now + offset); o.stop(now + offset + 0.15);
    });
  } catch (e) { /* browser audio may be unavailable */ }
  if (navigator.vibrate) navigator.vibrate([180, 90, 180, 90, 260]);
}
function playSuccessSound() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    const now = audioCtx.currentTime;
    [659.25, 783.99, 1046.5].forEach((frequency, index) => {
      const oscillator = audioCtx.createOscillator(), gain = audioCtx.createGain();
      const start = now + index * 0.1;
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      oscillator.connect(gain); gain.connect(audioCtx.destination);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.085, start + 0.018);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.2);
      oscillator.start(start); oscillator.stop(start + 0.21);
    });
  } catch (e) { /* sound is optional when the browser blocks audio */ }
}
let lastAlertCount = null;
function noteAlertCount(n) {
  if (lastAlertCount !== null && n > lastAlertCount) {
    playAlertSound();
    showToast("New inventory alert — check Alerts", "alert", 2200);
  }
  lastAlertCount = n;
  setAlertBadge(n);
}
function setAlertBadge(n) {
  [$("alert-count"), $("alert-count-mobile")].forEach((el) => { if (el) { el.textContent = n; el.hidden = !(n > 0); } });
}

// Unlock/resume audio after the first real user interaction on mobile browsers.
document.addEventListener("pointerdown", () => {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch (e) {}
}, { once: false, passive: true });

// ---------------- Confirm modal ----------------
let confirmAction = null;
$("confirm-cancel").addEventListener("click", () => { $("confirm-modal").hidden = true; });
$("confirm-ok").addEventListener("click", async () => { $("confirm-modal").hidden = true; if (confirmAction) await confirmAction(); });
function askConfirm(title, body, action, okLabel = "Delete") {
  $("confirm-title").textContent = title;
  $("confirm-body").textContent = body;
  $("confirm-ok").textContent = okLabel;
  confirmAction = action;
  $("confirm-modal").hidden = false;
}

// ---------------- Nav: drawer + view switching ----------------
function openDrawer() { $("sidebar").classList.add("is-open"); $("nav-backdrop").hidden = false; }
function closeDrawer() { $("sidebar").classList.remove("is-open"); $("nav-backdrop").hidden = true; }
$("menu-toggle").addEventListener("click", openDrawer);
$("sidebar-close").addEventListener("click", closeDrawer);
$("nav-backdrop").addEventListener("click", closeDrawer);
$("topbar-alerts").addEventListener("click", () => switchView("alerts"));

document.querySelectorAll(".rail-link[data-view]").forEach((btn) => btn.addEventListener("click", () => switchView(btn.dataset.view)));

function switchView(name) {
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
  $("stage")?.scrollTo?.({ top: 0, left: 0, behavior: "auto" });
  document.querySelectorAll(".rail-link").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view-${name}`));
  closeDrawer();
  if (name !== "scan") stopCamera();
  if (name === "dashboard") { loadSummary(); loadInventory(); loadMoversChart(); }
  if (name === "products" && !window.__keepProductSearch) {
    $("product-search").value = "";
    loadLatestProducts();
  }
  window.__keepProductSearch = false;
  if (name === "orders") loadOrderHistory();
  if (name === "reorder") loadReorderList();
  if (name === "alerts") loadAlerts();
  if (name === "trash") loadTrash();
  if (name === "history") { history.offset = 0; loadHistory(true); }
}

// Keep dashboard counters and alert state fresh without creating a new timer
// every time the user changes sidebar pages.
setInterval(() => {
  loadSummary();
  if ($("view-alerts")?.classList.contains("is-active")) loadAlerts();
  if ($("view-reorder")?.classList.contains("is-active")) loadReorderList();
}, 10000);

// ================================================================ //
// DASHBOARD
// ================================================================ //
const inv = { q: "", status: "", offset: 0, total: 0 };
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
    $("summary-row").innerHTML = chips.map((c) => `
      <button type="button" class="chip ${inv.status === c.key ? "is-on" : ""}" data-status="${c.key}">
        <span class="chip-n">${c.n.toLocaleString()}</span><span class="chip-l">${c.label}</span>
      </button>`).join("") + `<div class="chip-meta">${s.products.toLocaleString()} designs</div>`;
    $("summary-row").querySelectorAll(".chip").forEach((btn) => btn.addEventListener("click", () => {
      inv.status = btn.dataset.status; $("status-filter").value = inv.status; inv.offset = 0;
      loadSummary(); loadInventory();
    }));
    noteAlertCount(s.open_alerts);
  } catch (e) { /* decorative */ }
}

async function loadInventory() {
  const params = new URLSearchParams({ limit: PAGE_SIZE, offset: inv.offset });
  if (inv.q) params.set("q", inv.q);
  if (inv.status) params.set("status", inv.status);
  const body = $("inventory-body");
  try {
    const page = await getJSON(`${API}/variants?${params}`);
    inv.total = page.total;
    renderInventory(page.items);
    const from = page.total ? page.offset + 1 : 0, to = Math.min(page.offset + page.limit, page.total);
    $("pg-info").textContent = `${from.toLocaleString()}–${to.toLocaleString()} of ${page.total.toLocaleString()}`;
    $("pg-prev").disabled = page.offset === 0;
    $("pg-next").disabled = page.offset + page.limit >= page.total;
  } catch (e) { body.innerHTML = `<tr><td colspan="7" class="empty">Could not load inventory.</td></tr>`; }
}

function renderInventory(items) {
  const body = $("inventory-body");
  if (!items.length) { body.innerHTML = `<tr><td colspan="7" class="empty">Nothing matches these filters.</td></tr>`; return; }
  body.innerHTML = items.map((v) => `
    <tr>
      <td class="col-img">${v.image_path ? `<img class="thumb" src="${esc(v.image_path)}" alt="">` : `<div class="thumb-placeholder"></div>`}</td>
      <td class="sku-tag">${esc(v.sku)}</td>
      <td class="name-cell" title="${esc(v.product_name)}">${esc(v.product_name)}</td>
      <td><span class="size-chip">${esc(v.size)}</span></td>
      <td class="num">${v.status === "UNCOUNTED" ? "—" : v.current_stock}</td>
      <td class="num hide-sm">${v.reorder_threshold}</td>
      <td><span class="status-pill status-${v.status}">${STATUS_LABEL[v.status] || v.status}</span></td>
    </tr>`).join("");
}

$("search-box").addEventListener("input", debounce((e) => { inv.q = e.target.value.trim(); inv.offset = 0; loadInventory(); }));
$("status-filter").addEventListener("change", (e) => { inv.status = e.target.value; inv.offset = 0; loadSummary(); loadInventory(); });
$("pg-prev").addEventListener("click", () => { inv.offset = Math.max(0, inv.offset - PAGE_SIZE); loadInventory(); });
$("pg-next").addEventListener("click", () => { inv.offset += PAGE_SIZE; loadInventory(); });

// ---------------- Movers chart (hand-drawn bars, no chart library needed) ----------------
$("movers-days").addEventListener("change", loadMoversChart);
async function loadMoversChart() {
  const box = $("movers-chart");
  try {
    const days = $("movers-days").value;
    const movers = await getJSON(`${API}/analytics/movers?days=${days}&limit=8`);
    if (!movers.length) { box.innerHTML = `<p class="hint">No orders processed yet in this period.</p>`; return; }
    const max = Math.max(...movers.map((m) => m.units_moved), 1);
    box.innerHTML = movers.map((m) => `
      <div class="chart-row">
        <div class="chart-label" title="${esc(m.name)}">${esc(m.sku)}</div>
        <div class="chart-bar-track"><div class="chart-bar" style="width:${Math.max(4, (m.units_moved / max) * 100)}%"></div></div>
        <div class="chart-val">${m.units_moved}</div>
      </div>`).join("");
  } catch (e) { box.innerHTML = `<p class="hint">Could not load chart.</p>`; }
}

// ================================================================ //
// PRODUCTS (search) -> PRODUCT DETAIL
// ================================================================ //
$("product-search").addEventListener("input", debounce((e) => searchProducts(e.target.value.trim()), 300));

async function loadLatestProducts() {
  const grid = $("product-cards"), hint = $("product-search-hint");
  try {
    const products = await getJSON(`${API}/products?limit=12`);
    if (!products.length) {
      grid.innerHTML = "";
      hint.hidden = false;
      hint.textContent = "No products yet. Add your first product.";
      $("product-result-count").textContent = "";
      return;
    }
    hint.hidden = true;
    $("product-result-count").textContent = `Latest ${products.length} product${products.length === 1 ? "" : "s"}`;
    grid.innerHTML = products.map(productCardHTML).join("");
    grid.querySelectorAll(".pcard").forEach((card) => card.addEventListener("click", () => openProductDetail(card.dataset.sku)));
  } catch (e) {
    grid.innerHTML = "";
    hint.hidden = false;
    hint.textContent = "Could not load products right now.";
  }
}

async function searchProducts(q) {
  const grid = $("product-cards"), hint = $("product-search-hint");
  if (q.length < 2) { grid.innerHTML = ""; hint.hidden = false; hint.textContent = "Type at least 2 characters to search your catalog."; $("product-result-count").textContent = ""; return; }
  hint.hidden = true;
  try {
    const products = await getJSON(`${API}/products?q=${encodeURIComponent(q)}&limit=30`);
    if (!products.length) { grid.innerHTML = ""; hint.hidden = false; hint.textContent = `No design matches "${q}".`; $("product-result-count").textContent = ""; return; }
    $("product-result-count").textContent = `${products.length} product${products.length === 1 ? "" : "s"} found`;
    grid.innerHTML = products.map(productCardHTML).join("");
    grid.querySelectorAll(".pcard").forEach((card) => card.addEventListener("click", () => openProductDetail(card.dataset.sku)));
  } catch (e) { grid.innerHTML = ""; hint.hidden = false; hint.textContent = "Could not search right now."; }
}

function productCardHTML(p) {
  const sizes = p.variants.map((v) => `<span class="size-chip status-${v.status}">${esc(v.size)}: ${v.status === "UNCOUNTED" ? "—" : v.current_stock}</span>`).join("");
  return `
    <article class="pcard" data-sku="${esc(p.sku)}">
      <div class="pcard-photo-wrap">
        ${p.image_path ? `<img class="pcard-photo" src="${esc(p.image_path)}?v=${Date.now()}" alt="${esc(p.name)}" loading="lazy">` : `<div class="pcard-photo-ph"><i class="fa-solid fa-image"></i><span>No image</span></div>`}
      </div>
      <div class="pcard-body">
        <div class="pcard-name" title="${esc(p.name)}">${esc(p.name)}</div>
        <div class="pcard-meta"><span class="sku-tag">${esc(p.sku)}</span>${p.category ? " · " + esc(p.category) : ""}</div>
        <div class="pcard-sizes">${sizes}</div>
      </div>
    </article>`;
}

$("detail-back").addEventListener("click", () => switchView("products"));

async function openProductDetail(sku) {
  try {
    const product = await getJSON(`${API}/products/${encodeURIComponent(sku)}`);
    document.querySelectorAll(".rail-link").forEach((b) => b.classList.toggle("is-active", b.dataset.view === "products"));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("is-active", v.id === "view-detail"));
    renderDetail(product, $("detail-content"), { showDelete: true });
  } catch (e) { showToast("Could not open product", true); }
}

// Shared renderer: used by both the Product Detail page and the Scan result
// panel, so updating a size works identically everywhere.
function renderDetail(product, container, opts = {}) {
  const seats = product.variants.map((v) => `
    <div class="seat" data-code="${esc(v.variant_code)}">
      <div class="seat-size">${esc(v.size)}</div>
      <div class="seat-status"><span class="status-pill status-${v.status}">${STATUS_LABEL[v.status] || v.status}</span></div>
      <div class="seat-row">
        <input type="number" min="0" class="input seat-count" placeholder="${v.status === "UNCOUNTED" ? "count" : v.current_stock}">
        <button class="btn-secondary seat-save" type="button">Save</button>
      </div>
      <button class="seat-del" type="button"><i class="fa-solid fa-trash-can"></i> Delete size</button>
    </div>`).join("");

  container.innerHTML = `
    <div class="detail-head">
      <label class="detail-photo-box" id="detail-photo-box">
        ${product.image_path ? `<img src="${esc(product.image_path)}?v=${Date.now()}" alt="${esc(product.name)}">` : `<i class="fa-solid fa-image"></i><br><small>Add photo</small>`}
        <input type="file" accept="image/*" id="detail-photo-input" hidden>
      </label>
      <div class="detail-info">
        <input class="input" id="detail-name" value="${esc(product.name)}" placeholder="Product name">
        <div class="detail-info-row">
          <input class="input" id="detail-sku" value="${esc(product.sku)}" placeholder="Canonical SKU">
          <input class="input" id="detail-category" placeholder="Category" value="${esc(product.category || "")}">
        </div>
        <div class="detail-actions">
          <button class="btn-secondary" id="detail-save" type="button"><i class="fa-solid fa-floppy-disk"></i> Save Changes</button>
          <span class="sku-tag" style="align-self:center;">${esc(product.sku)}</span>
          ${opts.showDelete ? `<button class="btn-danger" id="detail-delete" type="button" style="margin-left:auto;"><i class="fa-solid fa-trash-can"></i> Delete Product</button>` : ""}
        </div>
        <div id="detail-msg" class="msg"></div>
      </div>
    </div>
    <h3 class="sub-head">Sizes</h3>
    <div class="seat-grid">
      ${seats}
      <div class="seat-add" id="seat-add"><i class="fa-solid fa-plus"></i> Add size</div>
    </div>`;

  container.querySelectorAll(".seat").forEach((seat) => {
    const code = seat.dataset.code;
    seat.querySelector(".seat-save").addEventListener("click", async () => {
      const input = seat.querySelector(".seat-count");
      if (input.value === "") { showToast("Enter a count first", true); return; }
      try {
        const updated = await apiPost(`${API}/inventory/adjust`, { variant_code: code, new_count: Number(input.value), note: "manual update" });
        input.value = ""; input.placeholder = updated.current_stock;
        seat.querySelector(".seat-status").innerHTML = `<span class="status-pill status-${updated.status}">${STATUS_LABEL[updated.status]}</span>`;
        showToast(`${code} set to ${updated.current_stock}`);
        await refreshInventoryViews();
      } catch (err) { showToast("Update failed: " + err.message, true); }
    });
    seat.querySelector(".seat-del").addEventListener("click", () => {
      askConfirm(`Delete size ${code}?`, "Moves this size to Trash. You can restore it later.", async () => {
        try {
          const r = await apiDelete(`${API}/variants/${encodeURIComponent(code)}`);
          if (r.product_also_deleted) { switchView(opts.showDelete ? "products" : "scan"); showToast(`${product.sku} deleted (last size removed)`); }
          else { seat.remove(); showToast(`${code} deleted`); }
          loadSummary();
        } catch (err) { showToast("Could not delete: " + err.message, true); }
      });
    });
  });

  // Fast warehouse counting: Enter saves and moves to the next size; Tab keeps
  // the browser's natural next-field behavior. ArrowUp/ArrowDown also move.
  const seatInputs = Array.from(container.querySelectorAll(".seat-count"));
  seatInputs.forEach((input, idx) => input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      input.closest(".seat")?.querySelector(".seat-save")?.click();
      setTimeout(() => seatInputs[idx + 1]?.focus(), 80);
    } else if (e.key === "ArrowDown") {
      e.preventDefault(); seatInputs[idx + 1]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault(); seatInputs[idx - 1]?.focus();
    }
  }));

  container.querySelector("#seat-add").addEventListener("click", async () => {
    const size = prompt("New size (e.g. S, M, L, XL, XXL):");
    if (!size || !size.trim()) return;
    try {
      const updated = await apiPost(`${API}/products/${encodeURIComponent(product.sku)}/variants`, { size: size.trim(), initial_stock: 0 });
      renderDetail(updated, container, opts);
      showToast(`Size ${size.trim().toUpperCase()} added`);
      await refreshInventoryViews();
    } catch (err) { showToast(err.message, true); }
  });

  const photoBox = container.querySelector("#detail-photo-box");
  const photoInput = container.querySelector("#detail-photo-input");
  photoBox.addEventListener("click", (e) => { if (e.target.tagName !== "INPUT") photoInput.click(); });
  photoInput.addEventListener("change", async () => {
    const file = photoInput.files[0];
    if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    try {
      const res = await fetch(`${API}/products/${encodeURIComponent(product.sku)}/image`, { method: "POST", body: fd });
      if (!res.ok) throw new Error((await res.json()).detail || res.status);
      const data = await res.json();
      photoBox.innerHTML = `<img src="${esc(data.image_path)}"><input type="file" accept="image/*" id="detail-photo-input" hidden>`;
      showToast("Photo updated");
    } catch (err) { showToast("Upload failed: " + err.message, true); }
  });

  container.querySelector("#detail-save").addEventListener("click", async () => {
    const msg = container.querySelector("#detail-msg");
    try {
      const res = await fetch(`${API}/products/${encodeURIComponent(product.sku)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sku: container.querySelector("#detail-sku").value.trim().toUpperCase(),
          name: container.querySelector("#detail-name").value,
          category: container.querySelector("#detail-category").value,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail);
      msg.textContent = "Saved"; msg.className = "msg success";
      showToast("Product + canonical SKU updated");
      const saved = await res.json();
      renderDetail(saved, container, opts);
      if (opts.showDelete) {
        window.__keepProductSearch = true;
        loadLatestProducts();
      }
    } catch (err) { msg.textContent = "Could not save"; msg.className = "msg error"; }
  });

  const delBtn = container.querySelector("#detail-delete");
  if (delBtn) delBtn.addEventListener("click", () => {
    askConfirm(`Delete ${product.name}?`, `Moves ${product.sku} and all its sizes to Trash. You can restore it from the Trash tab.`, async () => {
      try { await apiDelete(`${API}/products/${encodeURIComponent(product.sku)}`); showToast(`${product.sku} moved to Trash`); switchView("products"); loadSummary(); }
      catch (err) { showToast("Could not delete: " + err.message, true); }
    });
  });
}

// ---------------- Add Product modal ----------------
let addPhotoFile = null;
let addPhotoPreviewToken = 0;
function selectAddMode(mode) {
  const bulk = mode === "bulk";
  $("bulk-product-panel").hidden = !bulk;
  $("add-product-form").hidden = bulk;
  document.querySelectorAll(".add-mode-tab").forEach((button) => {
    const selected = button.dataset.addMode === mode;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
}
document.querySelectorAll(".add-mode-tab").forEach((button) => button.addEventListener("click", () => selectAddMode(button.dataset.addMode)));
$("open-add-product").addEventListener("click", () => {
  $("add-product-form").reset();
  resetAddForm();
  selectAddMode("single");
  $("add-product-msg").textContent = "";
  $("add-product-msg").className = "msg";
  $("bulk-product-file").value = "";
  $("bulk-product-file-title").textContent = "Choose your filled Excel file";
  $("bulk-product-file-meta").textContent = ".xlsx, .xls or .csv · up to 20,000 rows";
  $("bulk-product-submit").disabled = true;
  $("bulk-product-result").innerHTML = "";
  $("add-product-modal").hidden = false;
  setTimeout(() => $("add-product-form").querySelector('input[name="sku"]')?.focus(), 0);
});
$("close-add-product").addEventListener("click", () => { $("add-product-modal").hidden = true; });
$("add-product-modal").addEventListener("click", (e) => { if (e.target.id === "add-product-modal") $("add-product-modal").hidden = true; });

$("add-photo-box").addEventListener("click", () => $("add-photo-input").click());
$("add-photo-input").addEventListener("change", () => {
  const file = $("add-photo-input").files[0];
  if (!file) return;
  addPhotoFile = file;
  const token = ++addPhotoPreviewToken;
  const reader = new FileReader();
  reader.onload = () => {
    if (token !== addPhotoPreviewToken) return;
    $("add-photo-preview").src = reader.result;
    $("add-photo-preview").hidden = false;
    $("add-photo-placeholder").hidden = true;
  };
  reader.readAsDataURL(file);
});

function addSizeRow(size = "") {
  const row = document.createElement("div");
  row.className = "size-row";
  row.innerHTML = `
    <input class="input sz size-size" value="${esc(size)}" placeholder="Size">
    <div><div class="lbl">Stock</div><input class="input num-in size-stock" type="number" value="" placeholder="0" min="0" inputmode="numeric"></div>
    <div><div class="lbl">Alert below</div><input class="input num-in size-threshold" type="number" value="5" min="0"></div>
    <div><div class="lbl">Target</div><input class="input num-in size-target" type="number" value="20" min="0"></div>
    <button type="button" class="row-remove" aria-label="Remove size"><i class="fa-solid fa-trash-can"></i></button>`;
  row.querySelector(".row-remove").addEventListener("click", () => row.remove());
  row.querySelectorAll("input").forEach((input) => input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const rows = Array.from(document.querySelectorAll("#size-rows .size-row"));
    const index = rows.indexOf(row);
    if (input.classList.contains("size-stock")) {
      const next = rows[index + 1]?.querySelector(".size-stock");
      if (next) next.focus();
      else $("add-product-form").querySelector('button[type="submit"]')?.focus();
    } else if (input.classList.contains("size-size")) {
      row.querySelector(".size-stock")?.focus();
    } else if (input.classList.contains("size-threshold")) {
      row.querySelector(".size-target")?.focus();
    } else if (input.classList.contains("size-target")) {
      const next = rows[index + 1]?.querySelector(".size-size");
      if (next) next.focus();
      else $("add-product-form").querySelector('button[type="submit"]')?.focus();
    }
  }));
  $("size-rows").appendChild(row);
}
function resetAddForm() {
  $("size-rows").innerHTML = "";
  STANDARD_SIZES.forEach((s) => addSizeRow(s));
  addPhotoFile = null;
  ++addPhotoPreviewToken;
  $("add-photo-preview").src = "";
  $("add-photo-preview").hidden = true;
  $("add-photo-placeholder").hidden = false;
  $("add-photo-input").value = "";
}
resetAddForm();
$("add-size-row").addEventListener("click", () => addSizeRow());

$("add-product-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target, msg = $("add-product-msg");
  const submitButton = form.querySelector('button[type="submit"]');
  if (submitButton.disabled) return;
  const variants = Array.from(document.querySelectorAll("#size-rows .size-row")).map((row) => ({
    size: row.querySelector(".size-size").value.trim(),
    initial_stock: Number(row.querySelector(".size-stock").value) || 0,
    reorder_threshold: Number(row.querySelector(".size-threshold").value) || 5,
    target_stock_level: Number(row.querySelector(".size-target").value) || 20,
  })).filter((v) => v.size);
  if (!variants.length) { msg.textContent = "Add at least one size."; msg.className = "msg error"; return; }

  submitButton.disabled = true;
  submitButton.dataset.originalText = submitButton.textContent;
  submitButton.textContent = "Saving...";
  try {
    const data = await apiPost(`${API}/products`, { sku: form.sku.value, name: form.name.value, category: form.category.value, variants });
    if (addPhotoFile) {
      const fd = new FormData(); fd.append("file", addPhotoFile);
      await fetch(`${API}/products/${encodeURIComponent(data.sku)}/image`, { method: "POST", body: fd }).catch(() => {});
    }
    const newSku = data.sku;
    form.reset(); resetAddForm(); msg.textContent = "";
    $("add-product-modal").hidden = true;
    showToast(`Product added successfully — ${newSku}`, "success", 1500);
    await refreshInventoryViews();
    window.__keepProductSearch = true;
    switchView("products");
    $("product-search").value = "";
    await loadLatestProducts();
    await loadMoversChart();
  } catch (err) { msg.textContent = err.message; msg.className = "msg error"; showToast(err.message || "Could not add product", "error", 2200); }
  finally { submitButton.disabled = false; submitButton.textContent = submitButton.dataset.originalText || "Save Product"; }
});

// ---------------- Bulk product import ----------------
let bulkProductFile = null;
const bulkProductInput = $("bulk-product-file");
const bulkProductDropzone = $("bulk-product-dropzone");
function showBulkProductFile(file) {
  bulkProductFile = file || null;
  const submit = $("bulk-product-submit");
  if (!file) {
    $("bulk-product-file-title").textContent = "Choose your filled Excel file";
    $("bulk-product-file-meta").textContent = ".xlsx, .xls or .csv · up to 20,000 rows";
    submit.disabled = true;
    return;
  }
  const size = file.size < 1024 * 1024 ? `${Math.max(1, Math.round(file.size / 1024))} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`;
  $("bulk-product-file-title").textContent = file.name;
  $("bulk-product-file-meta").textContent = `${size} · Ready to import`;
  submit.disabled = false;
}
bulkProductInput.addEventListener("change", () => showBulkProductFile(bulkProductInput.files[0]));
bulkProductDropzone.addEventListener("dragover", (event) => { event.preventDefault(); bulkProductDropzone.classList.add("is-dragover"); });
bulkProductDropzone.addEventListener("dragleave", () => bulkProductDropzone.classList.remove("is-dragover"));
bulkProductDropzone.addEventListener("drop", (event) => {
  event.preventDefault(); bulkProductDropzone.classList.remove("is-dragover");
  const file = event.dataTransfer.files[0];
  if (!file) return;
  try { bulkProductInput.files = event.dataTransfer.files; } catch (e) {}
  showBulkProductFile(file);
});

function renderBulkProductResult(data) {
  const box = $("bulk-product-result");
  const hasErrors = data.errors.length > 0;
  const title = data.variants_created
    ? (hasErrors ? "Import complete with a few rows to fix" : "Your products are ready!")
    : "No products were added";
  const summary = `${data.products_created} product${data.products_created === 1 ? "" : "s"} and ${data.variants_created} size${data.variants_created === 1 ? "" : "s"} added. ${data.rows_skipped} row${data.rows_skipped === 1 ? "" : "s"} skipped.`;
  const errors = data.errors.length ? `<div class="bulk-row-errors"><strong>Rows to review</strong>${data.errors.map((item) => `<div>Row ${item.row}${item.sku ? ` · ${esc(item.sku)}` : ""}: ${esc(item.message)}</div>`).join("")}${data.errors_truncated ? "<div>More row errors were omitted. Correct these and upload again.</div>" : ""}</div>` : "";
  box.innerHTML = `<div class="bulk-import-result ${hasErrors ? "has-errors" : ""}"><div class="bulk-result-mark"><i class="fa-solid ${data.variants_created ? "fa-check" : "fa-triangle-exclamation"}"></i></div><div class="bulk-result-copy"><strong>${title}</strong><p>${summary}</p><div class="bulk-result-numbers"><span class="bulk-result-pill">${data.products_created} products</span><span class="bulk-result-pill">${data.variants_created} size rows</span><span class="bulk-result-pill">${data.blank_rows} blank rows</span></div></div></div>${errors}`;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("bulk-product-submit").addEventListener("click", async () => {
  if (!bulkProductFile) return;
  const button = $("bulk-product-submit"), box = $("bulk-product-result");
  if (bulkProductFile.size > 12 * 1024 * 1024) {
    box.innerHTML = `<div class="msg error">This file is over 12 MB. Split the workbook and upload it in smaller parts.</div>`;
    return;
  }
  button.disabled = true;
  const original = button.innerHTML;
  button.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Importing…`;
  bulkProductDropzone.classList.add("is-loading");
  box.innerHTML = `<p class="hint">Checking sizes and saving products safely…</p>`;
  try {
    const body = new FormData(); body.append("file", bulkProductFile);
    const response = await fetch(`${API}/products/bulk`, { method: "POST", body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Import failed (HTTP ${response.status})`);
    renderBulkProductResult(data);
    if (data.variants_created) {
      playSuccessSound();
      showToast(`${data.products_created} products and ${data.variants_created} sizes added`, "success", 2600);
      $("product-search").value = "";
      window.__keepProductSearch = true;
      await refreshInventoryViews();
    }
  } catch (error) {
    box.innerHTML = `<div class="msg error">${esc(error.message || "Import failed. Check the file and try again.")}</div>`;
    playAlertSound();
  } finally {
    button.innerHTML = original;
    button.disabled = !bulkProductFile;
    bulkProductDropzone.classList.remove("is-loading");
  }
});

// ================================================================ //
// SCAN & RECOUNT  (html5-qrcode: reads both barcodes and QR codes)
// ================================================================ //
let html5QrCode = null;
let scanning = false;
let lastCameraCode = "";
let lastCameraScanAt = 0;

$("scan-toggle").addEventListener("click", () => { scanning ? stopCamera() : startCamera(); });

async function startCamera() {
  const msg = $("scan-msg");
  try {
    // Always cleanly stop an older scanner instance before starting again.
    if (html5QrCode && scanning) {
      await html5QrCode.stop().catch(() => {});
      scanning = false;
    }
    html5QrCode = html5QrCode || new Html5Qrcode("scan-reader");

    const formats = [];
    const F = window.Html5QrcodeSupportedFormats;
    if (F) ["QR_CODE", "CODE_128", "CODE_39", "EAN_13", "EAN_8", "UPC_A", "UPC_E"].forEach((k) => {
      if (F[k] !== undefined) formats.push(F[k]);
    });
    const config = {
      fps: 12,
      qrbox: (vw, vh) => ({
        width: Math.min(Math.floor(vw * 0.82), 420),
        height: Math.min(Math.floor(vh * 0.34), 220)
      }),
      ...(formats.length ? { formatsToSupport: formats } : {})
    };

    // Do NOT force a facingMode constraint. Some Android/Chrome devices
    // expose no "environment" camera and throw OverconstrainedError.
    // We first ask the browser for any usable camera, then give html5-qrcode
    // an actual deviceId when possible. This works with front/rear/USB cameras.
    let cameraCandidates = [];
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera API unavailable");
      const probe = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      probe.getTracks().forEach((track) => track.stop());
    } catch (probeErr) {
      // Permission/security errors are more useful than an opaque constraint error.
      const name = probeErr?.name || "CameraError";
      if (name === "NotAllowedError" || name === "SecurityError") {
        throw new Error("Camera permission denied. Allow camera access for this site and reload the page.");
      }
      if (name === "NotFoundError") {
        throw new Error("No camera was found on this device. You can use a scanner gun or Type Instead.");
      }
    }

    try {
      cameraCandidates = await Html5Qrcode.getCameras();
    } catch (_) {
      cameraCandidates = [];
    }

    const rear = cameraCandidates.find((c) => /back|rear|environment|world/i.test(c.label || ""));
    const ordered = [rear, ...cameraCandidates.filter((c) => c && c !== rear)].filter(Boolean);
    let lastErr = null;

    // Try every real camera ID. If a browser rejects one device constraint,
    // immediately try the next one instead of showing an OverconstrainedError.
    for (const cam of ordered) {
      try {
        await html5QrCode.start(
          cam.id,
          config,
          (decodedText) => {
            const code = decodedText.trim().toUpperCase();
            const now = Date.now();
            if (!code || (code === lastCameraCode && now - lastCameraScanAt < 1200)) return;
            lastCameraCode = code;
            lastCameraScanAt = now;
            lookupVariant(code);
            showToast(`Scanned: ${code}`, "success", 900);
          },
          () => {}
        );
        scanning = true;
        $("scan-toggle").innerHTML = '<i class="fa-solid fa-stop"></i> Stop Camera';
        msg.textContent = `Camera ready — ${rear && cam.id === rear.id ? "rear camera" : "camera"} active. Keep scanning; camera stays on.`;
        msg.className = "msg success";
        return;
      } catch (err) {
        lastErr = err;
        await html5QrCode.stop().catch(() => {});
      }
    }

    // Last fallback: let the library/browser choose any camera without a
    // facingMode/device constraint. This avoids the common OverconstrainedError.
    for (const mode of ["user", "environment"]) {
      try {
        await html5QrCode.start(mode, config, (decodedText) => {
          const code = decodedText.trim().toUpperCase();
          const now = Date.now();
          if (!code || (code === lastCameraCode && now - lastCameraScanAt < 1200)) return;
          lastCameraCode = code;
          lastCameraScanAt = now;
          lookupVariant(code);
          showToast(`Scanned: ${code}`, "success", 900);
        }, () => {});
        scanning = true;
        $("scan-toggle").innerHTML = '<i class="fa-solid fa-stop"></i> Stop Camera';
        msg.textContent = "Camera ready — keep scanning continuously.";
        msg.className = "msg success";
        return;
      } catch (err) {
        lastErr = err;
        await html5QrCode.stop().catch(() => {});
      }
    }

    throw lastErr || new Error("No compatible camera could be opened.");
  } catch (err) {
    scanning = false;
    const raw = String(err?.message || err || "Camera unavailable");
    const friendly = /OverconstrainedError|constraint/i.test(raw)
      ? "This camera does not support the requested camera mode. Try another camera, or use the scanner gun / Type Instead."
      : raw;
    msg.textContent = `Could not access the camera: ${friendly}`;
    msg.className = "msg error";
  }
}
function stopCamera() {
  if (html5QrCode && scanning) { html5QrCode.stop().catch(() => {}); }
  scanning = false;
  $("scan-toggle").textContent = "Start Camera";
}

// USB/Bluetooth barcode-gun support. Scanner guns behave like a very fast
// keyboard and normally finish with Enter. We only intercept fast input while
// the Scan screen is open, so normal typing elsewhere remains untouched.
let gunBuffer = "", gunStartedAt = 0, gunTimer = null;
document.addEventListener("keydown", (e) => {
  if (!$("view-scan")?.classList.contains("is-active")) return;
  const now = performance.now();
  if (e.key === "Enter") {
    const value = gunBuffer.trim();
    const elapsed = gunStartedAt ? now - gunStartedAt : Infinity;
    if (value && value.length >= 4 && elapsed < 1000) {
      e.preventDefault();
      lookupVariant(value.toUpperCase());
      showToast(`Scanned: ${value}`, "success", 1000);
    }
    gunBuffer = ""; gunStartedAt = 0;
    return;
  }
  if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
    if (!gunBuffer) gunStartedAt = now;
    if (now - gunStartedAt > 1000) { gunBuffer = ""; gunStartedAt = now; }
    gunBuffer += e.key;
    clearTimeout(gunTimer);
    gunTimer = setTimeout(() => { gunBuffer = ""; gunStartedAt = 0; }, 1300);
  }
});
$("manual-lookup-form").addEventListener("submit", (e) => {
  e.preventDefault();
  lookupVariant(`${e.target.sku.value.trim().toUpperCase()}-${e.target.size.value.trim().toUpperCase()}`);
});

async function lookupVariant(variantCode) {
  const msg = $("scan-msg"), box = $("scan-result");
  try {
    const product = await getJSON(`${API}/variants/${encodeURIComponent(variantCode)}/product`);
    msg.textContent = "";
    box.hidden = false;
    renderDetail(product, box, { showDelete: false });
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    box.hidden = true;
    msg.textContent = `"${variantCode}" not found — check the SKU and size, or add it from Products first.`;
    msg.className = "msg error";
  }
}

// ================================================================ //
// BULK STOCK COUNT
// ================================================================ //
function updateBulkDownloadLink() {
  const params = new URLSearchParams();
  if ($("bulk-only-uncounted").checked) params.set("only_uncounted", "true");
  $("bulk-download").href = `${API}/inventory/count-sheet?${params}`;
}
$("bulk-only-uncounted").addEventListener("change", updateBulkDownloadLink);

$("bulk-file-input").addEventListener("change", async () => {
  const input = $("bulk-file-input"), file = input.files[0];
  if (!file) return;
  const box = $("bulk-result");
  box.innerHTML = `<p class="hint">Loading ${esc(file.name)}…</p>`;
  const fd = new FormData(); fd.append("file", file);
  try {
    const res = await fetch(`${API}/inventory/bulk-set`, { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) { box.innerHTML = `<div class="msg error">${esc(data.detail)}</div>`; return; }
    box.innerHTML = `
      <div class="stat-row">
        <div class="stat-card"><div class="n">${data.updated_count}</div><div class="l">Sizes updated</div></div>
        <div class="stat-card"><div class="n">${data.skipped_blank_count}</div><div class="l">Blank, skipped</div></div>
        <div class="stat-card"><div class="n">${data.not_found_count}</div><div class="l">Code not found</div></div>
        <div class="stat-card"><div class="n">${data.duplicate_rows_ignored}</div><div class="l">Repeat rows ignored</div></div>
      </div>
      ${data.not_found_count ? `<div class="msg error">Not found: ${data.not_found_sample.map(esc).join(", ")}${data.not_found_count > data.not_found_sample.length ? " …" : ""}</div>` : ""}`;
      await refreshInventoryViews();
    showToast(`${data.updated_count} sizes updated`);
  } catch (err) { box.innerHTML = `<div class="msg error">Upload failed: ${esc(err)}</div>`; }
  finally { input.value = ""; }
});

// ================================================================ //
// ORDERS
// ================================================================ //
async function loadOrderHistory() {
  const box = $("order-history");
  if (!box) return;
  try {
    const batches = await getJSON(`${API}/orders/history?limit=50`);
    if (!batches.length) {
      box.innerHTML = `<div class="panel"><p class="hint center">No order batches processed yet.</p></div>`;
      return;
    }
    box.innerHTML = `<div class="section-title-row"><h2>Processed Order Batches</h2><span class="hint">Saved after refresh</span></div>` + batches.map((b) => {
      const rows = b.lines.map((l) => `<tr><td>${esc(l.sku)}</td><td>${esc(l.size || "—")}</td><td class="hide-sm">${esc(l.product_name || "—")}</td><td class="num">${l.qty_ordered}</td><td>${esc(l.status.replace(/_/g, " "))}</td></tr>`).join("");
      return `<div class="order-batch panel" data-upload-id="${b.order_upload_id}">
        <div class="order-batch-head">
          <div><strong>${esc(b.filename || "Order file")}</strong><div class="hint">${new Date(b.uploaded_at).toLocaleString()} · Batch #${b.order_upload_id}</div></div>
          <div class="order-batch-actions"><span class="status-pill batch-${b.status === "PENDING" ? "pending" : b.status === "DISPATCHED" ? "done" : "warn"}">${esc(b.status)}</span>${b.pending_count ? `<button class="btn-primary batch-dispatch" type="button" data-id="${b.order_upload_id}"><i class="fa-solid fa-truck-fast"></i> Dispatch</button>` : ""}</div>
        </div>
        <div class="order-stats"><span>${b.total_rows} rows</span><span>${b.pending_count} pending</span><span>${b.fulfilled_count} dispatched</span><span>${b.not_found_count} not found</span></div>
        <div class="table-wrap"><table class="grid"><thead><tr><th>SKU</th><th>Size</th><th class="hide-sm">Product</th><th class="num">Orders</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>
      </div>`;
    }).join("");
    box.querySelectorAll(".batch-dispatch").forEach((btn) => btn.addEventListener("click", () => dispatchOrderBatch(Number(btn.dataset.id), true)));
  } catch (e) { box.innerHTML = `<div class="panel"><p class="hint">Could not load order history.</p></div>`; }
}

const dropzone = $("dropzone"), fileInput = $("order-file-input");
dropzone.addEventListener("dragover", (e) => e.preventDefault());
dropzone.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; handleOrderUpload(); } });
fileInput.addEventListener("change", handleOrderUpload);

async function handleOrderUpload() {
  const file = fileInput.files[0];
  if (!file) return;
  const box = $("upload-result");
  box.innerHTML = `<p class="hint">Processing ${esc(file.name)}…</p>`;
  const fd = new FormData(); fd.append("file", file);
  try {
    const res = await fetch(`${API}/orders/upload`, { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) { box.innerHTML = `<div class="msg error">${esc(data.detail)}</div>`; return; }
    renderUploadResult(data);
    await loadOrderHistory();
    await refreshInventoryViews();
  } catch (err) { box.innerHTML = `<div class="msg error">Upload failed: ${esc(err)}</div>`; }
  finally { fileInput.value = ""; }
}

function renderUploadResult(data) {
  const rows = data.lines.map((l) => `
    <tr>
      <td class="sku-tag">${esc(l.sku)}</td>
      <td><span class="size-chip">${esc(l.size || "—")}</span></td>
      <td class="name-cell hide-sm" title="${esc(l.product_name || "")}">${esc(l.product_name || "—")}</td>
      <td class="num">${l.qty_ordered}</td>
      <td class="num">${l.remaining_stock ?? "—"}</td>
      <td><span class="status-pill ${l.status === "PENDING" ? "status-OUT" : l.status === "FULFILLED" ? "status-OK" : l.status === "INSUFFICIENT_STOCK" ? "status-OUT" : "status-UNCOUNTED"}">${esc(l.status.replace(/_/g, " "))}</span>
        ${l.note ? `<div class="hint" style="margin-top:3px;max-width:200px;">${esc(l.note)}</div>` : ""}</td>
    </tr>`).join("");
  $("upload-result").innerHTML = `
    ${data.file_note ? `<div class="msg note" style="margin-bottom:10px;">${esc(data.file_note)}</div>` : ""}
    <div class="stat-row">
      <div class="stat-card"><div class="n">${data.unique_skus}</div><div class="l">Sizes in file</div></div>
      <div class="stat-card"><div class="n">${data.pending_count || 0}</div><div class="l">Pending Orders</div></div>
      <div class="stat-card"><div class="n">${data.not_found_count}</div><div class="l">Not found</div></div>
    </div>
    <div class="msg note" style="margin-bottom:10px;">Physical stock was not deducted. Use Picklist / Reorder Shortage to see what must be arranged. After dispatch, confirm the batch below.</div>
    ${(data.pending_count || 0) ? `<button class="btn-primary" type="button" id="dispatch-order-btn" data-upload-id="${data.order_upload_id}" style="margin-bottom:12px;"><i class="fa-solid fa-truck-fast"></i> Mark This Batch as Dispatched</button>` : ""}
    ${data.new_alerts.length ? `<div class="msg error" style="margin-bottom:10px;">New low-stock alerts: ${data.new_alerts.map(esc).join(", ")}</div>` : ""}
    <div class="table-wrap"><table class="grid">
      <thead><tr><th>SKU</th><th>Size</th><th class="hide-sm">Product</th><th class="num">Ordered</th><th class="num">Physical Stock</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  const btn = $("dispatch-order-btn");
  if (btn) btn.addEventListener("click", () => dispatchOrderBatch(Number(btn.dataset.uploadId)));
}

async function dispatchOrderBatch(uploadId, fromHistory = false) {
  if (!confirm("Dispatch this order batch and deduct the ordered quantity from physical inventory?")) return;
  const btn = $("dispatch-order-btn");
  if (btn) { btn.disabled = true; btn.textContent = "Dispatching…"; }
  try {
    const res = await fetch(`${API}/orders/${uploadId}/dispatch`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Dispatch failed");
    showToast(`${data.dispatched_units} units dispatched`);
    $("upload-result").innerHTML = `<div class="msg success">Batch #${uploadId} dispatched successfully. Physical inventory has been updated.</div>`;
    await refreshInventoryViews();
    await loadOrderHistory();
  } catch (err) {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-truck-fast"></i> Mark This Batch as Dispatched'; }
    $("upload-result").insertAdjacentHTML("afterbegin", `<div class="msg error" style="margin-bottom:10px;">${esc(err.message || err)}</div>`);
  }
}

// ================================================================ //
// REORDER LIST
// ================================================================ //
let reorderPart = "";
$("reorder-part").addEventListener("input", debounce((e) => {
  reorderPart = e.target.value.trim().toUpperCase();
  loadReorderList();
}, 250));

async function loadReorderList() {
  const body = $("reorder-body");
  try {
    const params = new URLSearchParams();
    if (reorderPart) params.set("part", reorderPart);
    const items = await getJSON(`${API}/reorder-list${params.toString() ? "?" + params : ""}`);
    const exportUrl = `${API}/reorder-list/export${reorderPart ? `?part=${encodeURIComponent(reorderPart)}` : ""}`;
    $("export-reorder").href = exportUrl;
    if (!items.length) { body.innerHTML = `<tr><td colspan="6" class="empty">No pending orders to restock right now.</td></tr>`; $("reorder-note").textContent = reorderPart ? `No pending orders for SKU part "${esc(reorderPart)}".` : ""; return; }
    const shown = items.slice(0, LIST_CAP);
    $("reorder-note").textContent = items.length > LIST_CAP ? `Showing ${LIST_CAP} of ${items.length} pending-order lines. Export for the full list.` : `${items.length} pending-order line${items.length === 1 ? "" : "s"}. Required = MAX(Pending Orders - Current Stock, 0).`;
    body.innerHTML = shown.map((i) => `
      <tr class="${i.is_new ? "picklist-new-row" : ""}"><td class="sku-tag">${esc(i.sku)} ${i.is_new ? '<span class="picklist-new-badge">NEW</span>' : ""}</td><td><span class="size-chip">${esc(i.size)}</span></td><td class="num">${i.current_stock}</td><td class="num">${i.ordered_qty}</td><td class="num">${i.dispatched_qty || 0}</td><td class="num" style="font-weight:800;">${i.required_qty}</td></tr>`).join("");
  } catch (e) { body.innerHTML = `<tr><td colspan="6" class="empty">Could not load the picklist.</td></tr>`; }
}

// ================================================================ //
// ALERTS (with inline quick-update)
// ================================================================ //
async function refreshInventoryViews() {
  await Promise.allSettled([loadSummary(), loadInventory(), loadReorderList(), loadAlerts()]);
  if ($("view-products")?.classList.contains("is-active")) await loadLatestProducts();
  if ($("view-dashboard")?.classList.contains("is-active")) await loadMoversChart();
}

async function loadAlerts() {
  const list = $("alerts-list");
  try {
    const [alerts, s] = await Promise.all([getJSON(`${API}/alerts?limit=300`), getJSON(`${API}/summary`)]);
    noteAlertCount(s.open_alerts);
    $("alerts-note").textContent = s.open_alerts > alerts.length ? `Showing the ${alerts.length} lowest of ${s.open_alerts.toLocaleString()} open alerts.` : "";
    if (!alerts.length) { list.innerHTML = `<p class="hint">No open alerts.</p>`; return; }
    list.innerHTML = alerts.map((a) => `
      <div class="alert-row ${a.alert_type}" data-code="${esc(a.variant_code)}">
        <div><div class="name">${esc(a.product_name)} <span class="sku-tag">(${esc(a.variant_code)})</span></div>
          <div class="meta">${a.alert_type === "ORDER_SHORTAGE" ? `Order shortage — Stock ${a.stock_at_alert}, Orders ${a.ordered_qty}, Need ${a.required_qty}` : a.alert_type === "OUT_OF_STOCK" ? `Out of stock — ${a.stock_at_alert} pcs` : `Low stock — ${a.stock_at_alert} pcs left`}</div></div>
        <div class="alert-quick">
          <input type="number" min="0" class="input quick-count" placeholder="new count">
          <button class="btn-secondary quick-save" type="button">Update</button>
        </div>
      </div>`).join("");
    list.querySelectorAll(".alert-row").forEach((row) => {
      row.querySelector(".quick-save").addEventListener("click", async () => {
        const input = row.querySelector(".quick-count");
        if (input.value === "") { showToast("Enter a count first", true); return; }
        const button = row.querySelector(".quick-save");
        if (button.disabled) return;
        button.disabled = true;
        try {
          const r = await apiPost(`${API}/inventory/adjust`, { variant_code: row.dataset.code, new_count: Number(input.value), note: "alert quick-update" });
          showToast(`${row.dataset.code} set to ${r.current_stock}`);
          await refreshInventoryViews();
        } catch (err) { showToast("Update failed: " + err.message, true); }
        finally { button.disabled = false; }
      });
    });
  } catch (e) { list.innerHTML = `<p class="hint">Could not load alerts.</p>`; }
}

// ================================================================ //
// HISTORY
// ================================================================ //
const history = { q: "", offset: 0 };
$("history-search").addEventListener("input", debounce((e) => { history.q = e.target.value.trim(); history.offset = 0; loadHistory(true); }));
$("hist-more").addEventListener("click", () => loadHistory(false));

async function loadHistory(reset) {
  const body = $("history-body");
  const params = new URLSearchParams({ limit: 50, offset: history.offset });
  if (history.q) params.set("q", history.q);
  try {
    const rows = await getJSON(`${API}/transactions?${params}`);
    const html = rows.map((t) => `
      <tr>
        <td class="hide-sm" style="white-space:nowrap;">${new Date(t.created_at).toLocaleString()}</td>
        <td class="sku-tag">${esc(t.sku)}</td>
        <td class="name-cell hide-sm" title="${esc(t.product_name)}">${esc(t.product_name)}</td>
        <td><span class="size-chip">${esc(t.size)}</span></td>
        <td class="num" style="color:${t.change_qty < 0 ? "var(--out)" : "var(--ok)"}">${t.change_qty > 0 ? "+" : ""}${t.change_qty}</td>
        <td class="num">${t.balance_after}</td>
        <td class="hide-sm">${esc(t.transaction_type.replace(/_/g, " "))}</td>
        <td class="hide-sm hint">${esc(t.reference || "")}</td>
      </tr>`).join("");
    body.innerHTML = reset ? (html || `<tr><td colspan="8" class="empty">No history yet.</td></tr>`) : body.innerHTML + html;
    history.offset += rows.length;
    $("hist-more").disabled = rows.length < 50;
  } catch (e) { if (reset) body.innerHTML = `<tr><td colspan="8" class="empty">Could not load history.</td></tr>`; }
}

// ================================================================ //
// PRINT LABELS
// ================================================================ //
$("label-search").addEventListener("input", debounce(async (e) => {
  const q = e.target.value.trim(), box = $("label-results");
  if (q.length < 2) { box.innerHTML = ""; return; }
  try {
    const products = await getJSON(`${API}/products?q=${encodeURIComponent(q)}&limit=15`);
    box.innerHTML = products.length ? products.map((p) => `<button type="button" class="result-row" data-sku="${esc(p.sku)}">${esc(p.sku)} — ${esc(p.name)}</button>`).join("") : `<p class="hint">No design matches "${esc(q)}".</p>`;
    box.querySelectorAll(".result-row").forEach((btn, i) => btn.addEventListener("click", () => selectLabelProduct(products[i])));
  } catch (err) { box.innerHTML = ""; }
}));

function selectLabelProduct(product) {
  $("label-results").innerHTML = "";
  $("label-selected").hidden = false;
  $("label-selected-name").textContent = `${product.sku} — ${product.name}`;
  $("label-variant-checks").innerHTML = product.variants.map((v) => `<label class="check-row"><input type="checkbox" value="${esc(v.variant_code)}" checked> ${esc(v.variant_code)}</label>`).join("");
  $("label-variant-checks").querySelectorAll("input").forEach((cb) => cb.addEventListener("change", updateLabelDownloadLink));
  updateLabelDownloadLink();
}
function updateLabelDownloadLink() {
  const codes = Array.from(document.querySelectorAll("#label-variant-checks input:checked")).map((cb) => cb.value);
  $("label-download").href = codes.length ? `${API}/labels/sheet?variant_codes=${encodeURIComponent(codes.join(","))}` : "#";
}

// ================================================================ //
// TRASH
// ================================================================ //
async function loadTrash() {
  const grid = $("trash-list"), empty = $("trash-empty");
  try {
    const items = await getJSON(`${API}/trash`);
    if (!items.length) { grid.innerHTML = ""; empty.hidden = false; return; }
    empty.hidden = true;
    grid.innerHTML = items.map((t) => `
      <article class="tcard" data-sku="${esc(t.sku)}">
        <div class="tcard-photo-wrap">
          ${t.image_path ? `<img class="trash-photo" src="${esc(t.image_path)}?v=${Date.now()}" alt="${esc(t.name)}">` : `<div class="trash-photo placeholder"><i class="fa-solid fa-image"></i></div>`}
        </div>
        <div class="tcard-info">
          <div class="tcard-name" title="${esc(t.name)}">${esc(t.name)}</div>
          <div class="tcard-meta"><span class="sku-tag">${esc(t.sku)}</span></div>
          <div class="tcard-meta">${t.variant_count} size${t.variant_count === 1 ? "" : "s"} · deleted ${new Date(t.deleted_at).toLocaleDateString()}</div>
        </div>
        <div class="tcard-actions">
          <button class="btn-secondary trash-restore" type="button"><i class="fa-solid fa-rotate-left"></i><span>Restore</span></button>
          <button class="btn-danger trash-wipe" type="button"><i class="fa-solid fa-trash-can"></i><span>Delete forever</span></button>
        </div>
      </article>`).join("");
    grid.querySelectorAll(".trash-restore").forEach((btn) => btn.addEventListener("click", async () => {
      const sku = btn.closest(".tcard").dataset.sku;
      try { await apiPost(`${API}/products/${encodeURIComponent(sku)}/restore`); showToast(`${sku} restored successfully`, "success"); loadTrash(); loadSummary(); }
      catch (err) { showToast("Restore failed: " + err.message, "error", 2200); }
    }));
    grid.querySelectorAll(".trash-wipe").forEach((btn) => btn.addEventListener("click", () => {
      const sku = btn.closest(".tcard").dataset.sku;
      askConfirm("Delete forever?", `${sku} and its full history will be permanently removed. This cannot be undone.`, async () => {
        try { await apiDelete(`${API}/trash/${encodeURIComponent(sku)}`); showToast(`${sku} permanently deleted`, "success"); loadTrash(); }
        catch (err) { showToast("Delete failed: " + err.message, "error", 2200); }
      }, "Delete Forever");
    }));
  } catch (e) { grid.innerHTML = `<p class="hint">Could not load Trash.</p>`; }
}


// ---------------- Init ----------------
loadSummary();
loadInventory();
loadMoversChart();
loadOrderHistory();
