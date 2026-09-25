// 성과 — 정산된 주차의 수익률, 후보군 대비 위치, 예측 범위, 단계별 기여
const Perf = (() => {
  let MKT = "ALL";
  const vis = () => App.runs.filter((r) => MKT === "ALL" || r.market === MKT);
  const lab = (r) => `${r.week}주 ${r.market === "KR" ? "한국" : "미국"}`;
  const nice = (lo, hi) => { const st = hi - lo > 30 ? 10 : 5; return [Math.floor(lo / st) * st, Math.ceil(hi / st) * st, st]; };

  function figs() {
    const s = vis().filter((r) => r.settled);
    const rets = s.map((r) => num(r.settled.ret_pct)).filter((v) => v !== null);
    const exc = s.map((r) => num(r.settled.excess_pct)).filter((v) => v !== null);
    const hit = s.filter((r) => r.settled.hit_range === "Y").length;
    const ranks = s.map((r) => r.cohort?.pick_rank).filter(Boolean);
    const F = [
      ["정산", `${s.length}`, `대기 ${vis().length - s.length}`, ""],
      ["평균 수익률", pct(avg(rets)), rets.length ? `최고 ${pct(Math.max(...rets))}` : "", sign(avg(rets))],
      ["평균 초과", pp(avg(exc)), "벤치마크 대비", sign(avg(exc))],
      ["벤치마크 이김", exc.length ? `${exc.filter((v) => v > 0).length}/${exc.length}` : "—", "초과수익이 0보다 큼", ""],
      ["범위 적중", s.length ? `${hit}/${s.length}` : "—", "실제가 예측 범위 안", ""],
      ["후보 내 순위", ranks.length ? `${avg(ranks).toFixed(1)}` : "—", "20종목 중 평균 등수", ""],
    ];
    $("#figs").innerHTML = F.map(([l, v, sub, c]) => `<div class="fig"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${sub}</div></div>`).join("");
  }

  async function strip() {
    const host = $("#strip"), runs = vis().filter((r) => r.cohort);
    if (!runs.length) { host.innerHTML = `<p class="muted">후보군 정산이 아직 없다. 작업 패널의 정산을 실행하면 생긴다.</p>`; return; }
    const ds = await Promise.all(runs.map((r) => api(`/api/runs/${r.week}/${r.market}`)));
    const vals = ds.flatMap((d) => d.cohort_rows.map((c) => num(c.ret_pct))).filter((v) => v !== null)
      .concat(runs.map((r) => num(r.settled?.benchmark_ret_pct)).filter((v) => v !== null));
    const [lo, hi, st] = nice(Math.min(0, ...vals), Math.max(0, ...vals));
    const W = host.clientWidth || 900, L = 110, R = 12, RH = 58, T = 10, H = T + RH * runs.length + 24;
    const x = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
    host.innerHTML = "";
    const g = svgEl("svg", { viewBox: `0 0 ${W} ${H}` }, host);
    for (let v = lo; v <= hi; v += st) {
      svgEl("line", { x1: x(v), x2: x(v), y1: T, y2: H - 20, class: v === 0 ? "zero" : "gl" }, g);
      svgEl("text", { x: x(v), y: H - 5, "text-anchor": "middle" }, g).textContent = `${v > 0 ? "+" : ""}${v}%`;
    }
    ds.forEach((d, i) => {
      const r = runs[i], cy = T + RH * i + RH / 2;
      svgEl("text", { x: 0, y: cy - 2, class: "lbl" }, g).textContent = lab(r);
      svgEl("text", { x: 0, y: cy + 13 }, g).textContent = `후보 평균 ${pct(r.cohort.candidates_avg, 1)}`;
      svgEl("line", { x1: 0, x2: W - R, y1: T + RH * (i + 1), y2: T + RH * (i + 1), class: "gl" }, g);
      const b = num(r.settled?.benchmark_ret_pct);
      if (b !== null) { const l = svgEl("line", { x1: x(b), x2: x(b), y1: cy - 20, y2: cy + 20, stroke: "#121416", "stroke-dasharray": "2 2" }, g); hover(l, `${r.settled.benchmark} ${pct(b)}`); }
      const ord = { candidate: 0, finalist: 1, pick: 2 };
      d.cohort_rows.filter((c) => num(c.ret_pct) !== null).sort((a, z) => ord[a.role] - ord[z.role]).forEach((c, j) => {
        const v = num(c.ret_pct), jit = c.role === "candidate" ? ((j * 7) % 5 - 2) * 3 : 0;
        const a = c.role === "pick" ? { r: 7, fill: "#121416", stroke: "#ffe14d", "stroke-width": 2.5 }
          : c.role === "finalist" ? { r: 5.5, fill: "#f8f9f7", stroke: "#121416", "stroke-width": 1.8 } : { r: 4.5, fill: "none", stroke: "#8a9095", "stroke-width": 1.3 };
        svgEl("circle", { cx: x(v), cy: cy + jit, ...a }, g);
        const h = svgEl("circle", { cx: x(v), cy: cy + jit, r: 11, fill: "transparent" }, g);
        hover(h, `<b>${esc(c.name || c.ticker)}</b> ${c.ticker}<br>${{ pick: "추천", finalist: "최종후보", candidate: "후보" }[c.role]}  ${pct(v)}`);
        if (c.role === "pick") {
          const anchor = x(v) > W - 90 ? "end" : x(v) < L + 90 ? "start" : "middle";
          svgEl("text", { x: x(v), y: cy - 13, "text-anchor": anchor, class: "lbl" }, g).textContent = `${c.name} ${pct(v, 1)}`;
        }
      });
    });
  }

  function range() {
    const host = $("#range"), runs = vis().filter((r) => r.pick?.expected_return_range);
    if (!runs.length) { host.innerHTML = `<p class="muted">예측 범위가 있는 주차가 없다.</p>`; return; }
    const vals = runs.flatMap((r) => [...r.pick.expected_return_range.map(Number), num(r.settled?.ret_pct)]).filter((v) => v !== null);
    const [lo, hi, st] = nice(Math.min(0, ...vals), Math.max(0, ...vals));
    const W = host.clientWidth || 500, L = 78, R = 14, RH = 36, T = 4, H = T + RH * runs.length + 22;
    const x = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
    host.innerHTML = "";
    const g = svgEl("svg", { viewBox: `0 0 ${W} ${H}` }, host);
    for (let v = lo; v <= hi; v += st) {
      svgEl("line", { x1: x(v), x2: x(v), y1: T, y2: H - 18, class: v === 0 ? "zero" : "gl" }, g);
      svgEl("text", { x: x(v), y: H - 4, "text-anchor": "middle" }, g).textContent = `${v > 0 ? "+" : ""}${v}%`;
    }
    runs.forEach((r, i) => {
      const cy = T + RH * i + RH / 2, [a, b] = r.pick.expected_return_range.map(Number);
      svgEl("text", { x: 0, y: cy + 4, class: "lbl" }, g).textContent = lab(r);
      const band = svgEl("rect", { x: x(a), y: cy - 6, width: Math.max(2, x(b) - x(a)), height: 12, fill: "none", stroke: "#121416", "stroke-width": 1.2 }, g);
      svgEl("rect", { x: x(a), y: cy - 6, width: Math.max(2, x(b) - x(a)), height: 12, fill: "url(#pat)", opacity: .7 }, g);
      hover(band, `예측 ${pct(a, 0)} ~ ${pct(b, 0)}`);
      const act = num(r.settled?.ret_pct);
      if (act === null) { svgEl("text", { x: x(b) + 8, y: cy + 4 }, g).textContent = "정산 대기"; return; }
      svgEl("rect", { x: x(act) - 5, y: cy - 9, width: 10, height: 18, fill: act >= 0 ? "#d1242f" : "#1e56c8" }, g);
      // 오른쪽에 붙이되, 차트 끝을 넘으면 점 왼쪽으로 (좁은 화면에서 라벨이 밖으로 나가지 않게)
      const right = act > b && x(act) + 56 < W;
      const t = svgEl("text", { x: x(act) + (right ? 10 : -10), y: cy + 4, "text-anchor": right ? "start" : "end", class: "lbl" }, g);
      t.textContent = pct(act, 1);
    });
    const defs = svgEl("defs", {}, g);
    defs.innerHTML = `<pattern id="pat" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="5" stroke="#121416" stroke-width=".7"/></pattern>`;
  }

  function contrib() {
    const rows = vis().filter((r) => r.cohort && r.settled);
    if (!rows.length) { $("#contrib").innerHTML = `<p class="muted">정산된 후보군이 없다.</p>`; return; }
    const cell = (v) => `<td class="${sign(v)}">${pp(v)}</td>`;
    const D = rows.map((r) => { const b = num(r.settled.benchmark_ret_pct), c = r.cohort; return [lab(r), b === null ? null : c.candidates_avg - b, c.finalists_avg - c.candidates_avg, c.pick - c.finalists_avg]; });
    const m = (i) => avg(D.map((d) => d[i]).filter((v) => v !== null));
    $("#contrib").innerHTML = `<table class="tb"><thead><tr><th>주차</th><th>Screener<br>후보−벤치</th><th>Analyst<br>3개−후보</th><th>Trader<br>추천−3개</th></tr></thead>
      <tbody>${D.map((d) => `<tr><td>${d[0]}</td>${cell(d[1])}${cell(d[2])}${cell(d[3])}</tr>`).join("")}</tbody>
      <tfoot><tr><td>평균</td>${cell(m(1))}${cell(m(2))}${cell(m(3))}</tr></tfoot></table>
      <p class="hint">주당 표본이 1개라 몇 주 쌓이기 전까지는 방향만 본다.</p>`;
  }

  function weeks() {
    const rs = [...vis()].reverse();
    $("#weeks").innerHTML = `<table class="tb"><thead><tr><th>주차</th><th class="l">종목</th><th>예측</th><th>수익률</th><th>벤치마크</th><th>초과</th><th class="l">범위</th></tr></thead><tbody>${rs.map((r) => {
      const s = r.settled, p = r.pick || {}, v = num(s?.ret_pct);
      return `<tr class="click" data-k="${r.week}-${r.market}"><td>${lab(r)}</td><td class="l">${esc(p.name || "—")} <span class="muted mono">${esc(p.ticker || "")}</span></td>
        <td>${p.expected_return_range ? p.expected_return_range.join(" ~ ") : "—"}</td><td class="${sign(v)}">${pct(v)}</td><td>${s ? pct(num(s.benchmark_ret_pct)) : "—"}</td>
        <td class="${sign(num(s?.excess_pct))}">${s ? pp(num(s.excess_pct)) : "—"}</td><td class="l">${s ? (s.hit_range === "Y" ? "적중" : "이탈") : `<span class="tag off">정산 대기</span>`}</td></tr>`;
    }).join("")}</tbody></table>`;
    $$("#weeks tr.click").forEach((tr) => tr.addEventListener("click", () => detail(App.runs.find((r) => `${r.week}-${r.market}` === tr.dataset.k))));
  }

  async function detail(r) {
    const d = await api(`/api/runs/${r.week}/${r.market}`), p = d.pick || {};
    const rej = (p.rejected || []).map((x) => { const c = d.cohort_rows.find((c) => c.ticker === String(x.ticker)); const v = num(c?.ret_pct);
      return `<li><b>${esc(c?.name || x.ticker)}</b> <span class="mono ${sign(v)}">${c ? pct(v) : ""}</span><br><span style="font-size:12.5px">${esc(x.reason)}</span></li>`; }).join("");
    Modal.open(`${lab(r)}  ${p.name || ""}`, `<dl class="kv"><dt>근거</dt><dd style="font-family:var(--sans)"><ol style="margin:0;padding-left:18px">${(p.thesis || []).map((t) => `<li>${esc(t)}</li>`).join("")}</ol></dd>
      <dt>촉매</dt><dd style="font-family:var(--sans)">${esc(p.catalyst)}</dd><dt>리스크</dt><dd style="font-family:var(--sans)">${esc(p.primary_risk)}</dd></dl>
      <h3 style="margin:16px 0 6px;font-size:14px">탈락한 최종후보와 그 주 실제 수익률</h3><ul style="margin:0;padding-left:18px">${rej}</ul>`,
      `<button class="btn" id="d-files">산출물</button><button class="btn primary" id="d-plan">도면에서 재생</button>`, true);
    $("#d-files").onclick = () => Files.open(r.week, r.market);
    $("#d-plan").onclick = () => { Modal.close(); location.hash = "#desk"; Plan.userPicked = true; Plan.load(r); };
  }

  async function finals() {
    const F = await api("/api/finals").catch(() => []);
    const el = $("#finals");
    if (!F.length) { el.innerHTML = `<p class="muted" style="margin:0">아직 최종 선택 기록이 없다. 데스크 작업 패널의 '최종 선택'에서 고른다.</p>`; return; }
    const agent = (w, m) => { const r = App.runs.find((x) => x.week === w && x.market === m); return r?.settled ? num(r.settled.ret_pct) : null; };
    const done = F.filter((f) => f.settled).map((f) => num(f.settled.ret_pct));
    el.innerHTML = `<div class="kv" style="margin-bottom:10px;grid-template-columns:auto 1fr auto 1fr;gap:4px 14px"><dt>정산된 주</dt><dd>${done.length}/${F.length}</dd><dt>평균 수익률</dt><dd class="${sign(avg(done))}">${pct(avg(done))}</dd></div>
      <table class="tb"><thead><tr><th>주차</th><th class="l">최종 선택</th><th class="l">경로</th><th>수익률</th><th>벤치마크 대비</th><th>에이전트 한국</th><th>에이전트 미국</th><th class="l">사유</th></tr></thead><tbody>
      ${F.map((f) => { const s = f.settled, v = num(s?.ret_pct), k = agent(f.week, "KR"), u = agent(f.week, "US");
        return `<tr><td>${f.week}주</td><td class="l">${esc(f.name)} <span class="muted mono">${esc(f.ticker)}</span></td><td class="l">${f.choice === "custom" ? `<span class="tag">직접 지정</span>` : `에이전트 ${f.market === "KR" ? "한국" : "미국"}`}</td>
          <td class="${sign(v)}">${s ? pct(v) : `<span class="tag off">정산 대기</span>`}</td><td class="${sign(num(s?.excess_pct))}">${s ? pp(num(s.excess_pct)) : "—"}</td>
          <td class="${sign(k)}">${pct(k)}</td><td class="${sign(u)}">${pct(u)}</td><td class="l" style="font-size:12px;max-width:260px">${esc(f.note || "")}</td></tr>`; }).join("")}</tbody></table>
      <p class="hint">에이전트 한국/미국은 그 주 에이전트 추천을 그대로 골랐다면의 결과다. 직접 지정이 에이전트보다 나았는지 여기서 본다.</p>`;
  }

  function render() { figs(); strip(); range(); contrib(); finals(); weeks(); }
  $$("#perf-mkt button").forEach((b) => b.addEventListener("click", () => { MKT = b.dataset.m; $$("#perf-mkt button").forEach((x) => x.setAttribute("aria-pressed", x === b)); render(); }));
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if ($("#v-perf").classList.contains("on")) { strip(); range(); } }, 150); });
  return { render };
})();
