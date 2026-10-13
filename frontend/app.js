const API = "/api";
const STANDARD_SIZES = ["S", "M", "L", "XL", "XXL"];
const PAGE_SIZE = 100;

const $ = (id) => document.getElementById(id);

// A small, non-blocking sweep tells staff when any app API request is taking
// longer than a quick response. Keep it local to /api so static assets do not
// flash the indicator while rendering a page.
const appLoadingIndicator = $("app-loading-indicator");
const nativeFetch = window.fetch.bind(window);
let appApiRequests = 0;
let appLoadingTimer = null;
window.fetch = (input, init) => {
  let isAppApi = false;
  try {
    const url = new URL(typeof input === "string" ? input : input.url, window.location.href);
    isAppApi = url.origin === window.location.origin && url.pathname.startsWith("/api/");
  } catch {}
  if (!isAppApi) return nativeFetch(input, init);
  appApiRequests += 1;
  if (appApiRequests === 1) appLoadingTimer = setTimeout(() => appLoadingIndicator?.classList.add("is-visible"), 320);
  return nativeFetch(input, init).finally(() => {
    appApiRequests = Math.max(0, appApiRequests - 1);
    if (!appApiRequests) {
      clearTimeout(appLoadingTimer);
      appLoadingIndicator?.classList.remove("is-visible");
    }
  });
};

// Theme choice is local to this browser and is restored before the page paints.
function setTheme(theme) {
  const isDark = theme === "dark";
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
  try { localStorage.setItem("inventoryTheme", isDark ? "dark" : "light"); } catch (e) {}
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    const label = isDark ? "Switch to light mode" : "Switch to dark mode";
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    button.setAttribute("aria-pressed", String(isDark));
    button.innerHTML = `<i class="fa-solid ${isDark ? "fa-sun" : "fa-moon"}" aria-hidden="true"></i>${button.classList.contains("rail-link") ? `<span class="rail-label">${isDark ? "Light mode" : "Dark mode"}</span>` : ""}`;
  });
}
setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
document.querySelectorAll("[data-theme-toggle]").forEach((button) => button.addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}));

const assistantProductNameBySku = new Map();
function registerAssistantProducts(items = []) {
  items.forEach((product) => {
    if (product?.sku && product?.name) assistantProductNameBySku.set(String(product.sku).toUpperCase(), String(product.name));
  });
}
function makeSpeechFriendly(message) {
  let speech = String(message);
  // Replace known codes with the design name, then make catalog-style names
  // readable for speech (for example KIDS_NK_101 -> KIDS NK 101).
  [...assistantProductNameBySku.entries()]
    .sort((a, b) => b[0].length - a[0].length)
    .forEach(([sku, name]) => {
      const escapedSku = sku.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const spokenName = name.replace(/[_-]+/g, " ");
      speech = speech.replace(new RegExp(`(^|[^a-z0-9])${escapedSku}(?![a-z0-9])`, "gi"), (_match, prefix) => `${prefix}${spokenName}`);
    });
  [...new Set(assistantProductNameBySku.values())]
    .sort((a, b) => b.length - a.length)
    .forEach((name) => {
      const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      speech = speech.replace(new RegExp(`(^|[^a-z0-9])${escapedName}(?![a-z0-9])`, "gi"), (_match, prefix) => `${prefix}${name.replace(/[_-]+/g, " ")}`);
    });
  // Do not read any remaining SKU-like code aloud; keep product names spoken
  // as words instead of announcing underscores or hyphens.
  return speech
    .replace(/\b[A-Z0-9]+(?:[_-][A-Z0-9]+)+\b/gi, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

// Keep the page behind dialogs still; touch scrolling stays inside the modal.
const modalScrollObserver = new MutationObserver(() => {
  document.body.classList.toggle("modal-open", Boolean(document.querySelector(".modal-backdrop:not([hidden])")));
});
document.querySelectorAll(".modal-backdrop").forEach((modal) => modalScrollObserver.observe(modal, { attributes: true, attributeFilter: ["hidden"] }));
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
async function apiPatch(url, body) {
  const r = await fetch(url, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
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

// ---------------- Success sound ----------------
let audioCtx = null;
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
    showToast("New inventory alert — check Alerts", "alert", 2200);
  }
  lastAlertCount = n;
  setAlertBadge(n);
}

let assistantSpeaking = false;
let assistantSpeechId = 0;
let assistantSpeechGuardTimer = null;
let assistantIgnoreTranscriptUntil = 0;
let assistantCurrentPhotoProduct = null;
let assistantPhotoZoom = 1;
let assistantAudioPlayer = null;
let assistantAudioObjectUrl = null;
function finishAssistantSpeech(speechId) {
  if (speechId !== assistantSpeechId) return;
  assistantSpeaking = false;
  assistantIgnoreTranscriptUntil = Date.now() + 900;
  clearTimeout(assistantSpeechGuardTimer);
  assistantAudioPlayer = null;
  if (assistantAudioObjectUrl) URL.revokeObjectURL(assistantAudioObjectUrl);
  assistantAudioObjectUrl = null;
}
function speakWithBrowserVoice(speechId, message) {
  if (speechId !== assistantSpeechId || assistantPaused || !window.speechSynthesis || !window.SpeechSynthesisUtterance) {
    finishAssistantSpeech(speechId);
    return;
  }
  const speechText = makeSpeechFriendly(message);
  const utterance = new SpeechSynthesisUtterance(speechText);
  const hasHindi = /[\u0900-\u097F]/.test(speechText);
  utterance.lang = hasHindi ? "hi-IN" : "en-IN";
  utterance.rate = 0.94;
  utterance.pitch = 1.1;
  const available = window.speechSynthesis.getVoices();
  const matching = available.filter((voice) => hasHindi ? /^hi(-|_)?in/i.test(voice.lang) : /^en(-|_)?in/i.test(voice.lang));
  const friendlyVoice = matching.find((voice) => /female|coral|shimmer|google हिन्दी|google hindi|swara|kalpana|heera|priya|neerja/i.test(voice.name));
  // Never substitute a Hindi voice for English (or vice versa): that was the
  // source of the noticeably different accent in the browser fallback.
  if (friendlyVoice) utterance.voice = friendlyVoice;
  utterance.onend = () => finishAssistantSpeech(speechId);
  utterance.onerror = () => finishAssistantSpeech(speechId);
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
  assistantSpeechGuardTimer = setTimeout(() => finishAssistantSpeech(speechId), Math.max(10000, speechText.length * 180));
}
async function speakAssistantMessage(message) {
  if (assistantPaused) return;
  const speechId = ++assistantSpeechId;
  window.speechSynthesis?.cancel();
  assistantAudioPlayer?.pause();
  if (assistantAudioObjectUrl) URL.revokeObjectURL(assistantAudioObjectUrl);
  assistantAudioObjectUrl = null;
  clearTimeout(assistantSpeechGuardTimer);
  assistantSpeaking = true;
  try {
    const response = await fetch(`${API}/assistant/speech`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: makeSpeechFriendly(message).slice(0, 1200) }),
    });
    if (!response.ok) throw new Error(`Speech API ${response.status}`);
    if (speechId !== assistantSpeechId || assistantPaused) return;
    const audioBlob = await response.blob();
    assistantAudioObjectUrl = URL.createObjectURL(audioBlob);
    const player = new Audio(assistantAudioObjectUrl);
    assistantAudioPlayer = player;
    player.onended = () => finishAssistantSpeech(speechId);
    player.onerror = () => finishAssistantSpeech(speechId);
    await player.play();
    assistantSpeechGuardTimer = setTimeout(() => finishAssistantSpeech(speechId), Math.max(15000, String(message).length * 240));
  } catch (_) {
    speakWithBrowserVoice(speechId, message);
  }
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
$("sidebar-collapse").addEventListener("click", () => {
  const collapsed = $("shell").classList.toggle("sidebar-collapsed");
  localStorage.setItem("sidebarCollapsed", String(collapsed));
  $("sidebar-collapse").setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
  $("sidebar-collapse").title = collapsed ? "Expand sidebar" : "Collapse sidebar";
});
if (localStorage.getItem("sidebarCollapsed") === "true") $("shell").classList.add("sidebar-collapsed");
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
}, 30000);

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
$("product-search").addEventListener("input", debounce((e) => {
  productPage.q = e.target.value.trim();
  productPage.offset = 0;
  loadProductsPage();
}, 300));

const productPage = { q: "", offset: 0, limit: 12, requestId: 0 };
async function loadLatestProducts() {
  productPage.q = "";
  productPage.offset = 0;
  $("product-search").value = "";
  await loadProductsPage();
}

async function loadProductsPage() {
  const grid = $("product-cards"), hint = $("product-search-hint");
  const requestId = ++productPage.requestId;
  try {
    const params = new URLSearchParams({ limit: productPage.limit, offset: productPage.offset, paged: "true" });
    if (productPage.q) params.set("q", productPage.q);
    const page = await getJSON(`${API}/products?${params}`);
    if (requestId !== productPage.requestId) return;
    const products = page.items;
    if (!products.length) {
      grid.innerHTML = "";
      hint.hidden = false;
      hint.textContent = productPage.q ? `No design matches "${productPage.q}".` : "No products yet. Add your first product.";
      $("product-result-count").textContent = "";
      $("products-page-info").textContent = "";
      $("products-prev").disabled = $("products-next").disabled = true;
      return;
    }
    hint.hidden = true;
    $("product-result-count").textContent = productPage.q ? `Search results · ${page.total} designs` : `${page.total} designs`;
    $("products-page-info").textContent = `${productPage.offset + 1}–${Math.min(productPage.offset + products.length, page.total)} of ${page.total}`;
    $("products-prev").disabled = productPage.offset === 0;
    $("products-next").disabled = productPage.offset + products.length >= page.total;
    grid.innerHTML = products.map(productCardHTML).join("");
    grid.querySelectorAll(".pcard").forEach((card) => card.addEventListener("click", () => openProductDetail(card.dataset.sku)));
  } catch (e) {
    if (requestId !== productPage.requestId) return;
    grid.innerHTML = "";
    hint.hidden = false;
    hint.textContent = "Could not load products right now.";
  }
}

async function searchProducts(q) {
  productPage.q = q;
  productPage.offset = 0;
  await loadProductsPage();
}
$("products-prev").addEventListener("click", () => { productPage.offset = Math.max(0, productPage.offset - productPage.limit); loadProductsPage(); });
$("products-next").addEventListener("click", () => { productPage.offset += productPage.limit; loadProductsPage(); });

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
        <a class="btn-ghost product-qr-link" href="${API}/products/${encodeURIComponent(product.sku)}/barcode" target="_blank" rel="noopener"><i class="fa-solid fa-barcode"></i> Product barcode (all sizes)</a>
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
  bulkProductFile = null;
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
    const data = await apiPost(`${API}/products`, {
      sku: form.elements.namedItem("sku").value,
      name: form.elements.namedItem("name").value,
      category: form.elements.namedItem("category").value,
      variants,
    });
    let photoWarning = "";
    if (addPhotoFile) {
      const fd = new FormData(); fd.append("file", addPhotoFile);
      try {
        const imageResponse = await fetch(`${API}/products/${encodeURIComponent(data.sku)}/image`, { method: "POST", body: fd });
        if (!imageResponse.ok) {
          const imageError = await imageResponse.json().catch(() => ({}));
          throw new Error(imageError.detail || `Photo upload failed (${imageResponse.status})`);
        }
      } catch (error) {
        photoWarning = ` Product was added, but its photo was not uploaded: ${error.message || "try again from Edit"}.`;
      }
    }
    const newSku = data.sku;
    form.reset(); resetAddForm(); msg.textContent = "";
    $("add-product-modal").hidden = true;
    showToast(photoWarning ? `Product added — ${newSku}.${photoWarning}` : `Product added successfully — ${newSku}`, photoWarning ? "info" : "success", photoWarning ? 4200 : 1500);
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
  const changed = data.variants_created || data.variants_merged;
  const title = changed
    ? (hasErrors ? "Import complete with a few rows to fix" : "Your products are ready!")
    : "No products were added";
  const summary = `${data.products_created} new product${data.products_created === 1 ? "" : "s"}; ${data.products_merged || 0} old size-product${data.products_merged === 1 ? "" : "s"} grouped into designs. ${data.variants_created} sizes added, ${data.variants_merged || 0} existing sizes kept. ${data.duplicate_rows_merged || 0} duplicate listing rows matched; ${data.free_size_rows || 0} missing-size rows imported as FREE SIZE. ${data.rows_skipped} row${data.rows_skipped === 1 ? "" : "s"} skipped.`;
  const errors = data.errors.length ? `<div class="bulk-row-errors"><strong>Rows to review</strong>${data.errors.map((item) => `<div>Row ${item.row}${item.sku ? ` · ${esc(item.sku)}` : ""}: ${esc(item.message)}</div>`).join("")}${data.errors_truncated ? "<div>More row errors were omitted. Correct these and upload again.</div>" : ""}</div>` : "";
  box.innerHTML = `<div class="bulk-import-result ${hasErrors ? "has-errors" : ""}"><div class="bulk-result-mark"><i class="fa-solid ${changed ? "fa-check" : "fa-triangle-exclamation"}"></i></div><div class="bulk-result-copy"><strong>${title}</strong><p>${summary}</p><div class="bulk-result-numbers"><span class="bulk-result-pill">${data.products_created} new designs</span><span class="bulk-result-pill">${data.variants_created + (data.variants_merged || 0)} sizes grouped</span><span class="bulk-result-pill">${data.blank_rows} blank rows</span></div></div></div>${errors}`;
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
    bulkProductFile = null;
    bulkProductInput.value = "";
    $("bulk-product-file-title").textContent = "Import finished — choose another file";
    $("bulk-product-file-meta").textContent = "Select a new workbook to start the next import";
    if (data.variants_created || data.variants_merged) {
      playSuccessSound();
      showToast(`${data.products_created} new products; ${data.variants_created} sizes added and ${data.variants_merged || 0} grouped`, "success", 2600);
      $("product-search").value = "";
      window.__keepProductSearch = true;
      await refreshInventoryViews();
    }
  } catch (error) {
    box.innerHTML = `<div class="msg error">${esc(error.message || "Import failed. Check the file and try again.")}</div>`;
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
            handleScannedCode(code);
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
          handleScannedCode(code);
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
      handleScannedCode(value.toUpperCase());
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
const manualProductSearch = $("manual-product-search");
const manualProductResults = $("manual-product-results");

function scanStockCard(product, onlyVariantCode = null, includeHeader = true) {
  const variants = (product.variants || []).filter((variant) => !onlyVariantCode || variant.variant_code.toUpperCase() === onlyVariantCode.toUpperCase());
  return `<section class="scan-product-card">
    ${includeHeader ? `<header><strong>${esc(product.name)}</strong><span class="sku-tag">${esc(product.sku)}</span></header>` : ""}
    ${product.category ? `<small class="scan-product-category">${esc(product.category)}</small>` : ""}
    <div class="scan-size-list">${variants.map((variant) => `<form class="scan-stock-form" data-code="${esc(variant.variant_code)}" data-current="${Number(variant.current_stock)}">
      <span class="size-chip">${esc(variant.size)}</span>
      <span class="scan-current-stock">Current: <b>${Number(variant.current_stock)}</b></span>
      <input class="input scan-new-count" type="number" min="0" step="1" inputmode="numeric" required placeholder="New count" aria-label="New stock count for ${esc(product.name)} size ${esc(variant.size)}">
      <button class="btn-primary scan-stock-save" type="submit">Update</button>
      <small class="scan-stock-feedback" aria-live="polite"></small>
    </form>`).join("") || '<p class="hint">No active sizes are available.</p>'}</div>
  </section>`;
}

function attachScanStockForms(container) {
  container.querySelectorAll(".scan-stock-form").forEach((form) => form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = form.querySelector(".scan-new-count"), button = form.querySelector(".scan-stock-save"), feedback = form.querySelector(".scan-stock-feedback");
    const newCount = Number(input.value);
    if (!Number.isInteger(newCount) || newCount < 0) { feedback.textContent = "Enter a whole count of zero or more."; return; }
    const oldCount = Number(form.dataset.current);
    button.disabled = true;
    feedback.textContent = "Saving…";
    try {
      const updated = await apiPost(`${API}/inventory/adjust`, {
        variant_code: form.dataset.code, new_count: newCount,
        expected_current_stock: oldCount, note: "scan and recount"
      });
      form.dataset.current = String(updated.current_stock);
      form.querySelector(".scan-current-stock b").textContent = updated.current_stock;
      input.value = "";
      feedback.textContent = "Stock updated.";
      showToast(`${form.dataset.code}: stock updated to ${updated.current_stock}`, "success", 2200);
      await loadSummary();
    } catch (error) {
      feedback.textContent = error.message || "Update failed. Refresh and try again.";
    } finally { button.disabled = false; }
  }));
}

async function searchManualProducts() {
  const query = manualProductSearch.value.trim();
  manualProductResults.hidden = true;
  if (query.length < 2) return;
  manualProductResults.hidden = false;
  manualProductResults.innerHTML = '<div class="manual-product-empty">Searching designs…</div>';
  try {
    const params = new URLSearchParams({ q: query, limit: "12", paged: "true" });
    const page = await getJSON(`${API}/products?${params}`);
    // Ignore a response for an older input value if the user has kept typing.
    if (manualProductSearch.value.trim() !== query) return;
    if (!page.items.length) {
      manualProductResults.innerHTML = '<div class="manual-product-empty">No matching design. Try another name or colour.</div>';
      return;
    }
    manualProductResults.innerHTML = page.items.map((product) => scanStockCard(product)).join("");
    attachScanStockForms(manualProductResults);
  } catch (error) {
    if (manualProductSearch.value.trim() === query) manualProductResults.innerHTML = '<div class="manual-product-empty">Could not search products. Try again.</div>';
  }
}
manualProductSearch.addEventListener("input", debounce(searchManualProducts, 220));
manualProductSearch.addEventListener("focus", () => { if (manualProductSearch.value.trim().length >= 2) searchManualProducts(); });

async function lookupVariant(variantCode) {
  const msg = $("scan-msg"), box = $("scan-result");
  try {
    const product = await getJSON(`${API}/variants/${encodeURIComponent(variantCode)}/product`);
    msg.textContent = "";
    box.hidden = false;
    renderScanProduct(product, variantCode);
  } catch (e) {
    box.hidden = true;
    msg.textContent = `"${variantCode}" not found — check the SKU and size, or add it from Products first.`;
    msg.className = "msg error";
  }
}

function handleScannedCode(scannedValue) {
  const code = String(scannedValue || "").trim().toUpperCase();
  const productQr = code.match(/^(?:PRODUCT|DESIGN):(.+)$/);
  if (productQr) return lookupProduct(productQr[1]);
  return lookupVariant(code);
}

async function lookupProduct(sku) {
  const msg = $("scan-msg"), box = $("scan-result");
  try {
    const product = await getJSON(`${API}/products/${encodeURIComponent(sku)}`);
    msg.textContent = `Design QR scanned: ${product.name} · ${product.variants.length} sizes`;
    msg.className = "msg success";
    renderScanProduct(product);
  } catch (error) {
    box.hidden = true;
    msg.textContent = `Design QR “${sku}” not found. Check that the design is active.`;
    msg.className = "msg error";
  }
}

function renderScanProduct(product, onlyVariantCode = null) {
  const box = $("scan-result");
  const normalizedVariantCode = String(onlyVariantCode || "").toUpperCase();
  const variants = (product.variants || []).filter((variant) => !normalizedVariantCode || String(variant.variant_code || "").toUpperCase() === normalizedVariantCode);
  box.hidden = false;
  box.innerHTML = `<div class="scan-result-head"><div><h2>${esc(product.name)}</h2><span class="sku-tag">${esc(product.sku)}</span></div><span class="hint">${onlyVariantCode ? "Barcode · one size" : "Product QR · all sizes"}</span></div>${scanStockCard({ ...product, variants }, null, false)}`;
  attachScanStockForms(box);
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
const ordersPage = { offset: 0, limit: 25 };
$("orders-prev").addEventListener("click", () => { ordersPage.offset = Math.max(0, ordersPage.offset - ordersPage.limit); loadOrderHistory(); });
$("orders-next").addEventListener("click", () => { ordersPage.offset += ordersPage.limit; loadOrderHistory(); });
async function loadOrderHistory() {
  const box = $("order-history");
  if (!box) return;
  try {
    const page = await getJSON(`${API}/orders/history?limit=${ordersPage.limit}&offset=${ordersPage.offset}&paged=true`);
    const batches = page.items;
    $("orders-page-info").textContent = batches.length ? `${ordersPage.offset + 1}–${Math.min(ordersPage.offset + batches.length, page.total)} of ${page.total}` : "";
    $("orders-prev").disabled = ordersPage.offset === 0;
    $("orders-next").disabled = ordersPage.offset + batches.length >= page.total;
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
    ordersPage.offset = 0;
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
$("reorder-prev").addEventListener("click", () => { reorderPage.offset = Math.max(0, reorderPage.offset - reorderPage.limit); loadReorderList(); });
$("reorder-next").addEventListener("click", () => { reorderPage.offset += reorderPage.limit; loadReorderList(); });
$("reorder-part").addEventListener("input", debounce((e) => {
  reorderPart = e.target.value.trim().toUpperCase();
  reorderPage.offset = 0;
  loadReorderList();
}, 250));

const reorderPage = { offset: 0, limit: 50, requestId: 0 };
async function loadReorderList() {
  const body = $("reorder-body");
  const requestId = ++reorderPage.requestId;
  try {
    const params = new URLSearchParams();
    params.set("limit", reorderPage.limit);
    params.set("offset", reorderPage.offset);
    if (reorderPart) params.set("part", reorderPart);
    const page = await getJSON(`${API}/reorder-list?${params}`);
    if (requestId !== reorderPage.requestId) return;
    const exportUrl = `${API}/reorder-list/export${reorderPart ? `?part=${encodeURIComponent(reorderPart)}` : ""}`;
    $("export-reorder").href = exportUrl;
    $("reorder-page-info").textContent = page.total ? `${page.offset + 1}–${Math.min(page.offset + page.items.length, page.total)} of ${page.total} sizes` : "";
    $("reorder-prev").disabled = page.offset === 0;
    $("reorder-next").disabled = page.offset + page.limit >= page.total;
    if (!page.items.length) { body.innerHTML = `<tr><td colspan="6" class="empty">No pending orders to restock right now.</td></tr>`; $("reorder-note").textContent = reorderPart ? `No pending orders for SKU part "${esc(reorderPart)}".` : ""; return; }
    $("reorder-note").textContent = `Restock = pending order quantity minus usable stock. Any existing negative balance is included in the required quantity.`;
    body.innerHTML = page.items.map((i) => `
      <tr class="${i.is_new ? "picklist-new-row" : ""}"><td class="sku-tag">${esc(i.sku)} ${i.is_new ? '<span class="picklist-new-badge">NEW</span>' : ""}</td><td><span class="size-chip">${esc(i.size)}</span></td><td class="num">${i.current_stock < 0 ? `<span class="negative-stock" title="Legacy stock deficit">${Math.abs(i.current_stock)} short</span>` : i.current_stock}</td><td class="num">${i.ordered_qty}</td><td class="num">${i.dispatched_qty || 0}</td><td class="num" style="font-weight:800;">${i.required_qty}</td></tr>`).join("");
  } catch (e) { if (requestId === reorderPage.requestId) body.innerHTML = `<tr><td colspan="6" class="empty">Could not load the picklist.</td></tr>`; }
}

// ================================================================ //
// ALERTS (with inline quick-update)
// ================================================================ //
async function refreshInventoryViews() {
  await Promise.allSettled([loadSummary(), loadInventory(), loadReorderList(), loadAlerts()]);
  if ($("view-products")?.classList.contains("is-active")) await loadLatestProducts();
  if ($("view-dashboard")?.classList.contains("is-active")) await loadMoversChart();
}

// ---------------- Global conversational inventory assistant ----------------
const assistantHistoryKey = "inventoryAssistantHistory.v1";
const assistantSessionsKey = "inventoryAssistantSessions.v1";
const assistantSessionIdKey = "inventoryAssistantSessionId.v1";
const assistantHistoryLimit = 60;
let assistantHistory = [];
let assistantSessions = [];
let assistantSessionId = localStorage.getItem(assistantSessionIdKey) || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
let assistantBusy = false;
let assistantRequestSequence = 0;
let assistantMicStream = null;
let assistantMicContext = null;
let assistantMicAnalyser = null;
let assistantMicFrame = 0;
let assistantMicRecorder = null;
let assistantMicChunks = [];
let assistantMicStarting = false;
let assistantMicSpeakingSince = 0;
let assistantMicLastVoiceAt = 0;
let assistantMicRecordingStartedAt = 0;
let assistantVoiceProcessing = false;
let assistantWakeArmed = false;
let assistantLastVoiceErrorAt = 0;
// Privacy by default: opening the app never turns on the microphone.
// The user must tap the Disha launcher (or Start voice) for each session.
let assistantPaused = true;
let assistantWakeEnabled = false;
let assistantMicBlocked = false;
let assistantPermissionNoticeShown = false;
let assistantConfirmationButton = null;
try {
  const saved = JSON.parse(localStorage.getItem(assistantHistoryKey) || "[]");
  if (Array.isArray(saved)) assistantHistory = saved.filter((item) => item && ["user", "assistant"].includes(item.role) && typeof item.content === "string").slice(-assistantHistoryLimit);
} catch (error) { assistantHistory = []; }
try {
  const savedSessions = JSON.parse(localStorage.getItem(assistantSessionsKey) || "[]");
  if (Array.isArray(savedSessions)) assistantSessions = savedSessions.filter((session) => session && typeof session.id === "string" && Array.isArray(session.messages)).slice(0, 30);
} catch (error) { assistantSessions = []; }

function saveAssistantHistory() {
  assistantHistory = assistantHistory.slice(-assistantHistoryLimit);
  try {
    localStorage.setItem(assistantHistoryKey, JSON.stringify(assistantHistory));
    localStorage.setItem(assistantSessionIdKey, assistantSessionId);
    if (assistantHistory.length) {
      const firstUserMessage = assistantHistory.find((item) => item.role === "user")?.content || "New conversation";
      const session = { id: assistantSessionId, title: firstUserMessage.slice(0, 72), updatedAt: new Date().toISOString(), messages: assistantHistory };
      assistantSessions = [session, ...assistantSessions.filter((item) => item.id !== assistantSessionId)].slice(0, 30);
      localStorage.setItem(assistantSessionsKey, JSON.stringify(assistantSessions));
    }
  }
  catch (error) { showToast("Chat history could not be saved in this browser.", "info", 2500); }
}

function renderAssistantHistory() {
  const list = $("assistant-history-list");
  if (!list) return;
  list.replaceChildren();
  const previousSessions = assistantSessions.filter((session) => session.id !== assistantSessionId);
  if (!previousSessions.length) {
    const empty = document.createElement("p");
    empty.className = "assistant-history-empty";
    empty.textContent = "No previous conversations yet.";
    list.appendChild(empty);
  } else {
    previousSessions.forEach((session) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "assistant-history-item";
      const title = document.createElement("strong");
      title.textContent = session.title || "Conversation";
      const date = document.createElement("small");
      date.textContent = session.updatedAt ? new Date(session.updatedAt).toLocaleString() : "Saved conversation";
      button.append(title, date);
      button.addEventListener("click", () => openAssistantSession(session.id));
      list.appendChild(button);
    });
  }
  list.hidden = false;
}

function openAssistantSession(sessionId) {
  const session = assistantSessions.find((item) => item.id === sessionId);
  if (!session) return;
  assistantRequestSequence += 1;
  assistantBusy = false;
  $("assistant-send").disabled = false;
  assistantSessionId = session.id;
  assistantHistory = session.messages.slice(-assistantHistoryLimit);
  assistantConfirmationButton = null;
  localStorage.setItem(assistantHistoryKey, JSON.stringify(assistantHistory));
  localStorage.setItem(assistantSessionIdKey, assistantSessionId);
  $("assistant-history-list").hidden = true;
  $("assistant-panel").hidden = false;
  $("assistant-launcher").setAttribute("aria-expanded", "true");
  restoreAssistantMessages();
}

function startNewAssistantConversation({ fromVoice = false } = {}) {
  if (assistantHistory.length) saveAssistantHistory();
  assistantRequestSequence += 1;
  assistantBusy = false;
  const send = $("assistant-send");
  if (send) send.disabled = false;
  assistantHistory = [];
  assistantConfirmationButton = null;
  assistantSessionId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  localStorage.setItem(assistantHistoryKey, "[]");
  localStorage.setItem(assistantSessionIdKey, assistantSessionId);
  $("assistant-messages")?.replaceChildren();
  $("assistant-history-list").hidden = true;
  openAssistant({ speakGreeting: true, focusInput: !fromVoice, greetingText: fromVoice ? "Ji, nayi conversation shuru kar di. Ab pehle se alag baat kar sakte hain." : undefined });
}

function isNewConversationRequest(message) {
  return /\b(new\s+(?:chat|conversation|convo|session)|start\s+(?:a\s+)?fresh\s+(?:chat|conversation)|fresh\s+start|start\s+over|reset\s+(?:(?:this|the)\s+)?(?:chat|conversation))\b|nayi\s+(?:chat|conversation|baatcheet)|नई\s+(?:चैट|बातचीत)|नई\s+बात/iu.test(message);
}
if (assistantHistory.length) saveAssistantHistory();

function setAssistantPhotoZoom(value) {
  assistantPhotoZoom = Math.min(4, Math.max(1, value));
  const image = $("assistant-photo-zoom-image");
  if (image) image.style.transform = `scale(${assistantPhotoZoom})`;
  const reset = document.querySelector('[data-photo-zoom="reset"]');
  if (reset) reset.textContent = `${Math.round(assistantPhotoZoom * 100)}%`;
}

function assistantProductImageUrl(path) {
  const source = String(path || "").trim();
  if (!source || /^(?:data:|blob:)/i.test(source)) return source;
  const rooted = /^(?:https?:)?\/\//i.test(source) || source.startsWith("/") ? source : `/${source}`;
  return `${rooted}${rooted.includes("?") ? "&" : "?"}v=${Date.now()}`;
}

function showUnavailableProductImage(image, name) {
  if (!image?.parentElement) return;
  const placeholder = document.createElement("div");
  placeholder.className = image.closest(".assistant-product-photo-button") ? "assistant-product-no-image" : "assistant-photo-preview-empty";
  placeholder.innerHTML = '<i class="fa-solid fa-image"></i><span>Photo unavailable</span>';
  placeholder.setAttribute("role", "img");
  placeholder.setAttribute("aria-label", `${name || "Product"} photo unavailable`);
  const button = image.closest(".assistant-product-photo-button");
  if (button) button.replaceWith(placeholder);
  else image.replaceWith(placeholder);
}

function openAssistantPhotoLightbox(product = assistantCurrentPhotoProduct) {
  if (!product?.image_path) return;
  assistantCurrentPhotoProduct = product;
  $("assistant-photo-lightbox-title").textContent = product.name;
  const image = $("assistant-photo-zoom-image");
  image.onerror = () => {
    image.hidden = true;
    const viewport = $("assistant-photo-viewport");
    let unavailable = viewport.querySelector(".assistant-photo-unavailable");
    if (!unavailable) {
      unavailable = document.createElement("div");
      unavailable.className = "assistant-photo-unavailable";
      viewport.appendChild(unavailable);
    }
    unavailable.textContent = `${product.name} photo is unavailable. Check the saved image file.`;
  };
  image.onload = () => {
    image.hidden = false;
    $("assistant-photo-viewport").querySelector(".assistant-photo-unavailable")?.remove();
  };
  image.hidden = false;
  $("assistant-photo-viewport").querySelector(".assistant-photo-unavailable")?.remove();
  image.src = assistantProductImageUrl(product.image_path);
  image.alt = `${product.name} product photo`;
  $("assistant-photo-lightbox").hidden = false;
  setAssistantPhotoZoom(1);
  $("assistant-photo-viewport").focus({ preventScroll: true });
}

function setAssistantPhotoPreview(product) {
  if (!product) return;
  assistantCurrentPhotoProduct = product;
  const preview = $("assistant-photo-preview");
  preview.hidden = false;
  preview.innerHTML = `<header class="assistant-photo-preview-head"><strong title="${esc(product.name)}">${esc(product.name)}</strong><button type="button" data-photo-close aria-label="Close product photo"><i class="fa-solid fa-xmark"></i></button></header>${product.image_path ? `<div class="assistant-photo-preview-image"><button type="button" data-photo-open aria-label="Zoom ${esc(product.name)} photo"><img src="${esc(assistantProductImageUrl(product.image_path))}" alt="${esc(product.name)} product photo"></button></div>` : '<div class="assistant-photo-preview-empty"><i class="fa-solid fa-image"></i>&nbsp; No photo added</div>'}<div class="assistant-photo-preview-actions"><button type="button" data-photo-change><i class="fa-solid fa-camera"></i> ${product.image_path ? "Change photo" : "Add photo"}</button><button type="button" data-photo-details>Open product & sizes</button></div>`;
  preview.querySelector(".assistant-photo-preview-image img")?.addEventListener("error", (event) => showUnavailableProductImage(event.currentTarget, product.name), { once: true });
  preview.querySelector("[data-photo-close]").addEventListener("click", () => { preview.hidden = true; });
  preview.querySelector("[data-photo-open]")?.addEventListener("click", () => openAssistantPhotoLightbox(product));
  preview.querySelector("[data-photo-change]").addEventListener("click", () => $("assistant-photo-input").click());
  preview.querySelector("[data-photo-details]").addEventListener("click", () => {
    preview.hidden = true;
    setDishaPaused(true);
    $("assistant-panel").hidden = true;
    $("assistant-launcher").setAttribute("aria-expanded", "false");
    openProductDetail(product.sku);
  });
}

document.querySelectorAll("[data-photo-zoom]").forEach((button) => button.addEventListener("click", () => {
  const action = button.dataset.photoZoom;
  setAssistantPhotoZoom(action === "reset" ? 1 : assistantPhotoZoom + (action === "in" ? 0.25 : -0.25));
}));
$("assistant-photo-viewport")?.addEventListener("wheel", (event) => {
  event.preventDefault();
  setAssistantPhotoZoom(assistantPhotoZoom + (event.deltaY < 0 ? 0.2 : -0.2));
}, { passive: false });
document.addEventListener("keydown", (event) => {
  if ($("assistant-photo-lightbox").hidden) return;
  if (event.key === "+" || event.key === "=") { event.preventDefault(); setAssistantPhotoZoom(assistantPhotoZoom + 0.25); }
  else if (event.key === "-") { event.preventDefault(); setAssistantPhotoZoom(assistantPhotoZoom - 0.25); }
  else if (event.key === "0") { event.preventDefault(); setAssistantPhotoZoom(1); }
  else if (event.key === "Escape") { event.preventDefault(); $("assistant-photo-lightbox").hidden = true; }
});
$("assistant-photo-lightbox-close")?.addEventListener("click", () => { $("assistant-photo-lightbox").hidden = true; });
$("assistant-photo-lightbox")?.addEventListener("click", (event) => {
  if (event.target.id === "assistant-photo-lightbox") event.currentTarget.hidden = true;
});
$("assistant-photo-change")?.addEventListener("click", () => $("assistant-photo-input").click());
$("assistant-photo-input")?.addEventListener("change", async (event) => {
  const file = event.target.files?.[0], product = assistantCurrentPhotoProduct;
  if (!file || !product) return;
  const form = new FormData(); form.append("file", file);
  try {
    const response = await fetch(`${API}/products/${encodeURIComponent(product.sku)}/image`, { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Upload failed (${response.status})`);
    product.image_path = data.image_path;
    setAssistantPhotoPreview(product);
    if (!$("assistant-photo-lightbox").hidden) openAssistantPhotoLightbox(product);
    const card = document.querySelector(`.assistant-product-card[data-sku="${CSS.escape(product.sku)}"]`);
    const thumb = card?.querySelector(".assistant-product-photo-button img");
    if (thumb) thumb.src = `${product.image_path}?v=${Date.now()}`;
    showToast(`${product.name} photo updated`, "success", 2200);
  } catch (error) {
    showToast(error.message || "Photo upload failed", "error", 3000);
  } finally { event.target.value = ""; }
});

function addAssistantMessage(role, content, options = {}) {
  const messages = $("assistant-messages");
  if (!messages) return null;
  const bubble = document.createElement("div");
  bubble.className = `assistant-message ${role}`;
  bubble.textContent = content;
  messages.appendChild(bubble);
  if (options.action) {
    const action = options.action;
    const card = document.createElement("div");
    card.className = "assistant-action-card";
    const actionText = action.type === "stock_update"
      ? `${esc(action.name)} · ${esc(action.sku)}<div>Size ${esc(action.size)}: ${Number(action.current_stock)} → <b>${Number(action.proposed_stock)}</b> pieces</div>`
      : action.type === "product_create"
        ? `प्रोडक्ट बनाएं: ${esc(action.name)} · ${esc(action.sku)}<div>साइज़: ${action.sizes.map(esc).join(", ")} · शुरुआती स्टॉक 0</div>`
        : action.type === "product_update"
          ? `Edit ${esc(action.sku)}<div>${Object.entries(action.changes).map(([key, value]) => `${esc(key)}: ${esc(value)}`).join(" · ")}</div>`
          : action.type === "product_archive"
            ? `Move ${esc(action.sku)} to Trash<div>Design and all its sizes</div>`
            : action.type === "product_restore"
              ? `Restore ${esc(action.sku)}<div>Design and its trashed sizes</div>`
              : `Dispatch batch ${Number(action.order_id)}<div>${esc(action.filename || "Orders")} · ${Number(action.lines)} lines · ${Number(action.units)} pieces</div>`;
    card.innerHTML = `<strong>${actionText}</strong><button class="btn-primary assistant-confirm-stock" type="button">${action.type === "stock_update" ? "स्टॉक बदलने की पुष्टि करें" : "इस बदलाव की पुष्टि करें"}</button>`;
    bubble.appendChild(card);
    const button = card.querySelector(".assistant-confirm-stock");
    assistantConfirmationButton = button;
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      if (assistantConfirmationButton === button) assistantConfirmationButton = null;
      button.disabled = true;
      button.textContent = "Updating…";
      try {
        let success = "बदलाव पूरा हो गया।";
        if (action.type === "stock_update") {
          const updated = await apiPost(`${API}/inventory/adjust`, {
            variant_code: action.variant_code, new_count: Number(action.proposed_stock),
            note: `AI assistant confirmed ${action.operation} ${action.quantity}`,
            expected_current_stock: Number(action.current_stock),
          });
          success = `${action.name}, ${action.sku}, size ${action.size}: stock ${Number(action.current_stock)} se ${updated.current_stock} pieces ho gaya. Update complete.`;
        } else if (action.type === "product_create") {
          await apiPost(`${API}/products`, { sku: action.sku, name: action.name, category: action.category || null, variants: action.sizes.map((size) => ({ size, initial_stock: 0 })) });
          success = `${action.name} प्रोडक्ट बन गया है। इसमें ${action.sizes.length} साइज़ हैं और शुरुआती स्टॉक शून्य है। गिनती के बाद स्टॉक अपडेट कर सकती हैं।`;
        } else if (action.type === "product_update") {
          await apiPatch(`${API}/products/${encodeURIComponent(action.sku)}`, action.changes);
          success = `${action.sku} product details update ho gayi.`;
        } else if (action.type === "product_archive") {
          await apiDelete(`${API}/products/${encodeURIComponent(action.sku)}`);
          success = `${action.sku} aur uske sizes Trash mein move ho gaye; history preserved hai.`;
        } else if (action.type === "product_restore") {
          await apiPost(`${API}/products/${encodeURIComponent(action.sku)}/restore`);
          success = `${action.sku} product Trash se restore ho gaya.`;
        } else if (action.type === "order_dispatch") {
          const result = await apiPost(`${API}/orders/${Number(action.order_id)}/dispatch`);
          success = `Batch ${Number(action.order_id)} dispatch complete: ${Number(result.dispatched_units)} pieces, ${Number(result.dispatched_lines)} lines.`;
        } else {
          throw new Error("This action is not supported.");
        }
        card.innerHTML = `<strong><i class="fa-solid fa-circle-check"></i> ${esc(success)}</strong>`;
        assistantConfirmationButton = null;
        assistantHistory.push({ role: "assistant", content: success });
        saveAssistantHistory();
        addAssistantMessage("assistant", success);
        speakAssistantMessage(success);
        playSuccessSound();
        showToast(success, "success", 3200);
        await refreshInventoryViews();
        if ($("view-orders")?.classList.contains("is-active")) await loadOrderHistory();
      } catch (error) {
        button.disabled = false;
        button.textContent = "Retry after checking";
        addAssistantMessage("system", `Action confirm nahi hui: ${error.message || error}. Agar connection ke waqt issue aaya, retry se pehle page refresh karke current data verify karein.`);
      }
    });
  }
  if (options.choices?.length) {
    const list = document.createElement("div");
    list.className = "assistant-choice-list";
    options.choices.forEach((choice) => {
      const button = document.createElement("button");
      button.className = "assistant-choice";
      button.type = "button";
      button.innerHTML = `<strong>${esc(choice.sku)}</strong><small>${esc(choice.name)}${choice.size ? ` · ${esc(choice.size)}` : ""}${choice.current_stock != null ? ` · Stock ${Number(choice.current_stock)}` : ""}</small>`;
      button.addEventListener("click", () => {
        const originalRequest = [...assistantHistory].reverse().find((item) => item.role === "user")?.content || `Find ${choice.sku}`;
        sendAssistantMessage(originalRequest, choice.sku);
      });
      list.appendChild(button);
    });
    bubble.appendChild(list);
  }
  if (options.products?.length) {
    const list = document.createElement("div");
    list.className = "assistant-product-results";
    options.products.forEach((product) => {
      const card = document.createElement("article");
      card.className = "assistant-product-card";
      card.dataset.sku = product.sku;
      const sizes = (product.sizes || []).map((item) => `${esc(item.size)}: ${item.counted ? Number(item.stock) : "not counted"}`).join(" · ") || "No active sizes";
      card.innerHTML = `${product.image_path ? `<button class="assistant-product-photo-button" type="button" aria-label="Show ${esc(product.name)} photo"><img src="${esc(assistantProductImageUrl(product.image_path))}" alt="${esc(product.name)}" loading="lazy"></button>` : '<div class="assistant-product-no-image"><i class="fa-solid fa-image"></i><span>Photo not added</span></div>'}<div class="assistant-product-copy"><strong>${esc(product.name)}</strong><small>${esc(product.sku)}${product.category ? ` · ${esc(product.category)}` : ""}</small><small>${sizes}</small><button class="assistant-product-pick" type="button">Open all sizes</button></div>`;
      card.querySelector(".assistant-product-photo-button img")?.addEventListener("error", (event) => showUnavailableProductImage(event.currentTarget, product.name), { once: true });
      card.querySelector(".assistant-product-photo-button")?.addEventListener("click", () => {
        setAssistantPhotoPreview(product);
        if (product.image_path) openAssistantPhotoLightbox(product);
      });
      card.querySelector(".assistant-product-pick").addEventListener("click", () => {
        $("assistant-photo-preview").hidden = true;
        $("assistant-panel").hidden = true;
        $("assistant-launcher").setAttribute("aria-expanded", "false");
        openProductDetail(product.sku);
      });
      list.appendChild(card);
    });
    bubble.appendChild(list);
    // Don't choose a photo for the user when a broad search has several
    // possible designs; that could display a different colour/design's image.
    if (options.products.length === 1) setAssistantPhotoPreview(options.products[0]);
  }
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function restoreAssistantMessages() {
  const messages = $("assistant-messages");
  if (!messages) return;
  messages.replaceChildren();
  if (!assistantHistory.length) return;
  assistantHistory.forEach((item) => addAssistantMessage(item.role, item.content));
}

function assistantGreeting() {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "सुप्रभात" : hour < 17 ? "नमस्कार" : "शुभ संध्या";
  return `${greeting}! मैं दिशा, आपकी इन्वेंटरी सहायक हूँ। प्रोडक्ट, रंग, साइज़ या स्टॉक के बारे में बताइए। मैं पहले मौजूदा स्टॉक जाँचूँगी और बदलाव करने से पहले आपसे पुष्टि करूँगी। आज मैं आपकी क्या मदद करूँ?`;
}

function openAssistant({ speakGreeting = true, focusInput = true, greetingText = null } = {}) {
  const panel = $("assistant-panel"), launcher = $("assistant-launcher");
  if (!panel || !launcher) return;
  panel.hidden = false;
  launcher.setAttribute("aria-expanded", "true");
  restoreAssistantMessages();
  if (!assistantHistory.length) {
    const greeting = greetingText || assistantGreeting();
    assistantHistory.push({ role: "assistant", content: greeting });
    saveAssistantHistory();
    addAssistantMessage("assistant", greeting);
    if (speakGreeting) speakAssistantMessage(greeting);
  }
  if (focusInput) $("assistant-chat-input")?.focus({ preventScroll: true });
}

async function sendAssistantMessage(message, selectedSku = null, { fromVoice = false } = {}) {
  const clean = String(message || "").trim().slice(0, 500);
  if (!clean) return;
  if (isNewConversationRequest(clean)) { startNewAssistantConversation({ fromVoice }); return; }
  if (assistantBusy) return;
  if (handlePendingAssistantConfirmation(clean)) return;
  assistantBusy = true;
  const requestSequence = ++assistantRequestSequence;
  const requestSessionId = assistantSessionId;
  const send = $("assistant-send"), input = $("assistant-chat-input");
  if (send) send.disabled = true;
  addAssistantMessage("user", clean);
  assistantHistory.push({ role: "user", content: clean });
  saveAssistantHistory();
  const typing = document.createElement("div");
  typing.className = "assistant-message assistant-typing";
  typing.textContent = "Samajh rahi hoon…";
  $("assistant-messages")?.appendChild(typing);
  try {
    if (isDailySalesRequest(clean)) {
      const summary = await getJSON(`${API}/analytics/daily-sales`);
      if (requestSequence !== assistantRequestSequence || requestSessionId !== assistantSessionId) return;
      const reply = formatDailySalesSummary(summary);
      assistantHistory.push({ role: "assistant", content: reply });
      saveAssistantHistory();
      addAssistantMessage("assistant", reply);
      speakAssistantMessage(reply);
      return;
    }
    const history = assistantHistory.slice(0, -1).slice(-12);
    const response = await fetch(`${API}/assistant/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: clean, history, selected_sku: selectedSku }),
    });
    const result = await response.json().catch(() => ({}));
    if (requestSequence !== assistantRequestSequence || requestSessionId !== assistantSessionId) return;
    if (!response.ok) throw new Error(result.detail || `Assistant request failed (${response.status})`);
    registerAssistantProducts(result.products || result.choices || []);
    if (result.action?.sku && result.action?.name) registerAssistantProducts([{ sku: result.action.sku, name: result.action.name }]);
    if (result.stock?.sku && result.stock?.name) registerAssistantProducts([result.stock]);
    const reply = String(result.reply || "Is request ko samajhne mein dikkat hui. Product aur size dobara batayein.");
    assistantHistory.push({ role: "assistant", content: reply });
    saveAssistantHistory();
    addAssistantMessage("assistant", reply, { action: ["confirm_stock", "confirm_action"].includes(result.status) ? { type: result.status === "confirm_stock" ? "stock_update" : result.action.type, ...result.action } : null, choices: result.status === "choose_product" ? result.choices : null, products: result.status === "product_search" ? result.products : null });
    const spokenReply = result.status === "product_search" && result.products?.length
      ? result.products.map((product) => product.name).filter(Boolean).join(". ")
      : reply;
    speakAssistantMessage(spokenReply);
  } catch (error) {
    if (requestSequence !== assistantRequestSequence || requestSessionId !== assistantSessionId) return;
    const reply = `Assistant abhi connect nahi ho paayi: ${error.message || error}. Manual stock entry abhi use kar sakte hain.`;
    assistantHistory.push({ role: "assistant", content: reply });
    saveAssistantHistory();
    addAssistantMessage("assistant", reply);
    speakAssistantMessage(reply);
  } finally {
    typing.remove();
    if (requestSequence === assistantRequestSequence && requestSessionId === assistantSessionId) {
      assistantBusy = false;
      if (send) send.disabled = false;
      if (input && !fromVoice) input.focus({ preventScroll: true });
    }
  }
}

$("assistant-launcher")?.addEventListener("click", () => {
  if ($("assistant-panel").hidden) {
    setDishaPaused(false);
    openAssistant();
  }
  else {
    setDishaPaused(true);
    $("assistant-panel").hidden = true;
    $("assistant-photo-preview").hidden = true;
    $("assistant-launcher").setAttribute("aria-expanded", "false");
  }
});
$("assistant-close")?.addEventListener("click", () => {
  setDishaPaused(true);
  $("assistant-panel").hidden = true;
  $("assistant-photo-preview").hidden = true;
  $("assistant-launcher").setAttribute("aria-expanded", "false");
});
$("assistant-new-chat")?.addEventListener("click", () => startNewAssistantConversation());
$("assistant-history-button")?.addEventListener("click", () => {
  const list = $("assistant-history-list");
  if (list.hidden) { saveAssistantHistory(); renderAssistantHistory(); }
  else list.hidden = true;
});
$("assistant-chat-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("assistant-chat-input"), message = input.value.trim();
  if (!message) { input.focus(); return; }
  input.value = "";
  sendAssistantMessage(message);
});
function updateDishaListeningUI(message = "") {
  const indicator = $("assistant-mic-status"), state = $("assistant-state"), dot = document.querySelector(".assistant-online-dot");
  const button = $("assistant-pause");
  const isListening = assistantWakeEnabled && !assistantPaused && !assistantMicBlocked && Boolean(assistantMicStream);
  indicator?.classList.toggle("is-listening", isListening);
  if (state) state.textContent = message || (assistantPaused ? "दिशा को आवाज़ देने के लिए टैप करें" : assistantMicBlocked ? "ब्राउज़र में माइक्रोफ़ोन की अनुमति दें" : isListening ? (assistantWakeArmed ? "सुन रही हूँ · ‘हाय दिशा’ बोलें" : "हिंदी में बोलें") : assistantMicStarting ? "माइक्रोफ़ोन जुड़ रहा है…" : "वॉइस चैट शुरू हो रही है…");
  dot?.classList.toggle("is-listening", isListening);
  if (button) {
    button.classList.toggle("is-paused", assistantPaused);
    button.setAttribute("aria-label", assistantPaused ? "Start Disha microphone" : "Pause Disha microphone and voice");
    button.title = assistantPaused ? "Start voice" : "Pause voice";
    button.innerHTML = `<i class="fa-solid ${assistantPaused ? "fa-microphone" : "fa-pause"}"></i>`;
  }
}

function setDishaPaused(paused) {
  assistantPaused = paused;
  localStorage.setItem("dishaPaused", String(paused));
  if (paused) {
    assistantWakeEnabled = false;
    assistantWakeArmed = false;
    assistantSpeechId += 1;
    clearTimeout(assistantSpeechGuardTimer);
    assistantSpeaking = false;
    window.speechSynthesis?.cancel();
    assistantAudioPlayer?.pause();
    assistantAudioPlayer = null;
    if (assistantAudioObjectUrl) URL.revokeObjectURL(assistantAudioObjectUrl);
    assistantAudioObjectUrl = null;
    stopDishaMic();
  } else {
    assistantWakeEnabled = true;
    assistantWakeArmed = true;
    assistantMicBlocked = false;
    startDishaMic();
  }
  updateDishaListeningUI();
}
$("assistant-pause")?.addEventListener("click", () => setDishaPaused(!assistantPaused));

async function startDishaMic() {
  if (assistantPaused || assistantMicStarting || assistantMicStream || document.visibilityState === "hidden") return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    assistantMicBlocked = true;
    assistantPaused = true;
    assistantWakeEnabled = false;
    assistantWakeArmed = false;
    updateDishaListeningUI("Mic recording is not supported · type to chat");
    addAssistantMessage("system", "Is browser mein continuous mic recording supported nahi hai. Chrome/Edge update karein ya type karke baat karein.");
    return;
  }
  assistantMicStarting = true;
  assistantWakeEnabled = true;
  assistantWakeArmed = true;
  updateDishaListeningUI("Connecting microphone…");
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  let audioContext = null;
  try {
    // Create and resume the analyser context synchronously from the tap. Mobile
    // Safari may refuse to start a context created after getUserMedia resolves.
    if (!AudioContextClass) throw new Error("Audio analyser is unavailable");
    audioContext = new AudioContextClass();
    assistantMicContext = audioContext;
    await audioContext.resume().catch(() => {});
    if (assistantPaused || document.visibilityState === "hidden") {
      await audioContext.close().catch(() => {});
      if (assistantMicContext === audioContext) assistantMicContext = null;
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (assistantPaused || document.visibilityState === "hidden") {
      stream.getTracks().forEach((track) => track.stop());
      await audioContext.close().catch(() => {});
      if (assistantMicContext === audioContext) assistantMicContext = null;
      return;
    }
    assistantMicStream = stream;
    stream.getAudioTracks().forEach((track) => track.addEventListener("ended", () => {
      if (assistantMicStream !== stream || assistantPaused) return;
      stopDishaMic();
      assistantPaused = true;
      assistantWakeEnabled = false;
      assistantWakeArmed = false;
      updateDishaListeningUI("Microphone stopped · tap Disha to reconnect");
    }, { once: true }));
    if (audioContext.state === "suspended") await audioContext.resume().catch(() => {});
    const source = audioContext.createMediaStreamSource(stream);
    assistantMicAnalyser = audioContext.createAnalyser();
    assistantMicAnalyser.fftSize = 1024;
    source.connect(assistantMicAnalyser);
    assistantMicBlocked = false;
    assistantMicStarting = false;
    updateDishaListeningUI("Mic on · say ‘Hi Disha’");
    monitorDishaMic();
  } catch (error) {
    await audioContext?.close().catch(() => {});
    if (assistantMicContext === audioContext) assistantMicContext = null;
    assistantMicStarting = false;
    assistantMicBlocked = true;
    assistantPaused = true;
    assistantWakeEnabled = false;
    assistantWakeArmed = false;
    updateDishaListeningUI("Microphone permission needed · allow it in browser settings");
    if (!assistantPermissionNoticeShown) {
      assistantPermissionNoticeShown = true;
      addAssistantMessage("system", "Disha ko microphone permission chahiye. Browser ke address-bar site controls mein Microphone ko Allow karein, phir Disha ko dobara tap karein. Earbuds connected hon to unka mic system ke input device mein select karein.");
    }
  }
}

function monitorDishaMic() {
  if (!assistantMicAnalyser || assistantPaused) return;
  const samples = new Float32Array(assistantMicAnalyser.fftSize);
  const step = (time) => {
    if (!assistantMicAnalyser || assistantPaused) return;
    assistantMicAnalyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
    const level = Math.sqrt(sum / samples.length);
    const now = Date.now();
    if (assistantSpeaking || assistantBusy || assistantVoiceProcessing || now < assistantIgnoreTranscriptUntil) {
      assistantMicSpeakingSince = 0;
      assistantMicLastVoiceAt = now;
      if (assistantMicRecorder?.state === "recording") assistantMicRecorder.stop();
    } else if (level > 0.012) {
      assistantMicSpeakingSince ||= now;
      assistantMicLastVoiceAt = now;
      if (!assistantMicRecorder && now - assistantMicSpeakingSince >= 140) startDishaUtteranceRecording();
    } else {
      assistantMicSpeakingSince = 0;
      if (assistantMicRecorder?.state === "recording" && now - assistantMicLastVoiceAt >= 1050) assistantMicRecorder.stop();
    }
    if (assistantMicRecorder?.state === "recording" && now - assistantMicRecordingStartedAt >= 15000) assistantMicRecorder.stop();
    assistantMicFrame = requestAnimationFrame(step);
  };
  assistantMicFrame = requestAnimationFrame(step);
}

function startDishaUtteranceRecording() {
  if (!assistantMicStream || assistantPaused || assistantMicRecorder) return;
  try {
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((type) => MediaRecorder.isTypeSupported(type));
    assistantMicChunks = [];
    assistantMicRecorder = mimeType ? new MediaRecorder(assistantMicStream, { mimeType }) : new MediaRecorder(assistantMicStream);
    assistantMicRecordingStartedAt = Date.now();
    assistantMicRecorder.ondataavailable = (event) => { if (event.data?.size) assistantMicChunks.push(event.data); };
    assistantMicRecorder.onstop = () => {
      const recorder = assistantMicRecorder;
      assistantMicRecorder = null;
      const chunks = assistantMicChunks;
      assistantMicChunks = [];
      if (!assistantPaused && chunks.length) {
        const type = recorder?.mimeType || "audio/webm";
        const extension = type.includes("mp4") ? "mp4" : type.includes("ogg") ? "ogg" : type.includes("wav") ? "wav" : "webm";
        transcribeDishaUtterance(new Blob(chunks, { type }), `disha-utterance.${extension}`);
      }
    };
    assistantMicRecorder.start(250);
  } catch (_) {
    updateDishaListeningUI("Could not record audio · type to chat");
  }
}

async function transcribeDishaUtterance(blob, filename) {
  if (!blob || blob.size < 900 || assistantPaused) return;
  assistantVoiceProcessing = true;
  updateDishaListeningUI("Understanding your Hindi/English…");
  try {
    const form = new FormData();
    form.append("file", blob, filename);
    const response = await fetch(`${API}/assistant/transcribe`, { method: "POST", body: form });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `Transcription failed (${response.status})`);
    const message = String(result.text || "").trim();
    if (message) handleDishaUtterance(message);
  } catch (error) {
    if (!assistantPaused && Date.now() - assistantLastVoiceErrorAt > 20000) {
      assistantLastVoiceErrorAt = Date.now();
      addAssistantMessage("system", `${error.message || "Voice transcription failed"} Aap type karke chat jaari rakh sakte hain.`);
    }
  } finally {
    assistantVoiceProcessing = false;
    if (!assistantPaused) updateDishaListeningUI(assistantWakeArmed ? "Mic on · say ‘Hi Disha’" : "Listening · speak Hindi or English");
  }
}

function stopDishaMic() {
  cancelAnimationFrame(assistantMicFrame);
  assistantMicFrame = 0;
  if (assistantMicRecorder?.state === "recording") {
    assistantMicRecorder.onstop = null;
    try { assistantMicRecorder.stop(); } catch (_) {}
  }
  assistantMicRecorder = null;
  assistantMicChunks = [];
  assistantMicAnalyser = null;
  assistantMicContext?.close().catch(() => {});
  assistantMicContext = null;
  assistantMicStream?.getTracks().forEach((track) => track.stop());
  assistantMicStream = null;
  assistantMicStarting = false;
  assistantVoiceProcessing = false;
  assistantWakeArmed = false;
  assistantMicSpeakingSince = 0;
  assistantMicLastVoiceAt = 0;
}

function isAffirmative(text) { return /^(?:(?:haan|han|ha|yes|yeah|yep|ji|jee|bilkul|confirm)(?:\b|$)|(?:हाँ|हां|जी)(?:\s|$)|कर\s+दो)/iu.test(text.trim().replace(/[.!?,।]+$/g, "")); }
function isNegative(text) { return /^(?:(?:nahi|nahin|no|nope|cancel)(?:\b|$)|(?:नहीं|ना|रद्द)(?:\s|$)|मत\s+करो)/iu.test(text.trim().replace(/[.!?,।]+$/g, "")); }

function handlePendingAssistantConfirmation(message) {
  const button = assistantConfirmationButton;
  if (!button || button.disabled) return false;
  if (isAffirmative(message)) {
    assistantConfirmationButton = null;
    assistantHistory.push({ role: "user", content: message });
    saveAssistantHistory();
    addAssistantMessage("user", message);
    button.click();
    return true;
  }
  if (isNegative(message)) {
    assistantConfirmationButton = null;
    button.disabled = true;
    button.textContent = "Cancelled";
    const response = "Theek hai, action cancel kar diya. Inventory change nahi hui.";
    assistantHistory.push({ role: "user", content: message }, { role: "assistant", content: response });
    saveAssistantHistory();
    addAssistantMessage("user", message);
    addAssistantMessage("assistant", response);
    speakAssistantMessage(response);
    return true;
  }
  return false;
}

function handleDishaUtterance(transcript) {
  if (assistantPaused || assistantSpeaking || assistantBusy) return;
  let message = String(transcript || "").trim();

  if (assistantWakeArmed) {
    const wakeMatch = message.match(/^(?:(?:hey|hi|hello|ok(?:ay)?|arey|अरे|हाय|हेलो)[,\s]+)*(?:disha\b|दिशा(?=\s|$|[,!.।-]))[\s,!.।-]*(.*)$/iu);
    if (!wakeMatch) {
      updateDishaListeningUI("Mic on · pehle ‘Hi Disha’ boliye");
      return;
    }
    assistantWakeArmed = false;
    message = String(wakeMatch[1] || "").trim();
    if (!message) {
      const greeting = "Haan ji, main sun rahi hoon. Bataiye, kya help karoon?";
      assistantHistory.push({ role: "assistant", content: greeting });
      saveAssistantHistory();
      addAssistantMessage("assistant", greeting);
      speakAssistantMessage(greeting);
      updateDishaListeningUI("Listening · boliye");
      return;
    }
  }

  if (assistantConfirmationButton && (isAffirmative(message) || isNegative(message))) {
    sendAssistantMessage(message, null, { fromVoice: true });
    return;
  }
  if (message) sendAssistantMessage(message, null, { fromVoice: true });
}

function isDailySalesRequest(message) {
  const text = String(message || "").toLocaleLowerCase("hi-IN");
  const asksTime = /\b(aaj|today|daily|din bhar)\b|आज|दैनिक/u.test(text);
  const asksSales = /\b(sale|sales|bik|bika|biki|bikri|sold|dispatch|dispatched|report|summary)\b|सेल|बिक|बिक्री/u.test(text);
  return (asksTime && asksSales) || /\b(daily|aaj)\b.*\b(report|summary|short)\b|आज.*(?:report|summary|रिपोर्ट|सारांश)/u.test(text);
}

function formatDailySalesSummary(summary) {
  if (!summary.dispatched_units) return `Aaj (${summary.date}) abhi tak koi dispatched sale record nahi hai. Pending orders ko sale count nahi kiya hai.`;
  const top = (summary.top_products || []).slice(0, 3).map((item) => `${item.name} (${item.sku}) ${item.units} pcs`).join(", ");
  return `Aaj (${summary.date}) ${summary.dispatched_units} pieces dispatch hue, ${summary.designs_sold} designs se. Top: ${top || "koi item detail nahi"}. Ye dispatched pieces hain; rupee revenue system mein record nahi hota.`;
}

$("assistant-sales-summary")?.addEventListener("click", () => sendAssistantMessage("Aaj ki dispatched sales ka short summary batao."));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && !assistantPaused) setDishaPaused(true);
});
updateDishaListeningUI();

async function loadAlerts() {
  const list = $("alerts-list");
  const requestId = ++alertPage.requestId;
  try {
    const q = $("alerts-search")?.value.trim() || "";
    const params = new URLSearchParams({ limit: String(alertPage.limit), offset: String(alertPage.offset) });
    if (q) params.set("q", q);
    const page = await getJSON(`${API}/alerts/groups?${params}`);
    if (requestId !== alertPage.requestId) return;
    const pageLimit = Math.max(1, Number(page.limit) || 30);
    const lastOffset = Math.max(0, Math.ceil(page.total / pageLimit) * pageLimit - pageLimit);
    if (alertPage.offset > lastOffset) {
      alertPage.offset = lastOffset;
      return loadAlerts();
    }
    $("alerts-note").textContent = page.total ? `${page.total.toLocaleString()} designs with alerts · grouped by design and sorted by severity` : "";
    const pageCount = Math.max(1, Math.ceil(page.total / pageLimit));
    const pageNumber = Math.floor(page.offset / pageLimit) + 1;
    const range = page.total ? ` · ${page.offset + 1}–${Math.min(page.offset + page.items.length, page.total)} of ${page.total}` : " · 0 designs";
    $("alerts-page-info").textContent = `Page ${pageNumber} of ${pageCount}${range}`;
    $("alerts-prev").disabled = page.offset <= 0;
    $("alerts-next").disabled = page.offset + page.items.length >= page.total || page.total === 0;
    if (!page.items.length) { list.innerHTML = `<p class="hint">${q ? "No matching alerts." : "No open alerts."}</p>`; return; }
    list.innerHTML = page.items.map((group) => `
      <section class="alert-design">
        <header class="alert-design-head"><div><div class="name">${esc(group.product_name)}</div><div class="sku-tag">${esc(group.sku)}</div></div><span class="alert-group-count">${group.alert_count} size alert${group.alert_count === 1 ? "" : "s"}</span></header>
        <div class="alert-design-sizes">${group.items.map((a) => {
          const message = a.alert_type === "ORDER_SHORTAGE" ? `Order shortage · Stock ${a.stock_at_alert} · Orders ${a.ordered_qty} · Need ${a.required_qty}` : a.alert_type === "OUT_OF_STOCK" ? `Out of stock · ${a.stock_at_alert} pcs` : `Low stock · ${a.stock_at_alert} pcs`;
          return `<form class="alert-row ${a.alert_type} alert-quick-form" data-code="${esc(a.variant_code)}">
            <div class="alert-size-label"><span class="size-chip">${esc(a.size)}</span><span class="meta">${message}</span></div>
            <div class="alert-quick"><input type="number" min="0" class="input quick-count" placeholder="New count" aria-label="New count for ${esc(group.sku)} size ${esc(a.size)}"><button class="btn-secondary quick-save" type="submit">Update</button></div>
          </form>`;
        }).join("")}</div>
      </section>`).join("");
    list.querySelectorAll(".alert-quick-form").forEach((form) => form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = form.querySelector(".quick-count"), button = form.querySelector(".quick-save");
      if (input.value === "") { showToast("Enter a count first", true); input.focus(); return; }
      if (button.disabled) return;
      button.disabled = true;
      try {
        const r = await apiPost(`${API}/inventory/adjust`, { variant_code: form.dataset.code, new_count: Number(input.value), note: "alert quick-update" });
        showToast(`${form.dataset.code} set to ${r.current_stock}`, "success", 2600);
        await refreshInventoryViews();
      } catch (err) { showToast("Update failed: " + err.message, true); }
      finally { button.disabled = false; }
    }));
  } catch (e) { if (requestId === alertPage.requestId) list.innerHTML = `<p class="hint">Could not load alerts.</p>`; }
}
const alertPage = { offset: 0, limit: 30, requestId: 0 };
$("alerts-search").addEventListener("input", debounce(() => { alertPage.offset = 0; loadAlerts(); }, 220));
$("alerts-prev").addEventListener("click", () => { alertPage.offset = Math.max(0, alertPage.offset - alertPage.limit); loadAlerts(); });
$("alerts-next").addEventListener("click", () => { alertPage.offset += alertPage.limit; loadAlerts(); });

// ================================================================ //
// HISTORY
// ================================================================ //
const history = { q: "", offset: 0, limit: 50 };
$("history-search").addEventListener("input", debounce((e) => { history.q = e.target.value.trim(); history.offset = 0; loadHistory(true); }));
$("hist-prev").addEventListener("click", () => { history.offset = Math.max(0, history.offset - history.limit); loadHistory(true); });
$("hist-next").addEventListener("click", () => { history.offset += history.limit; loadHistory(true); });

async function loadHistory(reset) {
  const body = $("history-body");
  const params = new URLSearchParams({ limit: history.limit, offset: history.offset, paged: "true" });
  if (history.q) params.set("q", history.q);
  try {
    const page = await getJSON(`${API}/transactions?${params}`);
    const rows = page.items;
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
    body.innerHTML = html || `<tr><td colspan="8" class="empty">No history yet.</td></tr>`;
    $("hist-page-info").textContent = rows.length ? `${history.offset + 1}–${Math.min(history.offset + rows.length, page.total)} of ${page.total}` : "";
    $("hist-prev").disabled = history.offset === 0;
    $("hist-next").disabled = history.offset + rows.length >= page.total;
  } catch (e) { if (reset) body.innerHTML = `<tr><td colspan="8" class="empty">Could not load history.</td></tr>`; }
}

// ================================================================ //
// PRINT LABELS
// ================================================================ //
const labelPage = { q: "", offset: 0, limit: 15 };
$("label-search").addEventListener("input", debounce(async (e) => {
  labelPage.q = e.target.value.trim();
  labelPage.offset = 0;
  loadLabelResults();
}, 250));
$("labels-prev").addEventListener("click", () => { labelPage.offset = Math.max(0, labelPage.offset - labelPage.limit); loadLabelResults(); });
$("labels-next").addEventListener("click", () => { labelPage.offset += labelPage.limit; loadLabelResults(); });
async function loadLabelResults() {
  const q = labelPage.q, box = $("label-results");
  if (q.length < 2) { box.innerHTML = ""; $("labels-page-info").textContent = ""; $("labels-pager").hidden = true; $("labels-prev").disabled = $("labels-next").disabled = true; return; }
  try {
    const page = await getJSON(`${API}/products?q=${encodeURIComponent(q)}&limit=${labelPage.limit}&offset=${labelPage.offset}&paged=true`);
    const products = page.items;
    box.innerHTML = products.length ? products.map((p) => `<button type="button" class="result-row" data-sku="${esc(p.sku)}">${esc(p.sku)} — ${esc(p.name)}</button>`).join("") : `<p class="hint">No design matches "${esc(q)}".</p>`;
    $("labels-page-info").textContent = products.length ? `${labelPage.offset + 1}–${Math.min(labelPage.offset + products.length, page.total)} of ${page.total}` : "";
    $("labels-pager").hidden = page.total <= labelPage.limit;
    $("labels-prev").disabled = labelPage.offset === 0;
    $("labels-next").disabled = labelPage.offset + products.length >= page.total;
    box.querySelectorAll(".result-row").forEach((btn, i) => btn.addEventListener("click", () => selectLabelProduct(products[i])));
  } catch (err) { box.innerHTML = ""; }
}

function selectLabelProduct(product) {
  $("label-results").innerHTML = "";
  $("label-selected").hidden = false;
  $("label-selected-name").textContent = `${product.sku} — ${product.name}`;
  $("label-size-summary").textContent = `${product.variants.length} size${product.variants.length === 1 ? "" : "s"} · one barcode opens the complete design`;
  $("product-qr-download").href = `${API}/products/${encodeURIComponent(product.sku)}/barcode`;
}

// ================================================================ //
// TRASH
// ================================================================ //
const trashPage = { offset: 0, limit: 24 };
$("trash-prev").addEventListener("click", () => { trashPage.offset = Math.max(0, trashPage.offset - trashPage.limit); loadTrash(); });
$("trash-next").addEventListener("click", () => { trashPage.offset += trashPage.limit; loadTrash(); });
async function loadTrash() {
  const grid = $("trash-list"), empty = $("trash-empty");
  try {
    const page = await getJSON(`${API}/trash?limit=${trashPage.limit}&offset=${trashPage.offset}`);
    const items = page.items;
    $("trash-page-info").textContent = page.total ? `${page.offset + 1}–${Math.min(page.offset + items.length, page.total)} of ${page.total} designs` : "";
    $("trash-prev").disabled = page.offset === 0;
    $("trash-next").disabled = page.offset + page.limit >= page.total;
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
      try { await apiPost(`${API}/products/${encodeURIComponent(sku)}/restore`); showToast(`${sku} restored successfully`, "success"); if (trashPage.offset && items.length === 1) trashPage.offset = Math.max(0, trashPage.offset - trashPage.limit); loadTrash(); loadSummary(); }
      catch (err) { showToast("Restore failed: " + err.message, "error", 2200); }
    }));
    grid.querySelectorAll(".trash-wipe").forEach((btn) => btn.addEventListener("click", () => {
      const sku = btn.closest(".tcard").dataset.sku;
      askConfirm("Delete forever?", `${sku} and its full history will be permanently removed. This cannot be undone.`, async () => {
        try { await apiDelete(`${API}/trash/${encodeURIComponent(sku)}`); showToast(`${sku} permanently deleted`, "success"); if (trashPage.offset && items.length === 1) trashPage.offset = Math.max(0, trashPage.offset - trashPage.limit); loadTrash(); }
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
