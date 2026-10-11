"use strict";

const $ = (sel) => document.querySelector(sel);
const body = document.body;
const form = $(".search");
const input = $("#q");
const results = $(".results");
const grid = $(".grid");
const track = $(".status-track");
const live = $("#live");
const outage = $(".outage");
const outageText = $(".outage-text");
const notice = $(".notice");
const historyBox = $(".history");
const historyList = $(".history-list");
const pager = $(".pager");
const prevBtn = $(".prev");
const nextBtn = $(".next");
const optsBtn = $(".opts-btn");
const optsPanel = $(".opts");
const minIn = $("#min");
const maxIn = $("#max");
const filtered = $(".filtered");
const filteredToggle = $(".filtered-toggle");
const filteredBody = $(".filtered-body");

const STORE_NAMES = { amazon: "Amazon", flipkart: "Flipkart" };
const MIN_WAIT = 900;
const MAX_PAGE = 20;
const STATUS_POLL = 20000;
const inr = (n) => "₹" + n.toLocaleString("en-IN");
const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

let current = "";
let page = 1;
let statusTimer = null;
let nextCheck = 0;
let checking = false;
let inflight = null;


const HISTORY_KEY = "search-history";
const HISTORY_MAX = 6;

function loadHistory() {
  try {
    const list = JSON.parse(localStorage.getItem(HISTORY_KEY));
    return Array.isArray(list) ? list.filter((x) => typeof x === "string").slice(0, HISTORY_MAX) : [];
  } catch { return []; }
}
function saveHistory(list) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list)); } catch {}
  renderHistory(list);
}
function remember(q) {
  const lower = q.toLowerCase();
  saveHistory([q, ...loadHistory().filter((x) => x.toLowerCase() !== lower)].slice(0, HISTORY_MAX));
}
function renderHistory(list = loadHistory()) {
  historyBox.hidden = list.length === 0;
  historyList.replaceChildren(...list.map((text) => {
    const row = $("#history-row").content.firstElementChild.cloneNode(true);
    row.querySelector(".hpick span").textContent = text;
    row.querySelector(".hpick").addEventListener("click", () => search(text));
    const x = row.querySelector(".hx");
    x.setAttribute("aria-label", `Remove “${text}” from recent searches`);
    x.addEventListener("click", () => saveHistory(loadHistory().filter((h) => h !== text)));
    return row;
  }));
}


let parsed = { q: null, low: null, high: null };
let priceCtrl = null;
let pricePending = Promise.resolve();
let priceTimer = null;

const rupees = (el) => el.value.replace(/\D/g, "");

function setPrice(el, v) {
  const next = v ? Number(v).toLocaleString("en-IN") : "";
  if (el.value === next) return;
  el.value = next;
  const field = el.parentElement;
  field.classList.remove("is-updated");
  void field.offsetWidth;
  field.classList.add("is-updated");
}

function syncPrice(raw) {
  const q = raw.trim().replace(/\s+/g, " ");
  if (q === parsed.q) return pricePending;
  priceCtrl?.abort();
  const ctrl = (priceCtrl = new AbortController());
  return (pricePending = fetch(`/api/price?q=${encodeURIComponent(q)}`, { signal: ctrl.signal })
    .then((r) => r.json())
    .then((p) => {
      if (p.low !== parsed.low) setPrice(minIn, p.low);
      if (p.high !== parsed.high) setPrice(maxIn, p.high);
      parsed = { q, text: p.query, low: p.low, high: p.high };
    })
    .catch(() => {}));
}

function priceText({ low, high } = {}) {
  if (low && high) return ` from ${inr(low)} to ${inr(high)}`;
  if (low) return ` from ${inr(low)}`;
  if (high) return ` up to ${inr(high)}`;
  return "";
}


const DEFAULTS = { sort: "relevance", rating: "", discount: "" };
const SORT_TEXT = {
  "price-asc": "lowest price first", "price-desc": "highest price first",
  rating: "best rated first", popular: "most popular first", newest: "newest first",
};
const MERGED_SORTS = new Set(["price-asc", "price-desc", "rating"]);

const options = () => Object.fromEntries(Object.keys(DEFAULTS).map((k) => [k, form.elements[k].value]));
function setOptions(o) {
  for (const k in DEFAULTS) form.elements[k].value = o[k] ?? DEFAULTS[k];
  $(".opts-dot").hidden = Object.keys(DEFAULTS).every((k) => form.elements[k].value === DEFAULTS[k]);
}
function optionsQs() {
  return Object.entries(options()).filter(([k, v]) => v !== DEFAULTS[k]).map(([k, v]) => `&${k}=${v}`).join("");
}
function openOpts(open) {
  optsPanel.hidden = !open;
  optsBtn.setAttribute("aria-expanded", String(open));
  if (open) optsPanel.querySelector("input:checked").focus({ preventScroll: true });
}
function optionsText({ sort, rating, discount } = {}) {
  return (rating ? ` · ${rating}★ & up` : "") + (discount ? ` · ${discount}%+ off` : "") + (SORT_TEXT[sort] ? `, ${SORT_TEXT[sort]}` : "");
}

optsBtn.addEventListener("click", () => openOpts(optsPanel.hidden));
$(".opts-done").addEventListener("click", () => { openOpts(false); optsBtn.focus(); });
$(".opts-reset").addEventListener("click", () => { setOptions(DEFAULTS); if (current) search(current); });
optsPanel.addEventListener("change", () => { setOptions(options()); if (current) search(current); });
document.addEventListener("click", (e) => {
  if (!optsPanel.hidden && !optsPanel.contains(e.target) && !optsBtn.contains(e.target)) openOpts(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !optsPanel.hidden) { e.stopImmediatePropagation(); openOpts(false); optsBtn.focus(); }
}, true);


function status(text, final = false) {
  const line = document.createElement("div");
  line.className = "status-line" + (final ? " is-final" : "");
  line.textContent = text;
  track.append(line);
  const lines = [...track.children];
  lines.forEach((l) => l.classList.toggle("is-current", l === line));
  track.style.transform = `translateY(${-24 * (lines.length - 1)}px)`;
  live.textContent = text;
}


function skeletons(n = 8) {
  grid.replaceChildren(...Array.from({ length: n }, () => {
    const d = document.createElement("div");
    d.className = "sk";
    d.innerHTML = "<i></i><i></i><i></i><i></i>";
    return d;
  }));
}

function interleave(listings) {
  const byStore = {};
  for (const x of listings) (byStore[x.store] ||= []).push(x);
  const queues = Object.values(byStore);
  const out = [];
  while (queues.some((q) => q.length)) for (const q of queues) if (q.length) out.push(q.shift());
  return out;
}

const arrange = (list) => (MERGED_SORTS.has(options().sort) ? list : interleave(list));

function card(x, i) {
  const a = $("#card").content.firstElementChild.cloneNode(true);
  a.href = x.url;
  a.style.animationDelay = `${Math.min(i, 12) * 45}ms`;
  const ph = a.querySelector(".ph");
  const img = a.querySelector("img");
  if (x.image) {
    img.src = x.image;
    img.alt = x.title;
    img.addEventListener("error", () => ph.classList.add("is-empty"), { once: true });
  } else {
    ph.classList.add("is-empty");
  }
  a.querySelector(".store").textContent = STORE_NAMES[x.store] || x.store;
  if (x.rating) {
    const stars = a.querySelector(".stars");
    stars.hidden = false;
    stars.querySelector(".stars-avg").textContent = `★ ${x.rating.toFixed(1)}`;
    if (x.ratings) stars.querySelector(".stars-count").textContent = `${compact.format(x.ratings)} ${x.ratings === 1 ? "rating" : "ratings"}`;
    stars.setAttribute("aria-label", `Rated ${x.rating.toFixed(1)} out of 5${x.ratings ? ` by ${x.ratings.toLocaleString("en-IN")} people` : ""}`);
  }
  a.querySelector(".name").textContent = x.title;
  a.querySelector(".price").textContent = inr(x.price);
  const mrp = a.querySelector(".mrp");
  const off = a.querySelector(".off");
  if (x.mrp && x.mrp > x.price) mrp.textContent = inr(x.mrp); else mrp.remove();
  if (x.discount_pct) off.textContent = `−${x.discount_pct}%`; else off.remove();
  if (x.failed?.length) {
    const why = a.querySelector(".why");
    why.textContent = `Missed: ${x.failed.join(" · ")}`;
    why.hidden = false;
  }
  return a;
}

function showNotice(title, text) {
  grid.replaceChildren();
  notice.hidden = false;
  notice.querySelector(".notice-title").textContent = title;
  notice.querySelector(".notice-body").textContent = text;
}

function showOutage(text) {
  outageText.textContent = text;
  outage.hidden = false;
}

function showPager(hasMore) {
  pager.hidden = page === 1 && !hasMore;
  prevBtn.disabled = page === 1;
  nextBtn.disabled = !hasMore || page >= MAX_PAGE;
  $(".page-now").textContent = `Page ${page}`;
}

function showFiltered(q, list) {
  filtered.hidden = !list.length;
  filteredBody.hidden = true;
  filteredToggle.setAttribute("aria-expanded", "false");
  if (!list.length) return;
  const n = list.length;
  filteredToggle.dataset.n = n;
  $(".filtered-label").textContent = `See ${n} filtered out`;
  $(".filtered-note").textContent = `${n === 1 ? "This one didn't" : "These didn't"} look like “${q}”. Each says which part it missed.`;
  $(".filtered-grid").replaceChildren(...arrange(list).map(card));
}

filteredToggle.addEventListener("click", () => {
  const open = filteredBody.hidden;
  filteredBody.hidden = !open;
  filteredToggle.setAttribute("aria-expanded", String(open));
  $(".filtered-label").textContent = open ? "Hide filtered out" : `See ${filteredToggle.dataset.n} filtered out`;
});

function render(q, data) {
  const failed = Object.keys(data.errors || {}).map((s) => STORE_NAMES[s] || s);
  const ok = Object.keys(STORE_NAMES).filter((s) => !(s in (data.errors || {}))).map((s) => STORE_NAMES[s]);
  const listings = data.listings || [];

  if (data.rejected) {
    status("That doesn't look like a product search.", true);
    showNotice("Try searching for a product", "Name the thing you want, like “wired earphones” or “boAt Airdopes”.");
    return;
  }
  if (!ok.length) {
    status("Search didn't finish.", true);
    showNotice("Couldn't reach the stores", "Amazon and Flipkart didn't respond. Try again in a moment.");
    showOutage("");
    return;
  }
  if (failed.length) showOutage(`${failed.join(" and ")} didn't respond, so these are from ${ok.join(" and ")} only.`);
  const leftOut = data.left_out || 0;
  showPager(listings.length + leftOut > 0);
  showFiltered(q, data.filtered || []);
  if (!listings.length && leftOut) {
    status(`None of these were “${q}”.`, true);
    showNotice("Nothing here matched exactly", `Left out ${leftOut} results that weren't “${q}”. See them below, try the next page, or search more broadly.`);
    return;
  }
  if (!listings.length && page > 1) {
    status(`No more results for “${q}”.`, true);
    showNotice("That's the last page", "Go back a page, or try a different search.");
    return;
  }
  if (!listings.length) {
    status(`No results for “${q}”.`, true);
    showNotice(`No results for “${q}”`, "Try fewer words or a different spelling.");
    return;
  }
  const noun = data.criteria ? "matches" : "results";
  const tail = leftOut ? ` Left out ${leftOut} that weren't.` : "";
  status(`Showing ${listings.length} ${noun} for “${data.searched || q}”${priceText(data.price)}${optionsText(data)}${page > 1 ? `, page ${page}` : ""}.${tail}`, true);
  grid.replaceChildren(...arrange(listings).map(card));
}


function enterMaintenance() {
  inflight?.abort();
  body.classList.remove("is-search", "is-loading");
  body.classList.add("is-maintenance");
  $(".maintenance").hidden = false;
  document.title = "Back shortly · Search Amazon and Flipkart";
  if (!statusTimer) {
    nextCheck = Date.now() + STATUS_POLL;
    statusTimer = setInterval(tickCountdown, 250);
    tickCountdown();
  }
}

function leaveMaintenance() {
  clearInterval(statusTimer);
  statusTimer = null;
  body.classList.remove("is-maintenance");
  $(".maintenance").hidden = true;
  fromUrl();
}

function tickCountdown() {
  const left = Math.max(0, nextCheck - Date.now());
  const secs = Math.ceil(left / 1000);
  $(".dial-count").textContent = checking ? "·" : secs;
  $(".dial-ring").style.strokeDashoffset = String(100 - (left / STATUS_POLL) * 100);
  $(".countdown-label").textContent = checking ? "checking now…" : "until the next check";
  $(".countdown").setAttribute("aria-label", checking ? "Checking now" : `Next check in ${secs} seconds`);
  if (!left && !checking) {
    checking = true;
    checkStatus().finally(() => {
      checking = false;
      nextCheck = Date.now() + STATUS_POLL;
    });
  }
}

async function checkStatus() {
  try {
    const { mode } = await (await fetch("/api/status")).json();
    if (mode === "MAINTENANCE") enterMaintenance();
    else if (body.classList.contains("is-maintenance")) leaveMaintenance();
  } catch {}
}


const root = document.documentElement;
const themeBtn = $(".theme");
const darkQuery = matchMedia("(prefers-color-scheme: dark)");

function labelTheme() {
  themeBtn.setAttribute("aria-label", root.dataset.theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
}

function storedTheme() {
  try { return localStorage.getItem("theme"); } catch { return null; }
}

themeBtn.addEventListener("click", () => {
  const next = root.dataset.theme === "dark" ? "light" : "dark";
  const apply = () => {
    root.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch {}
    labelTheme();
  };
  if (!document.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) return apply();
  const r = themeBtn.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  document.startViewTransition(apply).ready.then(() => {
    root.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 650, easing: "cubic-bezier(.65, 0, .35, 1)", pseudoElement: "::view-transition-new(root)" },
    );
  });
});
darkQuery.addEventListener("change", (e) => {
  if (storedTheme()) return;
  root.dataset.theme = e.matches ? "dark" : "light";
  labelTheme();
});
if (!root.dataset.theme) root.dataset.theme = darkQuery.matches ? "dark" : "light";
labelTheme();

async function search(raw, { push = true, to = 1 } = {}) {
  const q = raw.trim().replace(/\s+/g, " ");
  if (!q) return;
  current = q;
  page = to;
  input.value = q;
  remember(q);
  clearTimeout(priceTimer);
  await syncPrice(q);
  if (current !== q) return;
  const min = rupees(minIn), max = rupees(maxIn);
  const priced = min || max || parsed.low || parsed.high;
  const qs = `q=${encodeURIComponent(q)}${page > 1 ? `&page=${page}` : ""}${priced ? `&min=${min}&max=${max}` : ""}${optionsQs()}`;
  if (push) history.pushState({ q, page }, "", `?${qs}`);
  document.title = `${q}${page > 1 ? ` · Page ${page}` : ""} · Search Amazon and Flipkart`;

  body.classList.add("is-search", "is-loading");
  $(".clear").tabIndex = 0;
  results.hidden = false;
  notice.hidden = true;
  outage.hidden = true;
  pager.hidden = true;
  filtered.hidden = true;
  track.replaceChildren();
  track.style.transform = "";
  status(page > 1 ? `Loading page ${page}…` : "Searching Amazon and Flipkart…");
  skeletons();

  inflight?.abort();
  const ctrl = (inflight = new AbortController());
  const started = performance.now();
  const words = parsed.q === q && parsed.text ? parsed.text : q;
  const checking = setTimeout(() => status(`Checking which ones are really “${words}”…`), 2500);
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&page=${page}&min=${min}&max=${max}${optionsQs()}`, { signal: ctrl.signal });
    if (res.status === 503) return enterMaintenance();
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    await new Promise((r) => setTimeout(r, Math.max(0, MIN_WAIT - (performance.now() - started))));
    if (ctrl.signal.aborted) return;
    render(q, data);
  } catch (err) {
    if (ctrl.signal.aborted) return;
    status("Search didn't finish.", true);
    showNotice("Couldn't reach the stores", "Check your connection, then try again. Nothing you typed is lost.");
    showOutage("");
  } finally {
    clearTimeout(checking);
    if (inflight === ctrl) body.classList.remove("is-loading");
  }
}

function goHome({ push = true } = {}) {
  inflight?.abort();
  current = "";
  body.classList.remove("is-search", "is-loading");
  $(".clear").tabIndex = -1;
  results.hidden = true;
  input.value = "";
  minIn.value = maxIn.value = "";
  parsed = { q: "", low: null, high: null };
  document.title = "Search Amazon and Flipkart";
  if (push) history.pushState({}, "", location.pathname);
  renderHistory();
  input.focus({ preventScroll: true });
}

form.addEventListener("submit", (e) => { e.preventDefault(); search(input.value); });
$(".clear").addEventListener("click", () => goHome());
$(".history-clear").addEventListener("click", () => saveHistory([]));
$(".retry").addEventListener("click", () => search(current, { push: false, to: page }));

function turnPage(to) {
  search(current, { to });
  window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}
prevBtn.addEventListener("click", () => turnPage(page - 1));
nextBtn.addEventListener("click", () => turnPage(page + 1));
input.addEventListener("input", () => {
  clearTimeout(priceTimer);
  priceTimer = setTimeout(() => syncPrice(input.value), 120);
});
for (const el of [minIn, maxIn]) {
  el.addEventListener("input", () => { el.value = el.value.replace(/[^\d,]/g, ""); });
  el.addEventListener("change", () => {
    if (rupees(el)) el.value = Number(rupees(el)).toLocaleString("en-IN");
    if (current) search(current);
  });
}
input.addEventListener("keydown", (e) => { if (e.key === "Escape" && body.classList.contains("is-search")) goHome(); });

async function fromUrl() {
  const params = new URLSearchParams(location.search);
  const q = params.get("q");
  const to = Math.min(MAX_PAGE, Math.max(1, parseInt(params.get("page"), 10) || 1));
  if (!q || !q.trim()) return goHome({ push: false });
  await syncPrice(q);
  setOptions(Object.fromEntries(Object.keys(DEFAULTS).map((k) => [k, params.get(k) ?? DEFAULTS[k]])));
  if (params.has("min") || params.has("max")) {
    minIn.value = params.get("min") ? Number(params.get("min")).toLocaleString("en-IN") : "";
    maxIn.value = params.get("max") ? Number(params.get("max")).toLocaleString("en-IN") : "";
  }
  search(q, { push: false, to });
}
window.addEventListener("popstate", fromUrl);
fromUrl();
checkStatus();
