// 계좌 — 한국투자증권 연결 상태, 잔고, 비중, 브리핑, 주문 초안
const Account = (() => {
  const won = (v) => (v == null ? "—" : `${Math.round(v).toLocaleString()}원`);

  function setup(s) {
    return `<section class="blk"><header><h2>연결 설정</h2><p>${s.configured ? "키가 입력되어 있다" : "아직 비어 있다"}</p></header><div class="bd setup">
      <dl class="kv"><dt>증권사</dt><dd>한국투자증권 Open API</dd><dt>환경</dt><dd>${s.env === "real" ? "실전" : "모의투자"}</dd>
        <dt>APP KEY</dt><dd>${s.app_key || "<span class='muted'>비어 있음</span>"}</dd><dt>APP SECRET</dt><dd>${s.app_secret || "<span class='muted'>비어 있음</span>"}</dd>
        <dt>계좌</dt><dd>${s.account_no || "<span class='muted'>비어 있음</span>"}</dd><dt>주문</dt><dd>${s.order_enabled ? "허용" : "차단"}</dd></dl>
      ${s.configured ? "" : `<ol><li>한국투자증권 KIS Developers에서 앱을 등록해 APP KEY / APP SECRET을 받는다. 처음에는 <b>모의투자</b> 계좌로 받는다.</li>
        <li>저장소 루트의 <code>.env</code>에 <code>KIS_APP_KEY</code>, <code>KIS_APP_SECRET</code>, <code>KIS_ACCOUNT_NO</code>(예: 12345678-01)를 채운다. <code>KIS_ENV=paper</code>는 그대로 둔다.</li>
        <li>서버를 다시 켜고 이 화면에서 잔고 새로고침을 누른다.</li></ol>
        <p class="hint">키는 .env에만 둔다. 이 화면은 키를 입력받지 않고, 저장소에도 올라가지 않는다 (.gitignore). 한국투자증권이 막히면 같은 인터페이스(<code>src/broker/base.py</code>)로 토스 구현을 붙인다.</p>`}
    </div></section>`;
  }

  function spark(hist) {
    if (hist.length < 2) return `<p class="muted">조회 기록이 2건 이상 쌓이면 평가금액 추이가 그려진다.</p>`;
    const W = 560, H = 120, vs = hist.map((h) => h.total), lo = Math.min(...vs), hi = Math.max(...vs) || 1;
    const x = (i) => (i / (hist.length - 1)) * W, y = (v) => H - 8 - ((v - lo) / (hi - lo || 1)) * (H - 16);
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto"><polyline fill="none" stroke="#121416" stroke-width="1.6" points="${vs.map((v, i) => `${x(i)},${y(v)}`).join(" ")}"/>
      <text x="0" y="10" style="font:10px var(--mono);fill:#8a9095">${won(hi)}</text><text x="0" y="${H}" style="font:10px var(--mono);fill:#8a9095">${won(lo)}</text></svg>`;
  }

  async function render() {
    const a = await api("/api/account");
    const s = a.settings, last = a.last, b = a.briefing;
    let html = `<div class="page-hd"><h1>계좌</h1><div style="display:flex;gap:8px;align-items:center">
      ${last ? `<span class="muted" style="font-size:12.5px">마지막 조회 ${last.fetched_at.replace("T", " ").slice(0, 16)}</span>` : ""}
      <button class="btn primary" id="ac-refresh" ${s.configured ? "" : "disabled"}>잔고 새로고침</button></div></div>`;
    if (last) {
      const pos = last.positions;
      html += `<div class="figs" style="grid-template-columns:repeat(4,1fr)">
        <div class="fig"><div class="l">총평가</div><div class="v">${won(last.total_krw)}</div><div class="s">${last.env === "real" ? "실전" : "모의투자"}</div></div>
        <div class="fig"><div class="l">예수금</div><div class="v">${won(last.cash_krw)}</div><div class="s">현금 비중 ${last.total_krw ? (last.cash_krw / last.total_krw * 100).toFixed(1) : "—"}%</div></div>
        <div class="fig"><div class="l">평가손익</div><div class="v ${sign(last.pnl_krw)}">${won(last.pnl_krw)}</div><div class="s">국내 합계</div></div>
        <div class="fig"><div class="l">보유 종목</div><div class="v">${pos.length}</div><div class="s">국내 ${pos.filter((p) => p.currency === "KRW").length} / 해외 ${pos.filter((p) => p.currency === "USD").length}</div></div></div>
      <div class="two"><section class="blk"><header><h2>브리핑</h2><p>조회값으로만 쓴 요약 (LLM 없음)</p></header><div class="bd"><ol class="brief">${b.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ol></div></section>
        <section class="blk"><header><h2>평가금액 추이</h2><p>새로고침할 때마다 한 점</p></header><div class="bd">${spark(a.history)}</div></section></div>
      <section class="blk"><header><h2>보유 종목</h2><p>비중은 국내 총평가 기준</p></header><div class="bd"><table class="tb"><thead><tr><th>종목</th><th>수량</th><th>평단</th><th>현재가</th><th>평가금액</th><th>손익률</th><th class="l">비중</th></tr></thead><tbody>
        ${pos.map((p) => { const w = b.weights.find((x) => x.ticker === p.ticker); return `<tr><td>${esc(p.name)} <span class="muted mono">${p.ticker}</span></td><td>${p.qty}</td><td>${p.avg_price.toLocaleString()}</td><td>${p.price.toLocaleString()}</td>
          <td>${p.value.toLocaleString()} ${p.currency === "USD" ? "$" : ""}</td><td class="${sign(p.pnl_pct)}">${pct(p.pnl_pct)}</td><td class="l">${w ? `<div style="display:flex;gap:8px;align-items:center"><div class="bar" style="flex:1"><i style="width:${w.weight}%"></i></div><span class="mono" style="font-size:11.5px">${w.weight.toFixed(1)}%</span></div>` : "<span class='muted'>해외</span>"}</td></tr>`; }).join("")}
      </tbody></table></div></section>`;
    } else {
      html += `<div class="figs" style="grid-template-columns:repeat(4,1fr)">${["총평가", "예수금", "평가손익", "보유 종목"].map((l) => `<div class="fig"><div class="l">${l}</div><div class="v muted">—</div><div class="s">연결 후 표시</div></div>`).join("")}</div>`;
    }
    html += `<section class="blk"><header><h2>이번 주 추천 매수 초안</h2><p>주문은 보내지 않는다. 수량과 금액만 계산한다</p></header><div class="bd">
      <div style="display:flex;gap:8px;align-items:end;flex-wrap:wrap"><label style="font-size:12.5px">시장<br><select class="sel" id="pl-m"><option value="KR">한국</option><option value="US">미국</option></select></label>
      <label style="font-size:12.5px">투입 금액<br><input class="in num" id="pl-b" type="number" value="1000000" step="100000" style="width:160px"></label><button class="btn" id="pl-go">초안 만들기</button></div><div id="pl-out" style="margin-top:12px"></div></div></section>`;
    html += setup(s);
    $("#acct").innerHTML = html;
    $("#ac-refresh")?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "조회 중";
      try { await api("/api/account/refresh", { method: "POST" }); toast("잔고를 받아왔다"); render(); }
      catch (x) { toast(x.message); e.target.disabled = false; e.target.textContent = "잔고 새로고침"; }
    });
    $("#pl-go").addEventListener("click", async () => {
      const wk = Tasks.week()?.current.week_no;
      try {
        const p = await api("/api/account/plan", { method: "POST", body: { week: wk, market: $("#pl-m").value, budget: +$("#pl-b").value } });
        $("#pl-out").innerHTML = `<dl class="kv"><dt>종목</dt><dd>${esc(p.name)} ${p.ticker}</dd><dt>수량</dt><dd>${p.qty}주 (시장가)</dd><dt>기준가</dt><dd>${p.ref_price.toLocaleString()} ${p.currency}</dd>
          <dt>예상 금액</dt><dd>${p.est_amount.toLocaleString()} ${p.currency}</dd><dt>확인 문구</dt><dd>${p.confirm_phrase}</dd></dl><p class="hint">${esc(p.note)} ${esc(p.why_not)}</p>`;
      } catch (x) { $("#pl-out").innerHTML = `<p class="warn">${esc(x.message)}</p>`; }
    });
  }
  return { render };
})();
