"""이번 주 작업 패널 — 주차 설정, 스크립트 실행, manual 응답 저장, 산출물 열람.

스크립트는 화이트리스트에 있는 것만 서브프로세스로 돌린다. LLM을 쓰는 것은
agents auto 하나뿐이고, 요청 본문에 confirm_api=true가 없으면 거부한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
CONFIG = ROOT / "config.yaml"
PY = sys.executable
STAGES = ["analyst", "bull", "bear", "trader"]

sys.path.insert(0, str(ROOT / "src"))
import events  # noqa: E402

router = APIRouter()

from app.calendar_data import HOLIDAYS  # noqa: E402  (휴장일 표는 시장 패널 캘린더와 공유)


# ------------------------------------------------------------------ 주차 설정
def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    # null을 빈 값으로 바꿔 쓰지 않게 한다 (config.yaml의 temperature: null)
    y.representer.add_representer(
        type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null"))
    return y


def _load_cfg():
    return _yaml().load(CONFIG.read_text(encoding="utf-8"))


def _trading_days(market: str, buy: date, sell: date) -> list[date]:
    days, d = [], buy
    while d <= sell:
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS[market]:
            days.append(d)
        d += timedelta(days=1)
    return days


def _suggest(cfg) -> dict:
    """오늘 기준 다음 평가 주간. 1주차 월요일은 config의 현재 주차에서 역산한다."""
    run = cfg["run"]
    cur_mon = date.fromisoformat(str(run["buy_date"]))
    cur_mon -= timedelta(days=cur_mon.weekday())
    wk1 = cur_mon - timedelta(weeks=int(run["week_no"]) - 1)
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    if today.weekday() >= 4:            # 금~일이면 다음 주가 추천 대상
        mon += timedelta(weeks=1)
    fri = mon + timedelta(days=4)
    per = {}
    for m in ("KR", "US"):
        td = _trading_days(m, mon, fri)
        per[m] = {"buy_date": td[0].isoformat() if td else mon.isoformat(),
                  "sell_date": td[-1].isoformat() if td else fri.isoformat(),
                  "holding_days": len(td),
                  "holidays": [f"{k} {v}" for k, v in HOLIDAYS[m].items()
                               if mon.isoformat() <= k <= fri.isoformat()]}
    return {"week_no": (mon - wk1).days // 7 + 1, "buy_date": mon.isoformat(),
            "sell_date": fri.isoformat(), "markets": per}


def _current(cfg) -> dict:
    run = cfg["run"]
    per = {}
    for m in ("KR", "US"):
        mk = cfg["markets"][m]
        per[m] = {"buy_date": str(mk.get("buy_date") or run["buy_date"]),
                  "sell_date": str(mk.get("sell_date") or run["sell_date"]),
                  "holding_days": mk.get("holding_days")}
    return {"week_no": int(run["week_no"]), "buy_date": str(run["buy_date"]),
            "sell_date": str(run["sell_date"]), "agent_mode": str(run.get("agent_mode", "manual")),
            "markets_run": list(run["markets"]), "macro_calendar": str(run.get("macro_calendar") or ""),
            "prompt_version": str(cfg["meta"]["prompt_version"]), "markets": per}


@router.get("/api/week")
def get_week():
    cfg = _load_cfg()
    return {"current": _current(cfg), "suggested": _suggest(cfg)}


class MarketDates(BaseModel):
    buy_date: str
    sell_date: str
    holding_days: int


class WeekIn(BaseModel):
    week_no: int
    buy_date: str
    sell_date: str
    macro_calendar: str = ""
    markets: dict[str, MarketDates]


@router.post("/api/week")
def set_week(w: WeekIn):
    for v in [w.buy_date, w.sell_date] + [x for m in w.markets.values() for x in (m.buy_date, m.sell_date)]:
        date.fromisoformat(v)                           # 형식 검증
    y = _yaml()
    cfg = y.load(CONFIG.read_text(encoding="utf-8"))
    run = cfg["run"]
    run["week_no"] = w.week_no
    run["buy_date"] = w.buy_date
    run["sell_date"] = w.sell_date
    run["macro_calendar"] = w.macro_calendar
    for m, md in w.markets.items():
        mk = cfg["markets"][m]
        # 공통값과 같으면 시장별 덮어쓰기를 지운다 (config 주석의 운용 규칙)
        for k, common in (("buy_date", w.buy_date), ("sell_date", w.sell_date)):
            val = getattr(md, k)
            if val == common:
                mk.pop(k, None)
            else:
                mk[k] = val
        mk["holding_days"] = md.holding_days
    with CONFIG.open("w", encoding="utf-8") as f:
        y.dump(cfg, f)
    return get_week()


# ------------------------------------------------------------------ 작업 실행
class Job:
    def __init__(self, kind: str, label: str, argv: list[str], week: int | None, market: str | None):
        self.id = uuid.uuid4().hex[:8]
        self.kind, self.label, self.argv = kind, label, argv
        self.week, self.market = week, market
        self.status = "running"
        self.rc: int | None = None
        self.started = datetime.now().isoformat(timespec="seconds")
        self.ended: str | None = None
        self.lines: list[str] = []

    def to_dict(self, since: int = 0) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status,
                "rc": self.rc, "started": self.started, "ended": self.ended, "week": self.week,
                "market": self.market, "n": len(self.lines), "lines": self.lines[since:]}


JOBS: list[Job] = []
_lock = threading.Lock()


def _run(job: Job) -> None:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    try:
        p = subprocess.Popen(job.argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=env, text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in p.stdout:
            job.lines.append(line.rstrip("\n"))
        job.rc = p.wait()
        job.status = "ok" if job.rc == 0 else "fail"
    except Exception as e:
        job.lines.append(f"[실행 실패] {e}")
        job.status = "fail"
    job.ended = datetime.now().isoformat(timespec="seconds")


class JobIn(BaseModel):
    kind: str
    market: str | None = None
    week: int | None = None
    full: bool = False              # deck
    confirm_api: bool = False       # agents_auto


def _argv(j: JobIn, week: int) -> tuple[list[str], str]:
    mk = ["--market", j.market] if j.market else []
    wk = ["--week", str(week)]
    m = j.market or "전체"
    if j.kind == "screener":
        return [PY, "src/screener.py", *wk, *mk], f"스크리닝 {m}"
    if j.kind == "agents_manual":
        return [PY, "src/agents.py", "--mode", "manual", *wk, *mk], f"프롬프트 생성 {m}"
    if j.kind == "agents_auto":
        if not j.confirm_api:
            raise HTTPException(400, "auto 모드는 공용 LLM API를 쓴다. confirm_api=true가 필요하다.")
        return [PY, "src/agents.py", "--mode", "auto", *wk, *mk], f"에이전트 auto {m}"
    if j.kind == "settle":
        return [PY, "src/settle.py", *wk, *mk], f"정산 {m}"
    if j.kind == "cohort":
        return [PY, "src/cohort.py", *wk, *mk], f"후보군 정산 {m}"
    if j.kind == "final_settle":
        return [PY, "src/final.py", *wk], "최종 선택 정산"
    if j.kind == "deck_final":
        return ([PY, "src/deck/build.py", *wk, "--final"] + (["--full"] if j.full else []),
                f"최종 선택 리포트{' (상세판)' if j.full else ''}")
    if j.kind == "deck":
        if not j.market:
            raise HTTPException(400, "리포트는 시장을 골라야 한다.")
        return ([PY, "src/deck/build.py", *wk, *mk] + (["--full"] if j.full else []),
                f"리포트 {m}{' (상세판)' if j.full else ''}")
    raise HTTPException(400, f"알 수 없는 작업: {j.kind}")


@router.post("/api/jobs")
def start_job(j: JobIn):
    with _lock:
        if any(x.status == "running" for x in JOBS):
            raise HTTPException(409, "다른 작업이 실행 중이다.")
        week = j.week or int(_load_cfg()["run"]["week_no"])
        argv, label = _argv(j, week)
        job = Job(j.kind, label, argv, week, j.market)
        JOBS.append(job)
        del JOBS[:-30]
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job.to_dict()


@router.get("/api/jobs")
def list_jobs():
    return [j.to_dict(since=len(j.lines)) for j in reversed(JOBS)]


@router.get("/api/jobs/{jid}")
def get_job(jid: str, since: int = 0):
    for j in JOBS:
        if j.id == jid:
            return j.to_dict(since)
    raise HTTPException(404)


# ------------------------------------------------------------------ manual 모드
def _run_dir(week: int, market: str) -> Path:
    if market not in ("KR", "US", "final"):          # final = 직접 지정 종목의 리포트·차트
        raise HTTPException(400)
    return OUT / f"wk{week}" / market


@router.get("/api/runs/{week}/{market}/status")
def run_status(week: int, market: str):
    """이번 주 작업 패널의 체크리스트 상태."""
    d = _run_dir(week, market)
    resp = d / "responses"
    stages = [{"stage": s, "rendered": (d / f"rendered_{s}.txt").exists(),
               "response": (resp / f"{s}.md").exists()} for s in STAGES]
    nxt = next((s["stage"] for s in stages if not s["response"]), None)
    decks = sorted(p.name for p in d.glob("deck_*.pptx")) if d.exists() else []
    n_cand = 0
    if (d / "candidates.csv").exists():
        n_cand = max(0, len((d / "candidates.csv").read_text(encoding="utf-8-sig").strip().splitlines()) - 1)
    return {"week": week, "market": market, "exists": d.exists(), "n_candidates": n_cand,
            "screened": (d / "candidates.csv").exists(), "stages": stages, "next_stage": nxt,
            "picked": (d / "pick.json").exists(), "cohort": (d / "cohort.csv").exists(),
            "decks": decks,
            "final_decks": sorted(p.name for p in (OUT / f"wk{week}" / "final").glob("deck_*.pptx"))}


class ResponseIn(BaseModel):
    text: str


@router.post("/api/runs/{week}/{market}/response/{stage}")
def save_response(week: int, market: str, stage: str, r: ResponseIn):
    if stage not in STAGES:
        raise HTTPException(400)
    if len(r.text.strip()) < 50:
        raise HTTPException(400, "응답이 너무 짧다. 전문을 붙여넣을 것.")
    d = _run_dir(week, market)
    (d / "responses").mkdir(parents=True, exist_ok=True)
    (d / "responses" / f"{stage}.md").write_text(r.text, encoding="utf-8")
    pick = None
    if stage == "trader":
        import agents                                   # extract_pick 재사용 (LLM 호출 없음)
        pick = agents.extract_pick(r.text)
        if pick:
            (d / "pick.json").write_text(json.dumps(pick, ensure_ascii=False, indent=2), encoding="utf-8")
    events.bind(week, market)
    if events.active():
        events.emit(stage, "done", "응답 저장됨 (manual)", pick=(pick or {}).get("ticker"))
    return {"saved": True, "pick": pick,
            "warning": None if stage != "trader" or pick else "trader 응답에서 JSON을 찾지 못했다."}


# ------------------------------------------------------------------ 산출물
KIND = {".pptx": "deck", ".png": "image", ".md": "text", ".txt": "text", ".csv": "table", ".json": "data",
        ".jsonl": "data"}


@router.get("/api/runs/{week}/{market}/files")
def list_files(week: int, market: str):
    d = _run_dir(week, market)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.rglob("*")):
        if p.is_file():
            st = p.stat()
            out.append({"path": p.relative_to(d).as_posix(), "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes"),
                        "kind": KIND.get(p.suffix.lower(), "file")})
    return out


def _safe(week: int, market: str, rel: str) -> Path:
    d = _run_dir(week, market).resolve()
    p = (d / rel).resolve()
    if d not in p.parents or not p.is_file():
        raise HTTPException(404)
    return p


@router.get("/api/runs/{week}/{market}/file/{rel:path}")
def get_file(week: int, market: str, rel: str, download: bool = False):
    p = _safe(week, market, rel)
    return FileResponse(p, filename=p.name if download else None)


@router.post("/api/runs/{week}/{market}/open/{rel:path}")
def open_file(week: int, market: str, rel: str):
    """로컬 기본 앱으로 연다 (pptx -> PowerPoint). 서버가 이 PC에서 돌 때만 의미가 있다."""
    p = _safe(week, market, rel)
    if not hasattr(os, "startfile"):
        raise HTTPException(501, "Windows에서만 지원한다.")
    os.startfile(p)  # noqa: S606 — outputs/ 아래 파일로 제한됨
    return {"opened": p.name}


# ------------------------------------------------------------------ 최종 선택 (주차당 1종목)
def _read_json(f: Path) -> dict | None:
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _final_log() -> dict:
    f = ROOT / "final_log.csv"
    if not f.exists():
        return {}
    import csv
    with f.open(encoding="utf-8-sig") as fh:
        return {int(r["week"]): r for r in csv.DictReader(fh)}


@router.get("/api/week/{week}/final")
def get_final(week: int):
    opts = []
    for m in ("KR", "US"):
        p = _read_json(OUT / f"wk{week}" / m / "pick.json")
        if p:
            opts.append({"id": m, "market": m, "ticker": str(p["ticker"]), "name": p.get("name", ""),
                         "range": p.get("expected_return_range"), "catalyst": p.get("catalyst", "")})
    return {"week": week, "options": opts, "final": _read_json(OUT / f"wk{week}" / "final.json"),
            "settled": _final_log().get(week)}


@router.get("/api/finals")
def finals():
    log = _final_log()
    out = []
    for wk in sorted(OUT.glob("wk*"), key=lambda p: int(p.name[2:]) if p.name[2:].isdigit() else 0):
        f = _read_json(wk / "final.json")
        if f:
            out.append({**f, "settled": log.get(int(wk.name[2:]))})
    return out


_listing: dict = {}


def _kr_names() -> dict:
    if "kr" not in _listing:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        _listing["kr"] = dict(zip(df["Code"].astype(str), df["Name"]))
    return _listing["kr"]


def _us_names() -> dict:
    if "us" not in _listing:
        import csv
        with (ROOT / "data" / "us_universe.csv").open(encoding="utf-8") as f:
            _listing["us"] = {r["symbol"]: r["name"] for r in csv.DictReader(f)}
    return _listing["us"]


@router.get("/api/lookup")
def lookup(market: str, q: str):
    """직접 지정 종목 검색. KR은 KRX 전종목, US는 유니버스(S&P500+NDX) + 입력한 티커 그대로."""
    q = q.strip()
    if not q:
        return []
    names = _kr_names() if market == "KR" else _us_names()
    ql = q.lower()
    hits = [{"ticker": t, "name": n} for t, n in names.items() if ql in t.lower() or ql in str(n).lower()]
    hits.sort(key=lambda h: (h["ticker"].lower() != ql, len(h["name"])))
    if market == "US" and q.upper() not in names and q.replace("-", "").isalnum():
        hits.append({"ticker": q.upper(), "name": "(유니버스 밖 — 티커 그대로 사용)"})
    return hits[:12]


class FinalIn(BaseModel):
    choice: str                 # KR | US | custom
    market: str | None = None
    ticker: str | None = None
    name: str | None = None
    note: str = ""


@router.post("/api/week/{week}/final")
def set_final(week: int, f: FinalIn):
    if f.choice in ("KR", "US"):
        p = _read_json(OUT / f"wk{week}" / f.choice / "pick.json")
        if not p:
            raise HTTPException(400, "그 시장의 에이전트 추천이 아직 없다.")
        rec = {"choice": f.choice, "market": f.choice, "ticker": str(p["ticker"]), "name": p.get("name", ""),
               "source": "agent"}
    elif f.choice == "custom":
        if f.market not in ("KR", "US") or not f.ticker:
            raise HTTPException(400, "시장과 종목을 지정해야 한다.")
        t = f.ticker.strip().upper() if f.market == "US" else f.ticker.strip().zfill(6)
        name = f.name or (_kr_names().get(t) if f.market == "KR" else _us_names().get(t)) or t
        if f.market == "KR" and t not in _kr_names():
            raise HTTPException(400, f"KRX에 없는 종목코드다: {t}")
        rec = {"choice": "custom", "market": f.market, "ticker": t, "name": name, "source": "user"}
    else:
        raise HTTPException(400)
    rec.update(week=week, note=f.note.strip(), chosen_at=datetime.now().isoformat(timespec="seconds"))
    d = OUT / f"wk{week}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "final.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return get_final(week)
