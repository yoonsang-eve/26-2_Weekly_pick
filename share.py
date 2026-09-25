"""보기 전용 링크 만들기 — 더블클릭은 share.bat.

1. 보기 전용 서버를 8766 포트에 띄운다 (APP_MODE=viewer: 읽기만 허용, 계좌 화면 없음).
2. Cloudflare 빠른 터널로 인터넷 주소를 받는다 (계정·도메인 불필요, 무료).
3. 비밀 키가 붙은 링크를 출력하고 클립보드에 복사한다. 이 링크를 받은 사람만 열 수 있다.

창을 닫으면 링크도 끊긴다. 다시 켜면 주소가 바뀌므로 새 링크를 보내야 한다.
필요한 것: cloudflared (한 번만 설치 — winget install --id Cloudflare.cloudflared)
"""
from __future__ import annotations

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


def find_cloudflared() -> str | None:
    c = shutil.which("cloudflared")
    if c:
        return c
    for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "cloudflared" / "cloudflared.exe",
              Path(os.environ.get("ProgramFiles", "")) / "cloudflared" / "cloudflared.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe"):
        if p.exists():
            return str(p)
    return None


def main() -> int:
    cf = find_cloudflared()
    if not cf:
        print("cloudflared가 없습니다. 아래 한 줄을 PowerShell에 붙여넣어 한 번만 설치한 뒤 다시 실행하세요.\n")
        print("    winget install --id Cloudflare.cloudflared\n")
        input("엔터를 누르면 닫힙니다.")
        return 1
    t = token()
    env = {**os.environ, "APP_MODE": "viewer", "VIEWER_TOKEN": t, "PYTHONUTF8": "1"}
    py = str(PY if PY.exists() else sys.executable)
    server = subprocess.Popen([py, "-m", "uvicorn", "app.server:app", "--port", str(PORT)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tunnel = subprocess.Popen([cf, "tunnel", "--url", f"http://localhost:{PORT}"], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    url = None
    try:
        start = time.time()
        for line in tunnel.stdout:                      # 주소는 로그에 한 번 찍힌다
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                break
            if time.time() - start > 60:
                break
        if not url:
            print("터널 주소를 받지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요.")
            return 1
        link = f"{url}/?k={t}#desk"
        subprocess.run("clip", input=link, text=True, encoding="utf-16", shell=True)
        print("\n보기 전용 링크 (클립보드에 복사됨):\n")
        print(f"    {link}\n")
        print("카카오톡 등으로 이 링크를 보내면 됩니다. 이 창을 닫으면 링크가 끊깁니다.")
        print("(처음 열 때 몇 초 걸릴 수 있습니다. 링크 주소는 실행할 때마다 바뀝니다.)")
        for _ in tunnel.stdout:                         # 터널이 살아 있는 동안 대기
            pass
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
        server.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
