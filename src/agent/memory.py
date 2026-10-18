"""지난 주차 기록과 교훈 — 에이전트의 피드백 고리. LLM 없음.

과적합 방지가 이 모듈의 존재 이유다. 표본은 주당 1개라 한두 주 결과로 행동을 바꾸면 노이즈를 학습한다.
그래서 세 가지를 지킨다.

1. 결과(수익률)보다 과정(검증 실패·날짜 오류·국면 오판)을 기록한다. 과정 교훈은 다음 주에도 그대로
   적용되지만 '반도체가 안 됐다' 같은 결과 교훈은 다음 주 시장과 무관할 수 있다.
2. 교훈마다 지지·반증 주차를 센다. 지지 3주 미만은 '가설'로만 보여 주고 규칙으로 쓰지 말라고 적는다.
3. 원자료(주차별 수익률·벤치마크·당시 국면 판단)를 같이 보여 준다. 에이전트가 교훈을 원자료로 다시
   검증할 수 있게.

    data/agent_lessons.json         교훈 목록 (review 단계가 추가·강화·반박한다)
    outputs/wk{N}/agent/decision.json  그 주의 결정 (국면 판단·자세·종목)
    outputs/wk{N}/agent/review.json    사후 회고
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "outputs"
LESSONS = ROOT / "data" / "agent_lessons.json"
ESTABLISHED = 3          # 지지 주차가 이 이상이어야 '확립된 교훈'


def _csv(f: Path) -> list[dict]:
    if not f.exists():
        return []
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _json(f: Path) -> dict:
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_lessons() -> list[dict]:
    try:
        return json.loads(LESSONS.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_lessons(ls: list[dict]) -> None:
    LESSONS.parent.mkdir(parents=True, exist_ok=True)
    LESSONS.write_text(json.dumps(ls, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_review(week: int, review: dict) -> list[dict]:
    """review.json의 lessons 갱신을 교훈 목록에 반영한다.

    review["lesson_updates"] = [{"action": "new"|"support"|"against", "id": "L3", "text": ..., "kind": ...}]
    같은 주차의 회고를 다시 돌리면 그 주차 기록을 먼저 지우고 다시 넣는다 (중복 지지 방지).
    """
    ls = load_lessons()
    for l in ls:
        l["support"] = [w for w in l.get("support", []) if w != week]
        l["against"] = [w for w in l.get("against", []) if w != week]
    ls = [l for l in ls if l.get("created_week") != week]
    nxt = 1 + max((int(l["id"][1:]) for l in ls if str(l.get("id", "")).startswith("L")), default=0)
    by_id = {l["id"]: l for l in ls}
    for u in review.get("lesson_updates") or []:
        act = u.get("action")
        if act == "new" and (u.get("text") or "").strip():
            l = {"id": f"L{nxt}", "created_week": week, "text": u["text"].strip(),
                 "kind": u.get("kind") if u.get("kind") in ("process", "market") else "process",
                 "support": [week], "against": []}
            ls.append(l)
            by_id[l["id"]] = l
            nxt += 1
        elif act in ("support", "against") and u.get("id") in by_id:
            lst = by_id[u["id"]]["support" if act == "support" else "against"]
            if week not in lst:
                lst.append(week)
    save_lessons(ls)
    return ls


def lessons_text() -> str:
    ls = load_lessons()
    if not ls:
        return "(아직 교훈 없음)"
    lines = []
    for l in sorted(ls, key=lambda x: (-len(x.get("support", [])), x["id"])):
        s, a = len(l.get("support", [])), len(l.get("against", []))
        net = s - a
        tag = "확립" if net >= ESTABLISHED else ("반박됨" if net <= 0 and a else "가설")
        lines.append(f"- [{l['id']} · {tag} · {l.get('kind', 'process')} · 지지 {l.get('support')} / 반증 {l.get('against')}] {l['text']}")
    return "\n".join(lines)


def _bench(week: int) -> dict:
    """그 주 벤치마크 수익률 (시장별). log.csv에 있는 것만."""
    out = {}
    for r in _csv(ROOT / "log.csv"):
        if str(r["week"]) == str(week) and r.get("benchmark_ret_pct") not in ("", None):
            out[r["market"]] = r["benchmark_ret_pct"]
    for r in _csv(ROOT / "final_log.csv"):
        if str(r["week"]) == str(week) and r.get("benchmark_ret_pct") not in ("", None):
            out.setdefault(r["market"], r["benchmark_ret_pct"])
    return out


def _cohort(week: int, market: str) -> str:
    rows = _csv(OUT / f"wk{week}" / market / "cohort.csv")
    v = [float(r["ret_pct"]) for r in rows if r.get("role") == "candidate" and r.get("ret_pct") not in ("", None)]
    return f"{sum(v) / len(v):+.2f}%" if v else ""


def track_record(before_week: int) -> str:
    """에이전트에게 주는 지난 주차 원자료 + 교훈. before_week 이전 주차만."""
    fin = {int(r["week"]): r for r in _csv(ROOT / "final_log.csv")}
    weeks = sorted({w for w in fin if w < before_week} |
                   {int(d.name[2:]) for d in OUT.glob("wk*") if d.name[2:].isdigit() and int(d.name[2:]) < before_week})
    lines = ["| 주차 | 제출 종목 | 시장 | 수익률 | KOSPI | S&P500 | 당시 국면 판단 | 자세 | 구 스크리너 후보 평균(KR/US) |",
             "|---|---|---|---|---|---|---|---|---|"]
    rets = []
    for w in weeks:
        f = fin.get(w, {})
        b = _bench(w)
        dec = _json(OUT / f"wk{w}" / "agent" / "decision.json")
        if f.get("ret_pct"):
            rets.append(float(f["ret_pct"]))
        lines.append(" | ".join([
            f"| wk{w}", f"{f.get('name', '')} {f.get('ticker', '')}".strip() or "-", f.get("market", "-"),
            f"{f['ret_pct']}%" if f.get("ret_pct") else "(미정산)",
            f"{b['KR']}%" if b.get("KR") else "-", f"{b['US']}%" if b.get("US") else "-",
            dec.get("regime_view", "(고정 파이프라인 시절)") if dec else "(고정 파이프라인 시절)",
            dec.get("stance", "-") if dec else "-",
            f"{_cohort(w, 'KR') or '-'} / {_cohort(w, 'US') or '-'}"]) + " |")
    summ = (f"정산된 {len(rets)}주 단순합 {sum(rets):+.2f}%p · 평균 {sum(rets) / len(rets):+.2f}% · "
            f"플러스 주 {sum(r > 0 for r in rets)}/{len(rets)}") if rets else "정산된 주차 없음"
    reviews = []
    for w in weeks:
        rv = _json(OUT / f"wk{w}" / "agent" / "review.json")
        if rv.get("summary"):
            reviews.append(f"- wk{w}: {rv['summary']}")
    return "\n".join([
        "## 지난 주차 원자료",
        "wk3~7은 고정 파이프라인(모멘텀+눌림 필터 → ATR 상위 20 → LLM 체인) 시절 기록이다. "
        "'구 스크리너 후보 평균'은 그 필터를 통과한 20종목의 같은 주 평균 수익률이다.",
        *lines, "", summ, "",
        "## 주차별 회고 요약", *(reviews or ["(없음)"]), "",
        "## 누적 교훈 (지지·반증 주차 포함)",
        f"지지가 {ESTABLISHED}주 미만인 교훈은 가설이다. 가설만으로 이번 주 행동을 바꾸지 말고, 이번 주 데이터로 "
        "다시 확인될 때만 참고하라. 수익률 하나로 생긴 결과 교훈(market)보다 과정 교훈(process)을 우선한다.",
        lessons_text(),
    ])
