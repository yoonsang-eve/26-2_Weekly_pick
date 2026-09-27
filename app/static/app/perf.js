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
    const oc = s.map((r) => outcome(r.settled.ret_pct, r.settled.predicted_low, r.settled.predicted_high)).filter(Boolean);
    const wins = oc.filter((o) => o.key === "win" || o.key === "big-win").length, big = oc.filter((o) => o.key === "big-win").length;
    const ranks = s.map((r) => r.cohort?.pick_rank).filter(Boolean);
    const F = [
      ["정산", `${s.length}`, `대기 ${vis().length - s.length}`, ""],
      ["평균 수익률", pct(avg(rets)), rets.length ? `최고 ${pct(Math.max(...rets))}` : "", sign(avg(rets))],
      ["평균 초과", pp(avg(exc)), "벤치마크 대비", sign(avg(exc))],
      ["벤치마크 이김", exc.length ? `${exc.filter((v) => v > 0).length}/${exc.length}` : "—", "초과수익이 0보다 큼", ""],
      ["성공 이상", oc.length ? `${wins}/${oc.length}` : "—", `크게 성공(예측 상단 초과) ${big}`, ""],
      ["후보 내 순위", ranks.length ? `${avg(ranks).toFixed(1)}` : "—", "20종목 중 평균 등수", ""],
    ];
    $("#figs").innerHTML = F.map(([l, v, sub, c]) => `<div class="fig"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${sub}</div></div>`).join("");
  }

  // 후보 점 그래프. items: [{label, sub, rows: [{role, ret_pct, name, ticker}], bench, benchName}]
  function dots(host, items) {
    const vals = items.flatMap((it) => it.rows.map((c) => num(c.ret_pct))).filter((v) => v !== null)
      .concat(items.map((it) => it.bench).filter((v) => v !== null));
    const [lo, hi, st] = nice(Math.min(0, ...vals), Math.max(0, ...vals));
    const W = host.clientWidth || 900, L = 110, R = 12, RH = 58, T = 10, H = T + RH * items.length + 24;
    const x = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
    host.innerHTML = "";
    const g = svgEl("svg", { viewBox: `0 0 ${W} ${H}` }, host);
    for (let v = lo; v <= hi; v += st) {
      svgEl("line", { x1: x(v), x2: x(v), y1: T, y2: H - 20, class: v === 0 ? "zero" : "gl" }, g);
      svgEl("text", { x: x(v), y: H - 5, "text-anchor": "middle" }, g).textContent = `${v > 0 ? "+" : ""}${v}%`;
    }
    const ROLE = { pick: "추천", finalist: "최종후보", candidate: "후보", extra: "최종 선택(후보 밖)" };
    const ord = { candidate: 0, extra: 1, finalist: 1, pick: 2 };
    items.forEach((it, i) => {
      const cy = T + RH * i + RH / 2;
      svgEl("text", { x: 0, y: cy - 2, class: "lbl" }, g).textContent = it.label;
      svgEl("text", { x: 0, y: cy + 13 }, g).textContent = it.sub;
      svgEl("line", { x1: 0, x2: W - R, y1: T + RH * (i + 1), y2: T + RH * (i + 1), class: "gl" }, g);
      if (it.bench !== null && it.bench !== undefined) {
        const l = svgEl("line", { x1: x(it.bench), x2: x(it.bench), y1: cy - 20, y2: cy + 20, stroke: "#121416", "stroke-dasharray": "2 2" }, g);
        hover(l, `${it.benchName} ${pct(it.bench)}`);
      }
      it.rows.filter((c) => num(c.ret_pct) !== null).sort((a, z) => ord[a.role] - ord[z.role]).forEach((c, j) => {
        const v = num(c.ret_pct), jit = c.role === "candidate" ? ((j * 7) % 5 - 2) * 3 : 0;
        const a = c.role === "pick" ? { r: 7, fill: "#121416", stroke: "#ffe14d", "stroke-width": 2.5 }
          : c.role === "finalist" ? { r: 5.5, fill: "#f8f9f7", stroke: "#121416", "stroke-width": 1.8 }
          : c.role === "extra" ? { r: 6, fill: "#ffe14d", stroke: "#121416", "stroke-width": 1.8 }
          : { r: 4.5, fill: "none", stroke: "#8a9095", "stroke-width": 1.3 };
        svgEl("circle", { cx: x(v), cy: cy + jit, ...a }, g);
        const h = svgEl("circle", { cx: x(v), cy: cy + jit, r: 11, fill: "transparent" }, g);
        hover(h, `<b>${esc(c.name || c.ticker)}</b> ${c.ticker}<br>${ROLE[c.role]}  ${pct(v)}${c.last_date ? `<br>${c.last_date} 기준` : ""}`);
        if (c.role === "pick" || c.role === "extra") {
          const anchor = x(v) > W - 90 ? "end" : x(v) < L + 90 ? "start" : "middle";
          svgEl("text", { x: x(v), y: c.role === "pick" ? cy - 13 : cy + 21, "text-anchor": anchor, class: "lbl" }, g).textContent = `${c.name || c.ticker} ${pct(v, 1)}`;
        }
      });
    });
  }

  async function strip() {
    const host = $("#strip"), runs = vis().filter((r) => r.cohort);
    if (!runs.length) { host.innerHTML = `<p class="muted">후보군 정산이 아직 없다. 작업 패널의 정산을 실행하면 생긴다.</p>`; return; }
    const ds = await Promise.all(runs.map((r) => api(`/api/runs/${r.week}/${r.market}`)));
    dots(host, ds.map((d, i) => ({ label: lab(runs[i]), sub: `후보 평균 ${pct(runs[i].cohort.candidates_avg, 1)}`, rows: d.cohort_rows,
      bench: num(runs[i].settled?.benchmark_ret_pct), benchName: runs[i].settled?.benchmark })));
  }

  // ---- 실시간(잠정) — /api/live. 확정 정산(log.csv·cohort.csv)과 섞지 않는다
  let LIVE = null, liveBusy = false;
  async function live(fresh = false) {
    const host = $("#live"), btn = $("#live-refresh");
    if (STATIC) btn.hidden = true;                 // 구운 사이트는 구운 시점 값만 있다
    if (liveBusy) return;
    if (!LIVE || fresh) {
      liveBusy = true; btn.disabled = true; btn.textContent = "받는 중…";
      if (!LIVE) host.innerHTML = `<p class="muted" style="margin:0">시세를 받는 중이다 (40종목 안팎, 몇 초 걸린다)…</p>`;
      try { LIVE = await api(`/api/live${fresh ? "?fresh=1" : ""}`); }
      catch (e) {
        host.innerHTML = `<p class="muted" style="margin:0">${STATIC ? "이 사이트에는 실시간 값이 구워져 있지 않다." : `시세 조회 실패: ${esc(e.message)}`}</p>`;
        return;
      } finally { liveBusy = false; btn.disabled = false; btn.textContent = "새로고침"; }
    }
    const L = LIVE, ms = L.markets.filter((m) => MKT === "ALL" || m.market === MKT);
    const when = (L.markets[0]?.fetched_at || "").replace("T", " ").slice(5, 16);
    $("#live-sub").textContent = `${L.week}주 · 월 시가 → 지금까지의 마지막 가격 · ${STATIC ? "사이트를 구운" : "받은"} 시각 ${when} KST · 확정 정산 전 참고값`;
    if (!ms.length) { host.innerHTML = `<p class="muted" style="margin:0">이 시장의 후보가 없다.</p>`; return; }
    const ST = { before: `<span class="tag off">매수일 전</span>`, live: `<span class="tag mark">장 진행 중</span>`, closed: `<span class="tag done">매도일 종가 반영</span>` };
    const avgOf = (rs) => avg(rs.map((r) => num(r.ret_pct)).filter((x) => x !== null));
    const tr = ms.map((m) => {
      const R = m.rows.filter((r) => r.role !== "extra");
      const pk = R.find((r) => r.role === "pick"), pv = num(pk?.ret_pct), b = num(m.benchmark?.ret_pct);
      const ok = R.filter((r) => num(r.ret_pct) !== null);
      return { m, mk: m.market === "KR" ? "한국" : "미국", pk, pv, b, cand: avgOf(R), finl: avgOf(R.filter((r) => r.role !== "candidate")),
        rank: pv === null ? null : 1 + ok.filter((r) => num(r.ret_pct) > pv).length, n: ok.length, fail: R.length - ok.length };
    });
    const fin = L.final;
    const finRow = fin && L.markets.find((m) => m.market === fin.market)?.rows.find((r) => r.ticker === String(fin.ticker));
    const fv = num(finRow?.ret_pct);
    const ex = (t) => (t.pv === null || t.b === null ? null : t.pv - t.b);
    host.innerHTML = `
      ${fin ? `<div class="kv" style="margin-bottom:12px;grid-template-columns:auto 1fr"><dt>최종 선택</dt><dd style="font-family:var(--sans)"><b>${esc(fin.name)}</b> <span class="muted mono">${esc(fin.ticker)}</span>
        <span class="mono ${sign(fv)}" style="font-weight:700;margin-left:6px">${finRow ? pct(fv) : "—"}</span>${finRow?.last_date ? ` <span class="muted">(${md(finRow.last_date)} 기준)</span>` : ""}</dd></div>` : ""}
      <table class="tb"><thead><tr><th>시장</th><th>상태</th><th class="l">추천</th><th>추천</th><th>최종후보 3</th><th>후보 평균</th><th>벤치마크</th><th>추천−벤치</th><th>후보 내 순위</th><th>기준일</th></tr></thead><tbody>
      ${tr.map((t) => `<tr><td>${t.mk}</td><td>${ST[t.m.status] || ""}</td><td class="l">${esc(t.pk?.name || "—")} <span class="muted mono">${esc(t.pk?.ticker || "")}</span></td>
        <td class="${sign(t.pv)}"><b>${pct(t.pv)}</b></td><td class="${sign(t.finl)}">${pct(t.finl)}</td><td class="${sign(t.cand)}">${pct(t.cand)}</td>
        <td class="${sign(t.b)}">${pct(t.b)}</td><td class="${sign(ex(t))}">${pp(ex(t))}</td>
        <td>${t.rank ? `${t.rank}/${t.n}` : "—"}${t.fail ? ` <span class="muted">(실패 ${t.fail})</span>` : ""}</td><td>${t.m.asof ? `${md(t.m.asof)}(${dow(t.m.asof)})` : "—"}</td></tr>`).join("")}</tbody></table>
      <div class="chart" id="live-dots" style="margin-top:12px"></div>
      <p class="hint">장중에는 그 시점 가격이다(미국은 약 15분 지연). 확정 기록은 매도일 장 마감 뒤 작업 패널의 '정산'으로 남긴다.</p>`;
    const items = tr.filter((t) => t.m.rows.length).map((t) => ({ label: `${L.week}주 ${t.mk}`, sub: `후보 평균 ${pct(t.cand, 1)}`, rows: t.m.rows,
      bench: t.b, benchName: t.m.benchmark?.name }));
    if (items.length) dots($("#live-dots"), items); else $("#live-dots").remove();
  }
  $("#live-refresh").addEventListener("click", () => live(true));

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
    $("#weeks").innerHTML = `<table class="tb"><thead><tr><th>주차</th><th class="l">종목</th><th>예측</th><th>수익률</th><th>벤치마크</th><th>초과</th><th class="l">예측 대비</th></tr></thead><tbody>${rs.map((r) => {
      const s = r.settled, p = r.pick || {}, v = num(s?.ret_pct);
      return `<tr class="click" data-k="${r.week}-${r.market}"><td>${lab(r)}</td><td class="l">${esc(p.name || "—")} <span class="muted mono">${esc(p.ticker || "")}</span></td>
        <td>${p.expected_return_range ? p.expected_return_range.join(" ~ ") : "—"}</td><td class="${sign(v)}">${pct(v)}</td><td>${s ? pct(num(s.benchmark_ret_pct)) : "—"}</td>
        <td class="${sign(num(s?.excess_pct))}">${s ? pp(num(s.excess_pct)) : "—"}</td><td class="l">${s ? outcomeTag(outcome(s.ret_pct, p.expected_return_range?.[0] ?? s.predicted_low, p.expected_return_range?.[1] ?? s.predicted_high)) : `<span class="tag off">정산 대기</span>`}</td></tr>`;
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

  function render() { live(); figs(); strip(); range(); contrib(); finals(); weeks(); }
  $$("#perf-mkt button").forEach((b) => b.addEventListener("click", () => { MKT = b.dataset.m; $$("#perf-mkt button").forEach((x) => x.setAttribute("aria-pressed", x === b)); render(); }));
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if ($("#v-perf").classList.contains("on")) { strip(); range(); live(); } }, 150); });
  return { render };
})();
