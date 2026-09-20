"""Step 2, 3 — Analyst → Bull → Bear → Trader.

시장별로 완전히 독립된 파이프라인을 돌린다. KR에서 1종목, US에서 1종목이 나온다.
두 시장을 한 프롬프트에 섞지 않는다 — 섞으면 LLM이 시장 간 비교를 시작하는데,
그건 이 과제의 판단 기준(5영업일 catalyst 구체성)과 무관한 축이다.

모드:
  auto    config.yaml의 llm 설정으로 4단계를 순차 호출한다.
  manual  프롬프트 txt만 렌더링하고 멈춘다. 사용자가 Claude 웹에 붙여넣고
          응답을 responses/{stage}.md에 저장하면 다음 단계가 그걸 입력으로 받는다.

어느 모드든 bear가 bull에 동조하는 것을 막기 위해 각 단계는 이전 단계의 '응답 텍스트'만
받는다. 대화 히스토리를 누적하지 않는다 (manual이면 매 단계 새 대화에서 실행할 것).

    python src/agents.py                        # config.run.markets 전부, config의 모드
    python src/agents.py --market US             # US만
    python src/agents.py --market KR analyst     # 한 단계만
    python src/agents.py --mode manual           # 모드 강제
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm as llm_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# stage -> 필요한 선행 응답
DEPS = {
    "analyst": [],
    "bull": ["analyst"],
    "bear": ["analyst", "bull"],
    "trader": ["analyst", "bull", "bear"],
}
SEARCH_STAGES = {"analyst", "bear"}   # 사실 검증이 필요한 단계

SEARCH_ON = ("웹 검색 도구를 사용해 사실을 직접 확인하라. "
             "검색으로 확인되지 않은 수치·일정은 쓰지 않는다.\n"
             "이 실행에서 쓸 수 있는 검색 호출은 최대 {n}회다. 한도를 넘기면 그 시점부터 "
             "검색이 아예 실패하므로, 시작 전에 후보 전체에 검색을 어떻게 배분할지 정하고 "
             "움직여라. 한 종목에 여러 번 파고들기보다 전 종목을 한 번씩이라도 훑는 쪽이 "
             "낫고, 한 번의 검색 질의에 여러 종목을 묶어도 된다. "
             "한도가 남지 않아 확인하지 못한 항목은 '확인 불가'로 적는다.")
SEARCH_OFF = ("이 실행에서는 웹 검색 도구를 쓸 수 없다. 따라서 네 학습 데이터에 없는 "
              "최신 사실은 알 수 없다고 전제하라. 날짜·수치·일정을 지어내지 말고, "
              "확인이 필요한 항목은 전부 \"확인 불가(검색 도구 없음)\"로 표기한 뒤 "
              "그 불확실성을 판단에 반영하라.")


# ------------------------------------------------------------- 렌더링 재료

def candidates_table(csv: Path, market: str, mk: dict) -> str:
    df = pd.read_csv(csv, dtype={"ticker": str})  # 국내 티커 앞자리 0 보존
    cap_div = float(mk["cap_divisor"])
    exch = df["exchange"] if "exchange" in df.columns else df["market"]
    if market == "KR":
        price = df["close"].map(lambda v: f"{v:,.0f}")
        cap_col, adv_col = f"시총({mk['cap_unit']})", "20일평균거래대금(억)"
        cap = (df["mktcap"] / cap_div).map(lambda v: f"{v:,.0f}")
        adv = (df["adv20"] / 1e8).map(lambda v: f"{v:,.0f}")
    else:
        price = df["close"].map(lambda v: f"{v:,.2f}")
        cap_col, adv_col = f"시총({mk['cap_unit']})", "20일평균거래대금($M)"
        cap = (df["mktcap"] / cap_div).map(
            lambda v: "확인불가" if pd.isna(v) else f"{v:,.1f}")
        adv = (df["adv20"] / 1e6).map(lambda v: f"{v:,.0f}")

    out = pd.DataFrame({
        "시장": exch, "티커": df["ticker"], "종목명": df["name"],
        "업종": df["sector"].fillna(""),
        f"종가({mk['price_unit']})": price,
        cap_col: cap,
        adv_col: adv,
        "6개월모멘텀(최근1개월제외)": df["ret_mom"].map(lambda v: f"{v:+.1%}"),
        "20일수익률": df["ret20"].map(lambda v: f"{v:+.1%}"),
        "5일수익률": df["ret5"].map(lambda v: f"{v:+.1%}"),
        "52주고점대비": df["pct_from_52wh"].map(lambda v: f"-{v:.1%}"),
        "ATR20/종가": df["atr20_pct"].map(lambda v: f"{v:.1%}"),
    })
    return out.to_markdown(index=False)


def market_dates(cfg: dict, market: str) -> tuple[str, str, int]:
    """(매수일, 매도일, 보유 영업일수). 시장별 휴장을 markets 블록에서 덮어쓴다.

    2026 추석(9/25 금)처럼 한쪽 시장만 휴장하는 주가 있다. 그런 주에 양쪽에 같은
    매도일을 쓰면 프롬프트가 열리지도 않는 날의 종가를 목표로 삼게 된다.
    """
    run, mk = cfg["run"], cfg["markets"][market]
    buy = mk.get("buy_date") or run["buy_date"]
    sell = mk.get("sell_date") or run["sell_date"]
    days = mk.get("holding_days")
    if not days:
        # 기본값은 달력상 영업일 수(주말만 제외). 휴장이 있으면 config에 명시할 것.
        days = len(pd.bdate_range(buy, sell))
    return str(buy), str(sell), int(days)


def base_values(cfg: dict, market: str, week: int, out: Path,
                web_search: bool, max_searches: int = 0) -> dict[str, str]:
    mk = cfg["markets"][market]
    csv = out / "candidates.csv"
    if not csv.exists():
        raise FileNotFoundError(
            f"{csv.relative_to(ROOT)}가 없다. "
            f"먼저 `python src/screener.py --market {market}`를 실행할 것.")

    meta_f = out / "screen_meta.json"
    ref = (json.loads(meta_f.read_text(encoding="utf-8"))["ref_date"]
           if meta_f.exists() else "(screen_meta.json 없음 — 스크리너를 다시 돌릴 것)")

    macro = (cfg["run"]["macro_calendar"] or "").strip()
    if not macro:
        macro = "(확인되지 않음 — 직접 확인할 것)"
        print("경고: config.yaml의 run.macro_calendar가 비어 있다.\n"
              "      다음 주 FOMC·금통위 등 일정을 채우면 프롬프트 품질이 올라간다.\n")

    return {
        "week_no": str(week),
        "market": market,
        "market_label": mk["label"],
        "market_notes": mk["notes"].strip(),
        "buy_date": market_dates(cfg, market)[0],
        # 휴장이 끼면 시장마다 매도일과 영업일 수가 다르다 (2026 추석이 그 사례).
        "sell_date": market_dates(cfg, market)[1],
        "holding_days": str(market_dates(cfg, market)[2]),
        "ref_date": ref,
        "today": datetime.now().strftime("%Y-%m-%d"),
        "macro_calendar": macro,
        "search_policy": (SEARCH_ON.format(n=max_searches) if web_search
                          else SEARCH_OFF),
        "candidates_table": candidates_table(csv, market, mk),
    }


def render(cfg: dict, stage: str, values: dict, resp: Path) -> str:
    v = dict(values)
    for d in DEPS[stage]:
        v[f"{d}_output"] = (resp / f"{d}.md").read_text(encoding="utf-8")
    text = (ROOT / "prompts" / cfg["meta"]["prompt_version"] / f"{stage}.txt") \
        .read_text(encoding="utf-8")
    for k, val in v.items():
        text = text.replace("{{" + k + "}}", val)
    if "{{" in text:
        print(f"경고: 치환되지 않은 placeholder가 남아 있다: "
              f"{text[text.index('{{'):][:40]}", file=sys.stderr)
    return text


# ------------------------------------------------------------- run_meta

def load_meta(f: Path, week: int, market: str, cfg: dict, mode: str) -> dict:
    if f.exists():
        m = json.loads(f.read_text(encoding="utf-8"))
        m["mode"] = mode
        return m
    return {"week": week, "market": market, "mode": mode,
            "prompt_version": cfg["meta"]["prompt_version"],
            "config_version": cfg["meta"]["config_version"], "stages": {}}


def save_meta(f: Path, meta: dict) -> None:
    f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
        return True
    except Exception:
        return False


# ------------------------------------------------------------ trader JSON

JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def extract_pick(text: str) -> dict | None:
    """trader 응답 끝의 JSON을 꺼낸다. 코드블록으로 감싸 오는 경우도 받아 준다."""
    body = text
    fence = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        body = fence[-1]
    else:
        m = JSON_BLOCK.search(body)
        if not m:
            return None
        body = m.group(0)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and obj.get("ticker") else None


# ---------------------------------------------------------------- 실행

def run_manual(cfg: dict, market: str, week: int, out: Path, resp: Path,
               only: str | None) -> int:
    stage = only or next((s for s in DEPS if not (resp / f"{s}.md").exists()), None)
    if stage is None:
        print(f"[{market}] 4단계 응답이 모두 저장되어 있다. 최종 결과는 "
              f"{(resp / 'trader.md').relative_to(ROOT)}")
        return 0

    missing = [d for d in DEPS[stage] if not (resp / f"{d}.md").exists()]
    if missing:
        print(f"[{market}] '{stage}' 단계는 선행 응답이 필요하다. 없는 파일:",
              file=sys.stderr)
        for m in missing:
            print(f"  {(resp / f'{m}.md').relative_to(ROOT)}", file=sys.stderr)
        return 1

    # manual 모드에서는 사람이 Claude 웹에서 검색을 켜고 돌린다는 전제다.
    # manual 모드는 사람이 Claude 웹에서 돌리므로 검색 한도가 사실상 넉넉하다.
    values = base_values(cfg, market, week, out, web_search=True, max_searches=30)
    text = render(cfg, stage, values, resp)
    dst = out / f"rendered_{stage}.txt"
    dst.write_text(text, encoding="utf-8")

    meta_f = out / "run_meta.json"
    meta = load_meta(meta_f, week, market, cfg, "manual")
    meta["stages"][stage] = {
        "rendered_at": datetime.now().isoformat(timespec="seconds"),
        "model": "",  # 발표 자료의 '사용 모델' 항목에 그대로 들어간다
    }
    save_meta(meta_f, meta)

    nxt = [s for s in DEPS if not (resp / f"{s}.md").exists() and s != stage]
    print(f"[{market}/{stage}] 렌더링 완료 — {dst.relative_to(ROOT)} ({len(text):,}자)")
    print("클립보드에 복사됨." if to_clipboard(text) else "클립보드 복사 실패. 파일을 직접 열 것.")
    print(f"""
다음 순서로 진행할 것.
  1. Claude 웹/앱에서 '새 대화'를 연다 (이전 단계 대화를 이어쓰지 말 것)
  2. 붙여넣고 실행한다{' — 웹 검색을 켤 것' if stage in SEARCH_STAGES else ''}
  3. 응답 전문을 {(resp / f'{stage}.md').relative_to(ROOT)} 에 저장한다
  4. python src/agents.py --market {market}   ← {nxt[0] if nxt else '없음'} 단계가 렌더링된다""")
    return 0


def run_auto(cfg: dict, market: str, week: int, out: Path, resp: Path,
             only: str | None, provider) -> int:
    lc = cfg["llm"]
    models = lc["models"]
    max_tokens = int(lc.get("max_tokens") or 16000)
    temperature = lc.get("temperature")
    retries = int(lc.get("max_retries") or 2)
    values = base_values(cfg, market, week, out,
                         web_search=provider.supports_web_search,
                         max_searches=getattr(provider, "max_uses", 0))
    budgets = lc.get("search_budget") or {}

    meta_f = out / "run_meta.json"
    meta = load_meta(meta_f, week, market, cfg, "auto")
    meta["provider"] = provider.name

    stages = [only] if only else list(DEPS)
    for stage in stages:
        f = resp / f"{stage}.md"
        if f.exists() and not only:
            print(f"  [{market}/{stage}] 이미 응답이 있다 — 건너뛴다")
            continue
        missing = [d for d in DEPS[stage] if not (resp / f"{d}.md").exists()]
        if missing:
            print(f"[{market}] '{stage}'의 선행 응답이 없다: {missing}", file=sys.stderr)
            return 1

        use_search = stage in SEARCH_STAGES and provider.supports_web_search
        # 단계마다 검색 예산이 다르다. 프롬프트에 알려 주는 숫자와 실제로 거는 한도가
        # 어긋나면 모델이 예산을 잘못 배분하므로 같은 값을 쓴다.
        budget = int(budgets.get(stage) or getattr(provider, "max_uses", 0))
        if use_search:
            values["search_policy"] = SEARCH_ON.format(n=budget)

        text = render(cfg, stage, values, resp)
        (out / f"rendered_{stage}.txt").write_text(text, encoding="utf-8")
        model = models[stage]
        print(f"  [{market}/{stage}] 호출 — {model} "
              f"({len(text):,}자{f', 웹 검색 {budget}회' if use_search else ''})")

        t0 = datetime.now()
        reply = provider.complete(model=model, prompt=text, max_tokens=max_tokens,
                                  temperature=temperature, web_search=use_search,
                                  max_searches=budget if use_search else None)

        # trader는 구조화 JSON이 필수다. 파싱 실패하면 JSON만 다시 요구해 재시도한다.
        pick = None
        if stage == "trader":
            pick = extract_pick(reply.text)
            for attempt in range(retries):
                if pick:
                    break
                print(f"    trader JSON 파싱 실패 — 재요청 {attempt + 1}/{retries}")
                retry_prompt = (
                    text + "\n\n---\n직전 응답에서 JSON을 파싱할 수 없었다. "
                    "설명 없이 위 스키마의 JSON 객체 하나만 출력하라.")
                reply2 = provider.complete(model=model, prompt=retry_prompt,
                                           max_tokens=max_tokens,
                                           temperature=temperature, web_search=False)
                pick = extract_pick(reply2.text)
                if pick:
                    reply.text += "\n\n<!-- JSON 재요청 응답 -->\n" + reply2.text
            if pick:
                (out / "pick.json").write_text(
                    json.dumps(pick, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                print(f"[{market}] trader JSON을 끝내 못 받았다. "
                      f"{(resp / 'trader.md').relative_to(ROOT)}를 직접 확인할 것.",
                      file=sys.stderr)

        f.write_text(reply.text, encoding="utf-8")
        meta["stages"][stage] = {
            "rendered_at": t0.isoformat(timespec="seconds"),
            "model": reply.model,
            "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1),
            "web_search": use_search,
            "web_search_calls": reply.web_searches,
            "usage": reply.usage,
            "chars": len(reply.text),
        }
        save_meta(meta_f, meta)
        print(f"    저장 {f.relative_to(ROOT)} ({len(reply.text):,}자, "
              f"{meta['stages'][stage]['elapsed_sec']}초)")

    return 0


def run_market(cfg: dict, market: str, week: int, mode: str, only: str | None,
               provider) -> int:
    out = ROOT / "outputs" / f"wk{week}" / market
    resp = out / "responses"
    resp.mkdir(parents=True, exist_ok=True)
    print(f"\n{'=' * 78}\n[{market}] {cfg['markets'][market]['label']} | "
          f"mode={mode} | prompts={cfg['meta']['prompt_version']}\n{'=' * 78}")
    if mode == "manual":
        return run_manual(cfg, market, week, out, resp, only)
    return run_auto(cfg, market, week, out, resp, only, provider)


def summarize(week: int, markets: list[str]) -> None:
    print(f"\n{'=' * 78}\n[wk{week} 최종 추천]\n{'=' * 78}")
    for m in markets:
        f = ROOT / "outputs" / f"wk{week}" / m / "pick.json"
        if not f.exists():
            print(f"  {m}: (아직 없음)")
            continue
        p = json.loads(f.read_text(encoding="utf-8"))
        lo, hi = (p.get("expected_return_range") or [None, None])[:2]
        print(f"  {m}: {p.get('ticker')} {p.get('name')}")
        print(f"      catalyst  {p.get('catalyst')}")
        print(f"      예상범위  {lo}% ~ {hi}%")
        print(f"      최대리스크 {p.get('primary_risk')}")


def check_llm(cfg: dict) -> int:
    """게이트웨이 연결과 단계별 모델 이름을 실제 호출로 확인한다.

    모델(에셋) 이름은 게이트웨이마다 다르고 오타가 나도 실행 중반에야 터진다.
    본 실행 전에 짧은 호출 한 번씩으로 먼저 걸러 낸다.
    """
    try:
        provider = llm_mod.build(cfg)
    except llm_mod.LLMError as e:
        print(f"LLM 설정 오류:\n{e}", file=sys.stderr)
        return 1
    print(f"provider={provider.name}  "
          f"web_search={'사용' if provider.supports_web_search else '불가'}")
    rc = 0
    for stage, model in cfg["llm"]["models"].items():
        try:
            r = provider.complete(model=model, prompt="ping. 'pong'이라고만 답하라.",
                                  max_tokens=64, temperature=None, web_search=False)
            print(f"  OK    {stage:<8} {model:<20} -> {r.text.strip()[:40]!r}")
        except llm_mod.LLMError as e:
            print(f"  실패  {stage:<8} {model:<20} -> {e}", file=sys.stderr)
            rc = 1
    return rc


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("stage", nargs="?", choices=list(DEPS),
                   help="생략하면 남은 단계를 순서대로 진행한다")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--market", choices=["KR", "US"],
                   help="생략하면 config.run.markets 전부")
    p.add_argument("--week", type=int)
    p.add_argument("--mode", choices=["auto", "manual"],
                   help="생략하면 config.run.agent_mode")
    p.add_argument("--check", action="store_true",
                   help="게이트웨이 연결과 모델 이름만 확인하고 끝낸다")
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    if a.check:
        return check_llm(cfg)
    week = a.week or cfg["run"]["week_no"]
    mode = a.mode or cfg["run"]["agent_mode"]
    markets = [a.market] if a.market else list(cfg["run"]["markets"])

    provider = None
    if mode == "auto":
        try:
            provider = llm_mod.build(cfg)
        except llm_mod.LLMError as e:
            print(f"LLM 설정 오류:\n{e}\n\n"
                  f"설정을 채우기 전까지는 `--mode manual`로 프롬프트만 뽑아 쓸 수 있다.",
                  file=sys.stderr)
            return 1
        print(f"provider={provider.name}  "
              f"web_search={'사용' if provider.supports_web_search else '불가'}")

    rc = 0
    for m in markets:
        try:
            rc |= run_market(cfg, m, week, mode, a.stage, provider)
        except (FileNotFoundError, llm_mod.LLMError) as e:
            print(f"[{m}] 중단: {e}", file=sys.stderr)
            rc |= 1
    if mode == "auto":
        summarize(week, markets)
    return rc


if __name__ == "__main__":
    sys.exit(main())
