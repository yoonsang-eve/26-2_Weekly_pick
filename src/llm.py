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

    def complete(self, *, model: str, prompt: str, max_tokens: int,
                 temperature: float | None, web_search: bool) -> Reply:
        # web_search는 여기서 쓰지 않는다. 게이트웨이마다 툴 스펙 형식이 달라 확인 전에는
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

        last = ""
        for attempt in range(self.max_retries + 1):
            try:
                r = requests.post(self.url, headers=self.headers, json=body,
                                  timeout=self.timeout)
            except requests.RequestException as e:
                last = f"네트워크 오류: {e}"
            else:
                if r.status_code == 200:
                    return self._parse(r.json(), model)
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

    def complete(self, *, model: str, prompt: str, max_tokens: int,
                 temperature: float | None, web_search: bool) -> Reply:
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
                                "max_uses": self.max_uses}]

        # 서버사이드 검색이 길어지면 API가 턴을 끊고 stop_reason="pause_turn"으로
        # 돌려준다. 그대로 받으면 "검색을 시작하겠습니다" 한 줄만 남는다 (wk4 KR에서
        # 실제로 발생). 응답 내용을 그대로 돌려보내 턴을 이어 붙여야 한다.
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        texts: list[str] = []
        searches = 0
        usage: dict[str, Any] = {}
        last = None

        for round_no in range(self.max_pause_rounds):
            kwargs["messages"] = messages
            try:
                with self.client.messages.stream(**kwargs) as s:
                    msg = s.get_final_message()
            except self._anthropic.APIError as e:
                raise LLMError(f"Anthropic API 오류: {e}") from e

            if msg.stop_reason == "refusal":
                raise LLMError(
                    f"모델이 응답을 거부했다: {getattr(msg, 'stop_details', None)}")

            texts += [b.text for b in msg.content if b.type == "text"]
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

            if msg.stop_reason != "pause_turn":
                break
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

def build(cfg: dict):
    load_dotenv()
    llm = cfg["llm"]
    provider = llm.get("provider", "openai_compat")
    timeout = int(llm.get("timeout_sec") or 900)
    retries = int(llm.get("max_retries") or 2)
    if provider == "openai_compat":
        return OpenAICompatProvider(llm.get("openai_compat") or {}, timeout, retries)
    if provider == "anthropic":
        return AnthropicProvider(llm.get("anthropic") or {}, timeout, retries)
    raise LLMError(f"알 수 없는 llm.provider: {provider}")
