"""Step 4 — Selector: 한국 추천과 미국 추천 중 이번 주에 낼 1종목을 고른다.

Step 1~3은 시장마다 따로 돈다 (한 프롬프트에 두 시장 후보를 섞지 않는다). 과제는 주당 1종목만
내므로 두 결과를 마지막에 한 번 비교해야 한다. 규칙 비교(app/advice.py)로는 기준이 갈리면
판정을 못 내려서, 이 단계를 LLM으로 둔다.

입력은 각 시장의 최종 결과뿐이다 — pick.json, Trader 응답 전문, 스크리닝 지표. 탈락한 후보는
넣지 않는다. 넣으면 시장 파이프라인의 선택을 다시 뒤집는 단계가 되어 Step 3과 역할이 겹친다.

산출물은 주차 공통 폴더 outputs/wk{N}/final/ 에 둔다.
    rendered_selector.txt   responses/selector.md   selection.json   run_meta.json
selection.json이 생기면 final.json(최종 선택)도 채운다. 단 사용자가 직접 고른 최종 선택은
덮어쓰지 않는다 (final.json의 decided_by가 selector일 때만 갱신).

    python src/selector.py                    # config의 주차, config의 모드
    python src/selector.py --mode manual      # 프롬프트만 렌더링
    python src/selector.py --mode auto        # LLM 호출 (공용 API — 허락 후에만)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm as llm_mod  # noqa: E402
from agents import (ROOT, SEARCH_OFF, SEARCH_ON, extract_pick, market_dates,  # noqa: E402
                    save_meta, to_clipboard)

STAGE = "selector"
MARKETS = ("KR", "US")
MANUAL_SEARCH = 15          # manual은 Claude 웹에서 돌린다. 프롬프트에 알려 주는 한도


def out_dir(week: int) -> Path:
    return ROOT / "outputs" / f"wk{week}" / "final"


def _json(f: Path) -> dict | None:
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def dates(cfg: dict, week: int, market: str) -> tuple[str, str, int]:
    """screen_meta.json에 남긴 매매일이 우선이다. config의 run 블록은 '이번 주차' 값이라
    지난 주차를 다시 돌릴 때 믿으면 안 된다 (settle.py와 같은 원칙)."""
    m = _json(ROOT / "outputs" / f"wk{week}" / market / "screen_meta.json") or {}
    if m.get("buy_date") and m.get("sell_date"):
        return m["buy_date"], m["sell_date"], int(m.get("holding_days") or 5)
    return market_dates(cfg, market)


def metrics_table(d: Path, ticker: str, days: int, mk: dict) -> str:
    f = d / "candidates.csv"
    row = None
    if f.exists():
        with f.open(encoding="utf-8-sig") as fh:
            for i, r in enumerate(csv.DictReader(fh), 1):
                t = str(r["ticker"]).strip()
                if (t.zfill(6) if t.isdigit() else t) == ticker:
                    row = {**r, "rank": i}
                    break
    if row is None:
        return "(후보 목록에서 찾지 못함 — 지표 없음)"

    def pct(k, sign=True):
        try:
            v = float(row[k])
        except (TypeError, ValueError, KeyError):
            return "확인불가"
        return f"{v:+.1%}" if sign else f"{v:.1%}"
    try:
        atr = float(row["atr20_pct"])
        band = f"±{atr * math.sqrt(days):.1%} ({days}영업일)"
    except (TypeError, ValueError, KeyError):
        band = "확인불가"
    close = float(row["close"])
    price = f"{close:,.0f}" if mk.get("currency") == "KRW" else f"{close:,.2f}"
    lines = [
        f"- 스크리닝 순위: 후보 {row['rank']}위 (ATR 순 정렬)",
        f"- 기준일 종가: {price} {mk.get('price_unit', '')}",
        f"- 6개월 모멘텀(최근 1개월 제외): {pct('ret_mom')}",
        f"- 20일 수익률: {pct('ret20')} / 5일 수익률: {pct('ret5')}",
        f"- 52주 고점 대비: -{pct('pct_from_52wh', sign=False)}",
        f"- 하루 변동성 ATR20/종가: {pct('atr20_pct', sign=False)}",
        f"- 보유 기간 예상 변동폭 (ATR20 × √보유일): {band}",
    ]
    return "\n".join(lines)


def values(cfg: dict, week: int, web_search: bool, max_searches: int) -> dict[str, str]:
    missing = []
    for m in MARKETS:
        d = ROOT / "outputs" / f"wk{week}" / m
        for f in (d / "pick.json", d / "responses" / "trader.md"):
            if not f.exists():
                missing.append(str(f.relative_to(ROOT)))
    if missing:
        raise FileNotFoundError("두 시장의 Trader 결과가 모두 있어야 한다. 없는 파일:\n  "
                                + "\n  ".join(missing))
    macro = (cfg["run"].get("macro_calendar") or "").strip() or "(확인되지 않음 — 직접 확인할 것)"
    v = {"week_no": str(week), "today": datetime.now().strftime("%Y-%m-%d"),
         "macro_calendar": macro,
         "search_policy": SEARCH_ON.format(n=max_searches) if web_search else SEARCH_OFF}
    for m in MARKETS:
        d = ROOT / "outputs" / f"wk{week}" / m
        mk = cfg["markets"][m]
        pick = _json(d / "pick.json")
        buy, sell, days = dates(cfg, week, m)
        p = m.lower()
        v.update({
            f"{p}_label": mk["label"],
            f"{p}_notes": mk["notes"].strip(),
            f"{p}_buy_date": buy, f"{p}_sell_date": sell, f"{p}_holding_days": str(days),
            f"{p}_pick": json.dumps(pick, ensure_ascii=False, indent=2),
            f"{p}_metrics": metrics_table(d, str(pick["ticker"]), days, mk),
            f"{p}_trader_output": (d / "responses" / "trader.md").read_text(encoding="utf-8").strip(),
        })
    return v


def render(cfg: dict, v: dict) -> str:
    text = (ROOT / "prompts" / cfg["meta"]["prompt_version"] / f"{STAGE}.txt").read_text(encoding="utf-8")
    for k, val in v.items():
        text = text.replace("{{" + k + "}}", val)
    if "{{" in text:
        print(f"경고: 치환되지 않은 placeholder가 남아 있다: {text[text.index('{{'):][:40]}", file=sys.stderr)
    return text


# ------------------------------------------------------------- 응답 처리

def parse(week: int, text: str) -> dict | None:
    """응답 끝의 JSON에서 선택을 꺼낸다. ticker·name은 그 시장 pick.json 값으로 맞춘다
    (LLM이 티커를 잘못 옮겨 적어도 최종 선택은 실제 추천 종목이어야 한다)."""
    obj = extract_pick(text)
    if not obj:
        return None
    choice = str(obj.get("choice", "")).strip().upper()
    if choice not in MARKETS:
        return None
    pick = _json(ROOT / "outputs" / f"wk{week}" / choice / "pick.json")
    if not pick:
        return None
    if str(obj.get("ticker", "")).strip() != str(pick["ticker"]):
        obj["llm_ticker"] = obj.get("ticker")
    obj.update(choice=choice, market=choice, ticker=str(pick["ticker"]), name=pick.get("name", ""), week=week)
    return obj


def apply(week: int, sel: dict | None) -> str:
    """selection.json을 쓰고 final.json(최종 선택)에 반영한다. 반영 여부를 문장으로 돌려준다."""
    d = out_dir(week)
    sf, ff = d / "selection.json", ROOT / "outputs" / f"wk{week}" / "final.json"
    fin = _json(ff)
    mine = fin is None or fin.get("decided_by") == STAGE
    if sel is None:
        if sf.exists():
            sf.unlink()
        if fin and fin.get("decided_by") == STAGE:
            ff.unlink()
            return "Selector가 정한 최종 선택을 지웠다."
        return ""
    d.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")
    if not mine:
        return "최종 선택은 사용자가 이미 저장해 두어 덮어쓰지 않았다."
    rec = {"choice": sel["choice"], "market": sel["market"], "ticker": sel["ticker"], "name": sel["name"],
           "source": "agent", "decided_by": STAGE, "week": week,
           "note": sel.get("deciding_factor", ""), "chosen_at": datetime.now().isoformat(timespec="seconds")}
    ff.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"최종 선택을 {sel['name']}({sel['ticker']})로 저장했다."


# ---------------------------------------------------------------- 실행

def _meta(d: Path, week: int, cfg: dict, mode: str) -> tuple[Path, dict]:
    f = d / "run_meta.json"
    m = _json(f) or {"week": week, "market": "final", "prompt_version": cfg["meta"]["prompt_version"],
                     "config_version": cfg["meta"]["config_version"], "stages": {}}
    m["mode"] = mode
    return f, m


def run_manual(cfg: dict, week: int) -> int:
    d = out_dir(week)
    v = values(cfg, week, web_search=True, max_searches=MANUAL_SEARCH)
    text = render(cfg, v)
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"rendered_{STAGE}.txt"
    dst.write_text(text, encoding="utf-8")
    mf, meta = _meta(d, week, cfg, "manual")
    meta["stages"][STAGE] = {"rendered_at": datetime.now().isoformat(timespec="seconds"), "model": ""}
    save_meta(mf, meta)
    print(f"[selector] 렌더링 완료 — {dst.relative_to(ROOT)} ({len(text):,}자)")
    print("클립보드에 복사됨." if to_clipboard(text) else "클립보드 복사 실패. 파일을 직접 열 것.")
    print(f"""
  1. Claude 웹/앱에서 '새 대화'를 연다 — 웹 검색을 켤 것
  2. 붙여넣고 실행한다
  3. 응답 전문을 데스크의 '응답 붙여넣기'로 저장한다 ({(d / 'responses' / f'{STAGE}.md').relative_to(ROOT)})""")
    return 0


def run_auto(cfg: dict, week: int, provider) -> int:
    lc = cfg["llm"]
    d = out_dir(week)
    model = lc["models"].get(STAGE) or lc["models"]["trader"]
    budget = int((lc.get("search_budget") or {}).get(STAGE) or getattr(provider, "max_uses", 0))
    use_search = provider.supports_web_search
    v = values(cfg, week, web_search=use_search, max_searches=budget)
    text = render(cfg, v)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"rendered_{STAGE}.txt").write_text(text, encoding="utf-8")
    max_tokens = int(lc.get("max_tokens") or 16000)
    temperature = lc.get("temperature")
    print(f"  [selector] 호출 — {model} ({len(text):,}자{f', 웹 검색 {budget}회' if use_search else ''})")
    t0 = datetime.now()
    reply = provider.complete(model=model, prompt=text, max_tokens=max_tokens, temperature=temperature,
                              web_search=use_search, max_searches=budget if use_search else None,
                              on_search=lambda q: print(f"    검색  {q}"))
    sel = parse(week, reply.text)
    for attempt in range(int(lc.get("max_retries") or 2)):
        if sel:
            break
        print(f"    JSON 파싱 실패 — 재요청 {attempt + 1}")
        r2 = provider.complete(model=model, max_tokens=max_tokens, temperature=temperature, web_search=False,
                               prompt=text + "\n\n---\n직전 응답에서 JSON을 파싱할 수 없었다. "
                                             "설명 없이 위 스키마의 JSON 객체 하나만 출력하라.")
        sel = parse(week, r2.text)
        if sel:
            reply.text += "\n\n<!-- JSON 재요청 응답 -->\n" + r2.text
    (d / "responses").mkdir(parents=True, exist_ok=True)
    (d / "responses" / f"{STAGE}.md").write_text(reply.text, encoding="utf-8")
    mf, meta = _meta(d, week, cfg, "auto")
    meta["provider"] = provider.name
    meta["stages"][STAGE] = {"rendered_at": t0.isoformat(timespec="seconds"), "model": reply.model,
                             "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1),
                             "web_search": use_search, "web_search_calls": reply.web_searches,
                             "usage": reply.usage, "chars": len(reply.text)}
    save_meta(mf, meta)
    if not sel:
        print("[selector] JSON을 끝내 못 받았다. responses/selector.md를 직접 확인할 것.", file=sys.stderr)
        return 1
    print(f"  선택: {sel['choice']} {sel['name']}({sel['ticker']}) — {sel.get('deciding_factor', '')}")
    print(f"  {apply(week, sel)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--week", type=int)
    p.add_argument("--mode", choices=["auto", "manual"], help="생략하면 config.run.agent_mode")
    a = p.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    week = a.week or cfg["run"]["week_no"]
    mode = a.mode or cfg["run"]["agent_mode"]
    try:
        if mode == "manual":
            return run_manual(cfg, week)
        provider = llm_mod.build(cfg, week)
        print(f"provider={provider.name}  web_search={'사용' if provider.supports_web_search else '불가'}")
        return run_auto(cfg, week, provider)
    except (FileNotFoundError, llm_mod.LLMError) as e:
        print(f"[selector] 중단: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
