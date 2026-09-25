"""보기 전용 링크 만들기 — 더블클릭은 share.bat.

1. 보기 전용 서버를 8766 포트에 띄운다 (APP_MODE=viewer: 읽기만 허용, 계좌 화면 없음).
2. 인터넷 주소를 붙인다.
   - Tailscale이 설치돼 있으면 Funnel로 **고정 주소**를 쓴다: https://<PC이름>.<tailnet>.ts.net
     (외부 443 → 이 PC의 8766. 설정은 --bg로 남아 있어 다음 실행에도 주소가 같다)
   - 없으면 Cloudflare 빠른 터널로 임시 주소를 쓴다 (실행할 때마다 바뀐다).
3. 비밀 키가 붙은 링크를 출력하고 클립보드에 복사한다. 이 링크를 받은 사람만 열 수 있다.

이 창을 닫으면 서버가 꺼져 링크가 열리지 않는다. PC가 켜져 있고 이 창이 열려 있을 때만 보인다.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
PORT = 8766
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def token() -> str:
    """VIEWER_TOKEN을 .env에서 읽고, 없으면 만들어 적는다. 링크를 무효화하려면 이 줄을 지운다."""
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    m = re.search(r"^VIEWER_TOKEN=(\S+)", text, re.M)
    if m:
        return m.group(1)
    t = secrets.token_urlsafe(12)
    ENV.write_text(text.rstrip("\n") + f"\n\n# 보기 전용 링크의 비밀 키 (share.py). 지우면 기존 링크가 모두 막힌다\nVIEWER_TOKEN={t}\n",
                   encoding="utf-8")
    return t


def find(name: str, *candidates: Path) -> str | None:
    c = shutil.which(name)
    if c:
        return c
    return next((str(p) for p in candidates if p.exists()), None)


def tailscale_url(ts: str) -> str | None:
    """Funnel로 8766을 공개하고 고정 주소를 돌려준다. 처음 한 번은 승인 링크가 뜬다."""
    try:
        st = json.loads(subprocess.run([ts, "status", "--json"], capture_output=True, text=True,
                                       encoding="utf-8", timeout=15).stdout or "{}")
    except Exception:
        st = {}
    if st.get("BackendState") != "Running":
        print("Tailscale이 로그인되어 있지 않습니다. 작업 표시줄의 Tailscale 아이콘을 눌러 로그인한 뒤 다시 실행하세요.")
        return None
    host = (st.get("Self") or {}).get("DNSName", "").rstrip(".")
    print("Tailscale Funnel을 켜는 중...")
    # 처음이면 tailscale이 승인 링크를 출력하고 승인될 때까지 기다린다. 출력을 붙잡아 두면
    # 링크가 안 보인 채 멈춰 있으므로 한 줄씩 그대로 보여 주고, 링크는 브라우저로 연다.
    p = subprocess.Popen([ts, "funnel", "--bg", str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    opened = False
    for line in p.stdout:
        line = line.rstrip()
        if line:
            print("  " + line)
        link = re.search(r"https://login\.tailscale\.com/\S+", line)
        if link and not opened:
            opened = True
            print("\n브라우저에 Tailscale 승인 페이지를 열었습니다. [Enable]을 누르면 여기서 자동으로 이어집니다.\n")
            if hasattr(os, "startfile"):
                os.startfile(link.group(0))
    if p.wait() != 0:
        print("\nFunnel을 켜지 못했습니다. 위 메시지를 캡처해 보내 주세요.")
        return None
    return f"https://{host}" if host else None


def cloudflare_url(cf: str) -> tuple[str | None, subprocess.Popen]:
    tunnel = subprocess.Popen([cf, "tunnel", "--url", f"http://localhost:{PORT}"], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    start = time.time()
    for line in tunnel.stdout:
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            return m.group(0), tunnel
        if time.time() - start > 60:
            break
    return None, tunnel


def main() -> int:
    ts = find("tailscale", Path(os.environ.get("ProgramFiles", "")) / "Tailscale" / "tailscale.exe")
    cf = find("cloudflared", Path(os.environ.get("ProgramFiles(x86)", "")) / "cloudflared" / "cloudflared.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe")
    if not ts and not cf:
        print("공유 도구가 없습니다. https://tailscale.com/download 에서 Windows용을 설치하고 로그인한 뒤 다시 실행하세요.")
        input("엔터를 누르면 닫힙니다.")
        return 1

    t = token()
    env = {**os.environ, "APP_MODE": "viewer", "VIEWER_TOKEN": t, "PYTHONUTF8": "1"}
    py = str(PY if PY.exists() else sys.executable)
    server = subprocess.Popen([py, "-m", "uvicorn", "app.server:app", "--port", str(PORT)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tunnel = None
    try:
        if ts:
            url, fixed = tailscale_url(ts), True
            if not url:
                input("엔터를 누르면 닫힙니다.")
                return 1
        else:
            (url, tunnel), fixed = cloudflare_url(cf), False
            if not url:
                print("터널 주소를 받지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요.")
                return 1
        link = f"{url}/?k={t}#desk"
        subprocess.run("clip", input=link, text=True, encoding="utf-16", shell=True)
        print("\n보기 전용 링크 (클립보드에 복사됨):\n")
        print(f"    {link}\n")
        if fixed:
            print("고정 주소입니다. 한 번 보내 두면 다음에도 같은 링크로 들어옵니다.")
        else:
            print("임시 주소입니다. 다시 실행하면 주소가 바뀌니 새 링크를 보내야 합니다.")
        print("이 창을 닫으면 링크가 열리지 않습니다. (Ctrl+C 또는 창 닫기로 종료)")
        if tunnel:
            for _ in tunnel.stdout:
                pass
        else:
            server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel:
            tunnel.terminate()
        server.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
