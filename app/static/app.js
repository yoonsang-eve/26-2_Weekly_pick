// Weekly Pick 피드백 대시보드. 서버의 /api/runs 만 읽는다.
const $ = (s, el = document) => el.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const STAGE_KO = { analyst: "Analyst", bull: "Bull", bear: "Bear", trader: "Trader" };

let RUNS = [];
let MARKET = "ALL";

const num = (v) => (v === null || v === undefined || v === "" || isNaN(+v) ? null : +v);
const pct = (v, d = 2) => (v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const pp = (v) => (v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%p`);
const cls = (v) => (v === null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");
const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const label = (r) => `wk${r.week} ${r.market}`;

function svg(tag, attrs = {}, parent) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

// ── tooltip ────────────────────────────────────────────────
const tip = $("#tip");
function showTip(e, html) {
  tip.innerHTML = html;
  tip.classList.add("on");
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
  const y = e.clientY - tip.offsetHeight - 12;
  tip.style.left = `${x}px`;
  tip.style.top = `${y < 8 ? e.clientY + 16 : y}px`;
}
const hideTip = () => tip.classList.remove("on");
function hover(el, html) {
  el.addEventListener("mousemove", (e) => showTip(e, html));
  el.addEventListener("mouseleave", hideTip);
}

// ── data ───────────────────────────────────────────────────
const visible = () => RUNS.filter((r) => MARKET === "ALL" || r.market === MARKET);
const settled = () => visible().filter((r) => r.settled);

function nice(lo, hi) {
  const step = hi - lo > 30 ? 10 : 5;
  return [Math.floor(lo / step) * step, Math.ceil(hi / step) * step, step];
}

// ── KPIs ───────────────────────────────────────────────────
function renderKpis() {
  const s = settled();
  const rets = s.map((r) => num(r.settled.ret_pct)).filter((v) => v !== null);
  const exc = s.map((r) => num(r.settled.excess_pct)).filter((v) => v !== null);
  const hits = s.filter((r) => r.settled.hit_range);
  const hitY = hits.filter((r) => r.settled.hit_range === "Y").length;
  const ranks = s.map((r) => r.cohort?.pick_rank).filter(Boolean);
  const pending = visible().length - s.length;
  const k = [
    ["정산 완료", `${s.length}`, pending ? `대기 ${pending}건` : "전부 정산됨", ""],
    ["평균 수익률", pct(avg(rets)), `최고 ${pct(rets.length ? Math.max(...rets) : null)}`, cls(avg(rets))],
    ["평균 초과수익", pp(avg(exc)), "벤치마크 대비", cls(avg(exc))],
    ["벤치마크 승률", s.length ? `${exc.filter((v) => v > 0).length}/${exc.length}` : "—", "초과수익 > 0", ""],
    ["예측범위 적중", hits.length ? `${hitY}/${hits.length}` : "—", "실제가 구간 안", ""],
    ["후보 내 순위", ranks.length ? `${avg(ranks).toFixed(1)}위` : "—", "20종목 중 평균", ""],
  ];
  $("#kpis").innerHTML = k
    .map(([l, v, sub, c]) => `<div class="kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${sub}</div></div>`)
    .join("");
}

// ── strip plot: 후보군 분포 속 추천 종목 ──────────────────────
async function renderStrip() {
  const host = $("#strip");
  const runs = visible().filter((r) => r.cohort);
  if (!runs.length) {
    host.innerHTML = `<div class="empty">후보군 정산 데이터가 없다. <code>python src/cohort.py --week N</code>로 만든다.</div>`;
    return;
  }
  const details = await Promise.all(runs.map((r) => fetch(`/api/runs/${r.week}/${r.market}`).then((x) => x.json())));
  const all = details.flatMap((d) => d.cohort_rows.map((c) => num(c.ret_pct))).filter((v) => v !== null);
  const benches = runs.map((r) => num(r.settled?.benchmark_ret_pct)).filter((v) => v !== null);
  const [lo, hi, step] = nice(Math.min(0, ...all, ...benches), Math.max(0, ...all, ...benches));

  const W = host.clientWidth || 900, L = 120, R = 16, rowH = 56, T = 8;
  const H = T + rowH * runs.length + 26;
  const x = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
  host.innerHTML = "";
  const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "후보군 수익률 분포" }, host);

  for (let v = lo; v <= hi; v += step) {
    svg("line", { x1: x(v), x2: x(v), y1: T, y2: H - 22, class: v === 0 ? "zero" : "gl" }, g);
    const t = svg("text", { x: x(v), y: H - 6, "text-anchor": "middle" }, g);
    t.textContent = `${v > 0 ? "+" : ""}${v}%`;
  }

  details.forEach((d, i) => {
    const cy = T + rowH * i + rowH / 2;
    const r = runs[i];
    const t1 = svg("text", { x: 0, y: cy - 3, class: "row-l" }, g);
    t1.textContent = label(r);
    const t2 = svg("text", { x: 0, y: cy + 13, class: "row-s" }, g);
    t2.textContent = `후보 평균 ${pct(r.cohort.candidates_avg, 1)}`;

    // 후보 평균 표시 (가는 세로선)
    svg("line", { x1: x(r.cohort.candidates_avg), x2: x(r.cohort.candidates_avg), y1: cy - 14, y2: cy + 14,
      stroke: "var(--cand)", "stroke-width": 2, "stroke-dasharray": "3 3" }, g);

    const bench = num(r.settled?.benchmark_ret_pct);
    if (bench !== null) {
      const bl = svg("line", { x1: x(bench), x2: x(bench), y1: cy - 18, y2: cy + 18, stroke: "var(--ink-2)", "stroke-width": 2 }, g);
      hover(bl, `${r.settled.benchmark} ${pct(bench)}`);
    }

    // 후보 → 최종후보 → 추천 순으로 그려 추천이 맨 위에 오게 한다
    const order = { candidate: 0, finalist: 1, pick: 2 };
    const rows = d.cohort_rows.filter((c) => num(c.ret_pct) !== null).sort((a, b) => order[a.role] - order[b.role]);
    rows.forEach((c, j) => {
      const v = num(c.ret_pct);
      const jitter = c.role === "candidate" ? ((j * 7) % 5 - 2) * 3 : 0; // 겹침 완화용 고정 지터
      const rad = c.role === "pick" ? 8 : c.role === "finalist" ? 6 : 5;
      const fill = c.role === "pick" ? "var(--pick)" : c.role === "finalist" ? "var(--fin)" : "var(--cand)";
      const dot = svg("circle", { cx: x(v), cy: cy + jitter, r: rad, fill, stroke: "var(--surface)", "stroke-width": 2 }, g);
      const role = { pick: "추천", finalist: "최종후보", candidate: "후보" }[c.role];
      const hit = svg("circle", { cx: x(v), cy: cy + jitter, r: rad + 5, fill: "transparent", class: "hit" }, g);
      hover(hit, `<b>${esc(c.name || c.ticker)}</b> ${esc(c.ticker)}<br>${role} · ${pct(v)}`);
      if (c.role === "pick") {
        const tl = svg("text", { x: x(v), y: cy - 13, "text-anchor": "middle", class: "row-l" }, g);
        tl.textContent = `${c.name} ${pct(v, 1)}`;
      }
      void dot;
    });
  });
}

// ── 예측 범위 vs 실제 ───────────────────────────────────────
function renderRange() {
  const host = $("#range");
  const runs = visible().filter((r) => r.pick?.expected_return_range);
  if (!runs.length) { host.innerHTML = `<div class="empty">예측 범위가 있는 주차가 없다.</div>`; return; }
  const vals = runs.flatMap((r) => [...r.pick.expected_return_range.map(Number), num(r.settled?.ret_pct)]).filter((v) => v !== null);
  const [lo, hi, step] = nice(Math.min(0, ...vals), Math.max(0, ...vals));
  const W = host.clientWidth || 500, L = 76, R = 16, rowH = 38, T = 6;
  const H = T + rowH * runs.length + 24;
  const x = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
  host.innerHTML = "";
  const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "예측 범위 대비 실제 수익률" }, host);
  for (let v = lo; v <= hi; v += step) {
    svg("line", { x1: x(v), x2: x(v), y1: T, y2: H - 20, class: v === 0 ? "zero" : "gl" }, g);
    svg("text", { x: x(v), y: H - 5, "text-anchor": "middle" }, g).textContent = `${v > 0 ? "+" : ""}${v}%`;
  }
  runs.forEach((r, i) => {
    const cy = T + rowH * i + rowH / 2;
    const [a, b] = r.pick.expected_return_range.map(Number);
    svg("text", { x: 0, y: cy + 4, class: "row-l" }, g).textContent = label(r);
    const band = svg("rect", { x: x(a), y: cy - 7, width: Math.max(2, x(b) - x(a)), height: 14, rx: 4, fill: "var(--band)" }, g);
    hover(band, `예측 ${pct(a, 0)} ~ ${pct(b, 0)}`);
    const act = num(r.settled?.ret_pct);
    if (act === null) {
      svg("text", { x: x(b) + 8, y: cy + 4 }, g).textContent = "정산 대기";
    } else {
      const inRange = act >= a && act <= b;
      svg("circle", { cx: x(act), cy, r: 7, fill: "var(--pick)", stroke: "var(--surface)", "stroke-width": 2 }, g);
      const hit = svg("circle", { cx: x(act), cy, r: 13, fill: "transparent" }, g);
      hover(hit, `<b>${esc(r.pick.name)}</b><br>실제 ${pct(act)} · ${inRange ? "범위 안" : "범위 밖"}`);
      const t = svg("text", { x: x(act) + (act > b ? 11 : -11), y: cy + 4, "text-anchor": act > b ? "start" : "end", class: "row-l" }, g);
      t.textContent = pct(act, 1);
    }
  });
}

// ── 단계별 기여도 ───────────────────────────────────────────
function renderContrib() {
  const rows = visible().filter((r) => r.cohort && r.settled);
  if (!rows.length) { $("#contrib").innerHTML = `<div class="empty">정산된 후보군이 없다.</div>`; return; }
  const cell = (v) => `<td class="${cls(v)}">${pp(v)}</td>`;
  const data = rows.map((r) => {
    const b = num(r.settled.benchmark_ret_pct), c = r.cohort;
    return [label(r), b === null ? null : c.candidates_avg - b, c.finalists_avg - c.candidates_avg, c.pick - c.finalists_avg];
  });
  const mean = (i) => avg(data.map((d) => d[i]).filter((v) => v !== null));
  $("#contrib").innerHTML = `
    <table>
      <thead><tr><th>주차</th><th>Screener<br><small>후보평균−벤치</small></th><th>Analyst<br><small>최종후보−후보</small></th><th>Trader<br><small>추천−최종후보</small></th></tr></thead>
      <tbody>${data.map((d) => `<tr><td>${d[0]}</td>${cell(d[1])}${cell(d[2])}${cell(d[3])}</tr>`).join("")}</tbody>
      <tfoot><tr><td><b>평균</b></td>${cell(mean(1))}${cell(mean(2))}${cell(mean(3))}</tr></tfoot>
    </table>
    <p class="hint" style="margin-top:10px">표본이 주당 1개라 몇 주 쌓이기 전까지는 방향만 참고한다.</p>`;
}

// ── 주차 카드 ──────────────────────────────────────────────
function renderRuns() {
  const runs = [...visible()].reverse();
  $("#runs").innerHTML = runs.map((r, i) => {
    const ret = num(r.settled?.ret_pct);
    const p = r.pick || {};
    return `<button class="run" data-i="${RUNS.indexOf(r)}">
      <div class="meta"><span>wk${r.week} · ${r.market}</span><span class="badge ${ret === null ? "pending" : ""}">${ret === null ? "정산 대기" : r.settled.hit_range === "Y" ? "범위 적중" : "범위 이탈"}</span></div>
      <div class="nm">${esc(p.name || "—")} <span class="sub">${esc(p.ticker || "")}</span></div>
      <div class="ret ${cls(ret)}">${pct(ret)}</div>
      <div class="sub">${r.settled ? `${r.settled.benchmark} ${pct(num(r.settled.benchmark_ret_pct))} · 초과 ${pp(num(r.settled.excess_pct))}` : `예측 ${p.expected_return_range ? p.expected_return_range.join("% ~ ") + "%" : "—"}`}</div>
    </button>`;
  }).join("") || `<div class="empty">기록이 없다.</div>`;
  document.querySelectorAll(".run").forEach((b) => b.addEventListener("click", () => openRun(RUNS[+b.dataset.i])));
}

// ── 상세 드로어 ─────────────────────────────────────────────
async function openRun(r) {
  const d = await fetch(`/api/runs/${r.week}/${r.market}`).then((x) => x.json());
  const p = d.pick || {};
  const s = d.settled;
  const fmtK = (v) => (v ? `${(v / 1000).toFixed(0)}k` : "—");
  $("#drawer-body").innerHTML = `
    <p class="sub">wk${d.week} · ${d.market} · 프롬프트 ${d.prompt_version || "—"} · config v${d.config_version ?? "—"} · ${d.mode || "—"} 모드</p>
    <h2 style="font-size:22px;margin-top:4px">${esc(p.name)} <span class="sub">${esc(p.ticker)}</span></h2>
    <p style="font-size:20px;font-weight:700;margin:6px 0" class="${cls(num(s?.ret_pct))}">
      ${s ? `${pct(num(s.ret_pct))} <span class="sub">(${s.buy_date} 시가 ${(+s.buy_open).toLocaleString()} → ${s.sell_date} 종가 ${(+s.sell_close).toLocaleString()})</span>` : "정산 대기"}</p>
    ${d.cohort ? `<p class="sub">후보 ${d.cohort.n}종목 중 ${d.cohort.pick_rank}위 · 후보 평균 ${pct(d.cohort.candidates_avg)} · 최종후보 평균 ${pct(d.cohort.finalists_avg)}</p>` : ""}
    <h3>Thesis</h3><ul>${(p.thesis || []).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
    <h3>Catalyst</h3><p>${esc(p.catalyst)}</p>
    <h3>Primary risk</h3><p>${esc(p.primary_risk)}</p>
    <h3>탈락 (최종후보)</h3><ul>${(p.rejected || []).map((x) => {
      const c = d.cohort_rows.find((c) => c.ticker === String(x.ticker));
      return `<li><b>${esc(c?.name || x.ticker)}</b> ${c ? `<span class="${cls(num(c.ret_pct))}">${pct(num(c.ret_pct))}</span>` : ""} — ${esc(x.reason)}</li>`;
    }).join("")}</ul>
    <h3>에이전트 단계</h3>
    <div class="stages">${Object.entries(d.stages).map(([k, v]) => `
      <div class="stage"><b>${STAGE_KO[k]}</b>
        ${esc(v.model || "—")}<br>
        ${v.elapsed_sec ? `${v.elapsed_sec.toFixed(0)}s · ` : ""}검색 ${v.web_search_calls ?? "—"}<br>
        in ${fmtK(v.input_tokens)} / out ${fmtK(v.output_tokens)}
        <div class="btns">
          ${d.responses.includes(k) ? `<button data-k="response" data-s="${k}">응답</button>` : ""}
          <button data-k="prompt" data-s="${k}">프롬프트</button>
        </div>
      </div>`).join("")}</div>
    <div id="doc" class="doc"></div>`;
  document.querySelectorAll(".stage button").forEach((b) =>
    b.addEventListener("click", async () => {
      const res = await fetch(`/api/runs/${d.week}/${d.market}/text/${b.dataset.k}/${b.dataset.s}`);
      const txt = res.ok ? await res.text() : "(파일 없음)";
      const title = `${STAGE_KO[b.dataset.s]} ${b.dataset.k === "prompt" ? "렌더링된 프롬프트" : "응답 원문"}`;
      $("#doc").innerHTML = `<h3>${title}</h3>` +
        (b.dataset.k === "response" && window.marked ? marked.parse(txt) : `<pre>${esc(txt)}</pre>`);
      $("#doc").scrollIntoView({ behavior: "smooth" });
    }));
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); }
$("#drawer-x").addEventListener("click", closeDrawer);
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ── wiring ─────────────────────────────────────────────────
function renderAll() { renderKpis(); renderStrip(); renderRange(); renderContrib(); renderRuns(); }
document.querySelectorAll("#mkt-filter button").forEach((b) =>
  b.addEventListener("click", () => {
    MARKET = b.dataset.m;
    document.querySelectorAll("#mkt-filter button").forEach((x) => x.classList.toggle("on", x === b));
    renderAll();
  }));
let rt;
addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { renderStrip(); renderRange(); }, 150); });

fetch("/api/runs").then((r) => r.json()).then((d) => { RUNS = d; renderAll(); });
