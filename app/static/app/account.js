// 계좌 — 포트폴리오 트래커 구성(총자산·수익 곡선 vs 벤치마크 → 배분 → 보유 종목 → 종목 상세)을 따른다.
// 참고: Sharesight(벤치마크 비교), Getquin(배분 분해), Delta/토스증권(종목 상세 = 차트 + 내 매매 + 이벤트)
const Account = (() => {
  let DEMO = false, RANGE = 90, ALLOC = "holdings", DATA = null, autoTried = false;
  const won = (v) => (v == null ? "—" : `${Math.round(v).toLocaleString()}`);
  const wonS = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Math.round(v).toLocaleString()}`);
  const q = () => (DEMO ? "?demo=1" : "");
  const GRAYS = ["#121416", "#3d4348", "#6b7277", "#9aa0a4", "#c2c7c9"];

  // ---------------------------------------------------------------- 수익 곡선
  function lineChart(host, pts) {
    if (pts.length < 2) { host.innerHTML = `<p class="muted" style="padding:40px 0;text-align:center">매매 기록이 쌓이면 곡선이 그려진다.</p>`; return; }
    const W = host.clientWidth || 640, H = 240, L = 44, R = 60, T = 12, B = 24;
    const vals = pts.flatMap((p) => [p.ret, p.bench_ret]);
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals); const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
    const x = (i) => L + (i / (pts.length - 1)) * (W - L - R), y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    const step = (hi - lo) > 30 ? 10 : (hi - lo) > 12 ? 5 : 2;
    let g = "";
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step)
      g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="${v === 0 ? "zero" : "gl"}"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${v > 0 ? "+" : ""}${v}%</text>`;
    const path = (k) => pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p[k]).toFixed(1)}`).join("");
    const ticks = [0, Math.floor(pts.length / 2), pts.length - 1].map((i) => `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle"}">${md(pts[i].d)}</text>`).join("");
    const last = pts[pts.length - 1];
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}">${g}${ticks}
      <path d="${path("bench_ret")}" fill="none" stroke="#8a9095" stroke-width="1.5" stroke-dasharray="4 3"/>
      <path d="${path("ret")}" fill="none" stroke="#121416" stroke-width="2"/>
      <text x="${W - R + 6}" y="${y(last.ret) + 4}" class="lbl">계좌 ${pct(last.ret, 1)}</text>
      <text x="${W - R + 6}" y="${y(last.bench_ret) + 4}">KOSPI ${pct(last.bench_ret, 1)}</text>
      <line id="xh" y1="${T}" y2="${H - B}" stroke="#121416" stroke-width=".8" opacity="0"/><circle id="xd" r="4" fill="#ffe14d" stroke="#121416" opacity="0"/>
      <rect id="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>`;
    const hit = host.querySelector("#hit"), xh = host.querySelector("#xh"), xd = host.querySelector("#xd");
    hit.addEventListener("mousemove", (e) => {
      const r = hit.getBoundingClientRect(), i = Math.round(((e.clientX - r.left) / r.width) * (pts.length - 1)), p = pts[Math.max(0, Math.min(pts.length - 1, i))];
      xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("opacity", 1);
      xd.setAttribute("cx", x(i)); xd.setAttribute("cy", y(p.ret)); xd.setAttribute("opacity", 1);
      tip.innerHTML = `${p.d}<br>계좌 ${pct(p.ret)}  (${won(p.equity)}원)<br>KOSPI ${pct(p.bench_ret)}`;
      tip.classList.add("on"); tip.style.left = `${Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8)}px`; tip.style.top = `${e.clientY - tip.offsetHeight - 10}px`;
    });
    hit.addEventListener("mouseleave", () => { tip.classList.remove("on"); xh.setAttribute("opacity", 0); xd.setAttribute("opacity", 0); });
  }
  async function loadCurve() {
    const host = $("#eq");
    host.innerHTML = `<p class="muted" style="padding:40px 0;text-align:center">곡선 계산 중</p>`;
    const e = await api(`/api/account/equity?days=${RANGE}${DEMO ? "&demo=1" : ""}`);
    const cut = RANGE >= 9999 ? e.points : e.points.slice(-Math.round(RANGE * 5 / 7));
    if (cut.length) { const e0 = cut[0].equity, b0 = cut[0].bench; cut.forEach((p) => { p.ret = (p.equity / e0 - 1) * 100; p.bench_ret = (p.bench / b0 - 1) * 100; }); }
    lineChart(host, cut);
    $("#eq-note").textContent = e.note || "";
  }

  // ---------------------------------------------------------------- 배분
  function alloc(sm) {
    const items = sm.allocation[ALLOC].filter((a) => a.value > 0);
    const tot = items.reduce((s, a) => s + a.value, 0) || 1;
    let gi = 0;
    const col = items.map((a) => (a.key === "cash" ? "url(#cash)" : GRAYS[Math.min(gi++, GRAYS.length - 1)]));
    let xx = 0;
    const segs = items.map((a, i) => { const w = a.value / tot * 100; const s = `<rect x="${xx}%" width="calc(${w}% - 2px)" height="28" fill="${col[i]}" stroke="#121416" stroke-width="${a.key === "cash" ? 1 : 0}"><title>${esc(a.label)} ${(w).toFixed(1)}%</title></rect>`; xx += w; return s; }).join("");
    return `<svg width="100%" height="28" style="display:block"><defs><pattern id="cash" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#f8f9f7"/><line x1="0" y1="0" x2="0" y2="6" stroke="#121416" stroke-width="1"/></pattern></defs>${segs}</svg>
      <table class="tb" style="margin-top:10px"><tbody>${items.map((a, i) => `<tr><td><svg width="12" height="12" style="vertical-align:-1px;margin-right:8px"><rect width="12" height="12" fill="${col[i]}" stroke="#121416"/></svg>${esc(a.label)}</td><td>${won(a.value)}원</td><td>${(a.value / tot * 100).toFixed(1)}%</td></tr>`).join("")}</tbody></table>`;
  }

  // ---------------------------------------------------------------- 메인 화면
  async function render() {
    const a = await api(`/api/account${q()}`);
    DATA = a;
    const s = a.settings;
    if (!DEMO && s.configured && !a.today_fetched && !autoTried) {          // 오늘 첫 방문이면 잔고를 한 번 받아 온다
      autoTried = true;
      try { await api("/api/account/refresh", { method: "POST" }); return render(); } catch (e) { toast(e.message); }
    }
    const head = `<div class="page-hd"><h1>계좌</h1><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <div class="seg" role="group" aria-label="데이터"><button data-src="real" aria-pressed="${!DEMO}">${s.env === "real" ? "실전 계좌" : "모의투자 계좌"}</button><button data-src="demo" aria-pressed="${DEMO}">예시 데이터</button></div>
      ${a.state && !DEMO ? `<span class="muted" style="font-size:12.5px">조회 ${a.state.fetched_at.replace("T", " ").slice(0, 16)}</span>` : ""}
      ${DEMO ? "" : `<button class="btn primary" id="ac-refresh" ${s.configured ? "" : "disabled"}>잔고 새로고침</button>`}</div></div>
      ${DEMO ? `<p class="warn" style="margin:0 0 14px;color:var(--ink);border-color:var(--ink);background:var(--mark)">예시 데이터다. 지난 추천 종목을 추천일 시가에 샀다고 가정한 포트폴리오로, 실제 계좌가 아니다. 첫 추천은 평가 종료일에 판 것으로 두어 실현손익 예시를 만들었다.</p>` : ""}`;

    if (!a.state || !a.summary) {
      $("#acct").innerHTML = head + `<section class="blk"><div class="bd"><p style="margin:0">${s.configured ? "아직 조회 기록이 없다. 잔고 새로고침을 누른다." : "한국투자증권 키가 비어 있다. 아래 연결 설정을 따른다."} 화면 구성을 먼저 보려면 <b>예시 데이터</b>를 누른다.</p></div></section>` + setup(s) + planBlock();
      wire(); return;
    }
    const sm = a.summary, day = sm.day;
    const empty = !sm.positions.length;
    $("#acct").innerHTML = head + `
      <section class="blk" style="margin-top:0"><div class="acct-hero">
        <div class="acct-fig">
          <div class="l">총자산</div><div class="big num">${won(sm.total)}<small>원</small></div>
          <div class="num ${sign(day?.amount)}" style="font-size:14px;margin-top:4px">${day ? `오늘 ${wonS(day.amount)} (${pct(day.pct)})` : "오늘 변동 —"}</div>
          <dl class="kv" style="margin-top:16px">
            <dt>평가손익</dt><dd class="${sign(sm.unrealized)}">${wonS(sm.unrealized)} ${sm.unrealized_pct != null ? `(${pct(sm.unrealized_pct)})` : ""}</dd>
            <dt>실현손익</dt><dd class="${sign(sm.realized)}">${wonS(sm.realized)}</dd>
            <dt>주식 평가</dt><dd>${won(sm.value)}</dd><dt>현금</dt><dd>${won(sm.cash)}</dd>
            ${sm.usdkrw ? `<dt>환율</dt><dd>${sm.usdkrw.toFixed(2)}</dd>` : ""}</dl>
        </div>
        <div class="acct-curve"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <div class="legend" style="margin:0"><span><svg width="18" height="4"><line x1="0" x2="18" y1="2" y2="2" stroke="#121416" stroke-width="2"/></svg>계좌</span><span><svg width="18" height="4"><line x1="0" x2="18" y1="2" y2="2" stroke="#8a9095" stroke-width="1.5" stroke-dasharray="4 3"/></svg>KOSPI</span></div>
          <div class="seg" role="group" aria-label="기간">${[[30, "1개월"], [90, "3개월"], [9999, "전체"]].map(([d, l]) => `<button data-range="${d}" aria-pressed="${RANGE === d}">${l}</button>`).join("")}</div></div>
          <div class="chart" id="eq"></div><p class="hint" id="eq-note"></p></div>
      </div></section>

      <div class="two">
        <section class="blk"><header><h2>배분</h2><div class="seg" role="group" aria-label="배분 기준"><button data-alloc="holdings" aria-pressed="${ALLOC === "holdings"}">종목</button><button data-alloc="market" aria-pressed="${ALLOC === "market"}">시장</button></div></header><div class="bd" id="alloc">${alloc(sm)}</div></section>
        <section class="blk"><header><h2>브리핑</h2><p>조회값으로만 쓴 요약</p></header><div class="bd"><ol class="brief">${a.brief.map((l) => `<li>${esc(l)}</li>`).join("")}</ol></div></section>
      </div>

      <section class="blk"><header><h2>보유 종목</h2><p>행을 누르면 차트와 이벤트</p></header><div class="bd">${empty ? `<p class="muted" style="margin:0">보유 종목이 없다.</p>` : `
        <table class="tb"><thead><tr><th>종목</th><th class="l" style="width:150px">비중</th><th>수량</th><th>평단</th><th>현재가</th><th>평가금액(원)</th><th>손익(원)</th><th>수익률</th><th>오늘</th></tr></thead><tbody>
        ${sm.positions.map((p) => `<tr class="click" data-m="${p.market}" data-t="${esc(p.ticker)}"><td>${esc(p.name)} <span class="muted mono">${esc(p.ticker)}</span>${p.currency === "USD" ? ` <span class="tag off">USD</span>` : ""}</td>
          <td class="l"><div style="display:flex;gap:8px;align-items:center"><div class="bar" style="flex:1"><i style="width:${Math.min(100, p.weight)}%"></i></div><span class="mono" style="font-size:11.5px;width:40px;text-align:right">${p.weight.toFixed(1)}%</span></div></td>
          <td>${p.qty.toLocaleString()}</td><td>${p.avg_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td><td>${p.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
          <td>${won(p.value_krw)}</td><td class="${sign(p.value_krw - p.cost_krw)}">${wonS(p.value_krw - p.cost_krw)}</td><td class="${sign(p.pnl_pct)}">${pct(p.pnl_pct)}</td><td class="${sign(p.day_pct)}">${p.day_pct != null ? pct(p.day_pct) : "—"}</td></tr>`).join("")}
        </tbody></table>`}</div></section>

      <section class="blk"><header><h2>최근 체결</h2><p>국내 3개월 이내</p></header><div class="bd">${a.trades.length ? `<table class="tb"><thead><tr><th>날짜</th><th class="l">종목</th><th class="l">구분</th><th>수량</th><th>체결가</th><th>금액</th></tr></thead><tbody>
        ${[...a.trades].reverse().map((t) => `<tr><td class="mono">${t.date}</td><td class="l">${esc(t.name)}</td><td class="l ${t.side === "buy" ? "up" : "down"}">${t.side === "buy" ? "매수" : "매도"}</td><td>${t.qty.toLocaleString()}</td><td>${t.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td><td>${Math.round(t.amount).toLocaleString()}</td></tr>`).join("")}</tbody></table>` : `<p class="muted" style="margin:0">체결 내역이 없다.</p>`}</div></section>
      ${planBlock()}${setup(s)}`;
    wire();
    $$("#acct tr.click").forEach((tr) => tr.addEventListener("click", () => Holding.open(tr.dataset.m, tr.dataset.t, DEMO)));
    loadCurve();
  }

  function planBlock() {
    return `<section class="blk"><header><h2>이번 주 추천 매수 초안</h2><p>주문은 보내지 않는다. 수량과 금액만 계산한다</p></header><div class="bd">
      <div style="display:flex;gap:8px;align-items:end;flex-wrap:wrap"><label style="font-size:12.5px">시장<br><select class="sel" id="pl-m"><option value="KR">한국</option><option value="US">미국</option></select></label>
      <label style="font-size:12.5px">투입 금액<br><input class="in num" id="pl-b" type="number" value="1000000" step="100000" style="width:160px"></label><button class="btn" id="pl-go">초안 만들기</button></div><div id="pl-out" style="margin-top:12px"></div></div></section>`;
  }
  function setup(s) {
    return `<details class="blk" ${s.configured ? "" : "open"}><summary style="padding:12px 16px;cursor:pointer;font-weight:850">연결 설정 <span class="muted" style="font-weight:500;font-size:12.5px">${s.configured ? `${s.env === "real" ? "실전" : "모의투자"} ${s.account_no}` : "비어 있음"}</span></summary><div class="bd setup" style="border-top:1px solid var(--ink)">
      <dl class="kv"><dt>증권사</dt><dd>한국투자증권 Open API</dd><dt>환경</dt><dd>${s.env === "real" ? "실전" : "모의투자"} (.env KIS_ENV)</dd>
        <dt>APP KEY</dt><dd>${s.app_key || "비어 있음"}</dd><dt>계좌</dt><dd>${s.account_no || "비어 있음"}</dd><dt>주문</dt><dd>${s.order_enabled ? "허용" : "차단"}</dd></dl>
      <ol><li>KIS Developers에서 앱을 등록해 APP KEY / APP SECRET을 받는다. 처음에는 모의투자로 받는다.</li>
        <li><code>.env</code>에 <code>KIS_APP_KEY</code>, <code>KIS_APP_SECRET</code>, <code>KIS_ACCOUNT_NO</code>를 채운다. 실전으로 바꿀 때는 <code>KIS_ENV=real</code>과 실전 키로 갈아 끼운다.</li>
        <li>서버를 다시 켠다. 키는 이 화면에서 입력받지 않고 저장소에도 올라가지 않는다.</li></ol></div></details>`;
  }
  function wire() {
    $$("[data-src]").forEach((b) => b.addEventListener("click", () => { DEMO = b.dataset.src === "demo"; render(); }));
    $$("[data-range]").forEach((b) => b.addEventListener("click", () => { RANGE = +b.dataset.range; $$("[data-range]").forEach((x) => x.setAttribute("aria-pressed", x === b)); loadCurve(); }));
    $$("[data-alloc]").forEach((b) => b.addEventListener("click", () => { ALLOC = b.dataset.alloc; $$("[data-alloc]").forEach((x) => x.setAttribute("aria-pressed", x === b)); $("#alloc").innerHTML = alloc(DATA.summary); }));
    $("#ac-refresh")?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "조회 중";
      try { await api("/api/account/refresh", { method: "POST" }); toast("잔고를 받아왔다"); render(); }
      catch (x) { toast(x.message); e.target.disabled = false; e.target.textContent = "잔고 새로고침"; }
    });
    $("#pl-go")?.addEventListener("click", async () => {
      const wk = Tasks.week()?.current.week_no;
      try {
        const p = await api("/api/account/plan", { method: "POST", body: { week: wk, market: $("#pl-m").value, budget: +$("#pl-b").value } });
        $("#pl-out").innerHTML = `<dl class="kv"><dt>종목</dt><dd>${esc(p.name)} ${p.ticker}</dd><dt>수량</dt><dd>${p.qty}주 (시장가)</dd><dt>기준가</dt><dd>${p.ref_price.toLocaleString()} ${p.currency}</dd>
          <dt>예상 금액</dt><dd>${p.est_amount.toLocaleString()} ${p.currency}</dd><dt>확인 문구</dt><dd>${p.confirm_phrase}</dd></dl><p class="hint">${esc(p.note)} ${esc(p.why_not)}</p>`;
      } catch (x) { $("#pl-out").innerHTML = `<p class="warn">${esc(x.message)}</p>`; }
    });
  }
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if ($("#v-account").classList.contains("on") && DATA?.summary) loadCurve(); }, 200); });
  return { render };
})();

// ---------------------------------------------------------------- 종목 상세 (차트 + 매매 + 이벤트)
const Holding = (() => {
  let D, RANGE = 126;
  function chart() {
    const host = $("#hc"), bars = D.bars.slice(-RANGE);
    if (!bars.length) { host.innerHTML = `<p class="muted">시세가 없다.</p>`; return; }
    const W = host.clientWidth || 620, H = 330, L = 8, R = 62, T = 10, VH = 54, PH = H - VH - 30;
    const pos = D.position, avg = pos?.avg_price;
    const lows = bars.map((b) => b.l).concat(avg ? [avg] : []), highs = bars.map((b) => b.h).concat(avg ? [avg] : []);
    let lo = Math.min(...lows), hi = Math.max(...highs); const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
    const n = bars.length, bw = Math.max(1.5, ((W - L - R) / n) * 0.62);
    const x = (i) => L + (i + 0.5) * ((W - L - R) / n), y = (v) => T + (1 - (v - lo) / (hi - lo)) * PH;
    const vmax = Math.max(...bars.map((b) => b.v)) || 1, vy = (v) => H - 22 - (v / vmax) * VH;
    const fmt = (v) => v.toLocaleString(undefined, { maximumFractionDigits: D.currency === "USD" ? 2 : 0 });
    let s = "";
    for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4; s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="gl"/><text x="${W - R + 6}" y="${y(v) + 3}">${fmt(v)}</text>`; }
    bars.forEach((b, i) => {
      const up = b.c >= b.o, c = up ? "#d1242f" : "#1e56c8";
      s += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(b.h)}" y2="${y(b.l)}" stroke="${c}" stroke-width="1"/>
        <rect x="${x(i) - bw / 2}" y="${y(Math.max(b.o, b.c))}" width="${bw}" height="${Math.max(1, Math.abs(y(b.o) - y(b.c)))}" fill="${up ? c : c}"/>
        <rect x="${x(i) - bw / 2}" y="${vy(b.v)}" width="${bw}" height="${H - 22 - vy(b.v)}" fill="${c}" opacity=".35"/>`;
    });
    const ma = (k, col, dash) => { const p = bars.map((b, i) => (b[k] == null ? null : `${x(i).toFixed(1)} ${y(b[k]).toFixed(1)}`)).filter(Boolean); return p.length ? `<polyline points="${p.join(" ")}" fill="none" stroke="${col}" stroke-width="1.2" ${dash ? `stroke-dasharray="${dash}"` : ""}/>` : ""; };
    s += ma("ma20", "#121416") + ma("ma60", "#8a9095", "3 2");
    if (avg) s += `<line x1="${L}" x2="${W - R}" y1="${y(avg)}" y2="${y(avg)}" stroke="#121416" stroke-width="1" stroke-dasharray="6 4"/><rect x="${W - R + 2}" y="${y(avg) - 9}" width="${R - 4}" height="18" fill="#ffe14d" stroke="#121416"/><text x="${W - R + 6}" y="${y(avg) + 4}" style="fill:#121416;font-weight:700">평단</text>`;
    const idx = Object.fromEntries(bars.map((b, i) => [b.d, i]));
    D.trades.forEach((t) => {
      const i = idx[t.date]; if (i == null) return;
      const b = bars[i], buy = t.side === "buy", yy = buy ? y(b.l) + 12 : y(b.h) - 12;
      s += `<path d="${buy ? `M${x(i)} ${yy - 7} l6 10 h-12z` : `M${x(i)} ${yy + 7} l6 -10 h-12z`}" fill="${buy ? "#121416" : "#f8f9f7"}" stroke="#121416" stroke-width="1.2"><title>${buy ? "매수" : "매도"} ${t.qty}주 @ ${fmt(t.price)}</title></path>`;
    });
    D.events.filter((e) => e.kind !== "매매" && idx[e.date] != null).forEach((e) => {
      const i = idx[e.date];
      s += `<circle cx="${x(i)}" cy="${H - 10}" r="4" fill="${e.kind === "추천" ? "#ffe14d" : "#f8f9f7"}" stroke="#121416"><title>${e.date} ${esc(e.title)}</title></circle>`;
    });
    [0, Math.floor(n / 2), n - 1].forEach((i) => { s += `<text x="${x(i)}" y="${H - 22 + 14}" text-anchor="middle" opacity="0">${bars[i].d}</text>`; });
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}">${s}<line id="hx" y1="${T}" y2="${H - 22}" stroke="#121416" stroke-width=".7" opacity="0"/><rect id="hh" x="${L}" y="${T}" width="${W - L - R}" height="${H - 22 - T}" fill="transparent"/></svg>
      <div style="display:flex;justify-content:space-between;font:10.5px var(--mono);color:var(--ink-3)"><span>${bars[0].d}</span><span>${bars[n - 1].d}</span></div>`;
    const hh = host.querySelector("#hh"), hx = host.querySelector("#hx");
    hh.addEventListener("mousemove", (e) => {
      const r = hh.getBoundingClientRect(), i = Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - r.left) / r.width) * n))), b = bars[i];
      hx.setAttribute("x1", x(i)); hx.setAttribute("x2", x(i)); hx.setAttribute("opacity", 1);
      const prev = bars[i - 1]?.c, ch = prev ? (b.c / prev - 1) * 100 : null;
      const ev = D.events.filter((x) => x.date === b.d).map((x) => `<br>${esc(x.kind)}: ${esc(x.title)}`).join("");
      tip.innerHTML = `${b.d}<br>시 ${fmt(b.o)}  고 ${fmt(b.h)}<br>저 ${fmt(b.l)}  종 ${fmt(b.c)}${ch != null ? `  (${pct(ch)})` : ""}<br>거래량 ${Math.round(b.v).toLocaleString()}${ev}`;
      tip.classList.add("on"); tip.style.left = `${Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8)}px`; tip.style.top = `${Math.max(8, e.clientY - tip.offsetHeight - 10)}px`;
    });
    hh.addEventListener("mouseleave", () => { tip.classList.remove("on"); hx.setAttribute("opacity", 0); });
  }
  async function open(market, ticker, demo) {
    Modal.open("불러오는 중", `<p class="muted">시세와 이벤트를 모으는 중</p>`, "", true);
    try { D = await api(`/api/account/holding/${market}/${encodeURIComponent(ticker)}${demo ? "?demo=1" : ""}`); }
    catch (e) { $("#m-body").innerHTML = `<p class="warn">${esc(e.message)}</p>`; return; }
    const p = D.position, last = D.bars[D.bars.length - 1];
    const fmt = (v) => v.toLocaleString(undefined, { maximumFractionDigits: D.currency === "USD" ? 2 : 0 });
    $("#m-title").innerHTML = `${esc(D.name)} <span class="mono muted" style="font-size:13px">${esc(D.ticker)}</span> <span class="num" style="font-size:16px;margin-left:10px">${fmt(last.c)}</span>${p ? ` <span class="num ${sign(p.pnl_pct)}" style="font-size:14px">${pct(p.pnl_pct)}</span>` : ""}`;
    const kinds = ["전체", "매매", "추천", "가격"];
    $("#m-body").innerHTML = `<div class="hold">
      <div><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <div class="legend" style="margin:0"><span><svg width="16" height="4"><line x2="16" y1="2" y2="2" stroke="#121416" stroke-width="1.2"/></svg>MA20</span><span><svg width="16" height="4"><line x2="16" y1="2" y2="2" stroke="#8a9095" stroke-dasharray="3 2"/></svg>MA60</span>
            ${p ? `<span><svg width="16" height="4"><line x2="16" y1="2" y2="2" stroke="#121416" stroke-dasharray="6 4"/></svg>평단</span>` : ""}<span><svg width="12" height="10"><path d="M6 0l6 10H0z" fill="#121416"/></svg>매수</span><span><svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="#ffe14d" stroke="#121416"/></svg>추천</span></div>
          <div class="seg" role="group" aria-label="기간">${[[21, "1개월"], [63, "3개월"], [126, "6개월"]].map(([d, l]) => `<button data-hr="${d}" aria-pressed="${RANGE === d}">${l}</button>`).join("")}</div></div>
        <div class="chart" id="hc"></div>
        <h3 style="font-size:14px;margin:18px 0 6px">요약</h3><ol class="brief">${D.summary.map((l) => `<li>${esc(l)}</li>`).join("")}</ol></div>
      <div>${p ? `<dl class="kv"><dt>보유 수량</dt><dd>${p.qty.toLocaleString()}</dd><dt>평단</dt><dd>${fmt(p.avg_price)}</dd><dt>평가금액</dt><dd>${fmt(p.value)} ${D.currency === "USD" ? "USD" : "원"}</dd>
          <dt>평가손익</dt><dd class="${sign(p.pnl)}">${p.pnl > 0 ? "+" : ""}${fmt(p.pnl)}</dd><dt>비중</dt><dd>${p.weight ? p.weight.toFixed(1) + "%" : "—"}</dd></dl>` : `<p class="muted" style="margin:0">보유하지 않는 종목이다.</p>`}
        <div style="display:flex;justify-content:space-between;align-items:center;margin:18px 0 6px"><h3 style="font-size:14px;margin:0">이벤트</h3>
          <div class="seg" role="group" aria-label="이벤트 종류">${kinds.map((k, i) => `<button data-ek="${k}" aria-pressed="${i === 0}">${k}</button>`).join("")}</div></div>
        <ol class="tl" id="tl"></ol>
        <p class="hint">매매는 증권사 체결 내역, 추천은 이 파이프라인 기록, 가격은 일봉에서 계산한 값이다. 공시·뉴스는 DART 키를 넣으면 추가할 수 있다.</p></div></div>`;
    const tl = (k) => { $("#tl").innerHTML = D.events.filter((e) => k === "전체" || e.kind === k).map((e) => `<li><span class="d">${e.date ? md(e.date) : ""}</span><span class="k k-${e.kind}">${e.kind}</span><div><b>${esc(e.title)}</b>${e.value != null ? ` <span class="num ${sign(e.value)}">${pct(e.value, 1)}</span>` : ""}${e.body ? `<p>${esc(e.body)}</p>` : ""}</div></li>`).join("") || `<li class="muted">없음</li>`; };
    tl("전체");
    $$("#m-body [data-ek]").forEach((b) => b.addEventListener("click", () => { $$("#m-body [data-ek]").forEach((x) => x.setAttribute("aria-pressed", x === b)); tl(b.dataset.ek); }));
    $$("#m-body [data-hr]").forEach((b) => b.addEventListener("click", () => { RANGE = +b.dataset.hr; $$("#m-body [data-hr]").forEach((x) => x.setAttribute("aria-pressed", x === b)); chart(); }));
    chart();
  }
  return { open };
})();
