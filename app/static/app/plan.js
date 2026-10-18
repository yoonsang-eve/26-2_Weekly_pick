// 도면 — 파이프라인 단계를 방으로 그린 평면도. events(기록 또는 복원)로 상태를 바꾼다.
const Plan = (() => {
  // 복도(MID~COR)는 문 두 짝이 마주 열려도 가운데에 동선과 봉투가 지나갈 폭을 둔다.
  const TOP = 40, MID = 290, COR = 380, BOT = 620, HALL = (MID + COR) / 2;
  const ROOMS = {
    scr: { n: "01", name: "스크리닝실", sub: "기계적 필터, LLM 없음", x: 20, y: TOP, w: 300, h: MID - TOP, door: 250 },
    ana: { n: "02", name: "리서치실", sub: "Analyst: 20종목 중 3개만 남긴다", x: 320, y: TOP, w: 300, h: MID - TOP, door: 560 },
    deb: { n: "03", name: "토론실", sub: "Bull과 Bear, 서로 다른 대화", x: 620, y: TOP, w: 320, h: MID - TOP, door: 660 },
    ord: { n: "06", name: "주문실", sub: "계좌 연결 전", x: 20, y: COR, w: 300, h: BOT - COR, door: 250, locked: true },
    set: { n: "05", name: "복기실", sub: "월 시가 → 금 종가 정산", x: 320, y: COR, w: 300, h: BOT - COR, door: 560 },
    trd: { n: "04", name: "트레이더실", sub: "3개 중 1개를 고른다", x: 620, y: COR, w: 320, h: BOT - COR, door: 660 },
  };
  const ORDER = ["scr", "ana", "deb", "trd", "set"];
  const ROOM_OF = { screener: "scr", analyst: "ana", bull: "deb", bear: "deb", trader: "trd", settle: "set" };
  const AGENTS = [["screener", "Screener"], ["analyst", "Analyst"], ["bull", "Bull"], ["bear", "Bear"], ["trader", "Trader"], ["settle", "정산"]];
  const PAUSE = { start: 900, done: 900, step: 350, search: 550, note: 700, wait: 900, error: 900 };
  const svg = $("#plan");
  let S, EV = [], timer = null, es = null, speed = 1, cur = null, detail = null, source = "", seq = 0;
  const MK = { KR: "한국", US: "미국" };

  // ---------------------------------------------------------------- 그리기
  // 위치(transform 속성)와 애니메이션(CSS transform)을 다른 g에 둔다. 같은 g에 두면 CSS가 위치를 덮어쓴다.
  const person = (x, y, id) => `<g class="person" ${id ? `id="${id}"` : ""} transform="translate(${x} ${y})"><g class="fig"><ellipse class="body" rx="15" ry="9"/><circle class="head" cy="-1" r="6.5"/></g></g>`;
  const chair = (x, y) => `<rect class="fur" x="${x - 10}" y="${y - 8}" width="20" height="16" rx="3"/>`;
  function furniture(id, r) {
    const X = r.x, Y = r.y;
    switch (id) {
      case "scr": return [0, 1, 2, 3].map((i) => `<g><rect class="fur" x="${X + 40 + i * 32}" y="${Y + 78}" width="26" height="74"/><path class="fur-l" d="M${X + 40 + i * 32} ${Y + 78} l26 74 M${X + 66 + i * 32} ${Y + 78} l-26 74"/></g>`).join("")
        + `<path class="fur" d="M${X + 190} ${Y + 84} h84 l-30 38 v34 h-24 v-34 z"/><text class="dimt" x="${X + 232}" y="${Y + 76}" text-anchor="middle">전종목</text><text class="note hi" id="scr-n" x="${X + 232}" y="${Y + 172}" text-anchor="middle">—</text>`;
      case "ana": return `<rect class="fur" x="${X + 24}" y="${Y + 70}" width="22" height="100"/><path class="fur-l" d="M${X + 24} ${Y + 95}h22M${X + 24} ${Y + 120}h22M${X + 24} ${Y + 145}h22"/>
        <rect class="fur" x="${X + 100}" y="${Y + 88}" width="140" height="44"/><path class="fur-l" d="M${X + 140} ${Y + 96}h60"/><rect class="fur" x="${X + 250}" y="${Y + 94}" width="18" height="22"/>
        ${chair(X + 170, Y + 150)}${person(X + 170, Y + 148)}`;
      case "deb": return `<ellipse class="fur" cx="${X + 160}" cy="${Y + 128}" rx="74" ry="40"/>
        ${[0, 1, 2].map((i) => `<rect class="fur" x="${X + 132 + i * 20}" y="${Y + 116}" width="14" height="20" transform="rotate(${(i - 1) * 9} ${X + 139 + i * 20} ${Y + 126})"/>`).join("")}
        ${chair(X + 64, Y + 128)}${person(X + 64, Y + 128, "p-bull")}${chair(X + 256, Y + 128)}${person(X + 256, Y + 128, "p-bear")}
        <text class="dimt" x="${X + 64}" y="${Y + 158}" text-anchor="middle">Bull</text><text class="dimt" x="${X + 256}" y="${Y + 158}" text-anchor="middle">Bear</text>
        <g class="callout bull" id="co-bull"><path class="callout-line" d="M${X + 64} ${Y + 116} L${X + 64} ${Y + 92}"/><rect x="${X + 14}" y="${Y + 70}" height="22"/><text x="${X + 22}" y="${Y + 85}"></text></g>
        <g class="callout bear" id="co-bear"><path class="callout-line" d="M${X + 256} ${Y + 116} L${X + 256} ${Y + 92}"/><rect y="${Y + 70}" height="22"/><text y="${Y + 85}"></text></g>`;
      case "trd": return `<path class="fur" d="M${X + 90} ${Y + 64} h150 v24 h-126 v50 h-24 z"/>
        ${[0, 1, 2].map((i) => `<path class="fur-l" d="M${X + 130 + i * 36} ${Y + 70} h26" stroke-width="2.4"/>`).join("")}
        ${chair(X + 160, Y + 110)}${person(X + 160, Y + 108)}`;
      case "set": return `<rect class="fur" x="${X + 24}" y="${Y + 62}" width="150" height="64"/><g id="set-bars"></g>
        ${chair(X + 236, Y + 102)}${person(X + 236, Y + 100)}<rect class="fur" x="${X + 214}" y="${Y + 66}" width="44" height="22"/>`;
      case "ord": return `<rect class="fur" x="${X + 90}" y="${Y + 70}" width="120" height="40" stroke-dasharray="4 3"/><text class="rsub" x="${X + 150}" y="${Y + 140}" text-anchor="middle">한국투자증권 연결 후 사용</text>`;
    }
  }
  function door(r, top) {
    const d = r.door, y = top ? MID : COR, dir = top ? 1 : -1;      // 복도 쪽으로 열린다
    return `<rect class="gap" x="${d}" y="${y - 4}" width="36" height="8"/><path class="door" d="M${d} ${y} L${d} ${y + dir * 32} M${d + 36} ${y} A36 36 0 0 ${top ? 1 : 0} ${d} ${y + dir * 36}"/>`;
  }
  function build(bars) {
    const rooms = Object.entries(ROOMS).map(([id, r]) => {
      return `<g class="room ${r.locked ? "locked" : ""}" id="r-${id}" data-id="${id}" tabindex="${r.locked ? -1 : 0}" role="button" aria-label="${r.name}">
        <rect class="room-fill ${r.locked ? "hatch-off" : ""}" x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}"/>
        ${furniture(id, r)}
        <g class="rtag"><circle cx="${r.x + 28}" cy="${r.y + 28}" r="13"/><text class="n" x="${r.x + 28}" y="${r.y + 32}" text-anchor="middle">${r.n}</text></g>
        <text class="rname" x="${r.x + 50}" y="${r.y + 28}">${r.name}</text>
        <text class="rsub" x="${r.x + 50}" y="${r.y + 44}">${r.sub}</text>
        <text class="rstate" id="st-${id}" x="${r.x + r.w - 14}" y="${r.y + 28}" text-anchor="end">${r.locked ? "잠김" : "대기"}</text>
        <g id="nt-${id}">${[0, 1, 2, 3].map((i) => `<text class="note" x="${r.x + 18}" y="${r.y + r.h - 64 + i * 15}"></text>`).join("")}</g>
      </g>`;
    }).join("");
    svg.innerHTML = `<defs>
        <pattern id="hatch-mark" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="8" height="8" fill="#ffe14d" opacity=".55"/><line x1="0" y1="0" x2="0" y2="8" stroke="#6b5600" stroke-width="1" opacity=".25"/></pattern>
        <pattern id="hatch-gray" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="7" stroke="#8a9095" stroke-width=".8" opacity=".5"/></pattern>
        <marker id="ar" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path class="arrow" d="M0 0L8 4L0 8z"/></marker>
      </defs>
      <g id="dims"><path class="dim" d="M20 22 V14 M940 22 V14 M20 18 H940"/><text class="dimt" id="dim-t" x="480" y="12" text-anchor="middle"></text></g>
      ${rooms}
      <rect class="wall-o" x="20" y="${TOP}" width="920" height="${BOT - TOP}"/>
      <path class="path" marker-end="url(#ar)" d="M${ROOMS.scr.door + 18} ${HALL} H${ROOMS.deb.door + 18}"/>
      <path class="path" marker-end="url(#ar)" d="M${ROOMS.deb.door + 90} ${HALL} H${ROOMS.set.door + 70}" opacity=".45"/>
      <text class="dimt" x="36" y="${HALL + 4}">복도</text>
      <g id="env"><path d="M0 0h16l6 6v22h-22z" fill="#f8f9f7" stroke="#121416" stroke-width="1.4"/><path d="M16 0v6h6" fill="none" stroke="#121416"/><path d="M4 12h12M4 17h12M4 22h8" stroke="#121416" stroke-width=".8"/>
        <g class="cnt"><rect x="12" y="-10" width="22" height="14"/><text id="env-n" x="23" y="0" text-anchor="middle">—</text></g></g>`;
    // 방의 경계선: 방마다 사각형 + 문 위치의 벽을 비운다
    Object.entries(ROOMS).forEach(([id, r]) => {
      const g = $(`#r-${id}`);
      svgEl("rect", { class: "wall", x: r.x, y: r.y, width: r.w, height: r.h }, g);
    });
    // 벽 위에 문틈을 다시 덮는다 (벽이 나중에 그려져서)
    Object.values(ROOMS).forEach((r) => { svg.insertAdjacentHTML("beforeend", door(r, r.y === TOP)); });
    svg.appendChild($("#env"));
    drawBars(bars);
    $$(".room:not(.locked)", svg).forEach((g) => {
      g.addEventListener("click", () => openRoom(g.dataset.id));
      g.addEventListener("keydown", (e) => { if (e.key === "Enter") openRoom(g.dataset.id); });
    });
  }
  function drawBars(bars) {
    const r = ROOMS.set, g = $("#set-bars"), bs = bars.slice(-6);
    const mx = Math.max(5, ...bs.map((b) => Math.abs(b.v))), x0 = r.x + 34, base = r.y + 94, bw = 14;
    g.innerHTML = `<line class="fur-l" x1="${r.x + 30}" x2="${r.x + 168}" y1="${base}" y2="${base}"/>` + bs.map((b, i) => {
      const h = Math.max(1.5, Math.abs(b.v) / mx * 28), x = x0 + i * 22;
      return `<rect x="${x}" y="${b.v >= 0 ? base - h : base}" width="${bw}" height="${h}" fill="${b.v >= 0 ? "#d1242f" : "#1e56c8"}"><title>${b.label} ${pct(b.v)}</title></rect>`;
    }).join("") + (bs.length ? "" : `<text class="dimt" x="${r.x + 99}" y="${base + 4}" text-anchor="middle">정산 기록 없음</text>`);
  }

  // ---------------------------------------------------------------- 봉투 이동 (문 → 복도 → 문)
  const hall = (id) => [ROOMS[id].door + 46, HALL - 14];
  function moveEnv(id, n) {
    if (n !== undefined && n !== null) { S.envN = n; $("#env-n").textContent = n; }
    if (S.envRoom === id) return;
    const pts = [hall(id)];
    S.envRoom = id;
    const env = $("#env");
    let i = 0;
    const step = () => {
      if (i >= pts.length) return;
      const [x, y] = pts[i++];
      env.style.transitionDuration = `${0.35 / speed}s`;
      env.style.transform = `translate(${x}px, ${y}px)`;
      setTimeout(step, 360 / speed);
    };
    step();
  }

  // ---------------------------------------------------------------- 상태 반영
  function roomState(id, st, label) {
    const g = $(`#r-${id}`);
    g.classList.remove("idle", "work", "done", "err");
    g.classList.add(st);
    $(`#st-${id}`).textContent = label || { idle: "대기", work: "작업 중", done: "완료", err: "오류" }[st];
  }
  function note(id, text, cls = "") {
    const L = (S.notes[id] = S.notes[id] || []);
    L.push([text, cls]);
    if (L.length > 4) L.shift();
    const maxW = ROOMS[id].w - 36;
    $$(`#nt-${id} text`).forEach((t, i) => {
      const [s, c] = L[i] || ["", ""];
      t.setAttribute("class", `note ${c}`);
      fit(t, s, maxW);
    });
  }
  // 한글과 고정폭 영문이 섞여 글자 수로는 폭을 알 수 없다. 실제로 재서 자른다.
  // 웹폰트(Martian Mono)가 늦게 들어오면 대체 글꼴로 잰 폭이 틀려 벽을 넘는다. 원문과 한도를
  // 기억해 두었다가 폰트 로딩 뒤 다시 잰다.
  function fit(t, s, maxW) {
    t.dataset.full = s; t.dataset.max = maxW;
    maxW -= 8;
    t.textContent = s;
    if (!s || t.getComputedTextLength() <= maxW) return;
    let lo = 0, hi = s.length;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; t.textContent = s.slice(0, m) + "…"; if (t.getComputedTextLength() <= maxW) lo = m; else hi = m - 1; }
    t.textContent = s.slice(0, lo) + "…";
  }
  document.fonts?.ready.then(() => $$("#plan text[data-full]").forEach((t) => fit(t, t.dataset.full, +t.dataset.max)));
  function callout(who, text) {
    const g = $(`#co-${who}`);
    if (!text) { g.classList.remove("on"); return; }
    const t = g.querySelector("text"), r = g.querySelector("rect");
    fit(t, text, 200);
    const w = t.getComputedTextLength() + 16;
    r.setAttribute("width", w);
    if (who === "bear") { const x = ROOMS.deb.x + 306 - w; r.setAttribute("x", x); t.setAttribute("x", x + 8); }
    g.classList.add("on");
  }
  function stamp(ts) {
    const day = ts.slice(0, 10); S.day0 = S.day0 || day;
    const hm = ts.slice(11, 16);
    return day === S.day0 ? hm : `${md(day)} ${hm}`;
  }
  function logLine(e) {
    const label = (AGENTS.find((a) => a[0] === e.agent) || [0, e.agent])[1];
    const d = document.createElement("div");
    const mk = e.kind === "done" ? "완료 " : e.kind === "error" ? "오류 " : e.kind === "undo" ? "되돌림 " : "";
    d.innerHTML = `<span class="t">${stamp(e.ts)}</span><span class="a">${label}</span>${mk}${esc(e.msg)}`;
    $("#log").appendChild(d);
    $("#log").scrollTop = 1e9;
  }
  function renderSteps() {
    $("#steps").innerHTML = AGENTS.map(([id, label]) => {
      const a = S.agent[id] || {};
      return `<div class="${a.st || ""}"><i></i><span>${label}</span><em>${esc(a.st === "done" || a.st === "err" ? a.msg || "" : a.st === "work" ? "진행 중" : "")}</em></div>`;
    }).join("");
  }
  function apply(e) {
    const id = ROOM_OF[e.agent];
    if (!id) return;
    const a = (S.agent[e.agent] = S.agent[e.agent] || {});
    switch (e.kind) {
      case "start":
        a.st = "work"; roomState(id, "work"); moveEnv(id);
        note(id, `${e.agent} ${e.msg}`, "hi");
        if (e.agent === "bull") callout("bull", "상승 시나리오 작성 중");
        if (e.agent === "bear") { callout("bull", null); callout("bear", "반박 근거 찾는 중"); }
        break;
      case "wait":
        a.st = "work"; roomState(id, "work", "붙여넣기 대기"); moveEnv(id); note(id, e.msg); break;
      case "step":
        note(id, `${e.msg} ${e.data?.n ?? ""}`);
        if (e.data?.n != null) $("#scr-n").textContent = e.data.n.toLocaleString();
        break;
      case "search":
        note(id, `검색  ${e.msg}`);
        if (e.agent === "bear") callout("bear", e.msg);
        break;
      case "note":
        note(id, e.msg, e.msg.startsWith("통과") ? "ok" : "no");
        if (e.agent === "bear") callout("bear", e.msg.split(" · ")[1] || e.msg);
        break;
      case "done":
        a.st = "done"; a.msg = e.msg;
        note(id, `완료  ${e.msg}`, "ok");
        if (e.agent === "bull") { roomState(id, "work", "Bear 차례"); break; }
        roomState(id, "done");
        if (e.agent === "bear") callout("bear", null);
        if (e.agent === "screener") { moveEnv("scr", e.data?.n ?? 20); $("#scr-n").textContent = e.data?.n ?? "20"; }
        if (e.agent === "analyst") moveEnv("ana", e.data?.finalists?.length || 3);
        if (e.agent === "trader") moveEnv("trd", 1);
        if (e.agent === "settle") moveEnv("set", 1);
        break;
      case "undo":
        a.st = "work"; a.msg = ""; roomState(id, "work", "붙여넣기 대기"); note(id, e.msg); break;
      case "error":
        a.st = "err"; a.msg = e.msg; roomState(id, "err"); note(id, `오류  ${e.msg}`, "no"); break;
    }
    logLine(e); renderSteps();
  }

  // ---------------------------------------------------------------- 재생 / 실시간
  const key = (r) => `${r.week}-${r.market}`;
  const isLive = (r) => !STATIC && r.events_mtime && Date.now() / 1000 - r.events_mtime < 20 * 60 && !r.settled;
  function stop() { clearTimeout(timer); timer = null; if (es) { es.close(); es = null; } }
  function reset() {
    stop();
    S = { notes: {}, agent: {}, envRoom: null, envN: null, day0: null };
    const bars = App.runs.filter((r) => r.market === cur.market && r.settled?.ret_pct && r.week <= cur.week).map((r) => ({ label: `wk${r.week}`, v: +r.settled.ret_pct }));
    build(bars);
    $("#log").innerHTML = ""; renderSteps();
    moveEnv("scr", null);
    Object.keys(ROOMS).filter((k) => !ROOMS[k].locked).forEach((k) => roomState(k, "idle"));
  }
  function play() {
    let i = 0;
    const tick = () => {
      if (i >= EV.length) return;
      const e = EV[i++]; apply(e);
      timer = setTimeout(tick, (PAUSE[e.kind] || 600) / speed);
    };
    tick();
  }
  async function load(run) {
    // 시작할 때 도면(KR)을 불러오는 중에 사용자가 미국을 고르면, 늦게 끝난 KR 응답이 제목·방 내용을
    // 덮어써서 시장이 섞였다. 마지막 요청만 반영한다.
    const my = ++seq;
    stop();
    $("#run").value = key(run);
    let ev, d;
    try {
      [ev, d] = await Promise.all([api(`/api/runs/${run.week}/${run.market}/events`), api(`/api/runs/${run.week}/${run.market}`)]);
    } catch (e) {
      // 실패하면 이전 시장의 도면이 새 제목 아래 남아 섞여 보인다. 실패를 그대로 드러낸다
      if (my !== seq) return;
      cur = null; detail = null;
      $("#plan-title").innerHTML = `${run.week}주차 <span class="mkt mkt-${run.market}">${run.market} ${MK[run.market]}</span>`;
      $("#plan-src").innerHTML = `<span class="tag fail">불러오기 실패</span> ${esc(e.message)}`;
      svg.innerHTML = ""; $("#log").innerHTML = "";
      return;
    }
    if (my !== seq) return;
    cur = run; EV = ev.events; source = ev.source; detail = d;
    const live = source === "recorded" && isLive(run);
    $("#plan-title").innerHTML = `${run.week}주차 <span class="mkt mkt-${run.market}">${run.market} ${MK[run.market]}</span>`;
    $("#v-desk").dataset.market = run.market;
    $("#plan-src").innerHTML = live ? `<span class="tag mark">실시간</span>` : source === "recorded" ? "기록된 실행을 재생" : "기록 도입 전 주차라 산출물로 복원해 재생";
    $("#tb-run").textContent = `wk${run.week} ${MK[run.market]}(${run.market})  ${d.prompt_version || ""} ${d.mode || ""}`;
    $("#tb-src").textContent = live ? "실시간" : source === "recorded" ? "events.jsonl" : "복원";
    $("#run").value = key(run);
    reset();
    $("#dim-t").textContent = d.settled ? `평가구간 ${d.settled.buy_date} 시가 ~ ${d.settled.sell_date} 종가` : `${run.week}주차 평가구간`;
    if (live) {
      EV.forEach(apply);
      es = new EventSource(`/api/runs/${run.week}/${run.market}/stream?since=${EV.length}`);
      es.onmessage = (m) => { const e = JSON.parse(m.data); EV.push(e); apply(e); };
    } else setTimeout(play, 300);
  }

  // ---------------------------------------------------------------- 방 상세
  async function openRoom(id) {
    if (!cur || !detail || detail.week !== cur.week || detail.market !== cur.market) return;
    const w = cur.week, m = cur.market;
    if (id === "scr") {
      const rows = detail.candidates.map((c) => {
        const co = detail.cohort_rows.find((x) => x.ticker === c.ticker);
        const f = (v) => (v === "" || v == null ? "—" : `${(+v * 100).toFixed(1)}%`);
        const mark = co?.role === "pick" ? "추천" : co?.role === "finalist" ? "최종후보" : "";
        const r = num(co?.ret_pct);
        return `<tr><td>${esc(c.name)} <span class="muted mono">${c.ticker}</span></td><td class="l">${mark}</td><td>${f(c.ret_mom)}</td><td>${f(c.ret5)}</td><td>${f(c.atr20_pct)}</td><td class="${sign(r)}">${pct(r)}</td></tr>`;
      }).join("");
      Modal.open(`스크리닝 결과  ${w}주차 ${MK[m]}`, `<table class="tb"><thead><tr><th>종목</th><th class="l">단계</th><th>모멘텀</th><th>5일</th><th>ATR%</th><th>그 주 실제</th></tr></thead><tbody>${rows}</tbody></table>
        <p class="hint">모멘텀은 최근 20영업일을 뺀 120영업일, 5일은 반전 조건(≤0). 실제 수익률은 cohort.py로 정산한 값이다.</p>`, "", true);
      return;
    }
    if (id === "set") {
      const s = detail.settled;
      Modal.open(`정산  ${w}주차 ${MK[m]}`, s ? `<dl class="kv"><dt>종목</dt><dd>${esc(s.name)} ${s.ticker}</dd><dt>매수</dt><dd>${s.buy_date} 시가 ${(+s.buy_open).toLocaleString()}</dd><dt>매도</dt><dd>${s.sell_date} 종가 ${(+s.sell_close).toLocaleString()}</dd>
        <dt>수익률</dt><dd class="${sign(+s.ret_pct)}">${pct(+s.ret_pct)}</dd><dt>${s.benchmark}</dt><dd>${pct(num(s.benchmark_ret_pct))}</dd><dt>초과</dt><dd>${pp(num(s.excess_pct))}</dd><dt>예측범위</dt><dd>${s.predicted_low}% ~ ${s.predicted_high}%</dd><dt>예측 대비</dt><dd style="font-family:var(--sans)">${outcomeTag(outcome(s.ret_pct, s.predicted_low, s.predicted_high))}</dd></dl>`
        : `<p>아직 정산 전이다. 매도일 종가가 확정되면 오른쪽 작업 패널의 5번에서 정산한다.</p>`);
      return;
    }
    const stages = { ana: ["analyst"], deb: ["bull", "bear"], trd: ["trader"] }[id];
    const btns = stages.flatMap((s) => [detail.responses.includes(s) ? `<button class="btn sm" data-k="response" data-s="${s}">${s} 응답</button>` : "", `<button class="btn sm ghost" data-k="prompt" data-s="${s}">${s} 프롬프트</button>`]).join("");
    const st = stages.map((s) => detail.stages[s]).filter((x) => x?.model);
    const meta = st.map((x, i) => `${stages[i]} ${x.model}${x.elapsed_sec ? `, ${Math.round(x.elapsed_sec)}초` : ""}${x.web_search_calls ? `, 검색 ${x.web_search_calls}회` : ""}`).join(" / ");
    Modal.open(`${ROOMS[id].name}  ${w}주차 ${MK[m]}`, `<p class="hint" style="margin:0 0 10px">${esc(meta)}</p><div style="display:flex;gap:6px;flex-wrap:wrap">${btns}</div><div class="doc" id="doc" style="margin-top:14px"></div>`, "", true);
    $$("#m-body [data-k]").forEach((b) => b.addEventListener("click", async () => {
      $$("#m-body [data-k]").forEach((x) => x.classList.toggle("primary", x === b));
      const t = await api(`/api/runs/${w}/${m}/text/${b.dataset.k}/${b.dataset.s}`).catch(() => "(파일 없음)");
      $("#doc").innerHTML = b.dataset.k === "response" && window.marked ? marked.parse(t) : `<pre class="mono" style="white-space:pre-wrap;font-size:11.5px">${esc(t)}</pre>`;
    }));
    $("#m-body [data-k]")?.click();
  }

  // ---------------------------------------------------------------- 선택 목록
  function fill() {
    $("#run").innerHTML = [...App.runs].reverse().map((r) => `<option value="${key(r)}">${r.week}주차 ${r.market === "KR" ? "한국" : "미국"}${r.pick ? `  ${esc(r.pick.name)}` : ""}${isLive(r) ? "  (실시간)" : ""}</option>`).join("");
    if (cur) $("#run").value = key(cur);
  }
  $("#run").addEventListener("change", (e) => { Plan.userPicked = true; load(App.runs.find((r) => key(r) === e.target.value)); });
  $("#replay").addEventListener("click", () => { if (!cur) return; reset(); setTimeout(play, 300); });
  $$("[data-sp]").forEach((b) => b.addEventListener("click", () => {
    speed = +b.dataset.sp; $$("[data-sp]").forEach((x) => x.setAttribute("aria-pressed", x === b));
  }));

  return {
    userPicked: false,
    fill, load, isLive, key,
    current: () => cur,
    pickDefault(weekNo) {
      const live = App.runs.filter(isLive).sort((a, b) => b.events_mtime - a.events_mtime)[0];
      const wk = App.runs.filter((r) => r.week === weekNo);
      return live || wk.find((r) => r.market === "KR") || wk[0] || App.runs[App.runs.length - 1];
    },
  };
})();
