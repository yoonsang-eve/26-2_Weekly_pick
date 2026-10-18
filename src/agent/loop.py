"""Messages API 도구 사용 루프.

고정 체인과 다른 점은 '다음에 무엇을 할지'를 모델이 정한다는 것뿐이다. 이 루프는 그 결정을 실행하고
예산을 지킨다.

- 응답에 도구 호출이 있으면 실행해 결과를 한 user 메시지에 모아 돌려준다 (병렬 호출 결과를 쪼개지 않는다).
- 서버 검색이 길어 pause_turn이 오면 그대로 이어 받는다.
- 응답 내용(thinking 블록 포함)은 손대지 않고 그대로 이어 붙인다. 지난 턴을 고치면 thinking이 무효가 된다.
- 매 도구 결과 끝에 남은 예산(턴·검색·비판)을 붙인다. 예산이 거의 끝나면 제출을 재촉한다.
- 토큰은 llm.Budget 장부에 호출마다 적는다. 주차 상한에 닿으면 다음 호출을 거부한다.
- 캐시: system에 하나, 가장 최근 user 메시지 끝에 하나. 대화가 길어져도 앞부분은 캐시 읽기로 과금된다.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import LLMError  # noqa: E402

RESULT_CAP = 14000

# unit(≈USD) per 1M 토큰: (입력, 출력). 캐시 쓰기 1.25배, 캐시 읽기 0.1배. 게이트웨이 단가는 config 주석 기준
PRICE = {"claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
         "claude-opus-5": (5.0, 25.0), "claude-haiku-5-5": (0.10, 0.50)}


def cost(model: str, u: dict) -> float:
    """사용량 → 예상 unit. 모르는 모델은 Opus 5 단가로 보수적으로 잡는다."""
    pi, po = next((v for k, v in PRICE.items() if str(model).startswith(k)), (5.0, 25.0))
    g = lambda k: float(u.get(k) or 0)  # noqa: E731
    return (pi * (g("input_tokens") + 1.25 * g("cache_creation_input_tokens") + 0.1 * g("cache_read_input_tokens"))
            + po * g("output_tokens")) / 1e6 + 0.01 * g("web_search_requests")


def _add_usage(acc: dict, u: dict) -> None:
    for k, v in u.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            acc[k] = acc.get(k, 0) + v


def _mark_cache(messages: list[dict]) -> None:
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)
    last = messages[-1]
    if last["role"] == "user" and isinstance(last["content"], list) and last["content"]:
        last["content"][-1]["cache_control"] = {"type": "ephemeral"}


class Transcript:
    """사람이 읽는 작업 기록 (transcript.md). 발표 자료의 '에이전트가 실제로 한 일'의 원자료."""

    def __init__(self, f: Path):
        self.f = f
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# 에이전트 작업 기록 ({datetime.now():%Y-%m-%d %H:%M})\n", encoding="utf-8")

    def w(self, s: str) -> None:
        with self.f.open("a", encoding="utf-8") as fp:
            fp.write(s + "\n")

    def assistant(self, turn: int, content) -> None:
        self.w(f"\n## 턴 {turn}")
        for b in content:
            t = getattr(b, "type", "")
            if t == "text" and b.text.strip():
                self.w(f"\n{b.text.strip()}")
            elif t == "tool_use":
                args = json.dumps(b.input, ensure_ascii=False)
                self.w(f"\n→ **{b.name}** `{args[:600]}`")
            elif t == "server_tool_use":
                q = (getattr(b, "input", None) or {}).get("query", "")
                self.w(f"\n🔍 검색: {q}")

    def results(self, results: list[dict]) -> None:
        for r in results:
            body = r["content"] if isinstance(r["content"], str) else str(r["content"])
            self.w(f"\n<details><summary>← 결과{' (오류)' if r.get('is_error') else ''}</summary>\n\n"
                   f"{body[:3000]}\n\n</details>")


def run(provider, *, model: str, system: str, first_user: str, toolbox, max_turns: int,
        search_budget: int, max_tokens: int, effort: str | None, out: Path,
        per_turn_searches: int = 5, client=None, cost_limit: float = 0.0) -> dict:
    """루프를 돌리고 통계를 돌려준다. 결정은 toolbox.decision에 남는다."""
    client = client or provider.client
    budget = getattr(provider, "budget", None)
    tools: list[dict[str, Any]] = toolbox.tool_schemas()
    if search_budget > 0:
        tools.append({"type": provider.tool_type, "name": "web_search", "max_uses": per_turn_searches})
    sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": [{"type": "text", "text": first_user}]}]
    tr = Transcript(out / "transcript.md")
    tr.w(f"\n> 시작 지시\n\n{first_user}")

    usage: dict = {}
    searches = nudges = turn = 0
    t0 = time.time()
    run._spent = 0.0
    spent = 0.0
    stop = "max_turns"
    while turn < max_turns:
        turn += 1
        _mark_cache(messages)
        if budget:
            budget.check()
        kw: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": sys_blocks, "tools": tools,
                              "messages": messages, "thinking": {"type": "adaptive"}}
        if effort:
            kw["output_config"] = {"effort": effort}
        try:
            with client.messages.stream(**kw) as s:
                msg = s.get_final_message()
        except Exception as e:
            raise LLMError(f"에이전트 호출 실패 (턴 {turn}): {e}") from e
        u = msg.usage.model_dump() if msg.usage else {}
        _add_usage(usage, u)
        stu = u.get("server_tool_use") or {}
        u_cost = dict(u, web_search_requests=(stu.get("web_search_requests") if isinstance(stu, dict) else 0) or 0)
        spent_main = getattr(run, "_spent", 0.0) + cost(msg.model or model, u_cost)
        run._spent = spent_main
        spent = spent_main + sum(cost(c["model"], c.get("usage") or {}) for c in toolbox.critic_usage)
        if budget:
            budget.record(msg.model, u)
        if msg.stop_reason == "refusal":
            raise LLMError(f"모델이 응답을 거부했다: {getattr(msg, 'stop_details', None)}")

        messages.append({"role": "assistant", "content": msg.content})
        tr.assistant(turn, msg.content)
        n_search = sum(1 for b in msg.content if getattr(b, "type", "") == "web_search_tool_result")
        searches += n_search
        print(f"  턴 {turn:>2}  {spent:5.2f}u  {msg.stop_reason:<9} 검색 +{n_search} (누적 {searches})  "
              f"입력 {u.get('input_tokens', 0):,}+캐시 {u.get('cache_read_input_tokens', 0):,} · 출력 {u.get('output_tokens', 0):,}")

        if msg.stop_reason == "pause_turn":
            continue                                  # 서버 검색 이어 받기

        calls = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
        if calls:
            results = []
            for b in calls:
                print(f"    → {b.name}")
                text, err = toolbox.run(b.name, dict(b.input or {}))
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": text[:RESULT_CAP] + ("\n…(잘림)" if len(text) > RESULT_CAP else ""),
                                "is_error": err})
            tr.results(results)
            if toolbox.decision is not None:
                stop = "submitted"
                break
            left = max_turns - turn
            status = (f"[남은 예산] 턴 {left} · 웹 검색 {max(0, search_budget - searches)} · "
                      f"비판 {max(0, int(toolbox.ac.get('max_critiques') or 3) - len(toolbox.critiques))}")
            if searches >= search_budget:
                status += " · 웹 검색 예산을 다 썼다. 더 검색하지 마라."
            if cost_limit and spent >= cost_limit:
                stop = "cost_limit"
                tr.w(f"\n> 비용 상한 {cost_limit} unit 도달 ({spent:.2f}) — 중단")
                break
            if cost_limit and spent >= 0.7 * cost_limit:
                status += (f" · 비용 {spent:.1f}/{cost_limit} unit 사용. 새 탐색을 멈추고, 비판을 받은 후보가 있으면 바로 "
                           "제출하라. 없으면 가장 유력한 후보 하나만 비판받고 제출하라.")
            if left <= 3:
                status += " · 예산이 곧 끝난다. 지금까지 검증한 후보 중 최선을 submit_recommendation으로 제출하라."
            messages.append({"role": "user", "content": results + [{"type": "text", "text": status}]})
            continue

        # 도구 호출 없이 멈췄다 (end_turn / max_tokens). 제출 전이면 이어가게 한다.
        nudges += 1
        if nudges > 3:
            stop = "no_submit"
            break
        hint = ("응답이 길이 제한에 걸렸다. 이어서 진행하라." if msg.stop_reason == "max_tokens" else
                "아직 submit_recommendation을 호출하지 않았다. 남은 작업을 이어가거나, 충분히 검증했다면 제출하라.")
        messages.append({"role": "user", "content": [{"type": "text", "text": hint}]})
        tr.w(f"\n> {hint}")

    return {"stop": stop, "turns": turn, "cost_unit": round(spent, 3), "web_searches": searches, "usage": usage,
            "elapsed_sec": round(time.time() - t0, 1), "model": model}
