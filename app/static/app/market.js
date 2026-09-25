// 시장 — 요약, 시세판(지수·환율·금리·변동성·원자재·크립토), 히트맵, 캘린더
// 참고: Finviz 홈(시장 지도 + 자산군 표 + 경제 캘린더), TradingView 마켓 개요(자산군별 표 + 스파크라인)
const Market = (() => {
  let PERIOD = "week", HM = "US", HP = "d1", MONTH = null, B = null, HEAT = {};
  const fmtN = (v, d = 2) => (v == null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const digits = (s) => (["KRW=X", "JPYKRW=X", "^KS11", "^KQ11", "^GSPC", "^IXIC", "^DJI", "^SOX", "^N225", "BTC-USD", "ETH-USD", "GC=F"].includes(s) ? 2 : 2);

  function spark(v, up) {
    if (!v?.length) return "";
    const W = 96, H = 26, lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1;
    const pts = v.map((x, i) => `${(i / (v.length - 1) * W).toFixed(1)},${(H - 2 - (x - lo) / r * (H - 4)).toFixed(1)}`).join(" ");
    return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><polyline points="${pts}" fill="none" stroke="${up ? "#d1242f" : "#1e56c8"}" stroke-width="1.3"/></svg>`;
  }

  async function summary() {
    const el = $("#mk-sum");
    el.innerHTML = `<p class="muted">요약 계산 중</p>`;
    try {
      const s = await api(`/api/market/summary?period=${PERIOD}`);
      el.innerHTML = `<div style="display:flex;gap:6px;margin-bottom:10px">${s.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
        <ol class="brief">${s.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ol><p class="hint">숫자로만 쓴 요약이다. 시세 기준 ${s.asof.replace("T", " ")}.</p>`;
    } catch (e) { el.innerHTML = `<p class="warn">${esc(e.message)}</p>`; }
  }

  async function board() {
    const el = $("#mk-board");
    if (!B) { el.innerHTML = `<p class="muted">시세 받는 중</p>`; B = await api("/api/market/board").catch((e) => ({ error: e.message })); }
    if (B.error) { el.innerHTML = `<p class="warn">${esc(B.error)}</p>`; return; }
    const k = PERIOD === "week" ? "w1" : "m1";
    el.innerHTML = B.groups.map((g) => `<section class="blk mk-g"><header><h2>${g.group}</h2></header>
      <table class="tb"><thead><tr><th>종목</th><th>현재</th><th>1일</th><th>${PERIOD === "week" ? "1주" : "1개월"}</th><th>연초 대비</th><th class="l">2개월</th></tr></thead><tbody>
      ${g.rows.map((r) => r.last == null ? `<tr><td>${r.label}</td><td colspan="5" class="muted">시세 없음</td></tr>`
        : `<tr><td>${esc(r.label)}<br><span class="muted mono" style="font-size:10.5px">${esc(r.symbol)}  ${md(r.asof)}</span></td><td>${fmtN(r.last, digits(r.symbol))}${r.symbol === "^TNX" ? "%" : ""}</td>
          <td class="${sign(r.d1)}">${pct(r.d1)}</td><td class="${sign(r[k])}">${pct(r[k])}</td><td class="${sign(r.ytd)}">${pct(r.ytd, 1)}</td><td class="l">${spark(r.spark, (r.spark?.at(-1) ?? 0) >= (r.spark?.[0] ?? 0))}</td></tr>`).join("")}
      </tbody></table></section>`).join("");
  }

  // ---------------------------------------------------------------- 히트맵 (squarified treemap)
  function squarify(items, x, y, w, h) {
    const out = [], tot = items.reduce((s, i) => s + i.v, 0);
    if (!tot) return out;
    const scale = (w * h) / tot;
    let rest = items.map((i) => ({ ...i, a: i.v * scale }));
    while (rest.length) {
      const short = Math.min(w, h);
      let row = [rest[0]], best = worst(row, short);
      for (let i = 1; i < rest.length; i++) {
        const t = [...row, rest[i]], wv = worst(t, short);
        if (wv > best) break; row = t; best = wv;
      }
      const sum = row.reduce((s, r) => s + r.a, 0), side = sum / short;
      let off = 0;
      row.forEach((r) => {
        const len = r.a / side;
        out.push(w >= h ? { ...r, x, y: y + off, w: side, h: len } : { ...r, x: x + off, y, w: len, h: side });
        off += len;
      });
      if (w >= h) { x += side; w -= side; } else { y += side; h -= side; }
      rest = rest.slice(row.length);
    }
    return out;
    function worst(r, s) { const sum = r.reduce((a, b) => a + b.a, 0), mx = Math.max(...r.map((b) => b.a)), mn = Math.min(...r.map((b) => b.a)); return Math.max((s * s * mx) / (sum * sum), (sum * sum) / (s * s * mn)); }
  }
  // 발산형: 하락 파랑 ← 회색(0) → 상승 빨강. 한국 시장 관례.
  function heatColor(v, lim) {
    if (v == null) return "#c5cac7";
    const t = Math.max(-1, Math.min(1, v / lim));
    const mix = (a, b, k) => a.map((c, i) => Math.round(c + (b[i] - c) * k));
    const mid = [196, 200, 198], up = [178, 24, 38], dn = [22, 70, 170];
    const c = t >= 0 ? mix(mid, up, t) : mix(mid, dn, -t);
    return `rgb(${c.join(",")})`;
  }
  async function heat() {
    const el = $("#mk-heat");
    if (!HEAT[HM]) {
      el.innerHTML = `<p class="muted" style="padding:60px 0;text-align:center">${HM === "US" ? "S&P 500 상위 150종목 시세를 받는 중. 처음 한 번은 1분 가까이 걸린다." : "KOSPI·KOSDAQ 시총 상위 종목을 받는 중"}</p>`;
      HEAT[HM] = await api(`/api/market/heatmap?mkt=${HM}`).catch((e) => ({ error: e.message }));
    }
    const H = HEAT[HM];
    if (H.error) { el.innerHTML = `<p class="warn">${esc(H.error)}</p>`; return; }
    const W = el.clientWidth || 1100, HH = Math.round(W * 0.48), lim = HP === "d1" ? 3 : 8;
    const groups = {};
    H.tiles.forEach((t) => (groups[t.group] = groups[t.group] || []).push(t));
    const gl = Object.entries(groups).map(([g, ts]) => ({ g, ts: ts.sort((a, b) => b.cap - a.cap), v: ts.reduce((s, t) => s + t.cap, 0) })).sort((a, b) => b.v - a.v);
    let s = "";
    squarify(gl, 0, 0, W, HH).forEach((G) => {
      const pad = 1, hd = G.h > 40 && G.w > 60 ? 15 : 0;
      s += `<rect x="${G.x}" y="${G.y}" width="${G.w}" height="${G.h}" fill="#121416"/>`;
      if (hd) s += `<text x="${G.x + 5}" y="${G.y + 11}" class="hm-g">${esc(G.g)}</text>`;
      squarify(G.ts.map((t) => ({ ...t, v: t.cap })), G.x + pad, G.y + hd + pad, Math.max(0, G.w - pad * 2), Math.max(0, G.h - hd - pad * 2)).forEach((t) => {
        const v = t[HP];
        s += `<g class="hm-t" data-t="${esc(t.ticker)}"><rect x="${t.x + .5}" y="${t.y + .5}" width="${Math.max(0, t.w - 1)}" height="${Math.max(0, t.h - 1)}" fill="${heatColor(v, lim)}"/>`;
        if (t.w > 34 && t.h > 22) {
          const fs = Math.max(9, Math.min(20, Math.sqrt(t.w * t.h) / 6));
          s += `<text x="${t.x + t.w / 2}" y="${t.y + t.h / 2 - (t.h > fs * 2.4 ? fs * .25 : -fs * .35)}" text-anchor="middle" class="hm-l" style="font-size:${fs}px">${esc(t.label)}</text>`;
          if (t.h > fs * 2.4) s += `<text x="${t.x + t.w / 2}" y="${t.y + t.h / 2 + fs * .95}" text-anchor="middle" class="hm-v" style="font-size:${fs * .72}px">${pct(v)}</text>`;
        }
        s += `</g>`;
      });
    });
    const legend = [-lim, -lim / 2, 0, lim / 2, lim].map((v) => `<span style="display:inline-flex;align-items:center;gap:4px"><i style="width:22px;height:12px;background:${heatColor(v, lim)};display:inline-block"></i>${v > 0 ? "+" : ""}${v}%</span>`).join("");
    el.innerHTML = `<svg viewBox="0 0 ${W} ${HH}" class="hm">${s}</svg><div class="legend" style="margin:8px 0 0;justify-content:space-between"><span>${esc(H.grouping)}  크기 = 시가총액</span><span style="display:inline-flex;gap:10px">${legend}</span></div>`;
    const idx = Object.fromEntries(H.tiles.map((t) => [t.ticker, t]));
    $$(".hm-t", el).forEach((g) => hover(g, (() => { const t = idx[g.dataset.t]; return `<b>${esc(t.name || t.label)}</b> ${esc(t.ticker)}<br>1일 ${pct(t.d1)}  1주 ${pct(t.w1)}<br>시총 ${HM === "US" ? "$" + (t.cap / 1e9).toFixed(0) + "B" : (t.cap / 1e12).toFixed(1) + "조원"}`; })()));
  }

  // ---------------------------------------------------------------- 캘린더
  const KIND = { 휴장: "k-hol", FOMC: "k-fomc", 실적: "k-earn", 주차: "k-week", 일정: "k-user" };
  async function calendar() {
    const el = $("#mk-cal");
    const c = await api(`/api/market/calendar${MONTH ? `?month=${MONTH}` : ""}`);
    MONTH = c.month;
    const start = new Date(c.start + "T00:00:00"), today = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
    const by = {};
    c.events.forEach((e) => (by[e.date.slice(0, 10)] = by[e.date.slice(0, 10)] || []).push(e));
    let cells = "";
    for (let i = 0; i < 42; i++) {
      const d = new Date(start.getTime() + i * 864e5), ds = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const inM = ds.slice(0, 7) === MONTH, we = i % 7 >= 5;
      cells += `<div class="cal-d ${inM ? "" : "out"} ${we ? "we" : ""} ${ds === today ? "today" : ""}"><span class="n">${d.getDate()}</span>
        ${(by[ds] || []).map((e) => `<span class="cal-e ${KIND[e.kind] || "k-user"}" ${e.user ? `data-del="${e.id}"` : ""} title="${esc(e.title)}">${esc(e.title)}</span>`).join("")}</div>`;
    }
    const [y, m] = MONTH.split("-").map(Number);
    el.innerHTML = `<div class="cal-hd"><button class="btn sm ghost" data-mv="-1" aria-label="이전 달">이전</button><b>${y}년 ${m}월</b><button class="btn sm ghost" data-mv="1" aria-label="다음 달">다음</button>
        <span class="sp"></span><div class="legend" style="margin:0">${Object.entries(KIND).map(([k, c]) => `<span><i class="cal-e ${c}" style="width:14px;height:10px;padding:0"></i>${k}</span>`).join("")}</div></div>
      <div class="cal">${["월", "화", "수", "목", "금", "토", "일"].map((d) => `<div class="cal-w">${d}</div>`).join("")}${cells}</div>
      <form class="cal-add" id="cal-add"><input class="in num" type="date" name="date" required style="width:160px"><input class="in" name="title" placeholder="일정 (예: 미국 CPI 발표, 금통위)" required>
        <select class="sel" name="market"><option value="">공통</option><option value="KR">한국</option><option value="US">미국</option></select><button class="btn">추가</button></form>
      <p class="hint">휴장·FOMC는 코드에 적힌 표(app/calendar_data.py), 실적일은 이번 주차 미국 후보 20종목(yfinance), 주차는 파이프라인 기록에서 온다. 직접 추가한 일정은 눌러서 지운다.</p>`;
    $$("[data-mv]", el).forEach((b) => b.addEventListener("click", () => { const d = new Date(y, m - 1 + +b.dataset.mv, 1); MONTH = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; calendar(); }));
    $$("[data-del]", el).forEach((b) => b.addEventListener("click", async () => { if (confirm(`'${b.title}' 일정을 지울까?`)) { await api(`/api/market/calendar/${b.dataset.del}`, { method: "DELETE" }); calendar(); } }));
    $("#cal-add").addEventListener("submit", async (e) => {
      e.preventDefault(); const f = new FormData(e.target);
      await api("/api/market/calendar", { method: "POST", body: { date: f.get("date"), title: f.get("title"), market: f.get("market"), kind: "일정" } });
      toast("일정을 추가했다"); calendar();
    });
  }

  let inited = false;
  function render() {
    if (!inited) {
      inited = true;
      $$("#mk-period button").forEach((b) => b.addEventListener("click", () => { PERIOD = b.dataset.p; $$("#mk-period button").forEach((x) => x.setAttribute("aria-pressed", x === b)); summary(); board(); }));
      $$("#hm-mkt button").forEach((b) => b.addEventListener("click", () => { HM = b.dataset.m; $$("#hm-mkt button").forEach((x) => x.setAttribute("aria-pressed", x === b)); heat(); }));
      $$("#hm-per button").forEach((b) => b.addEventListener("click", () => { HP = b.dataset.p; $$("#hm-per button").forEach((x) => x.setAttribute("aria-pressed", x === b)); heat(); }));
      let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if ($("#v-market").classList.contains("on")) heat(); }, 200); });
    }
    summary(); board(); heat(); calendar();
  }
  return { render };
})();
