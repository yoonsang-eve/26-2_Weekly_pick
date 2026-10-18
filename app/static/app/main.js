// 라우팅, 시계, 초기화
(() => {
  const views = { agent: "#v-agent", desk: "#v-desk", market: "#v-market", perf: "#v-perf", account: "#v-account", guide: "#v-guide" };
  function route() {
    const v = (location.hash || "#agent").slice(1);
    let id = views[v] ? v : "agent";
    if (App.viewer && id === "account") id = "agent";            // 보기 전용 링크에는 계좌가 없다
    $$(".view").forEach((el) => el.classList.toggle("on", `#${el.id}` === views[id]));
    $$(".nav a").forEach((a) => (a.dataset.view === id ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    if (id === "agent") Agent.render();
    if (id === "perf") Perf.render();
    if (id === "market") Market.render();
    if (id === "account") Account.render();
    if (id === "guide") Guide.render();
  }
  addEventListener("hashchange", route);

  // KST 시계와 추천 마감 (다음 평가주 월요일 09:00 장 시작 전)
  function tick() {
    const now = new Date(Date.now() + 9 * 3600e3);
    $("#kst").textContent = now.toISOString().slice(11, 16);
    const W = Tasks.week();
    if (!W) return;
    const buy = W.current.markets.KR.buy_date;
    const due = new Date(`${buy}T09:00:00+09:00`).getTime() - Date.now();
    $("#deadline").innerHTML = due > 0
      ? `${W.current.week_no}주차 추천 마감(월 09:00 개장)까지 <span class="dday">${Math.floor(due / 864e5)}일 ${Math.floor((due % 864e5) / 36e5)}시간</span>`
      : `${W.current.week_no}주차 평가 진행 중`;
  }

  async function init() {
    const info = await api("/api/app").catch(() => ({}));
    App.viewer = info.viewer;
    if (info.static && info.built_at) $("#built").innerHTML = `업데이트 <strong class="num">${md(info.built_at.slice(0, 10))} ${info.built_at.slice(11, 16)}</strong>`;
    if (App.viewer) {
      document.body.classList.add("viewer");
      $('.nav a[data-view="account"]')?.remove();
      $(".panel-hd .wk span").textContent = "이번 주 진행 상황";
    }
    await loadRuns();
    await Tasks.refresh();
    Plan.fill();
    Plan.load(Plan.pickDefault(Tasks.week().current.week_no));
    route(); tick();
    setInterval(tick, 30000);
    // 새로 돌기 시작한 실행이 있으면 도면이 따라간다 (사용자가 직접 고르지 않았을 때)
    App.onRuns.push(() => {
      Plan.fill();
      const live = App.runs.filter(Plan.isLive).sort((a, b) => b.events_mtime - a.events_mtime)[0];
      const cur = Plan.current();
      if (!Plan.userPicked && live && (!cur || Plan.key(live) !== Plan.key(cur))) Plan.load(live);
    });
    setInterval(loadRuns, 10000);
  }
  init();
})();
