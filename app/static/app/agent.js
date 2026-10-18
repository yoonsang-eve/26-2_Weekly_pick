// 에이전트 — 스케줄, 작업 흐름 도면, 결정, 턴 기록, 비판, 회고·교훈, 알림. 파일만 읽는다 (app/agent_view.py)
const Agent = (() => {
  const MK = { KR: "한국", US: "미국" };
  const REGIME = { risk_on: "위험 선호", neutral: "중립", risk_off: "위험 회피", crash: "급락" };
  const STANCE = { aggressive: "공격", balanced: "균형", defensive: "방어", hedge: "헤지" };
  const CONF = { low: "낮음", medium: "보통", high: "높음" };
  const TOOL = { market_overview: "시장 개관", track_record: "지난 기록", sector_rotation: "섹터 로테이션",
                 screen_stocks: "주식 스크리닝", screen_etfs: "ETF 스크리닝", instrument_detail: "종목 상세",
                 write_note: "메모", request_critique: "비판 요청", submit_recommendation: "제출" };
  const PH_CLS = { "국면 조사": "p-regime", "범위 결정": "p-scope", "후보 탐색": "p-search", "검색": "p-web",
                   "비판": "p-critic", "제출": "p-submit", "제출 거부": "p-reject", "메모": "p-note" };
  let S = { ov: null, week: null, run: "agent", det: null };
  const mdHtml = (t) => (window.marked ? marked.parse(t || "") : `<pre>${esc(t)}</pre>`);
  const t16 = (s) => (s || "").slice(5, 16).replace("T", " ").replace("-", ".");
  const vtag = (v) => (v ? `<span class="tag ${v === "fatal" ? "fail" : "done"}">${v}</span>` : "");

  // ---------------------------------------------------------------- 상태 줄
  function figs(ov) {
    const s = ov.schedule, c = ov.config;
    const nr = s.next_recommend;
    const rv = s.reviews_pending[0];
    const cur = ov.weeks[0];
    const f = (l, v, sub = "") => `<div class="fig"><div class="l">${l}</div><div class="v ag-v">${v}</div><div class="s">${sub}</div></div>`;
    return `<div class="figs">
      ${f("다음 추천", nr ? `${md(nr.slice(0, 10))} ${nr.slice(11, 16)}` : "—", `${s.target_week}주차 · ${s.decided ? "완료" : "첫 거래일 전날 · 이후 첫 실행"}`)}
      ${f("다음 결과 보고", rv ? `${md(rv.ready_at.slice(0, 10))} ${rv.ready_at.slice(11, 16)}~` : "—", rv ? `${rv.week}주차 ${esc(rv.name)} · 매도일 종가 확정 후` : "대기 중인 주차 없음")}
      ${f("스케줄러", s.last_tick ? s.last_tick.slice(11, 16) : "—", s.last_tick ? `마지막 실행 ${md(s.last_tick.slice(0, 10))} · 매시 5분` : "실행 기록 없음 · 매시 5분")}
      ${f("휴장·일정 갱신", s.calendar_fetched ? md(s.calendar_fetched.slice(0, 10)) : "—", s.calendar_due ? "다음 실행에서 갱신" : "주 1회 (토·일)")}
      ${f("이번 주 비용", cur?.week_unit != null ? `${cur.week_unit.toFixed(2)}u` : "—", `추천 1회 상한 ${c.cost_limit_unit ?? "-"}u · 장부 추정`)}
      ${f("상태", c.enabled ? "자동" : "수동", `${esc(c.model || "")} · effort ${esc(c.effort || "기본")}`)}
    </div>`;
  }

  // ---------------------------------------------------------------- 작업 흐름 도면
  function counts(det) {
    const n = { regime: 0, scope: 0, search: 0, web: 0, critic: 0, fatal: 0, minor: 0, submit: 0, reject: 0, note: 0, back: 0 };
    let sawFatal = false;
    for (const t of det.trace) for (const it of t.items) {
      const p = it.phase;
      if (p === "국면 조사") n.regime++;
      else if (p === "범위 결정") n.scope++;
      else if (p === "후보 탐색") { n.search++; if (sawFatal) { n.back++; sawFatal = false; } }
      else if (p === "검색") n.web++;
      else if (p === "메모") n.note++;
      else if (p === "비판") { n.critic++; if (it.verdict === "fatal") { n.fatal++; sawFatal = true; } else if (it.verdict === "minor") n.minor++; }
      else if (p === "제출") n.submit++;
      else if (p === "제출 거부") n.reject++;
    }
    return n;
  }

  function diagram(det) {
    const n = counts(det);
    const W = 1000, H = 300, y = 70, bw = 118, bh = 74;
    const nodes = [
      { id: "prep", x: 14, t: "데이터 준비", s: "코드 · LLM 없음", c: "주식·ETF 지표", on: true, code: true },
      { id: "regime", x: 156, t: "국면 조사", s: "시장 개관·지난 기록", c: `${n.regime}회`, on: n.regime },
      { id: "scope", x: 298, t: "범위 결정", s: "섹터 로테이션", c: `${n.scope}회`, on: n.scope },
      { id: "search", x: 440, t: "후보 탐색", s: "스크리닝·상세 조회", c: `${n.search}회`, on: n.search },
      { id: "critic", x: 582, t: "비판", s: "별도 LLM · 검색", c: `${n.critic}회 (fatal ${n.fatal})`, on: n.critic, ext: true },
      { id: "judge", x: 724, t: "판단", s: "버릴까, 낼까", c: n.back ? `되돌아감 ${n.back}회` : "—", on: n.critic },
      { id: "submit", x: 866, t: "제출 검증", s: "코드가 검사", c: `${n.submit ? "통과" : "—"}${n.reject ? ` · 거부 ${n.reject}` : ""}`, on: n.submit || n.reject, code: true },
    ];
    let s = `<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#121416"/></marker>
      <marker id="ahr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#d1242f"/></marker></defs>`;
    // 루프 테두리
    s += `<rect x="146" y="${y - 34}" width="702" height="${bh + 116}" class="ag-loop"/><text x="156" y="${y - 18}" class="ag-cap">에이전트 루프 · 최대 ${S.ov?.config?.max_turns ?? 30}턴</text>`;
    nodes.forEach((d, i) => {
      s += `<g class="ag-node ${d.on ? "on" : ""} ${d.code ? "code" : ""} ${d.ext ? "ext" : ""}" transform="translate(${d.x},${y})">
        <rect width="${bw}" height="${bh}"/><text x="10" y="22" class="t">${d.t}</text><text x="10" y="40" class="s">${d.s}</text><text x="10" y="62" class="c">${d.c}</text></g>`;
      if (i < nodes.length - 1) s += `<line x1="${d.x + bw}" y1="${y + bh / 2}" x2="${nodes[i + 1].x - 3}" y2="${y + bh / 2}" class="ag-ar" marker-end="url(#ah)"/>`;
    });
    // 되돌아가는 화살표
    const back = (from, to, dy, label, hot) => {
      const a = nodes.find((x) => x.id === from), b = nodes.find((x) => x.id === to);
      const x1 = a.x + bw / 2, x2 = b.x + bw / 2, yy = y + bh + dy;
      s += `<path d="M${x1} ${y + bh} V${yy} H${x2} V${y + bh + 3}" class="ag-back ${hot ? "hot" : ""}" marker-end="url(#${hot ? "ahr" : "ah"})"/>
        <text x="${(x1 + x2) / 2}" y="${yy - 5}" text-anchor="middle" class="ag-bl">${label}</text>`;
    };
    back("judge", "search", 22, "fatal → 다른 후보 탐색", n.back > 0);
    back("judge", "regime", 46, "국면 판단이 흔들리면 처음부터", false);
    back("submit", "search", 70, "검증 실패 → 사유를 돌려받아 계속", n.reject > 0);
    // 웹 검색 · 회고
    s += `<g class="ag-pill"><rect x="440" y="${y - 30}" width="118" height="20" rx="10"/><text x="499" y="${y - 16}" text-anchor="middle">웹 검색 ${n.web}회</text></g>`;
    s += `<g class="ag-node mem" transform="translate(14,${y + bh + 52})"><rect width="${bw}" height="56"/><text x="10" y="20" class="t">회고 · 교훈</text><text x="10" y="38" class="s">주차 종료 후 (별도 LLM)</text></g>
      <path d="M${14 + bw} ${y + bh + 80} H${156 + bw / 2} V${y + bh + 3}" class="ag-back" marker-end="url(#ah)"/>
      <text x="${14 + bw + 8}" y="${y + bh + 95}" class="ag-bl" text-anchor="start">지난 기록·교훈 → 다음 주 국면 조사</text>`;
    return `<svg viewBox="0 0 ${W} ${H}" class="ag-dia" role="img" aria-label="에이전트 작업 흐름">${s}</svg>`;
  }

  function strip(det) {
    const cells = det.trace.map((t) => {
      const phs = t.items.filter((i) => i.phase && i.phase !== "메모");
      const main = phs.find((i) => i.phase === "제출" || i.phase === "제출 거부" || i.phase === "비판") || phs.find((i) => i.phase !== "검색") || phs[0];
      const p = main?.phase || "기타";
      const tips = phs.map((i) => (i.type === "search" ? `검색: ${i.query}` : `${TOOL[i.tool] || i.tool}${i.args?.ticker ? ` ${i.args.ticker}` : ""}${i.verdict ? ` (${i.verdict})` : ""}`)).join("<br>");
      return `<button class="ag-cell ${PH_CLS[p] || ""}" data-turn="${t.turn}" data-tip="${esc(tips)}"><b>${t.turn}</b><span>${p}${main?.verdict ? ` ${main.verdict}` : ""}</span></button>`;
    });
    return `<div class="ag-strip">${cells.join("")}</div>
      <div class="legend" style="margin-top:8px">${Object.entries(PH_CLS).map(([k, v]) => `<span><i class="ag-sw ${v}"></i>${k}</span>`).join("")}<span class="muted">칸을 누르면 그 턴으로 이동</span></div>`;
  }

  // ---------------------------------------------------------------- 결정
  function decision(det) {
    const d = det.decision, r = det.result || {};
    const rng = d.expected_return_range || [];
    const ret = num(r.ret_pct);
    const alt = (d.alternatives || []).map((x) => `<tr><td class="l mono">${esc(x.market || "")} ${esc(x.ticker)}</td><td class="l">${esc(x.name || "")}</td><td class="l">${esc(x.reason)}</td></tr>`).join("");
    const bc = typeof d.bull_case === "object" && d.bull_case ? d.bull_case : null;
    return `<div class="ag-dec">
      <div class="ag-pick">
        <div class="muted" style="font-size:12px">${d.week}주차 · ${MK[d.market] || d.market} · ${d.instrument_type === "etf" ? "ETF" : "주식"}</div>
        <div class="ag-name">${esc(d.name)} <span class="mono muted">${esc(d.ticker)}</span></div>
        <div class="ag-tags"><span class="tag">${REGIME[d.regime_view] || d.regime_view}</span><span class="tag">자세 ${STANCE[d.stance] || d.stance}</span><span class="tag ${d.confidence === "low" ? "off" : ""}">확신 ${CONF[d.confidence] || d.confidence}</span></div>
        <div class="kv" style="margin-top:12px">
          <div class="muted">예상 범위</div><div class="mono">${rng[0]}% ~ ${rng[1]}%</div>
          <div class="muted">실제</div><div>${ret !== null ? `<b class="mono ${sign(ret)}">${pct(ret)}</b> <span class="muted">${esc(r.benchmark || "")} ${r.benchmark_ret_pct || ""}%</span> ${outcomeTag(outcome(ret, rng[0], rng[1]))}` : "<span class='muted'>정산 전</span>"}</div>
          <div class="muted">매매</div><div>${esc(((d.plan || {})[d.market] || {}).buy_date || "")} 시가 → ${esc(((d.plan || {})[d.market] || {}).sell_date || "")} 종가</div>
          <div class="muted">Catalyst</div><div>${esc(d.catalyst)}</div>
          <div class="muted">최대 리스크</div><div>${esc(d.primary_risk)}</div>
        </div>
      </div>
      <div class="ag-why">
        <h3>국면 판단</h3><p>${esc(d.regime_rationale)}</p>
        <h3>선정 근거</h3><ol class="brief">${(d.thesis || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
        ${bc ? `<h3>상승 시나리오</h3><p>${esc(bc.scenario)}</p><p class="muted">트리거: ${esc(bc.trigger)}</p>` : ""}
        <h3>비판에 대한 답</h3><p>${esc(d.response_to_critique)}</p>
        <h3>탐색 과정</h3><p>${esc(d.process_summary)}</p>
      </div>
    </div>
    ${alt ? `<table class="tb" style="margin-top:14px"><thead><tr><th class="l">탈락 후보</th><th class="l">이름</th><th class="l">탈락 사유</th></tr></thead><tbody>${alt}</tbody></table>` : ""}`;
  }

  // ---------------------------------------------------------------- 턴 기록
  function turns(det) {
    return `<ol class="ag-turns">${det.trace.map((t) => `<li id="ag-t${t.turn}"><div class="ag-tn">턴 ${t.turn}</div><div>${t.items.map((it) => {
      if (it.type === "text") return `<p class="ag-say">${esc(it.text)}</p>`;
      if (it.type === "system") return `<p class="ag-sys">${esc(it.text)}</p>`;
      if (it.type === "search") return `<div class="ag-it"><span class="ag-chip p-web">검색</span> ${esc(it.query)}</div>`;
      const a = { ...it.args };
      const head = it.tool === "request_critique" ? `${esc(a.market)} ${esc(a.ticker)} ${esc(a.name || "")} ${vtag(it.verdict)}`
        : it.tool === "submit_recommendation" ? `${esc(a.market)} ${esc(a.ticker)} ${esc(a.name || "")} ${it.phase === "제출 거부" ? '<span class="tag fail">거부</span>' : '<span class="tag done">접수</span>'}`
        : it.tool === "write_note" ? esc(a.title || "")
        : esc(Object.entries(a).filter(([k]) => k !== "raw").map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ").slice(0, 220));
      return `<details class="ag-it"><summary><span class="ag-chip ${PH_CLS[it.phase] || ""}">${TOOL[it.tool] || it.tool}</span> ${head}</summary>
        ${it.tool === "write_note" ? `<div class="doc">${mdHtml(a.text || "")}</div>` : ""}
        ${it.result ? `<div class="doc ag-res">${mdHtml(it.result)}</div>` : ""}</details>`;
    }).join("")}</div></li>`).join("")}</ol>`;
  }

  function critiques(det) {
    if (!det.critiques.length) return `<p class="muted">비판 기록 없음</p>`;
    return det.critiques.map((c) => `<details class="ag-crit"><summary><b>비판 ${c.n}</b> · <span class="mono">${esc(c.ticker)}</span> ${vtag(c.verdict)}</summary><div class="doc">${mdHtml(c.text)}</div></details>`).join("");
  }

  function lessons(ov) {
    if (!ov.lessons.length) return `<p class="muted">아직 교훈이 없다. 첫 회고(주차 종료 후)부터 쌓인다. 지지 3주 미만은 '가설'로만 다음 주에 전달된다.</p>`;
    return `<table class="tb"><thead><tr><th class="l">ID</th><th class="l">상태</th><th class="l">종류</th><th class="l">교훈</th><th>지지</th><th>반증</th></tr></thead><tbody>
      ${ov.lessons.map((l) => `<tr><td class="l mono">${l.id}</td><td class="l"><span class="tag ${l.label === "확립" ? "done" : l.label === "반박됨" ? "fail" : "off"}">${l.label}</span></td><td class="l">${l.kind === "market" ? "시장" : "과정"}</td><td class="l">${esc(l.text)}</td><td class="mono">${(l.support || []).join(",")}</td><td class="mono">${(l.against || []).join(",")}</td></tr>`).join("")}</tbody></table>`;
  }

  function history(ov) {
    if (!ov.weeks.length) return `<p class="muted">에이전트가 결정한 주차가 없다.</p>`;
    return `<table class="tb"><thead><tr><th class="l">주차</th><th class="l">종목</th><th class="l">국면 / 자세</th><th>예상</th><th>실제</th><th>턴·검색</th><th>비용</th><th class="l">회고</th></tr></thead><tbody>
      ${ov.weeks.map((w) => { const r = num(w.result?.ret_pct); return `<tr class="ag-row" data-week="${w.week}"><td class="l mono">wk${w.week}</td><td class="l">${MK[w.market] || ""} ${esc(w.name)} <span class="mono muted">${esc(w.ticker)}</span></td>
        <td class="l">${REGIME[w.regime_view] || ""} / ${STANCE[w.stance] || ""}</td><td class="mono">${(w.range || []).join(" ~ ")}%</td>
        <td class="mono ${sign(r)}">${r !== null ? pct(r) : "—"}</td><td class="mono">${w.turns ?? "-"}·${w.searches ?? "-"}</td><td class="mono">${w.week_unit != null ? w.week_unit.toFixed(2) + "u" : "-"}</td>
        <td class="l">${w.review ? esc(w.review.summary || "") : "<span class='muted'>대기</span>"}</td></tr>`; }).join("")}</tbody></table>`;
  }

  function sidebar(ov) {
    const ev = ov.events.length ? `<ol class="ag-ev">${ov.events.map((e) => `<li><span class="mono">${md(e.date)}</span> <span class="tag off">${esc(e.market)}</span> ${esc(e.title.split(". ")[0])}${e.time_kst ? ` <span class="muted">${esc(e.time_kst)} KST</span>` : ""}</li>`).join("")}</ol>` : `<p class="muted">없음</p>`;
    const nt = ov.notify.length ? `<ol class="ag-ev">${ov.notify.map((n) => `<li><details><summary><span class="mono muted">${t16(n.at)}</span> ${esc(n.subject.replace("[weekly_pick] ", ""))}</summary><pre class="ag-pre">${esc(n.body)}</pre></details></li>`).join("")}</ol>` : `<p class="muted">없음</p>`;
    return `<section class="blk"><header><div><h2>다가오는 대형 일정</h2><p>3주 · 매주 자동 검색</p></div></header><div class="bd">${ev}</div></section>
      <section class="blk"><header><div><h2>알림 기록</h2><p>추천·결과·오류. 메일 대신 여기와 Windows 알림으로</p></div></header><div class="bd">${nt}</div></section>`;
  }

  // ---------------------------------------------------------------- 조립
  async function loadWeek() {
    const box = $("#ag-week");
    if (!S.week) { box.innerHTML = `<p class="muted" style="padding:20px">아직 에이전트 실행 기록이 없다.</p>`; return; }
    try { S.det = await api(`/api/agent/week/${S.week}?run=${S.run}`); }
    catch (e) { box.innerHTML = `<p class="warn">${esc(e.message)}</p>`; return; }
    const det = S.det;
    const runSel = det.runs.length > 1 ? `<select class="sel" id="ag-run">${det.runs.map((r) => `<option value="${r}" ${r === S.run ? "selected" : ""}>${r === "agent" ? "최종 실행" : r.replace("agent_run", "이전 실행 ")}</option>`).join("")}</select>` : "";
    box.innerHTML = `
      <section class="blk"><header><div><h2>작업 흐름</h2><p>${S.week}주차 ${S.run === "agent" ? "최종 실행" : "이전 실행"} — 숫자는 이번 실행에서 그 단계를 몇 번 거쳤는지</p></div>${runSel}</header>
        <div class="bd">${diagram(det)}${strip(det)}</div></section>
      <section class="blk"><header><div><h2>결정</h2><p>submit_recommendation으로 제출하고 코드 검증을 통과한 내용</p></div></header><div class="bd">${decision(det)}</div></section>
      <div class="ag-two">
        <section class="blk"><header><div><h2>턴 기록</h2><p>에이전트가 실제로 한 일. 항목을 누르면 도구 결과</p></div></header><div class="bd">${turns(det)}</div></section>
        <div>
          <section class="blk" style="margin-top:16px"><header><div><h2>비판자 의견</h2><p>별도 LLM · 매수 논거만 받고 검색으로 검증</p></div></header><div class="bd">${critiques(det)}</div></section>
          ${det.notes.trim() ? `<section class="blk"><header><h2>작업 메모</h2></header><div class="bd doc">${mdHtml(det.notes.replace(/^# .*\n/, ""))}</div></section>` : ""}
          ${det.review_md ? `<section class="blk"><header><div><h2>회고</h2><p>주차 종료 후 실제 결과와 비교</p></div></header><div class="bd doc">${mdHtml(det.review_md.replace(/```json[\s\S]*```/, ""))}</div></section>` : ""}
        </div>
      </div>`;
    $("#ag-run")?.addEventListener("change", (e) => { S.run = e.target.value; loadWeek(); });
    $$(".ag-cell").forEach((b) => {
      hover(b, b.dataset.tip || "");
      b.onclick = () => $(`#ag-t${b.dataset.turn}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  async function run(kind, label, cost, extra = {}) {
    Modal.open(label, `<p>공용 LLM API를 쓴다. 예상 비용 ${cost}.</p><p class="hint">실행 중 출력은 이전 데스크의 작업 기록 창과 같은 작업 목록(/api/jobs)에 남는다. 다른 작업이 돌고 있으면 시작되지 않는다.</p>`,
      `<button class="btn" data-close>취소</button><button class="btn primary" id="ag-go">실행</button>`);
    $("#ag-go").onclick = async () => {
      Modal.close();
      try { await api("/api/jobs", { method: "POST", body: { kind, confirm_api: true, ...extra } }); toast(`${label} 시작 — 끝나면 알림 기록에 남는다`); }
      catch (e) { toast(e.message); }
    };
  }

  async function render() {
    const host = $("#agent");
    if (!S.ov) host.innerHTML = `<p class="muted" style="padding:40px 0">불러오는 중</p>`;
    try { S.ov = await api("/api/agent"); } catch (e) { host.innerHTML = `<p class="warn">${esc(e.message)}</p>`; return; }
    const ov = S.ov;
    if (S.week === null) S.week = ov.weeks[0]?.week ?? 0;
    const sel = ov.weeks.length ? `<select class="sel" id="ag-wk">${ov.weeks.map((w) => `<option value="${w.week}" ${w.week === S.week ? "selected" : ""}>${w.week}주차 · ${esc(w.name)}</option>`).join("")}</select>` : "";
    host.innerHTML = `<div class="page-hd"><div><h1>에이전트</h1><p class="muted" style="margin:6px 0 0;font-size:13px">국면 조사 → 탐색 → 비판 → (되돌아가기) → 제출을 스스로 진행한다. 매주 자동 실행 · <a href="#desk">이전 버전(고정 체인) 데스크</a></p></div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${sel}
          <span class="ag-acts"><button class="btn sm" id="ag-rec">${ov.schedule.decided ? "다시 추천" : "지금 추천"}</button><button class="btn sm" id="ag-rev">회고</button><button class="btn sm ghost" id="ag-cal">일정 갱신</button></span>
          <button class="btn sm ghost" id="ag-reload">새로고침</button></div></div>
      ${figs(ov)}
      <div class="ag-main"><div id="ag-week"></div><aside>${sidebar(ov)}</aside></div>
      <section class="blk"><header><div><h2>주차별 결정</h2><p>행을 누르면 그 주차의 흐름을 본다</p></div></header><div class="bd">${history(ov)}</div></section>
      <section class="blk"><header><div><h2>누적 교훈</h2><p>회고가 남긴 교훈. 지지가 3주 미만이면 '가설'이고 다음 주 에이전트에게 그렇게 표시해 전달한다</p></div></header><div class="bd">${lessons(ov)}</div></section>`;
    $("#ag-wk")?.addEventListener("change", (e) => { S.week = +e.target.value; S.run = "agent"; loadWeek(); });
    $$(".ag-row").forEach((r) => (r.onclick = () => { S.week = +r.dataset.week; S.run = "agent"; render(); scrollTo({ top: 0, behavior: "smooth" }); }));
    $("#ag-reload").onclick = render;
    const recWeek = ov.schedule.decided ? ov.schedule.target_week - 1 : ov.schedule.target_week;
    $("#ag-rec").onclick = () => run("agent_recommend", ov.schedule.decided ? `${recWeek}주차 다시 추천 (기존 결정을 덮어씀)` : `${recWeek}주차 추천 실행`,
      "약 1~2 unit (상한 " + ov.config.cost_limit_unit + "u)", { full: ov.schedule.decided });
    $("#ag-rev").onclick = () => run("agent_review", `${S.week}주차 회고`, "약 0.1~0.2 unit", { week: S.week });
    $("#ag-cal").onclick = () => run("agent_calendar", "휴장일·대형 일정 갱신", "약 0.1 unit");
    await loadWeek();
  }

  return { render };
})();
