"""파이프라인 이벤트 기록 — 데스크 UI(app/)가 읽는 outputs/wk{N}/{시장}/events.jsonl.

각 스크립트는 시장 하나를 처리하기 전에 bind()로 대상을 정하고, 이후 emit()만 부른다.
기록은 부수 효과일 뿐이라 어떤 이유로든 실패해도 파이프라인을 멈추지 않는다.

    events.bind(week, "KR")
    events.emit("analyst", "start", "검색 예산 24", model="claude-sonnet-5")

agent: screener | analyst | bull | bear | trader | settle
kind : start | step | search | done | error | wait
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_target: Path | None = None


def path_for(week: int, market: str) -> Path:
    return ROOT / "outputs" / f"wk{week}" / market / "events.jsonl"


def bind(week: int, market: str) -> None:
    global _target
    _target = path_for(week, market)


def emit(agent: str, kind: str, msg: str = "", **data) -> None:
    if _target is None:
        return
    rec = {"ts": datetime.now().isoformat(timespec="milliseconds"),
           "agent": agent, "kind": kind, "msg": msg}
    if data:
        rec["data"] = data
    try:
        _target.parent.mkdir(parents=True, exist_ok=True)
        with _target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # 기록 실패로 스크리닝·LLM 호출을 날리지 않는다
        print(f"  (이벤트 기록 실패: {e})", file=sys.stderr)


def active() -> bool:
    """이 실행의 기록 파일이 이미 있는가."""
    return _target is not None and _target.exists()


def seen(agent: str, kind: str) -> bool:
    """이미 기록된 이벤트인가. manual 모드에서 중복 'done'을 막는 데 쓴다."""
    if _target is None or not _target.exists():
        return False
    try:
        for line in _target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("agent") == agent and r.get("kind") == kind:
                return True
    except OSError:
        pass
    return False
