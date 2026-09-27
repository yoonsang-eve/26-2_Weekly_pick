"""보기 전용 데스크를 정적 사이트로 굽는다 — PC가 꺼져 있어도 열리게.

서버를 띄우지 않고 FastAPI 앱을 보기 전용 모드로 불러 모든 GET 응답을 파일로 저장한다.
화면 코드(core.js의 U())가 같은 규칙으로 파일 경로를 찾는다.

    python tools/export_static.py --out site                 # SITE_KEY 환경변수가 있으면 그 경로 아래에
    python tools/export_static.py --out site --key abc123    # site/abc123/ 에 굽는다

site/index.html은 빈 안내 페이지다. 실제 화면은 site/<key>/ 에만 있어서 주소를 아는 사람만 들어온다.
계좌 화면·실행 버튼·계좌 API는 보기 전용 모드라 처음부터 포함되지 않는다.
GitHub Actions(.github/workflows/publish.yml)가 매일 이 스크립트를 돌려 Cloudflare Pages에 올린다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["APP_MODE"] = "viewer"
os.environ["VIEWER_TOKEN"] = ""            # 굽는 동안에는 키 검사를 하지 않는다 (주소 경로가 키 역할)


def static_path(path: str) -> str:
    """core.js의 U()와 같은 규칙."""
    p, _, q = path.partition("?")
    p = p.lstrip("/")
    if p.startswith("static/") or "/file/" in p:
        return p
    qs = "_".join(x.replace("=", "-", 1) for x in q.split("&") if x and not x.startswith(("download=", "since=", "fresh=")))
    return p + (f"__{qs}" if qs else "") + (".txt" if "/text/" in p else ".json")


class Baker:
    def __init__(self, out: Path):
        from fastapi.testclient import TestClient

        from app.server import app
        self.c = TestClient(app)
        self.out = out
        self.ok = self.fail = 0

    def get(self, path: str, required: bool = False):
        r = self.c.get(path)
        if r.status_code != 200:
            self.fail += 1
            msg = f"  건너뜀 {path} ({r.status_code})"
            if required:
                raise SystemExit(msg)
            print(msg)
            return None
        dst = self.out / static_path(path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(r.content)
        self.ok += 1
        ct = r.headers.get("content-type", "")
        return r.json() if "json" in ct else r.text


def month_add(d: date, k: int) -> str:
    m = d.month - 1 + k
    return f"{d.year + m // 12:04d}-{m % 12 + 1:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--key", default=os.environ.get("SITE_KEY", ""))
    ap.add_argument("--no-market", action="store_true", help="시장 데이터(외부 시세)를 건너뛴다")
    a = ap.parse_args()
    if not a.key:
        raise SystemExit("SITE_KEY가 없다. --key 또는 환경변수로 주소 경로를 정할 것.")

    site = ROOT / a.out
    if site.exists():
        shutil.rmtree(site)
    out = site / a.key
    b = Baker(out)
    t0 = datetime.now()

    # 화면 파일
    shutil.copytree(ROOT / "app" / "static" / "app", out / "static" / "app")
    html = (ROOT / "app" / "static" / "app" / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/app/', 'href="static/app/').replace('src="/static/app/', 'src="static/app/')
    html = html.replace("<head>", '<head><meta name="robots" content="noindex,nofollow"><script>window.STATIC_SITE = true;</script>', 1)
    (out / "index.html").write_text(html, encoding="utf-8")

    # 앱 정보 (보기 전용 + 정적)
    (out / "api").mkdir(parents=True, exist_ok=True)
    (out / "api" / "app.json").write_text(json.dumps({"viewer": True, "static": True,
                                                      "built_at": t0.isoformat(timespec="minutes")}), encoding="utf-8")

    # 주차·실행 기록
    runs = b.get("/api/runs", required=True)
    week = b.get("/api/week", required=True)
    b.get("/api/finals")
    weeks = sorted({r["week"] for r in runs} | {week["current"]["week_no"]})
    for w in weeks:
        b.get(f"/api/week/{w}/final")
        markets = [r["market"] for r in runs if r["week"] == w]
        for m in {"KR", "US"} | set(markets):
            if not b.get(f"/api/runs/{w}/{m}/status"):
                continue
            if m not in markets:
                continue
            b.get(f"/api/runs/{w}/{m}")
            b.get(f"/api/runs/{w}/{m}/events")
            for kind in ("response", "prompt"):
                for st in ("analyst", "bull", "bear", "trader"):
                    if b.c.get(f"/api/runs/{w}/{m}/text/{kind}/{st}").status_code == 200:
                        b.get(f"/api/runs/{w}/{m}/text/{kind}/{st}")
        for m in ("KR", "US", "final"):
            files = b.get(f"/api/runs/{w}/{m}/files") or []
            for f in files:
                b.get(f"/api/runs/{w}/{m}/file/{f['path']}")

    # 시장 (외부 시세. 실패한 항목은 화면에서 '받지 못함'으로 보인다)
    if not a.no_market:
        print("시장 데이터 굽는 중...")
        b.get("/api/market/board")
        for p in ("week", "month"):
            b.get(f"/api/market/summary?period={p}")
        for m in ("US", "KR"):
            b.get(f"/api/market/heatmap?mkt={m}")
        b.get("/api/live")                      # 성과 화면의 잠정 수익률 (구운 시점 기준)
        today = date.today()
        b.get("/api/market/calendar")
        for k in range(-3, 4):
            b.get(f"/api/market/calendar?month={month_add(today, k)}")

    # 루트: 주소를 모르면 아무것도 보이지 않는다
    (site / "index.html").write_text('<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
                                     '<title>주간픽</title><p style="font:16px sans-serif;margin:40px">받으신 링크 전체로 들어와 주세요.</p>',
                                     encoding="utf-8")
    (site / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (site / "_headers").write_text("/*\n  X-Robots-Tag: noindex\n  Cache-Control: no-cache\n", encoding="utf-8")

    print(f"완료: {b.ok}개 파일, 건너뜀 {b.fail}개, {(datetime.now() - t0).seconds}초 -> {site}/{a.key}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
