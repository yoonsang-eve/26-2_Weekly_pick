// 공통 유틸 — API, 모달, 툴팁, 토스트
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (v) => (v === null || v === undefined || v === "" || isNaN(+v) ? null : +v);
const pct = (v, d = 2) => (v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const pp = (v) => (v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%p`);
const sign = (v) => (v === null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const md = (s) => (s || "").slice(5).replace("-", ".");      // 2026-09-28 -> 09.28
const DOW = "일월화수목금토";
const dow = (s) => DOW[new Date(s + "T00:00:00").getDay()];

async function api(path, opt = {}) {
  const r = await fetch(path, { headers: { "content-type": "application/json" }, ...opt,
    body: opt.body && typeof opt.body !== "string" ? JSON.stringify(opt.body) : opt.body });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data?.detail || data || r.statusText);
  return data;
}

// ---- modal
const Modal = {
  open(title, body, foot = "", wide = false) {
    $("#m-title").textContent = title;
    $("#m-body").innerHTML = body;
    $("#m-foot").innerHTML = foot;
    $("#modal").classList.toggle("wide", wide);
    $("#modal").classList.add("on"); $("#scrim").classList.add("on");
    this._prev = document.activeElement;
    setTimeout(() => ($("#m-body").querySelector("input,textarea,select,button") || $("#modal .x")).focus(), 30);
  },
  close() {
    $("#modal").classList.remove("on"); $("#scrim").classList.remove("on");
    this._prev?.focus?.();
  },
};
document.addEventListener("click", (e) => { if (e.target.closest("[data-close]") || e.target.id === "scrim") Modal.close(); });
addEventListener("keydown", (e) => { if (e.key === "Escape") Modal.close(); });

// ---- tooltip / toast
const tip = $("#tip");
function hover(el, html) {
  el.addEventListener("mousemove", (e) => {
    tip.innerHTML = html; tip.classList.add("on");
    tip.style.left = `${Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${Math.max(8, e.clientY - tip.offsetHeight - 10)}px`;
  });
  el.addEventListener("mouseleave", () => tip.classList.remove("on"));
}
let toastT;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 2600);
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("복사했다"); }
  catch { toast("복사 실패 — 직접 선택해서 복사할 것"); }
}

const SVGNS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}, parent) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

// ---- 공유 상태
const App = { runs: [], week: null, onRuns: [] };
async function loadRuns() {
  App.runs = await api("/api/runs");
  App.onRuns.forEach((f) => f());
  return App.runs;
}
