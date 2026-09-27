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

// 정적 사이트(tools/export_static.py로 구운 것)에서는 서버가 없다. API 주소를 구워 둔 파일
// 경로로 바꾼다: /api/x/y?a=b -> api/x/y__a-b.json, 글 원문은 .txt, 실제 파일은 경로 그대로.
// 이 규칙은 export_static.py의 static_path()와 같아야 한다.
const STATIC = !!window.STATIC_SITE;
function U(path) {
  if (!STATIC || !path.startsWith("/")) return path;
  let [p, q] = path.split("?");
  p = p.slice(1);
  if (p.startsWith("static/") || p.includes("/file/")) return p;
  const qs = (q || "").split("&").filter((x) => x && !/^(download|since|fresh)=/.test(x)).map((x) => x.replace("=", "-")).join("_");
  return p + (qs ? "__" + qs : "") + (p.includes("/text/") ? ".txt" : ".json");
}

async function api(path, opt = {}) {
  if (STATIC && opt.method && opt.method !== "GET") throw new Error("보기 전용 사이트에서는 할 수 없는 동작입니다.");
  const r = await fetch(U(path), { headers: { "content-type": "application/json" }, ...opt,
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

// ---- 결과 판정: 예측 범위 [lo, hi] 대비 실제 수익률 v
//  크게 성공 = 상단 초과 / 성공 = 0 이상 상단 이하 / 범위 내 실패 = 하단 이상 0 미만 / 크게 실패 = 하단 미만
function outcome(v, lo, hi) {
  v = num(v); lo = num(lo); hi = num(hi);
  if (v === null || lo === null || hi === null) return null;
  const d = (x) => `${x > 0 ? "+" : ""}${x.toFixed(1)}%p`;
  if (v > hi) return { key: "big-win", label: "크게 성공", cls: "o-bigwin", note: `상단 ${hi}% 대비 ${d(v - hi)}` };
  if (v >= 0) return { key: "win", label: "성공", cls: "o-win", note: `상단까지 ${d(v - hi)}` };
  if (v >= lo) return { key: "miss", label: "범위 내 실패", cls: "o-miss", note: `하단 ${lo}%까지 여유 ${d(v - lo)}` };
  return { key: "big-miss", label: "크게 실패", cls: "o-bigmiss", note: `하단 ${lo}% 대비 ${d(v - lo)}` };
}
const outcomeTag = (o) => (o ? `<span class="tag ${o.cls}">${o.label}</span> <span class="muted" style="font-size:11.5px">${o.note}</span>` : "—");
