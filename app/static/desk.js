// Day Desk — events(기록 또는 복원)를 방·터미널·봉투 상태로 풀어 보여준다.
// 과거 주차는 재생, 파이프라인이 지금 돌고 있는 주차는 SSE로 실시간 반영한다.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cssVar = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const ROOMS = [
  { id: "scr", n: 1, name: "스크리닝", sub: "Screener · LLM 없음", c: "--c-scr" },
  { id: "ana", n: 2, name: "리서치", sub: "Analyst · 20→3", c: "--c-ana" },
  { id: "deb", n: 3, name: "토론실", sub: "Bull vs Bear", c: "--c-bull" },
  { id: "ord", n: 6, name: "주문 데스크", sub: "Phase C · 미연결", c: "--c-ord", locked: true },
  { id: "set", n: 5, name: "복기실", sub: "Settle · 정산", c: "--c-set" },
  { id: "trd", n: 4, name: "트레이더", sub: "Trader · 3→1", c: "--c-trd" },
];
const PATH = ["scr", "ana", "deb", "trd", "set"];           // 봉투 동선
const ROOM_OF = { screener: "scr", analyst: "ana", bull: "deb", bear: "deb", trader: "trd", settle: "set" };
const AGENTS = [["screener", "Screener", "--c-scr"], ["analyst", "Analyst", "--c-ana"], ["bull", "Bull", "--c-bull"],
                ["bear", "Bear", "--c-bear"], ["trader", "Trader", "--c-trd"], ["settle", "Settle", "--c-set"]];
const PAUSE = { start: 900, done: 900, step: 350, search: 550, note: 700, wait: 900, error: 900 };

let RUNS = [], CUR = null, DETAIL = null, EVENTS = [], SOURCE = "", SPEED = 1;
let timer = null, es = null, userPicked = false;
let S;   // 화면 상태

// ---------------------------------------------------------------- 방 그리기
function buildRooms(bars) {
  $("#rooms").innerHTML = ROOMS.map((r) => `
    <section class="room idle ${r.locked ? "locked" : ""}" id="r-${r.id}" style="--c:var(${r.c})" tabindex="${r.locked ? -1 : 0}">
      <div class="rh"><span class="num">${r.n}</span><b>${r.name}</b><small>${r.sub}</small><span class="pill" id="p-${r.id}">${r.locked ? "예정" : "대기"}</span></div>
      <div class="scene"><svg viewBox="0 0 320 130" preserveAspectRatio="xMidYMid meet">${defs}${r.id === "set" ? SC.set(cssVar(r.c), bars) : SC[r.id](cssVar(r.c))}</svg></div>
      <div class="term" id="t-${r.id}"><div class="ln mu">${r.locked ? "KIS 계좌 연결 전 · dry-run" : "— idle —"}</div></div>
    </section>`).join("");
  ROOMS.filter((r) => !r.locked).forEach((r) => {
    const el = $(`#r-${r.id}`);
    el.addEventListener("click", () => openRoom(r.id));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") openRoom(r.id); });
  });
}

const floor = $(".floor"), corr = $("#corr"), env = $("#env");
function center(id) {
  const a = $(`#r-${id}`).getBoundingClientRect(), f = floor.getBoundingClientRect();
  return [a.left - f.left + a.width / 2, a.top - f.top + a.height / 2, a, f];
}
function drawCorr() {
  const pts = PATH.map(center);
  corr.setAttribute("viewBox", `0 0 ${floor.clientWidth} ${floor.clientHeight}`);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(0)} ${p[1].toFixed(0)}`).join(" ");
  corr.innerHTML = `<path d="${d}" fill="none" stroke="rgba(120,100,80,.10)" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="${d}" fill="none" stroke="rgba(217,119,6,.55)" stroke-width="2" stroke-dasharray="1 10" stroke-linecap="round"/>`;
  if (S) moveEnv(S.envRoom, S.envN);
}
function moveEnv(id, n) {
  S.envRoom = id; S.envN = n;
  const [x, , a, f] = center(id);
  env.style.left = `${x + a.width * 0.36}px`;
  env.style.top = `${a.top - f.top + a.height * 0.5}px`;
  $("#envn").textContent = n ?? "—";
}

// ---------------------------------------------------------------- 상태 반영
function fresh() {
  return { room: {}, term: {}, agent: {}, envRoom: "scr", envN: null, day0: null };
}
function setRoom(id, st, label) {
  S.room[id] = st;
  const r = $(`#r-${id}`);
  r.classList.remove("idle", "work", "done", "err");
  r.classList.add(st);
  $(`#p-${id}`).textContent = label || { idle: "대기", work: "작업 중", done: "완료", err: "오류" }[st];
  termRender(id);   // 커서 표시를 상태에 맞춘다
}
function termPush(id, html) {
  const t = (S.term[id] = S.term[id] || []);
  t.push(html);
  if (t.length > 4) t.shift();
  termRender(id);
}
function termRender(id) {
  const t = S.term[id] || [];
  if (!t.length) return;
  $(`#t-${id}`).innerHTML = t.map((l, i) => `<div class="ln ${i === t.length - 1 && S.room[id] === "work" ? "caret" : ""}">${l}</div>`).join("");
}
function bubble(which, text) {
  const g = $(`#${which}`);
  if (!g) return;
  if (!text) { g.classList.remove("on"); return; }
  $(`#${which}t`).textContent = text.length > 20 ? text.slice(0, 19) + "…" : text;
  g.classList.add("on");
}
function stamp(ts) {
  const d = new Date(ts);
  const day = ts.slice(0, 10);
  S.day0 = S.day0 || day;
  const hm = d.toTimeString().slice(0, 5);
  return day === S.day0 ? hm : `${ts.slice(5, 10)} ${hm}`;
}
function logLine(e) {
  const [, label, c] = AGENTS.find((a) => a[0] === e.agent) || [0, e.agent, "--ink2"];
  const mark = e.kind === "done" ? `<span class="okc">✓</span> ` : e.kind === "error" ? `<span class="noc">✗</span> ` : "";
  const d = document.createElement("div");
  d.innerHTML = `<t>${stamp(e.ts)}</t><span style="color:var(${c})">${label.toLowerCase()}</span> ${mark}${esc(e.msg)}`;
  const log = $("#log");
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}

function apply(e) {
  const room = ROOM_OF[e.agent];
  if (!room) return;
  const a = (S.agent[e.agent] = S.agent[e.agent] || {});
  const who = `<span class="hi">${e.agent}</span>`;
  switch (e.kind) {
    case "start":
      a.st = "work";
      setRoom(room, "work");
      moveEnv(room, S.envN);
      termPush(room, `<span class="mu">$</span> ${who} ${esc(e.msg)}`);
      if (e.agent === "bull") bubble("bb", "상승 시나리오 작성 중");
      if (e.agent === "bear") { bubble("bb", null); bubble("br", "반박 근거 찾는 중"); }
      break;
    case "wait":
      a.st = "work";
      setRoom(room, "work", "수동 대기");
      moveEnv(room, S.envN);
      termPush(room, `<span class="mu">…</span> ${esc(e.msg)}`);
      break;
    case "step":
      termPush(room, `<span class="mu">${esc(e.msg)}</span> → <b>${e.data?.n ?? ""}</b>`);
      break;
    case "search":
      termPush(room, `<span class="mu">search</span> ${esc(e.msg)}`);
      if (e.agent === "bear") bubble("br", e.msg);
      if (e.agent === "analyst") a.searches = (a.searches || 0) + 1;
      break;
    case "note":
      termPush(room, e.msg.startsWith("통과") ? `<span class="ok">✓</span> ${esc(e.msg)}` : `<span class="no">!</span> ${esc(e.msg)}`);
      if (e.agent === "bear") bubble("br", e.msg.split(" · ")[1] || e.msg);
      break;
    case "done":
      a.st = "done"; a.msg = e.msg;
      termPush(room, `<span class="ok">✓</span> ${esc(e.msg)}`);
      if (e.agent === "bull") { setRoom(room, "work", "Bear 차례"); break; }
      setRoom(room, "done");
      if (e.agent === "bear") bubble("br", null);
      if (e.agent === "screener") moveEnv("scr", e.data?.n ?? 20);
      if (e.agent === "analyst") moveEnv("ana", e.data?.finalists?.length || 3);
      if (e.agent === "trader") { moveEnv("trd", 1); showPick(); }
      if (e.agent === "settle") { moveEnv("set", 1); showPick(e.data); }
      break;
    case "error":
      a.st = "err"; a.msg = e.msg;
      setRoom(room, "err");
      termPush(room, `<span class="no">✗</span> ${esc(e.msg)}`);
      break;
  }
  logLine(e);
  renderSteps();
}

function renderSteps() {
  $("#steps").innerHTML = AGENTS.map(([id, label, c]) => {
    const a = S.agent[id] || {};
    const st = a.st === "err" ? "err" : a.st || "";
    return `<div class="st ${st}" style="--c:var(${c})"><i></i><span>${label}</span><em>${esc(a.st === "done" || a.st === "err" ? a.msg : a.st === "work" ? "진행 중" : "")}</em></div>`;
  }).join("");
}

function showPick(settle) {
  const p = DETAIL?.pick;
  if (!p) return;
  const range = p.expected_return_range ? `예측 ${p.expected_return_range[0]}% ~ ${p.expected_return_range[1]}%` : "";
  const res = settle?.ret_pct != null
    ? `<div class="res ${settle.ret_pct >= 0 ? "up" : "down"}">${settle.ret_pct > 0 ? "+" : ""}${settle.ret_pct.toFixed(2)}%<small> 벤치마크 ${settle.benchmark_ret_pct ?? "—"}%</small></div>` : "";
  $("#pick").innerHTML = `<div class="t">FINAL PICK</div><div class="n">${esc(p.name)} <span class="tk">${esc(p.ticker)}</span></div>
    <div class="r">${esc(range)}</div><div class="r">${esc(p.catalyst || "")}</div>${res}`;
  $("#pick").classList.add("on");
}

// ---------------------------------------------------------------- 재생 / 실시간
function stop() {
  clearTimeout(timer); timer = null;
  if (es) { es.close(); es = null; }
}
function reset() {
  stop();
  S = fresh();
  const bars = RUNS.filter((r) => r.market === CUR.market && r.settled?.ret_pct && r.week <= CUR.week)
    .map((r) => ({ label: `wk${r.week}`, v: +r.settled.ret_pct }));
  buildRooms(bars);
  $("#log").innerHTML = "";
  $("#pick").classList.remove("on");
  $("#pick").innerHTML = "";
  renderSteps();
  requestAnimationFrame(() => { drawCorr(); moveEnv("scr", null); });
}
function play(from = 0) {
  let i = from;
  const tick = () => {
    if (i >= EVENTS.length) { timer = null; return; }
    const e = EVENTS[i++];
    apply(e);
    timer = setTimeout(tick, (PAUSE[e.kind] || 600) / SPEED);
  };
  tick();
}
function isLive(run) {
  // 기록 파일이 최근 20분 안에 바뀌었고 아직 정산 전이면 실행 중인 것으로 본다
  return run.events_mtime && Date.now() / 1000 - run.events_mtime < 20 * 60 && !run.settled;
}
function goLive() {
  EVENTS.forEach(apply);
  es = new EventSource(`/api/runs/${CUR.week}/${CUR.market}/stream?since=${EVENTS.length}`);
  es.onmessage = (m) => { const e = JSON.parse(m.data); EVENTS.push(e); apply(e); };
}

async function load(run) {
  CUR = run;
  const [ev, detail] = await Promise.all([
    fetch(`/api/runs/${run.week}/${run.market}/events`).then((r) => r.json()),
    fetch(`/api/runs/${run.week}/${run.market}`).then((r) => r.json()),
  ]);
  EVENTS = ev.events; SOURCE = ev.source; DETAIL = detail;
  $("#ver").innerHTML = `PROMPT <b>${esc(detail.prompt_version || "—")}</b> · ${esc(detail.mode || "—")}`;
  reset();
  const live = SOURCE === "recorded" && isLive(run);
  $("#mode").className = `chip ${live ? "live" : ""}`;
  $("#mode").textContent = live ? "실시간" : SOURCE === "recorded" ? "재생 · 기록" : "재생 · 산출물로 복원";
  $("#mode").title = SOURCE === "reconstructed"
    ? "events.jsonl 도입 전 주차다. run_meta·pick·log에 남은 값만으로 흐름을 복원했고 검색어는 남아 있지 않다." : "";
  setTimeout(() => (live ? goLive() : play()), 350);
}

// ---------------------------------------------------------------- 방 상세
async function openRoom(id) {
  const w = CUR.week, m = CUR.market;
  const stages = { ana: ["analyst"], deb: ["bull", "bear"], trd: ["trader"] }[id];
  let html = `<p class="sub">wk${w} · ${m}</p>`;
  if (id === "scr") {
    html += `<h2>후보 ${DETAIL.candidates.length}종목</h2><table><thead><tr><th>종목</th><th>모멘텀</th><th>5일</th><th>ATR%</th><th>실제</th></tr></thead><tbody>` +
      DETAIL.candidates.map((c) => {
        const co = DETAIL.cohort_rows.find((x) => x.ticker === c.ticker);
        const f = (v) => (v === "" || v == null ? "—" : `${(+v * 100).toFixed(1)}%`);
        const role = co?.role === "pick" ? " ★" : co?.role === "finalist" ? " ·" : "";
        return `<tr><td>${esc(c.name)}${role}</td><td>${f(c.ret_mom)}</td><td>${f(c.ret5)}</td><td>${f(c.atr20_pct)}</td><td>${co?.ret_pct ? `${co.ret_pct}%` : "—"}</td></tr>`;
      }).join("") + `</tbody></table><p class="sub">★ 추천 · 최종후보 / 실제 = 같은 주 월 시가→금 종가 (cohort.py)</p>`;
  } else if (id === "set") {
    const s = DETAIL.settled;
    html += s ? `<h2>${esc(s.name)} ${s.ret_pct}%</h2><p>${s.buy_date} 시가 ${(+s.buy_open).toLocaleString()} → ${s.sell_date} 종가 ${(+s.sell_close).toLocaleString()}</p>
      <p>${s.benchmark} ${s.benchmark_ret_pct}% · 초과 ${s.excess_pct}%p · 예측범위 ${s.hit_range === "Y" ? "적중" : "이탈"}</p>
      <p><a href="/">피드백 대시보드에서 자세히 →</a></p>` : `<h2>정산 대기</h2><p>매도일 종가가 확정되면 <code>python src/settle.py</code></p>`;
  } else {
    html += `<div class="tabs2">${stages.flatMap((s) => [
      DETAIL.responses.includes(s) ? `<button data-k="response" data-s="${s}">${s} 응답</button>` : "",
      `<button data-k="prompt" data-s="${s}">${s} 프롬프트</button>`]).join("")}</div><div id="doc" class="doc"></div>`;
  }
  $("#drawer-body").innerHTML = html;
  document.querySelectorAll(".tabs2 button").forEach((b) => b.addEventListener("click", async () => {
    document.querySelectorAll(".tabs2 button").forEach((x) => x.classList.toggle("on", x === b));
    const r = await fetch(`/api/runs/${w}/${m}/text/${b.dataset.k}/${b.dataset.s}`);
    const t = r.ok ? await r.text() : "(파일 없음)";
    $("#doc").innerHTML = b.dataset.k === "response" && window.marked ? marked.parse(t) : `<pre>${esc(t)}</pre>`;
  }));
  document.querySelector(".tabs2 button")?.click();
  $("#drawer").classList.add("open");
}
const closeDrawer = () => $("#drawer").classList.remove("open");
$("#drawer-x").onclick = closeDrawer;
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ---------------------------------------------------------------- 선택 / 초기화
const key = (r) => `${r.week}-${r.market}`;
function fillSelect() {
  const sel = $("#run");
  sel.innerHTML = [...RUNS].reverse().map((r) =>
    `<option value="${key(r)}">wk${r.week} · ${r.market}${r.pick ? " · " + esc(r.pick.name) : ""}${isLive(r) ? " ●" : ""}</option>`).join("");
  if (CUR) sel.value = key(CUR);
}
function defaultRun() {
  const live = RUNS.filter(isLive).sort((a, b) => b.events_mtime - a.events_mtime)[0];
  return live || RUNS[RUNS.length - 1];
}
$("#run").addEventListener("change", (e) => { userPicked = true; load(RUNS.find((r) => key(r) === e.target.value)); });
$("#replay").onclick = () => { stop(); reset(); setTimeout(() => play(), 350); };
document.querySelectorAll("[data-sp]").forEach((b) => b.addEventListener("click", () => {
  SPEED = +b.dataset.sp;
  document.querySelectorAll("[data-sp]").forEach((x) => x.classList.toggle("on", x === b));
}));
let rt;
addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(drawCorr, 120); });

async function refreshRuns() {
  RUNS = await fetch("/api/runs").then((r) => r.json());
  fillSelect();
  // 사용자가 직접 고르지 않았다면, 새로 돌기 시작한 실행으로 자동 전환한다
  const live = RUNS.filter(isLive).sort((a, b) => b.events_mtime - a.events_mtime)[0];
  if (!userPicked && live && (!CUR || key(live) !== key(CUR))) load(live);
}
(async () => {
  RUNS = await fetch("/api/runs").then((r) => r.json());
  const r = defaultRun();
  fillSelect();
  $("#run").value = key(r);
  load(r);
  setInterval(refreshRuns, 15000);
})();
