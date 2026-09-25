// 이번 주 작업 패널 — 주차 설정부터 발표자료까지 순서대로.
const Tasks = (() => {
  let W = null, M = "KR", ST = null, PICK = null, FIN = null, MODE = "manual", job = null, poll = null;
  let CUSTOM = { market: "KR", ticker: "", name: "" };
  const MK = { KR: "한국", US: "미국" };
  const STAGE_KO = { analyst: "Analyst", bull: "Bull", bear: "Bear", trader: "Trader" };
  const today = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);   // KST 날짜

  async function refresh() {
    W = await api("/api/week");
    const wk = W.current.week_no;
    ST = await api(`/api/runs/${wk}/${M}/status`);
    PICK = ST.picked ? (await api(`/api/runs/${wk}/${M}`)).pick : null;
    FIN = await api(`/api/week/${wk}/final`);
    render();
  }

  function head() {
    const c = W.current, mk = c.markets[M];
    $("#p-wk").textContent = `${c.week_no}주차`;
    const s = stateOf(), busy = job && job.status === "running";
    $("#p-cta").innerHTML = s.stale
      ? `<button class="btn primary" data-a="start" ${busy ? "disabled" : ""}>${W.suggested.week_no}주차 추천 시작</button><span class="hint" style="margin:0">주차 설정 → 스크리닝(한국·미국) → ${MODE === "manual" ? "Analyst 프롬프트 생성" : "에이전트 auto"}</span>`
      : !ST.screened
        ? `<button class="btn primary" data-a="start" ${busy ? "disabled" : ""}>${c.week_no}주차 추천 시작</button><span class="hint" style="margin:0">스크리닝(한국·미국) → ${MODE === "manual" ? "Analyst 프롬프트 생성" : "에이전트 auto"}</span>`
        : "";
    $("#p-dates").innerHTML = `<span>매수 <b class="num">${md(mk.buy_date)}</b> ${dow(mk.buy_date)} 시가</span><span>매도 <b class="num">${md(mk.sell_date)}</b> ${dow(mk.sell_date)} 종가</span><span>${mk.holding_days ?? "?"}영업일</span>`;
  }

  function stateOf() {
    const c = W.current, sug = W.suggested;
    const stale = sug.week_no > c.week_no;
    const agentsDone = ST.stages.every((s) => s.response);
    const sellPassed = today() > c.markets[M].sell_date;
    return { stale, agentsDone, sellPassed };
  }

  function render() {
    head();
    const c = W.current, sug = W.suggested, s = stateOf();
    const busy = job && job.status === "running";
    const dis = busy ? "disabled" : "";
    const settled = App.runs.find((r) => r.week === c.week_no && r.market === M)?.settled;
    const items = [];

    // 1 주차
    items.push({ done: !s.stale, h: "주차 설정", body: s.stale
      ? `<p>오늘 기준으로는 <b>${sug.week_no}주차</b>(${md(sug.buy_date)} ~ ${md(sug.sell_date)})를 준비할 때다. 지금 설정은 ${c.week_no}주차.</p>
         <div class="acts"><button class="btn primary sm" data-a="week-new" ${dis}>${sug.week_no}주차로 넘기기</button></div>`
      : `<p>${c.week_no}주차로 설정되어 있다. 휴장이 끼면 시장별 날짜를 고친다.</p><div class="acts"><button class="btn sm ghost" data-a="week-edit" ${dis}>날짜 고치기</button></div>` });

    // 2 스크리닝
    items.push({ done: ST.screened, h: "스크리닝", tag: ST.screened ? `<span class="tag done">후보 ${ST.n_candidates}</span>` : "",
      body: `<p>${MK[M]} 전종목에서 필터 1~4를 적용한다. 시세만 받고 LLM은 쓰지 않는다.</p>
        <div class="acts"><button class="btn sm ${ST.screened ? "ghost" : "primary"}" data-a="screener" ${dis}>${ST.screened ? "다시 스크리닝" : "스크리닝 실행"}</button>
        ${ST.screened ? `<button class="btn sm ghost" data-a="files">후보 보기</button>` : ""}</div>` });

    // 3 에이전트
    const rows = ST.stages.map((x) => {
      const state = x.response ? `<span class="tag done">응답 저장</span>` : x.rendered ? `<span class="tag mark">붙여넣기 대기</span>` : `<span class="muted">—</span>`;
      const acts = MODE === "manual" && x.rendered && !x.response
        ? `<span><button class="btn sm" data-a="copy" data-s="${x.stage}">프롬프트 복사</button> <button class="btn sm primary" data-a="paste" data-s="${x.stage}">응답 붙여넣기</button></span>`
        : x.response ? `<button class="btn sm ghost" data-a="view" data-s="${x.stage}">보기</button>` : "<span></span>";
      return `<div><b>${STAGE_KO[x.stage]}</b>${state}${acts}</div>`;
    }).join("");
    const nxt = ST.next_stage, nxtRendered = ST.stages.find((x) => x.stage === nxt)?.rendered;
    items.push({ done: s.agentsDone, h: "에이전트", body: `
      <div class="acts" style="margin-top:6px"><div class="seg" role="group" aria-label="실행 방식">
        <button data-a="mode" data-m="manual" aria-pressed="${MODE === "manual"}">manual</button><button data-a="mode" data-m="auto" aria-pressed="${MODE === "auto"}">auto</button></div></div>
      <p>${MODE === "manual" ? "프롬프트를 Claude 웹의 <b>새 대화</b>에 붙여넣고, 응답 전문을 다시 붙여넣는다. API를 쓰지 않는다."
        : "공용 LLM API로 4단계를 자동 호출한다. 실행 전에 한 번 더 확인한다."}</p>
      <div class="stages">${rows}</div>
      <div class="acts">${!ST.screened ? `<span class="muted" style="font-size:12px">스크리닝을 먼저 한다</span>`
        : s.agentsDone ? "" : MODE === "manual"
          ? `<button class="btn sm primary" data-a="render" ${dis || (nxtRendered ? "disabled" : "")}>${nxt ? `${STAGE_KO[nxt]} 프롬프트 만들기` : ""}</button>`
          : `<button class="btn sm primary" data-a="auto" ${dis}>auto 실행</button>`}</div>` });

    // 4 추천
    items.push({ done: !!PICK, h: "추천 확정", body: PICK ? `<div class="pickbox"><div class="nm">${esc(PICK.name)} <span class="mono muted" style="font-size:12px">${esc(PICK.ticker)}</span></div>
        <dl><dt>예상</dt><dd class="mono">${PICK.expected_return_range?.join("% ~ ")}%</dd><dt>촉매</dt><dd>${esc(PICK.catalyst)}</dd><dt>리스크</dt><dd>${esc(PICK.primary_risk)}</dd></dl></div>
        <div class="acts"><button class="btn sm" data-a="copy-pick">제출용 문구 복사</button></div>`
      : `<p>Trader 응답이 저장되면 최종 종목이 여기에 뜬다.</p>` });

    // 5 최종 선택 (주차당 1종목 — 시장 토글과 무관)
    items.push({ done: !!FIN.final, h: "최종 선택", tag: `<span class="tag off">주차 공통</span>`, body: finalBody(dis) });

    // 6 정산
    const fs = FIN.settled, anyPassed = ["KR", "US"].some((m) => today() > c.markets[m].sell_date);
    items.push({ done: !!(fs || settled), h: "정산",
      tag: fs ? `<span class="tag ${+fs.ret_pct >= 0 ? "done" : "fail"} mono">제출 ${pct(+fs.ret_pct)}</span>` : settled ? `<span class="tag ${+settled.ret_pct >= 0 ? "done" : "fail"} mono">${MK[M]} ${pct(+settled.ret_pct)}</span>` : "",
      body: `<p>에이전트 추천(한국·미국), 후보 20종목, 최종 선택을 한 번에 정산한다. ${anyPassed ? "" : `${md(c.markets.KR.sell_date)} 장 마감 뒤에 할 수 있다.`}</p>
        <div class="acts"><button class="btn sm ${fs ? "ghost" : "primary"}" data-a="settle" ${dis || (!anyPassed ? "disabled" : "")}>${fs || settled ? "다시 정산" : "정산 실행"}</button></div>` });

    // 7 발표자료
    const deckRow = (d, mk) => `<div class="acts"><span class="mono" style="font-size:11.5px;flex:1">${esc(d)}</span><button class="btn sm" data-a="open" data-mk="${mk}" data-f="${esc(d)}">PowerPoint로 열기</button><a class="btn sm ghost" href="/api/runs/${c.week_no}/${mk}/file/${encodeURIComponent(d)}?download=true">받기</a></div>`;
    items.push({ done: ST.decks.length + ST.final_decks.length > 0, h: "발표자료", body: `<p>8장짜리 pptx. 최종 선택이 직접 지정 종목이면 에이전트 과정 대신 '에이전트 추천 vs 내 선택'과 선택 사유를 넣는다.</p>
      <div class="acts"><label style="display:inline-flex;gap:6px;align-items:center;font-size:12.5px"><input type="checkbox" id="deck-full"> 발표용</label>
        <button class="btn sm primary" data-a="deck-final" ${dis || (!FIN.final ? "disabled" : "")}>최종 선택으로 만들기</button>
        <button class="btn sm ghost" data-a="deck" ${dis || (!PICK ? "disabled" : "")}>${MK[M]} 에이전트 추천으로</button></div>
      ${ST.final_decks.map((d) => deckRow(d, "final")).join("")}${ST.decks.map((d) => deckRow(d, M)).join("")}` });

    const nowIdx = items.findIndex((x) => !x.done);
    $("#tasks").innerHTML = items.map((x, i) => `<li class="task ${i === nowIdx ? "now" : ""}"><div class="no">${String(i + 1).padStart(2, "0")}</div>
      <div class="bd"><h3>${x.h} ${x.done ? `<span class="tag done">완료</span>` : ""} ${x.tag || ""}</h3>${x.body}</div></li>`).join("");
  }

  function finalBody(dis) {
    const o = FIN.options, f = FIN.final;
    const kr = o.find((x) => x.id === "KR"), us = o.find((x) => x.id === "US");
    const cur = f?.choice || (kr ? "KR" : us ? "US" : "custom");
    const opt = (id, n, lab, x) => x ? `<option value="${id}" ${cur === id ? "selected" : ""}>${n}) ${lab}: ${esc(x.name)} ${esc(x.ticker)}</option>` : "";
    const saved = f ? `<div class="pickbox" style="margin-top:8px"><div style="font-size:12px;color:var(--ink-3)">이번 주 제출 종목</div><div class="nm">${esc(f.name)} <span class="mono muted" style="font-size:12px">${esc(f.ticker)}</span></div>
        <dl><dt>경로</dt><dd>${f.choice === "custom" ? `직접 지정 (${MK[f.market]})` : `에이전트 추천 (${MK[f.market]})`}</dd>${f.note ? `<dt>사유</dt><dd>${esc(f.note)}</dd>` : ""}
        ${FIN.settled ? `<dt>결과</dt><dd class="mono ${sign(+FIN.settled.ret_pct)}">${pct(+FIN.settled.ret_pct)}</dd>` : ""}</dl></div>` : "";
    return `<p>과제에는 한 주에 1종목만 낸다. 에이전트 추천 중에서 고르거나, 추천 과정 없이 직접 지정한다. 직접 지정한 종목도 정산·발표자료·성과에 들어간다.</p>
      <div style="display:grid;gap:8px;margin-top:10px">
        <select class="sel" id="fin-sel" aria-label="최종 선택">${opt("KR", 1, "최종 추천안", kr)}${opt("US", 2, "차선책", us)}<option value="custom" ${cur === "custom" ? "selected" : ""}>3) 그 외 사용자 지정</option></select>
        <div id="fin-custom" style="display:${cur === "custom" ? "grid" : "none"};gap:6px;grid-template-columns:90px 1fr">
          <select class="sel" id="fin-mk">${["KR", "US"].map((m) => `<option value="${m}" ${(f?.choice === "custom" ? f.market : CUSTOM.market) === m ? "selected" : ""}>${MK[m]}</option>`).join("")}</select>
          <input class="in" id="fin-q" placeholder="종목명 또는 코드/티커" value="${esc(f?.choice === "custom" ? `${f.name} ${f.ticker}` : CUSTOM.ticker ? `${CUSTOM.name} ${CUSTOM.ticker}` : "")}" autocomplete="off">
          <div></div><div id="fin-hits" class="hits"></div></div>
        <textarea class="in" id="fin-note" rows="2" placeholder="선택 사유 (발표자료에 들어간다)" style="font-family:var(--sans)">${esc(f?.note || "")}</textarea>
        <div class="acts" style="margin:0"><button class="btn sm primary" data-a="final-save" ${dis}>${f ? "최종 선택 바꾸기" : "최종 선택 저장"}</button></div>
      </div>${saved}`;
  }
  async function saveFinal() {
    const choice = $("#fin-sel").value, body = { choice, note: $("#fin-note").value };
    if (choice === "custom") {
      if (!CUSTOM.ticker && FIN.final?.choice === "custom") Object.assign(CUSTOM, { market: FIN.final.market, ticker: FIN.final.ticker, name: FIN.final.name });
      if (!CUSTOM.ticker) { toast("검색 결과에서 종목을 고른다"); return; }
      Object.assign(body, { market: CUSTOM.market, ticker: CUSTOM.ticker, name: CUSTOM.name });
    }
    try { FIN = await api(`/api/week/${W.current.week_no}/final`, { method: "POST", body }); toast("최종 선택을 저장했다"); render(); }
    catch (e) { toast(e.message); }
  }
  let lk;
  $("#tasks").addEventListener("input", (e) => {
    if (e.target.id !== "fin-q") return;
    clearTimeout(lk);
    lk = setTimeout(async () => {
      const q = e.target.value.trim(), mk = $("#fin-mk").value;
      if (!q) { $("#fin-hits").innerHTML = ""; return; }
      const hits = await api(`/api/lookup?market=${mk}&q=${encodeURIComponent(q)}`).catch(() => []);
      $("#fin-hits").innerHTML = hits.map((h) => `<button type="button" data-a="pick-hit" data-t="${esc(h.ticker)}" data-n="${esc(h.name)}">${esc(h.name)} <span class="mono muted">${esc(h.ticker)}</span></button>`).join("") || `<span class="muted" style="font-size:12px">결과 없음</span>`;
    }, 250);
  });
  $("#tasks").addEventListener("change", (e) => {
    if (e.target.id === "fin-sel") $("#fin-custom").style.display = e.target.value === "custom" ? "grid" : "none";
    if (e.target.id === "fin-mk") { CUSTOM = { market: e.target.value, ticker: "", name: "" }; $("#fin-q").value = ""; $("#fin-hits").innerHTML = ""; }
  });

  async function startWeek() {
    const chain = async () => {
      const j = await run("screener", { market: null });
      if (j?.status !== "ok") return;
      if (MODE === "auto") confirmAuto(true);
      else await run("agents_manual", { market: null });
    };
    if (stateOf().stale) openWeek(true, chain); else chain();
  }

  // ---------------------------------------------------------------- 동작
  async function run(kind, extra = {}) {
    try {
      job = await api("/api/jobs", { method: "POST", body: { kind, market: M, week: W.current.week_no, ...extra } });
    } catch (e) { toast(e.message); return null; }
    $("#c-title").textContent = job.label; $("#c-st").textContent = "실행 중"; $("#c-out").textContent = "";
    render();
    return new Promise((res) => {
      let n = 0;
      clearInterval(poll);
      poll = setInterval(async () => {
        const j = await api(`/api/jobs/${job.id}?since=${n}`);
        n = j.n;
        if (j.lines.length) { $("#c-out").textContent += j.lines.join("\n") + "\n"; $("#c-out").scrollTop = 1e9; }
        if (j.status !== "running") {
          clearInterval(poll); job = j;
          $("#c-st").textContent = j.status === "ok" ? "완료" : `실패 (코드 ${j.rc})`;
          await loadRuns(); await refresh(); res(j);
        }
      }, 700);
      setTimeout(loadRuns, 2500);          // 새 주차 폴더가 생기면 도면이 따라가게
    });
  }

  function weekForm(v) {
    const mk = (m) => `<div class="h">${MK[m]}</div>
      <label>매수 <input class="in num" type="date" name="${m}-buy" value="${v.markets[m].buy_date}"></label>
      <label>매도 <input class="in num" type="date" name="${m}-sell" value="${v.markets[m].sell_date}"></label>
      <div></div><label>보유 영업일 <input class="in num" type="number" min="1" max="5" name="${m}-days" value="${v.markets[m].holding_days ?? 5}"></label><div class="hint" style="margin:0">${(W.suggested.markets[m].holidays || []).join(", ") || "제안 주간에 알려진 휴장 없음"}</div>`;
    return `<form class="form" id="wf">
      <div class="h">주차</div><label><input class="in num" type="number" name="week_no" value="${v.week_no}"></label><div></div>
      <div class="h">공통</div><label>매수 <input class="in num" type="date" name="buy" value="${v.buy_date}"></label><label>매도 <input class="in num" type="date" name="sell" value="${v.sell_date}"></label>
      ${mk("KR")}${mk("US")}
      <div class="h">매크로 일정</div><div style="grid-column:span 2"><textarea class="in" name="macro" rows="3" placeholder="평가 기간에 걸리는 일정만 적는다 (FOMC, CPI, 실적 등)">${esc(v.macro ?? W.current.macro_calendar)}</textarea></div>
    </form><p class="hint">휴장일 표는 <code>app/tasks.py</code>의 HOLIDAYS에 있다. '확인 필요'가 붙은 날은 KRX 공지로 확인할 것. 저장하면 config.yaml의 run·markets 값만 바뀌고 주석은 그대로 남는다.</p>`;
  }
  function openWeek(fromSuggest, after) {
    const v = fromSuggest ? { ...W.suggested, macro: "" } : W.current;
    Modal.open(fromSuggest ? `${W.suggested.week_no}주차 설정` : "주차 날짜 고치기", weekForm(v),
      `<button class="btn ghost" data-close>취소</button><button class="btn primary" id="wf-save">저장</button>`);
    $("#wf-save").onclick = async () => {
      const f = new FormData($("#wf"));
      const body = { week_no: +f.get("week_no"), buy_date: f.get("buy"), sell_date: f.get("sell"), macro_calendar: f.get("macro"),
        markets: Object.fromEntries(["KR", "US"].map((m) => [m, { buy_date: f.get(`${m}-buy`), sell_date: f.get(`${m}-sell`), holding_days: +f.get(`${m}-days`) }])) };
      try { await api("/api/week", { method: "POST", body }); Modal.close(); toast("config.yaml에 저장했다"); await refresh(); if (after) after(); }
      catch (e) { toast(e.message); }
    };
  }

  function openPaste(stage) {
    Modal.open(`${STAGE_KO[stage]} 응답 붙여넣기`, `<p style="margin:0 0 8px;font-size:13px">Claude 웹 응답의 <b>전문</b>을 붙여넣는다. 앞 단계와 같은 대화에서 받은 응답이면 안 된다.</p>
      <textarea class="in" id="resp" rows="16" placeholder="응답 전문"></textarea>${stage === "trader" ? `<p class="hint">JSON 블록이 들어 있어야 최종 종목이 확정된다.</p>` : ""}`,
      `<button class="btn ghost" data-close>취소</button><button class="btn primary" id="resp-save">저장</button>`, true);
    $("#resp-save").onclick = async () => {
      try {
        const r = await api(`/api/runs/${W.current.week_no}/${M}/response/${stage}`, { method: "POST", body: { text: $("#resp").value } });
        Modal.close(); toast(r.warning || "저장했다"); await refresh(); await loadRuns();
      } catch (e) { toast(e.message); }
    };
  }

  function confirmAuto(all = false) {
    Modal.open("auto 실행 확인", `<p style="margin:0">공용 LLM API(Letsur 게이트웨이)로 ${all ? "한국·미국" : MK[M]} 4단계를 호출한다.</p>
      <div class="warn">wk4 실측: 시장 하나에 입력 약 64만 토큰, 약 1 unit. 이미 응답이 있는 단계는 건너뛴다. 공용 키이므로 필요할 때만 쓴다.</div>
      <p class="hint">진행 상황은 왼쪽 도면에 실시간으로 뜬다 (검색어 포함).</p>`,
      `<button class="btn ghost" data-close>취소</button><button class="btn primary" id="auto-go">API 사용에 동의하고 실행</button>`);
    $("#auto-go").onclick = () => { Modal.close(); run("agents_auto", { confirm_api: true, ...(all ? { market: null } : {}) }); };
  }

  async function copyPrompt(stage) {
    const t = await api(`/api/runs/${W.current.week_no}/${M}/text/prompt/${stage}`);
    copy(t);
  }
  function copyPick() {
    const p = PICK, c = W.current;
    copy(`[${c.week_no}주차 ${MK[M]}] ${p.name}(${p.ticker})\n근거: ${(p.thesis || []).join(" / ")}\n촉매: ${p.catalyst}\n예상 범위: ${p.expected_return_range?.join("% ~ ")}%\n주요 리스크: ${p.primary_risk}`);
  }

  $("#tasks").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-a]"); if (!b || b.disabled) return;
    const a = b.dataset.a, s = b.dataset.s;
    if (a === "start") startWeek();
    else if (a === "final-save") saveFinal();
    else if (a === "pick-hit") { CUSTOM = { market: $("#fin-mk").value, ticker: b.dataset.t, name: b.dataset.n }; $("#fin-q").value = `${b.dataset.n} ${b.dataset.t}`; $("#fin-hits").innerHTML = ""; }
    else if (a === "deck-final") run("deck_final", { full: $("#deck-full").checked, market: null });
    else if (a === "week-new") openWeek(true);
    else if (a === "week-edit") openWeek(false);
    else if (a === "screener") run("screener");
    else if (a === "mode") { MODE = b.dataset.m; render(); }
    else if (a === "render") run("agents_manual");
    else if (a === "auto") confirmAuto();
    else if (a === "copy") copyPrompt(s);
    else if (a === "paste") openPaste(s);
    else if (a === "view") Files.open(W.current.week_no, M, `responses/${s}.md`);
    else if (a === "copy-pick") copyPick();
    else if (a === "settle") {                // 에이전트 추천 → 후보군 → 최종 선택 순서로 이어서 정산
      await run("settle", { market: null });
      await run("cohort", { market: null });
      if (FIN.final) await run("final_settle", { market: null });
    }
    else if (a === "deck") run("deck", { full: $("#deck-full").checked });
    else if (a === "open") api(`/api/runs/${W.current.week_no}/${b.dataset.mk || M}/open/${encodeURIComponent(b.dataset.f)}`, { method: "POST" }).then(() => toast("PowerPoint로 열었다")).catch((x) => toast(x.message));
    else if (a === "files") Files.open(W.current.week_no, M, "candidates.csv");
  });
  $("#p-cta").addEventListener("click", (e) => { if (e.target.closest("[data-a='start']") && !e.target.disabled) startWeek(); });
  $$("#p-mkt button").forEach((b) => b.addEventListener("click", () => {
    M = b.dataset.m; $$("#p-mkt button").forEach((x) => x.setAttribute("aria-pressed", x === b)); refresh();
  }));
  $("#p-files").addEventListener("click", () => Files.open(W.current.week_no, M));

  return { refresh, week: () => W };
})();

// ---------------------------------------------------------------- 산출물 모달
const Files = (() => {
  let W, M, list = [];
  const label = { deck: "발표", image: "차트", text: "문서", table: "표", data: "데이터", file: "파일" };
  async function open(week, market, select) {
    W = week; M = market;
    list = await api(`/api/runs/${week}/${market}/files`).catch(() => []);
    if (!list.length) { Modal.open(`산출물  wk${week} ${market}`, `<p>아직 산출물이 없다. 스크리닝부터 실행한다.</p>`); return; }
    const order = { deck: 0, image: 1, table: 2, text: 3, data: 4, file: 5 };
    list.sort((a, b) => order[a.kind] - order[b.kind] || a.path.localeCompare(b.path));
    Modal.open(`산출물  ${week}주차 ${market === "KR" ? "한국" : "미국"}`, `<div class="files"><ul>${list.map((f, i) => `<li><button data-i="${i}"><span>${esc(f.path)}</span><span class="k">${label[f.kind]}</span></button></li>`).join("")}</ul><div class="pv" id="pv"></div></div>`, "", true);
    $("#m-body").style.padding = "0";
    $$("#m-body [data-i]").forEach((b) => b.addEventListener("click", () => show(+b.dataset.i)));
    show(Math.max(0, list.findIndex((f) => f.path === select)));
  }
  async function show(i) {
    const f = list[i], url = `/api/runs/${W}/${M}/file/${encodeURIComponent(f.path).replace(/%2F/g, "/")}`;
    $$("#m-body [data-i]").forEach((b) => b.setAttribute("aria-current", +b.dataset.i === i));
    const pv = $("#pv");
    const meta = `<p class="hint" style="margin:0 0 10px">${f.mtime.replace("T", " ")}  ${(f.size / 1024).toFixed(1)} KB  <a href="${url}?download=true">받기</a></p>`;
    if (f.kind === "deck") {
      pv.innerHTML = meta + `<p>pptx는 브라우저에서 바로 보이지 않는다. 이 PC의 PowerPoint로 연다.</p><button class="btn primary" id="pv-open">PowerPoint로 열기</button>`;
      $("#pv-open").onclick = () => api(`/api/runs/${W}/${M}/open/${encodeURIComponent(f.path)}`, { method: "POST" }).then(() => toast("열었다")).catch((e) => toast(e.message));
    } else if (f.kind === "image") pv.innerHTML = meta + `<img src="${url}" alt="${esc(f.path)}">`;
    else if (f.kind === "table") {
      const t = await (await fetch(url)).text();
      const rows = t.replace(/^﻿/, "").trim().split(/\r?\n/).map((l) => l.split(","));
      pv.innerHTML = meta + `<table class="tb"><thead><tr>${rows[0].map((h) => `<th class="l">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.slice(1).map((r) => `<tr>${r.map((c) => `<td class="l mono">${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    } else {
      const t = await (await fetch(url)).text();
      pv.innerHTML = meta + (f.path.endsWith(".md") && window.marked ? `<div class="doc">${marked.parse(t)}</div>` : `<pre>${esc(t)}</pre>`);
    }
  }
  return { open };
})();
