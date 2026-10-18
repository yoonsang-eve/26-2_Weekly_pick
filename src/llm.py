"""LLM 호출 계층 (agents.py auto 모드용).

두 가지 백엔드를 지원한다. config.yaml의 llm.provider로 고른다.

  openai_compat — OpenAI Chat Completions 호환 게이트웨이.
                  수업에서 받은 Letsur AI 게이트웨이가 여기에 해당한다.
                  base_url 과 API 키 환경변수만 채우면 된다.
  anthropic     — Anthropic Messages API 직접 호출 (공식 SDK, 서버사이드 웹 검색 가능).

이 모듈은 '문자열 프롬프트 하나 -> 문자열 응답 하나'만 책임진다. 단계 간 컨텍스트를
쌓지 않는 것이 파이프라인의 설계 전제이므로(CLAUDE.md 5절) 대화 히스토리를 들고
있는 객체를 만들지 않는다.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
ROOT = Path(__file__).resolve().parent.parent


class LLMError(RuntimeError):
    pass


def load_dotenv(path: Path | None = None) -> None:
    """저장소 루트의 .env를 환경변수로 읽는다 (이미 설정된 값은 덮어쓰지 않는다).

    API 키를 셸 세션마다 다시 export 하지 않으려는 용도다. .env는 .gitignore에
    들어 있으므로 커밋되지 않는다. 외부 의존성을 늘리지 않으려고 직접 파싱한다.
    """
    f = path or ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Reply:
    text: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    web_searches: int = 0


def billed_input(usage: dict) -> int:
    """과금되는 입력 토큰. 캐시 쓰기·읽기도 입력으로 센다 (보수적으로)."""
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "prompt_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens"))


def billed_output(usage: dict) -> int:
    return int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)


class Budget:
    """주차 단위 토큰 상한 (config llm.token_budget).

    호출할 때마다(서버 검색의 pause_turn 라운드 포함) 사용량을
    outputs/wk{N}/llm_spend.jsonl에 바로 적는다. 실패한 호출도 돈은 나가므로
    run_meta.json(성공한 단계만 기록)이 아니라 이 장부로 센다. 같은 단계를 실수로
    여러 번 돌려도 장부가 누적되므로 주차 상한에서 멈춘다.
    상한을 올리려면 config를 고치거나 장부 파일을 지운다 — 둘 다 의도적인 행동이어야 한다.
    """

    def __init__(self, ledger: Path, conf: dict):
        self.ledger = ledger
        self.max_input = int(conf.get("week_input") or 0)
        self.max_output = int(conf.get("week_output") or 0)
        self.per_call_input = int(conf.get("per_call_input") or 0)
        # 캐시 읽기는 단가가 입력의 1/10이다. 대화를 이어 붙이는 에이전트 루프는 캐시 읽기가 대부분이라
        # 1로 세면 실제 비용보다 몇 배 빨리 상한에 닿는다. 기본값 1은 기존 파이프라인과 같은 보수적 계산이다.
        self.cache_read_weight = float(conf.get("cache_read_weight", 1.0))

    def spent(self) -> tuple[int, int]:
        i = o = 0
        if self.ledger.exists():
            for line in self.ledger.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    i += int(r.get("input") or 0)
                    o += int(r.get("output") or 0)
        return i, o

    def check(self) -> None:
        i, o = self.spent()
        if (self.max_input and i >= self.max_input) or (self.max_output and o >= self.max_output):
            raise LLMError(
                f"주차 토큰 예산 소진 — 입력 {i:,}/{self.max_input:,}, 출력 {o:,}/{self.max_output:,} "
                f"({self.ledger.relative_to(ROOT)}). 더 쓰려면 config llm.token_budget을 올릴 것.")

    def record(self, model: str, usage: dict) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        weighted = billed_input(usage) - cache_read + cache_read * self.cache_read_weight
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": model,
               "input": int(round(weighted)), "output": billed_output(usage)}
        if self.cache_read_weight != 1.0:
            row["cache_read_raw"] = cache_read
        with self.ledger.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(row) + "\n")


def _sleep(attempt: int) -> None:
    time.sleep(min(2 ** attempt + random.random(), 30))


# --------------------------------------------------------- openai_compat

class OpenAICompatProvider:
    """OpenAI /chat/completions 호환 게이트웨이.

    게이트웨이마다 인증 헤더가 다르다. Authorization: Bearer 가 아니면 config의
    llm.openai_compat.auth_header / auth_prefix 를 바꾼다.
    """

    name = "openai_compat"

    def __init__(self, conf: dict, timeout: int, max_retries: int):
        base = (conf.get("base_url") or "").rstrip("/")
        if not base:
            raise LLMError(
                "config.yaml의 llm.openai_compat.base_url이 비어 있다.\n"
                "Letsur 플랫폼 > AI 게이트웨이 > 'API 사용 가이드'의 엔드포인트를 넣을 것.")
        self.url = base + ("" if base.endswith("/chat/completions")
                           else "/chat/completions")
        env = conf.get("api_key_env") or "LETSUR_API_KEY"
        key = os.environ.get(env, "").strip()
        if not key:
            raise LLMError(
                f"API 키가 없다. 저장소 루트에 .env 파일을 만들고 아래 한 줄을 넣을 것.\n"
                f"  {env}=발급받은_키\n"
                f'  (또는 PowerShell에서 $env:{env} = "..." )\n'
                f"  .env는 .gitignore에 있어 커밋되지 않는다.")
        self.headers = {"Content-Type": "application/json"}
        self.headers[conf.get("auth_header") or "Authorization"] = \
            (conf.get("auth_prefix") or "") + key
        self.headers.update(conf.get("extra_headers") or {})
        self.supports_web_search = bool(conf.get("web_search"))
        self.timeout = timeout
        self.max_retries = max_retries
        self.budget: Budget | None = None

    def complete(self, *, model: str, prompt: str, max_tokens: int,
                 temperature: float | None, web_search: bool,
                 max_searches: int | None = None, on_search=None) -> Reply:
        # web_search·max_searches는 여기서 쓰지 않는다. 게이트웨이마다 툴 스펙 형식이 달라 확인 전에는
        # 보낼 수 없고, config의 web_search는 '게이트웨이가 알아서 검색해 주는가'라는
        # 선언으로만 쓰여 프롬프트 문구(search_policy)를 고른다.
        import requests

        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if self.budget:
            self.budget.check()

        last = ""
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(self.url, headers=self.headers, json=body,
                                  timeout=self.timeout)
            except requests.RequestException as e:
                last = f"네트워크 오류: {e}"
            else:
                if r.status_code == 200:
                    reply = self._parse(r.json(), model)
                    if self.budget:
                        self.budget.record(reply.model, reply.usage)
                    return reply
                last = f"HTTP {r.status_code}: {r.text[:500]}"
                if r.status_code not in RETRY_STATUS:
                    break
            if attempt < self.max_retries:
                print(f"    재시도 {attempt + 1}/{self.max_retries} — {last[:120]}")
                _sleep(attempt)
        raise LLMError(f"{self.url} 호출 실패. {last}")

    @staticmethod
    def _parse(data: dict, model: str) -> Reply:
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"예상과 다른 응답 형식: {json.dumps(data)[:500]}") from e
        content = msg.get("content")
        if isinstance(content, list):
            # 일부 게이트웨이는 Anthropic 스타일 블록 배열을 그대로 돌려준다
            content = "".join(b.get("text", "") for b in content
                              if isinstance(b, dict))
        if not (content or "").strip():
            raise LLMError(f"빈 응답. finish_reason="
                           f"{data.get('choices', [{}])[0].get('finish_reason')}")
        return Reply(text=content, model=data.get("model") or model,
                     usage=data.get("usage") or {})


# ------------------------------------------------------------- anthropic

class AnthropicProvider:
    """Anthropic Messages API. 서버사이드 웹 검색 툴을 쓸 수 있다."""

    name = "anthropic"

    def __init__(self, conf: dict, timeout: int, max_retries: int):
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("anthropic 패키지가 없다. pip install anthropic") from e
        env = conf.get("api_key_env") or "ANTHROPIC_API_KEY"
        if not os.environ.get(env, "").strip():
            raise LLMError(f"API 키가 없다. .env에 {env}=... 한 줄을 넣을 것.")
        self._anthropic = anthropic
        # base_url을 게이트웨이로 돌릴 수 있다. SDK가 /v1/messages를 붙이므로
        # base_url에 /v1을 넣으면 /v1/v1/messages가 되어 실패한다.
        kw: dict[str, Any] = {"api_key": os.environ[env], "timeout": timeout,
                              "max_retries": max_retries}
        if (conf.get("base_url") or "").strip():
            kw["base_url"] = conf["base_url"].strip().rstrip("/")
        self.client = anthropic.Anthropic(**kw)
        self.supports_web_search = bool(conf.get("web_search", True))
        self.tool_type = conf.get("web_search_tool_type") or "web_search_20260209"
        self.max_uses = int(conf.get("max_web_searches") or 8)
        # pause_turn을 몇 번까지 이어받을지. 검색 예산이 클수록 더 필요하다.
        self.max_pause_rounds = int(conf.get("max_pause_rounds") or 8)
        self.budget: Budget | None = None

    def complete(self, *, model: str, prompt: str, max_tokens: int,
                 temperature: float | None, web_search: bool,
                 max_searches: int | None = None, on_search=None) -> Reply:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "adaptive"},
        }
        if temperature is not None:
            # 주의: 현재 세대 모델(Opus 5 / Sonnet 5 등)은 temperature를 받지 않는다.
            # config에서 null로 두는 것이 기본값인 이유다.
            kwargs["temperature"] = temperature
        if web_search and self.supports_web_search:
            kwargs["tools"] = [{"type": self.tool_type, "name": "web_search",
                                "max_uses": max_searches or self.max_uses}]

        # 서버사이드 검색이 길어지면 API가 턴을 끊고 stop_reason="pause_turn"으로
        # 돌려준다. 그대로 받으면 "검색을 시작하겠습니다" 한 줄만 남는다 (wk4 KR에서
        # 실제로 발생). 응답 내용을 그대로 돌려보내 턴을 이어 붙여야 한다.
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        texts: list[str] = []
        searches = 0
        seen: set[str] = set()      # on_search로 이미 알린 server_tool_use id

        def notify(block) -> None:
            # 데스크 UI용 부수 효과. 어떤 이유로든 실패해도 호출은 계속한다.
            if on_search is None or getattr(block, "type", "") != "server_tool_use":
                return
            bid = getattr(block, "id", None)
            if bid in seen:
                return
            seen.add(bid)
            try:
                q = (getattr(block, "input", None) or {}).get("query", "")
                on_search(q)
            except Exception:
                pass
        usage: dict[str, Any] = {}
        last = None

        for round_no in range(self.max_pause_rounds):
            kwargs["messages"] = messages
            if self.budget:
                self.budget.check()
            try:
                with self.client.messages.stream(**kwargs) as s:
                    if on_search is not None:
                        # 검색 블록이 완성되는 즉시 알린다 (pause_turn까지 기다리지 않도록)
                        for ev in s:
                            if getattr(ev, "type", "") == "content_block_stop":
                                notify(getattr(ev, "content_block", None))
                    msg = s.get_final_message()
            except self._anthropic.APIError as e:
                raise LLMError(f"Anthropic API 오류: {e}") from e

            if msg.stop_reason == "refusal":
                raise LLMError(
                    f"모델이 응답을 거부했다: {getattr(msg, 'stop_details', None)}")

            texts += [b.text for b in msg.content if b.type == "text"]
            for b in msg.content:       # 스트림에서 놓친 것 보충
                notify(b)
            searches += sum(1 for b in msg.content
                            if b.type == "web_search_tool_result")
            for k, v in (msg.usage.model_dump() if msg.usage else {}).items():
                if isinstance(v, (int, float)):
                    usage[k] = usage.get(k, 0) + v
                elif isinstance(v, dict):
                    acc = usage.setdefault(k, {})
                    for k2, v2 in v.items():
                        if isinstance(v2, (int, float)):
                            acc[k2] = acc.get(k2, 0) + v2
            last = msg
            if self.budget:
                self.budget.record(msg.model, msg.usage.model_dump() if msg.usage else {})

            if msg.stop_reason != "pause_turn":
                break
            # pause_turn을 이어받으면 지금까지의 대화 전체가 다시 입력으로 들어간다.
            # 한 호출이 상한을 넘었으면 이어받지 않고 끊는다.
            cap = self.budget.per_call_input if self.budget else 0
            if cap and billed_input(usage) >= cap:
                raise LLMError(f"호출 1회 입력 토큰 상한 초과 ({billed_input(usage):,} >= {cap:,}). "
                               f"검색 예산(llm.search_budget)을 줄일 것.")
            print(f"    pause_turn — 턴 이어받기 {round_no + 1}/"
                  f"{self.max_pause_rounds - 1}")
            messages = messages + [{"role": "assistant", "content": msg.content}]
        else:
            raise LLMError(f"pause_turn이 {self.max_pause_rounds}회 연속됐다. "
                           f"max_web_searches를 줄일 것.")

        usage["stop_reason"] = last.stop_reason
        text = "".join(texts)
        if not text.strip():
            raise LLMError(f"빈 응답. stop_reason={last.stop_reason}")
        return Reply(text=text, model=last.model, usage=usage,
                     web_searches=searches)


# ------------------------------------------------------------------ 팩토리

def build(cfg: dict, week: int | None = None):
    load_dotenv()
    llm = cfg["llm"]
    provider = llm.get("provider", "openai_compat")
    timeout = int(llm.get("timeout_sec") or 900)
    retries = int(llm.get("max_retries") or 2)
    if provider == "openai_compat":
        p = OpenAICompatProvider(llm.get("openai_compat") or {}, timeout, retries)
    elif provider == "anthropic":
        p = AnthropicProvider(llm.get("anthropic") or {}, timeout, retries)
    else:
        raise LLMError(f"알 수 없는 llm.provider: {provider}")
    if llm.get("token_budget"):
        wk = week or cfg["run"]["week_no"]
        p.budget = Budget(ROOT / "outputs" / f"wk{wk}" / "llm_spend.jsonl", llm["token_budget"])
        i, o = p.budget.spent()
        print(f"토큰 예산 wk{wk}: 입력 {i:,}/{p.budget.max_input:,} · 출력 {o:,}/{p.budget.max_output:,} "
              f"· 호출당 입력 {p.budget.per_call_input:,}")
    return p
