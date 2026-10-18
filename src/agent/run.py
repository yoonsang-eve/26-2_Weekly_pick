"""에이전트 실행 — 추천·회고·스케줄 tick.

    python src/agent/run.py tick                 # 스케줄러가 매시간 부른다. 할 일이 없으면 바로 끝난다
    python src/agent/run.py recommend            # 다음 평가 주간 추천 (LLM 호출)
    python src/agent/run.py review --week 8      # 주차 종료 후 정산 → 회고(LLM) → 교훈 갱신 → 발표 자료
    python src/agent/run.py tools-test           # LLM 없이 데이터·도구만 시험
    python src/agent/run.py check                # 게이트웨이가 도구 사용을 중계하는지 (LLM 소량 호출)
    python src/agent/run.py status               # 이번 주 일정·상태
    python src/agent/run.py calendar             # 휴장일·대형 일정 갱신 (LLM + 검색, 약 0.1 unit)

recommend·review·check는 LLM을 부른다. 토큰은 outputs/wk{N}/llm_spend.jsonl 장부로 주차 상한을 지킨다
(config agent.token_budget).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import traceback
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import yaml

SRC = Path(__file__).resolve().parent.parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT))

from agent import calendar_refresh, data, loop, memory, schedule, tools  # noqa: E402
import llm as llm_mod  # noqa: E402

OUT = ROOT / "outputs"
CONFIG = ROOT / "config.yaml"
LOGDIR = ROOT / "private" / "agent"
PY = sys.executable.replace("pythonw.exe", "python.exe")

SEARCH_ON = ("웹 검색 도구로 사실을 직접 확인하라. 검색으로 확인되지 않은 수치·일정은 쓰지 않는다. "
             "이 실행에서 쓸 수 있는 검색은 최대 {n}회다. 한도를 넘기면 검색이 실패하므로 가장 중요한 사실부터 확인하라.")
REF_INSTRUMENTS = {
    "KR": [("069500", "KODEX 200"), ("114800", "KODEX 인버스"), ("122630", "KODEX 레버리지"),
           ("459580", "KODEX CD금리액티브(합성)")],
    "US": [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("SH", "S&P 500 인버스"), ("BIL", "초단기 국채")],
}


def load_cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def ac(cfg: dict) -> dict:
    return cfg.get("agent") or {}


def prompt_dir(cfg: dict) -> Path:
    return ROOT / "prompts" / str(ac(cfg).get("prompt_version") or "v4")


def fill(text: str, values: dict) -> str:
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    if "{{" in text:
        print(f"경고: 치환되지 않은 placeholder: {text[text.index('{{'):][:40]}", file=sys.stderr)
    return text


def agent_dir(week: int) -> Path:
    return OUT / f"wk{week}" / "agent"


def _json(f: Path) -> dict:
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def notify(subject: str, body: str) -> None:
    try:
        from app.notify import send
        print(f"  알림: {send(subject, body)}")
    except Exception as e:
        print(f"  알림 실패: {e}", file=sys.stderr)


# ------------------------------------------------------------------ config 주차 갱신

def update_config_week(week: int, monday: date, plan: dict) -> None:
    """config.run을 이번 주차로 맞춘다 (데스크 화면·정산 기본값이 이 값을 본다). 주석은 보존한다."""
    try:
        from ruamel.yaml import YAML
    except ImportError:
        print("  ruamel.yaml이 없어 config.run을 갱신하지 않았다 (pip install -r requirements-app.txt)")
        return
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.representer.add_representer(type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null"))
    cfg = y.load(CONFIG.read_text(encoding="utf-8"))
    run = cfg["run"]
    common_buy, common_sell = monday.isoformat(), (monday + timedelta(days=4)).isoformat()
    run["week_no"], run["buy_date"], run["sell_date"] = week, common_buy, common_sell
    for m in ("KR", "US"):
        mk = cfg["markets"][m]
        p = plan.get(m)
        if not p:
            continue
        for k, common in (("buy_date", common_buy), ("sell_date", common_sell)):
            if p[k] == common:
                mk.pop(k, None)
            else:
                mk[k] = p[k]
        mk["holding_days"] = p["holding_days"]
    with CONFIG.open("w", encoding="utf-8") as f:
        y.dump(cfg, f)
    print(f"  config.run → wk{week} ({common_buy} ~ {common_sell})")


# ------------------------------------------------------------------ 추천

def instrument_policy(tb: tools.Toolbox) -> str:
    a = tb.allow
    parts = []
    if a["stocks"]:
        parts.append("개별 주식")
    if a["etfs"]:
        e = "ETF(지수·섹터·해외·채권/현금성·원자재"
        e += (", 인버스" if a["inverse"] else "") + (", 레버리지" if a["leveraged"] else "") + ")"
        parts.append(e)
    return " · ".join(parts)


def markets_block(plan_all: dict, plan: dict, cfg: dict) -> str:
    lines = []
    for m, p in plan_all.items():
        mk = cfg["markets"][m]
        state = "제출 가능" if m in plan else "제출 불가 (매수 시가가 이미 지났다)"
        lines.append(f"- {m} {mk['label']}: {p['buy_date']} 시가 매수 → {p['sell_date']} 종가 매도 "
                     f"({p['holding_days']}영업일){' · 휴장 ' + ', '.join(p['holidays']) if p['holidays'] else ''} — {state}")
    for m in ("KR", "US"):
        if m not in plan_all:
            lines.append(f"- {m}: 이번 주 거래일 없음")
    return "\n".join(lines)


def make_critic_renderer(cfg: dict, plan: dict):
    tmpl = (prompt_dir(cfg) / "critic.txt").read_text(encoding="utf-8")
    budget = int((ac(cfg).get("search_budget") or {}).get("critic") or 5)

    def render(market, ticker, name, stance, thesis, catalyst, rng, metrics) -> str:
        p = plan[market]
        return fill(tmpl, {
            "market_label": cfg["markets"][market]["label"], "ticker": ticker, "name": name, "stance": stance,
            "buy_date": p["buy_date"], "sell_date": p["sell_date"], "holding_days": p["holding_days"],
            "today": datetime.now().strftime("%Y-%m-%d"), "metrics": metrics,
            "expected_range": f"{rng[0]}% ~ {rng[1]}%" if rng and len(rng) == 2 else "(미제시)",
            "thesis": thesis, "catalyst": catalyst, "search_policy": SEARCH_ON.format(n=budget)})
    return render


def build_provider(cfg: dict, week: int):
    p = llm_mod.build(cfg, week)
    if p.name != "anthropic":
        raise llm_mod.LLMError("에이전트는 도구 사용이 필요해 llm.provider: anthropic 에서만 돈다.")
    # 에이전트 경로는 검색 도구 종류를 따로 정한다. _20260209는 결과를 코드 실행으로 거르는데, wk8 첫 실행에서
    # 그 코드가 실패해 검색 예산만 소진됐고 모델이 그 REPL 안에서 우리 도구를 부르려 했다.
    if ac(cfg).get("web_search_tool_type"):
        p.tool_type = ac(cfg)["web_search_tool_type"]
    tb = ac(cfg).get("token_budget")
    if tb:
        p.budget = llm_mod.Budget(OUT / f"wk{week}" / "llm_spend.jsonl", tb)
        i, o = p.budget.spent()
        print(f"에이전트 토큰 예산 wk{week}: 입력(캐시 가중) {i:,}/{p.budget.max_input:,} · 출력 {o:,}/{p.budget.max_output:,}")
    return p


def recommend(cfg: dict, now: datetime, *, monday: date | None = None, markets: list[str] | None = None,
              force: bool = False, ignore_time: bool = False, use_cache: bool = True) -> int:
    a = ac(cfg)
    monday = monday or schedule.target_monday(now)
    plan_all = schedule.week_plan(monday)
    plan = plan_all if ignore_time else schedule.eligible(plan_all, now, int((a.get("schedule") or {}).get("margin_min", 30)))
    if markets:
        plan = {m: p for m, p in plan.items() if m in markets}
    week = schedule.week_no(monday, cfg)
    out = agent_dir(week)
    print(f"[추천] wk{week} ({monday} 주간) · 제출 가능 시장 {', '.join(plan) or '없음'}")
    if (out / "decision.json").exists() and not force:
        print(f"  이미 결정이 있다: {(out / 'decision.json').relative_to(ROOT)} (다시 하려면 --force)")
        return 0
    if not plan:
        print("  제출 가능한 시장이 없다 (매수 시가가 모두 지났다).", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("critique_*"):
        f.unlink()
    (out / "notes.md").write_text(f"# wk{week} 작업 메모\n", encoding="utf-8")
    update_config_week(week, monday, plan_all)
    cfg = load_cfg()

    provider = build_provider(cfg, week)
    tb = tools.Toolbox(cfg, week, plan, out, provider, make_critic_renderer(cfg, plan))
    tb.prepare(use_cache)

    search_budget = int((a.get("search_budget") or {}).get("agent") or 20)
    max_turns = int(a.get("max_turns") or 40)
    system = fill((prompt_dir(cfg) / "agent_system.txt").read_text(encoding="utf-8"), {
        "today": now.strftime("%Y-%m-%d (%a) %H:%M"), "week_no": week,
        "markets_block": markets_block(plan_all, plan, cfg), "instrument_policy": instrument_policy(tb),
        "max_turns": max_turns, "search_budget": search_budget, "max_critiques": int(a.get("max_critiques") or 3)})
    (out / "rendered_system.txt").write_text(system, encoding="utf-8")
    first = (f"{week}주차 추천을 시작하라. 제출 가능한 시장: {', '.join(plan)}. "
             "국면 조사부터 시작해 필요한 만큼 탐색하고, 비판을 받은 뒤 submit_recommendation으로 제출하라.")

    print(f"[루프] {a.get('model')} · 턴 {max_turns} · 검색 {search_budget} · 비판 {a.get('max_critiques', 3)}")
    try:
        stats = loop.run(provider, model=a.get("model") or cfg["llm"]["models"]["trader"], system=system,
                         first_user=first, toolbox=tb, max_turns=max_turns, search_budget=search_budget,
                         max_tokens=int(a.get("max_tokens") or 32000), effort=a.get("effort"), out=out,
                         per_turn_searches=int(a.get("per_turn_searches") or 5),
                         cost_limit=float(a.get("cost_limit_unit") or 0))
        print(f"[비용] 추천 1회 약 {stats['cost_unit']:.2f} unit (상한 {a.get('cost_limit_unit')})")
    except Exception as e:
        _write_json(out / "run_meta.json", {"week": week, "error": str(e), "tool_calls": tb.calls,
                                             "critics": tb.critic_usage, "at": datetime.now().isoformat(timespec="seconds")})
        notify(f"[weekly_pick] wk{week} 추천 실패", f"{e}\n\n기록: {out}")
        raise
    if tb.decision is None:
        _write_json(out / "run_meta.json", {"week": week, "stats": stats, "tool_calls": tb.calls, "critics": tb.critic_usage})
        notify(f"[weekly_pick] wk{week} 추천 미완료", f"에이전트가 제출하지 못했다 ({stats['stop']}). {out / 'transcript.md'}를 확인할 것.")
        return 1
    d = write_outputs(cfg, week, monday, plan_all, plan, tb, stats)
    rng = d["expected_return_range"]
    notify(f"[weekly_pick] wk{week} 추천: {d['name']} ({d['ticker']}, {d['market']})",
           f"국면 {d['regime_view']} · 자세 {d['stance']} · 확신 {d['confidence']}\n"
           f"예상 {rng[0]}% ~ {rng[1]}%\ncatalyst: {d['catalyst']}\n\n"
           f"매수 {plan[d['market']]['buy_date']} 시가 → 매도 {plan[d['market']]['sell_date']} 종가\n"
           f"턴 {stats['turns']} · 검색 {stats['web_searches']} · 비판 {len(tb.critiques)}회 · {stats['elapsed_sec'] / 60:.0f}분 · 약 {stats['cost_unit']:.2f} unit")
    return 0


def _write_json(f: Path, obj) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data.to_jsonable(obj), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_outputs(cfg: dict, week: int, monday: date, plan_all: dict, plan: dict, tb: tools.Toolbox, stats: dict) -> dict:
    """결정과 함께, 기존 산출물 형식(pick.json·candidates.csv·responses/)도 시장 폴더에 쓴다.

    정산(settle·final·cohort), 발표 자료(deck), 데스크 화면, 자동 매매가 고치지 않고 그대로 읽게 하려는 것이다.
    """
    d = dict(tb.decision)
    m, t = d["market"], d["ticker"]
    p = plan[m]
    out = agent_dir(week)
    mdir = OUT / f"wk{week}" / m
    (mdir / "responses").mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")
    pv = str(ac(cfg).get("prompt_version") or "v4")

    _write_json(out / "decision.json", {**d, "week": week, "decided_at": now, "plan": plan_all,
                                        "eligible": list(plan), "prompt_version": pv,
                                        "stats": {k: stats.get(k) for k in ("stop", "turns", "web_searches", "elapsed_sec", "model", "cost_unit")}})
    same = [x for x in d.get("alternatives") or [] if (x.get("market") or m) == m]
    _write_json(mdir / "pick.json", {
        "market": m, "ticker": t, "name": d["name"], "thesis": d["thesis"], "catalyst": d["catalyst"],
        "expected_return_range": d["expected_return_range"], "primary_risk": d["primary_risk"],
        "rejected": [{"ticker": x["ticker"], "reason": x["reason"]} for x in same],
        "instrument_type": d["instrument_type"], "regime_view": d["regime_view"], "stance": d["stance"],
        "confidence": d["confidence"], "decided_by": "agent"})
    _write_json(mdir / "screen_meta.json", {
        "market": m, "week": week, "ref_date": f"{tb.ref(m):%Y-%m-%d}", "buy_date": p["buy_date"],
        "sell_date": p["sell_date"], "holding_days": p["holding_days"], "n_candidates": len(tb.examined),
        "config_version": cfg["meta"]["config_version"], "screened_at": now, "source": "agent"})

    # 후보표 = 에이전트가 상세 조회한 종목 (선택 종목은 반드시 포함). 스크리너 CSV와 같은 열.
    recs = [r for k, r in tb.examined.items() if k.startswith(m + ":")]
    if f"{m}:{t}" not in tb.examined:
        row = tb.lookup(m, t)
        if row is not None:
            recs.append(row.to_dict())
    for x in same:
        xt = str(x["ticker"]).upper()
        if f"{m}:{xt}" not in tb.examined and (row := tb.lookup(m, xt)) is not None:
            recs.append(row.to_dict())
    cols = ["market", "exchange", "ticker", "name", "sector", "close", "mktcap", "adv20", "ret_mom", "ret20", "ret5",
            "pct_from_52wh", "atr20_pct", "score"]
    cand = pd.DataFrame(recs)
    for c in cols:
        if c not in cand.columns:
            cand[c] = math.nan
    cand["market"] = m
    cand["exchange"] = cand["exchange"].fillna(cand.get("kind", ""))
    cand["score"] = cand["atr20_pct"]
    cand = cand.drop_duplicates("ticker")
    cand[cols].to_csv(mdir / "candidates.csv", index=False, encoding="utf-8-sig")

    bc = d.get("bull_case")
    if not isinstance(bc, dict):
        bc = {"scenario": "(제출 시 누락 — 아래 thesis로 대체)\n" + "\n".join(f"- {x}" for x in d.get("thesis") or []),
              "trigger": d.get("catalyst", ""), "not_priced_in": "(제출 시 누락)"}
    (mdir / "responses" / "bull.md").write_text(
        f"# 매수 논거 — {d['name']}\n\n## {d['name']} ({t})\n\n"
        f"**국면 판단**\n{d['regime_view']} / 자세 {d['stance']} / 확신 {d['confidence']}\n{d['regime_rationale']}\n\n"
        f"**상승 시나리오**\n{bc['scenario']}\n\n**트리거**\n{bc['trigger']}\n\n**미반영 근거**\n{bc['not_priced_in']}\n\n"
        f"**비판에 대한 답변**\n{d['response_to_critique']}\n", encoding="utf-8")
    crit = [tb.critiques[f"{m}:{t}"]] + [v for k, v in tb.critiques.items() if k != f"{m}:{t}"]
    (mdir / "responses" / "bear.md").write_text("# 비판자 반박\n\n" + "\n\n---\n\n".join(crit), encoding="utf-8")
    notes = "\n\n".join(f"### {a}\n{b}" for a, b in tb.notes)
    alts = "\n".join(f"- {x.get('market', m)} {x.get('name', '')} ({x['ticker']}): {x['reason']}" for x in d.get("alternatives") or [])
    (mdir / "responses" / "agent.md").write_text(
        f"# 에이전트 탐색 요약 — wk{week}\n\n## 과정\n{d['process_summary']}\n\n## 탈락 후보\n{alts}\n\n## 작업 메모\n{notes}\n",
        encoding="utf-8")

    stages = {"agent": {"model": stats["model"], "elapsed_sec": stats["elapsed_sec"], "web_search": True,
                        "web_search_calls": stats["web_searches"], "usage": stats["usage"], "turns": stats["turns"],
                        "stop": stats["stop"], "rendered_at": now}}
    for i, c in enumerate(tb.critic_usage, 1):
        stages[f"critic_{i}"] = {"model": c["model"], "elapsed_sec": c["elapsed_sec"], "web_search": True,
                                 "web_search_calls": c["web_search_calls"], "usage": c["usage"], "ticker": c["ticker"]}
    calls: dict[str, int] = {}
    for c in tb.calls:
        calls[c["tool"]] = calls.get(c["tool"], 0) + 1
    meta = {"week": week, "market": m, "mode": "agent", "prompt_version": pv,
            "config_version": cfg["meta"]["config_version"], "provider": "anthropic", "stages": stages,
            "tool_calls": calls, "tool_log": tb.calls}
    _write_json(mdir / "run_meta.json", meta)
    _write_json(out / "run_meta.json", meta)

    ff = OUT / f"wk{week}" / "final.json"
    old = _json(ff)
    # 사용자가 직접 정한 최종 선택은 덮어쓰지 않는다 (CLAUDE.md 2절 Step 4와 같은 규칙)
    if not old or old.get("decided_by") in ("agent", "selector"):
        _write_json(ff, {"choice": m, "market": m, "ticker": t, "name": d["name"], "source": "agent",
                         "decided_by": "agent", "week": week,
                         "note": f"에이전트 추천 — 국면 {d['regime_view']}, 자세 {d['stance']}, 확신 {d['confidence']}",
                         "chosen_at": now})
    else:
        print(f"  final.json은 사용자가 정한 값이라 그대로 둔다 ({old.get('ticker')})")
    print(f"\n[결정] {m} {t} {d['name']} · {d['regime_view']} / {d['stance']} · 예상 {d['expected_return_range']}")
    return d


# ------------------------------------------------------------------ 회고

def review(cfg: dict, week: int, *, force: bool = False, deck: bool = True) -> int:
    import cohort
    import final as final_mod
    import settle

    out = agent_dir(week)
    dec = _json(out / "decision.json")
    if not dec:
        print(f"wk{week}: 에이전트 결정이 없다 (고정 파이프라인 주차는 회고 대상이 아니다).")
        return 1
    if (out / "review.json").exists() and not force:
        print(f"wk{week}: 이미 회고가 있다 (다시 하려면 --force).")
        return 0
    m, t = dec["market"], dec["ticker"]
    plan = dec["plan"]
    if not settle.close_final(m, plan[m]["sell_date"]):
        print(f"wk{week}: {m} 매도일 {plan[m]['sell_date']} 종가가 아직 확정되지 않았다.")
        return 2

    print(f"[정산] wk{week}")
    frow = final_mod.settle_final(cfg, week)
    if frow is None:
        return 2
    final_mod.append(frow)
    lrow = settle.settle_one(cfg, week, m)
    if lrow is None:
        return 2
    settle.append_log([lrow])
    try:
        cohort.run(cfg, week, m, None, None)
    except Exception as e:
        print(f"  후보군 정산 실패(무시): {e}", file=sys.stderr)

    print("[비교군]")
    cf = []
    seen = set()
    for x in dec.get("alternatives") or []:
        xm = x.get("market") or m
        if xm in plan and (xm, x["ticker"]) not in seen:
            seen.add((xm, x["ticker"]))
            r = cohort.settle_ticker(str(x["ticker"]), xm, plan[xm]["buy_date"], plan[xm]["sell_date"])
            cf.append({"구분": "탈락 후보", "market": xm, "ticker": x["ticker"], "name": x.get("name", ""), "ret_pct": r["ret_pct"]})
    for xm, items in REF_INSTRUMENTS.items():
        if xm not in plan:
            continue
        for xt, xn in items:
            r = cohort.settle_ticker(xt, xm, plan[xm]["buy_date"], plan[xm]["sell_date"])
            cf.append({"구분": "기준 상품", "market": xm, "ticker": xt, "name": xn, "ret_pct": r["ret_pct"]})
    cf_text = "\n".join(f"- [{c['구분']}] {c['market']} {c['name']} ({c['ticker']}): {c['ret_pct']}%" for c in cf)
    outcome = (f"- 에이전트 추천 {m} {dec['name']}({t}): {lrow['buy_date']} 시가 {lrow['buy_open']} → "
               f"{lrow['sell_date']} 종가 {lrow['sell_close']} = {lrow['ret_pct']}% "
               f"(예상 {dec['expected_return_range'][0]}~{dec['expected_return_range'][1]}%)\n"
               f"- 벤치마크 {lrow['benchmark']} {lrow['benchmark_ret_pct']}%, 초과 {lrow['excess_pct']}%p")
    if frow.get("ticker") != t:
        outcome += f"\n- 실제 제출(사용자 선택): {frow.get('name')} ({frow.get('ticker')}) {frow.get('ret_pct')}%"
    crit = (OUT / f"wk{week}" / m / "responses" / "bear.md")
    dec_view = {k: dec.get(k) for k in ("market", "ticker", "name", "instrument_type", "regime_view", "regime_rationale",
                                        "stance", "confidence", "thesis", "catalyst", "expected_return_range",
                                        "primary_risk", "response_to_critique", "alternatives", "process_summary")}
    prompt = fill((prompt_dir(cfg) / "review.txt").read_text(encoding="utf-8"), {
        "week_no": week, "decision": json.dumps(dec_view, ensure_ascii=False, indent=2),
        "critique": (crit.read_text(encoding="utf-8")[:8000] if crit.exists() else "(없음)"),
        "outcome": outcome, "counterfactuals": cf_text or "(없음)", "track_record": memory.track_record(week + 1)})
    (out / "rendered_review.txt").write_text(prompt, encoding="utf-8")

    print("[회고] LLM")
    provider = build_provider(cfg, week)
    model = ac(cfg).get("review_model") or ac(cfg).get("model")
    reply = provider.complete(model=model, prompt=prompt, max_tokens=16000, temperature=None, web_search=False)
    rv = tools.parse_json_block(reply.text) or {}
    (out / "review.md").write_text(reply.text, encoding="utf-8")
    rv.update({"week": week, "model": reply.model, "usage": reply.usage, "outcome": lrow, "counterfactuals": cf,
               "reviewed_at": datetime.now().isoformat(timespec="seconds")})
    _write_json(out / "review.json", rv)
    if rv.get("lesson_updates") is not None:
        memory.apply_review(week, rv)
    else:
        print("  회고 JSON을 못 읽었다 — 교훈은 갱신하지 않았다", file=sys.stderr)

    deck_path = ""
    if deck:
        print("[발표 자료]")
        r = subprocess.run([PY, str(SRC / "deck" / "build.py"), "--week", str(week), "--final"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(r.stdout[-800:], r.stderr[-800:])
        deck_path = next((ln.split("저장:")[1].strip() for ln in r.stdout.splitlines() if "저장:" in ln), "")
    notify(f"[weekly_pick] wk{week} 결과: {dec['name']} {lrow['ret_pct']:+.2f}%",
           f"벤치마크 {lrow['benchmark_ret_pct']}% · 초과 {lrow['excess_pct']}%p\n"
           f"회고: {rv.get('summary', '(요약 없음)')}\n발표 자료: {deck_path or '(생성 안 됨)'}")
    return 0


# ------------------------------------------------------------------ 일정 갱신

def calendar(cfg: dict) -> int:
    week = schedule.week_no(schedule.target_monday(schedule.now_kst()), cfg)
    provider = build_provider(cfg, week)
    tmpl = (prompt_dir(cfg) / "calendar.txt").read_text(encoding="utf-8")
    render = lambda start, end, today, n: fill(tmpl, {"start": start, "end": end, "today": today,  # noqa: E731
                                                      "search_policy": SEARCH_ON.format(n=n)})
    r = calendar_refresh.refresh(cfg, provider, render)
    if r["added"] or r["removed"]:
        notify("[weekly_pick] 휴장일 변경 확인 필요",
               "새로 찾은 휴장: " + (", ".join(r["added"]) or "없음") + "\n사라진 휴장: " + (", ".join(r["removed"]) or "없음")
               + "\n\n자동 검색 결과다. 매수·매도일 계산에 바로 쓰이니 틀렸으면 data/calendar_auto.json을 고칠 것.")
    return 0


# ------------------------------------------------------------------ 스케줄 tick

def _lock() -> bool:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    f = LOGDIR / "tick.lock"
    if f.exists() and datetime.now().timestamp() - f.stat().st_mtime < 3 * 3600:
        return False
    f.write_text(str(os.getpid()), encoding="utf-8")
    return True


def tick(cfg: dict) -> int:
    a = ac(cfg)
    if not a.get("enabled", False):
        print("config agent.enabled가 false다 — 아무것도 하지 않는다.")
        return 0
    if not _lock():
        print("다른 tick이 실행 중이다.")
        return 0
    try:
        now = schedule.now_kst()
        print(f"[tick] {now:%Y-%m-%d %H:%M} KST")
        # 1. 끝난 주차 회고
        for wk in sorted(OUT.glob("wk*")):
            if not wk.name[2:].isdigit() or not (wk / "agent" / "decision.json").exists() or (wk / "agent" / "review.json").exists():
                continue
            try:
                review(load_cfg(), int(wk.name[2:]))
            except Exception as e:
                traceback.print_exc()
                notify(f"[weekly_pick] {wk.name} 회고 실패", str(e))
        # 2. 휴장일·대형 일정 (주 1회, 추천 전에)
        if calendar_refresh.due(now):
            try:
                calendar(load_cfg())
            except Exception as e:
                traceback.print_exc()
                notify("[weekly_pick] 일정 갱신 실패", f"{e}\n고정 휴장일 표로 계속 진행한다.")
        # 3. 다음 주 추천
        monday = schedule.target_monday(now)
        plan_all = schedule.week_plan(monday)
        if not plan_all:
            return 0
        week = schedule.week_no(monday, cfg)
        if (agent_dir(week) / "decision.json").exists():
            print(f"  wk{week} 추천 완료 상태")
            return 0
        rd = schedule.run_day(plan_all)
        at = time.fromisoformat(str((a.get("schedule") or {}).get("recommend_at") or "18:00"))
        due = now.date() > rd or (now.date() == rd and now.time() >= at)
        print(f"  wk{week} 추천 예정 {rd} {at:%H:%M} — {'지금 실행' if due else '대기'}")
        if due:
            try:
                return recommend(load_cfg(), now)
            except Exception as e:
                traceback.print_exc()
                return 1
        return 0
    finally:
        (LOGDIR / "tick.lock").unlink(missing_ok=True)


# ------------------------------------------------------------------ 시험

def tools_test(cfg: dict, buy_monday: date | None) -> int:
    """LLM 없이 데이터 준비와 도구 실행을 끝까지 돌려 본다."""
    now = schedule.now_kst()
    monday = buy_monday or schedule.target_monday(now)
    plan = schedule.week_plan(monday)
    week = schedule.week_no(monday, cfg)
    out = ROOT / "cache" / "agent" / "tools_test"
    tb = tools.Toolbox(cfg, week, plan, out)
    tb.prepare()
    m0 = next(iter(plan))
    calls = [("market_overview", {}), ("track_record", {}),
             *[(n, {"market": m}) for m in plan for n in ("sector_rotation",)],
             ("screen_stocks", {"market": m0, "sort_by": "beta60", "ascending": True, "filters": {"ret20": [None, 0]}, "n": 8}),
             ("screen_stocks", {"market": "US", "sectors": ["Energy"], "sort_by": "ret_mom", "n": 5}),
             ("screen_etfs", {"market": m0, "tags": ["inverse"], "sort_by": "ret5", "n": 5}),
             ("screen_etfs", {"market": "US", "tags": ["bond_cash"], "sort_by": "vol20", "ascending": True, "n": 5}),
             ("instrument_detail", {"market": "KR", "ticker": "114800"}),
             ("instrument_detail", {"market": "US", "ticker": "XOM"}),
             ("write_note", {"title": "시험", "text": "도구 시험 메모"}),
             ("request_critique", {"market": "US", "ticker": "XOM", "name": "Exxon", "stance": "balanced",
                                   "thesis": "t", "catalyst": "c", "expected_return_range": [-3, 5]}),
             ("submit_recommendation", {"market": "US", "ticker": "XOM"})]
    for name, args in calls:
        text, err = tb.run(name, args)
        print(f"\n===== {name} {json.dumps(args, ensure_ascii=False)} {'(오류)' if err else ''} — {len(text):,}자")
        print(text[:2500])
    return 0


def check(cfg: dict) -> int:
    """게이트웨이가 클라이언트 도구 사용(tool_use → tool_result)을 중계하는지 확인한다. 입력 수천 토큰."""
    p = build_provider(cfg, int(cfg["run"]["week_no"]))
    model = ac(cfg).get("model")
    tool = {"name": "ping", "description": "연결 확인용. 인자 x를 그대로 돌려준다.",
            "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}}
    msgs = [{"role": "user", "content": "ping 도구를 x='ok'로 한 번 호출한 뒤, 결과를 한 단어로 답하라."}]
    r1 = p.client.messages.create(model=model, max_tokens=2000, tools=[tool], messages=msgs)
    p.budget and p.budget.record(r1.model, r1.usage.model_dump())
    tu = next((b for b in r1.content if b.type == "tool_use"), None)
    print(f"1차 stop_reason={r1.stop_reason}, tool_use={'있음' if tu else '없음'}")
    if not tu:
        return 1
    msgs += [{"role": "assistant", "content": r1.content},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu.id, "content": "ok"}]}]
    r2 = p.client.messages.create(model=model, max_tokens=2000, tools=[tool], messages=msgs)
    p.budget and p.budget.record(r2.model, r2.usage.model_dump())
    print(f"2차 stop_reason={r2.stop_reason}: {''.join(b.text for b in r2.content if b.type == 'text')[:200]}")
    print("도구 사용 중계 확인 완료")
    return 0


def status(cfg: dict) -> int:
    now = schedule.now_kst()
    monday = schedule.target_monday(now)
    plan = schedule.week_plan(monday)
    week = schedule.week_no(monday, cfg)
    print(f"지금 {now:%Y-%m-%d %H:%M} KST · 대상 wk{week} ({monday} 주간)")
    for m, p in plan.items():
        print(f"  {m}: {p['buy_date']} → {p['sell_date']} ({p['holding_days']}일) 매수 시가 {schedule.buy_open_kst(m, p['buy_date']):%m-%d %H:%M} KST")
    if plan:
        print(f"  추천 실행일 {schedule.run_day(plan)} {(ac(cfg).get('schedule') or {}).get('recommend_at', '18:00')} · "
              f"결정 {'있음' if (agent_dir(week) / 'decision.json').exists() else '없음'}")
    for wk in sorted(OUT.glob("wk*/agent/decision.json")):
        w = wk.parent.parent.name
        print(f"  {w}: 결정 있음 · 회고 {'있음' if (wk.parent / 'review.json').exists() else '없음'}")
    print(f"  enabled={ac(cfg).get('enabled', False)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["tick", "recommend", "review", "tools-test", "check", "status", "calendar"])
    ap.add_argument("--week", type=int)
    ap.add_argument("--monday", help="평가 주간 월요일 (YYYY-MM-DD). 생략하면 오늘 기준 다음 평가 주간")
    ap.add_argument("--market", action="append", choices=["KR", "US"], help="후보 시장 제한 (여러 번 가능)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--ignore-time", action="store_true", help="매수 시가가 지난 시장도 넣는다 (사후 재현용)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-deck", action="store_true")
    ap.add_argument("--log", action="store_true", help="출력을 private/agent/ 로그 파일로 보낸다 (스케줄러용)")
    a = ap.parse_args()

    if a.log or sys.stdout is None:
        LOGDIR.mkdir(parents=True, exist_ok=True)
        fh = (LOGDIR / f"{datetime.now():%Y%m%d}.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = fh
    else:
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_cfg()
    monday = date.fromisoformat(a.monday) if a.monday else None
    if a.cmd == "tick":
        return tick(cfg)
    if a.cmd == "recommend":
        return recommend(cfg, schedule.now_kst(), monday=monday, markets=a.market, force=a.force,
                         ignore_time=a.ignore_time, use_cache=not a.no_cache)
    if a.cmd == "review":
        if not a.week:
            ap.error("--week가 필요하다")
        return review(cfg, a.week, force=a.force, deck=not a.no_deck)
    if a.cmd == "tools-test":
        return tools_test(cfg, monday)
    if a.cmd == "check":
        return check(cfg)
    if a.cmd == "calendar":
        return calendar(cfg)
    return status(cfg)


if __name__ == "__main__":
    sys.exit(main())
