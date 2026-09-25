// 안내 — 처음 보는 사람을 위한 화면 읽는 법 (guide.html)
const Guide = (() => {
  let loaded = false;
  async function render() {
    if (loaded) return;
    $("#guide").innerHTML = await (await fetch(U("/static/app/guide.html"))).text();
    loaded = true;
  }
  return { render };
})();
