"""Step 2, 3 — 프롬프트 렌더링 (manual 모드).

각 단계의 프롬프트를 채워 outputs/wk{N}/rendered_*.txt로 저장한다.
사용자가 그 내용을 Claude 웹/앱에 붙여넣고, 응답을 responses/{stage}.md에 저장하면
다음 단계가 그 응답을 입력으로 받는다.

bear가 bull에 동조하는 것을 막기 위해 각 단계는 이전 단계의 '응답 텍스트'만 받는다.
대화 히스토리를 누적하지 않으므로 매 단계 새 대화에서 실행할 것.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent

# stage -> 필요한 선행 응답
DEPS = {
    "analyst": [],
    "bull": ["analyst"],
    "bear": ["analyst", "bull"],
    "trader": ["analyst", "bull", "bear"],
}


def candidates_table(csv: Path) -> str:
    df = pd.read_csv(csv, dtype={"ticker": str})  # 국내 티커 앞자리 0 보존
    out = pd.DataFrame({
        "시장": df["market"], "티커": df["ticker"], "종목명": df["name"],
        "업종": df["sector"],
        "종가": df["close"].map(lambda v: f"{v:,.0f}"),
        "시총(억)": (df["mktcap"] / 1e8).map(lambda v: f"{v:,.0f}"),
        "20일평균거래대금(억)": (df["adv20"] / 1e8).map(lambda v: f"{v:,.0f}"),
        "6개월모멘텀(최근1개월제외)": df["ret_mom"].map(lambda v: f"{v:+.1%}"),
        "20일수익률": df["ret20"].map(lambda v: f"{v:+.1%}"),
        "5일수익률": df["ret5"].map(lambda v: f"{v:+.1%}"),
        "52주고점대비": df["pct_from_52wh"].map(lambda v: f"-{v:.1%}"),
        "ATR20/종가": df["atr20_pct"].map(lambda v: f"{v:.1%}"),
    })
    return out.to_markdown(index=False)


def to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
        return True
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("stage", nargs="?", choices=list(DEPS),
                   help="생략하면 아직 응답이 없는 첫 단계를 자동 선택")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--week", type=int)
    p.add_argument("--model", default="",
                   help="이 단계를 돌릴 Claude 모델명. run_meta.json에 기록된다")
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    week = a.week or cfg["run"]["week_no"]
    ver = cfg["meta"]["prompt_version"]
    out = ROOT / "outputs" / f"wk{week}"
    resp = out / "responses"
    resp.mkdir(parents=True, exist_ok=True)

    stage = a.stage or next((s for s in DEPS if not (resp / f"{s}.md").exists()), None)
    if stage is None:
        print(f"wk{week}: 4단계 응답이 모두 저장되어 있다. 최종 결과는 "
              f"{(resp / 'trader.md').relative_to(ROOT)}")
        return 0

    missing = [d for d in DEPS[stage] if not (resp / f"{d}.md").exists()]
    if missing:
        print(f"'{stage}' 단계는 선행 응답이 필요하다. 없는 파일:", file=sys.stderr)
        for m in missing:
            print(f"  {(resp / f'{m}.md').relative_to(ROOT)}", file=sys.stderr)
        return 1

    csv = out / "candidates.csv"
    if not csv.exists():
        print(f"{csv.relative_to(ROOT)}가 없다. 먼저 screener.py를 실행할 것.",
              file=sys.stderr)
        return 1

    macro = cfg["run"]["macro_calendar"].strip()
    if not macro:
        macro = "(확인되지 않음 — 직접 웹 검색으로 확인할 것)"
        print("경고: config.yaml의 run.macro_calendar가 비어 있다.\n"
              "      다음 주 FOMC·금통위 등 일정을 채우면 프롬프트 품질이 올라간다.\n")

    values = {
        "week_no": str(week),
        "buy_date": cfg["run"]["buy_date"],
        "sell_date": cfg["run"]["sell_date"],
        "today": datetime.now().strftime("%Y-%m-%d"),
        "macro_calendar": macro,
        "candidates_table": candidates_table(csv),
    }
    for d in DEPS[stage]:
        values[f"{d}_output"] = (resp / f"{d}.md").read_text(encoding="utf-8")

    text = (ROOT / "prompts" / ver / f"{stage}.txt").read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    if "{{" in text:
        print(f"경고: 치환되지 않은 placeholder가 남아 있다: {text[text.index('{{'):][:40]}",
              file=sys.stderr)

    dst = out / f"rendered_{stage}.txt"
    dst.write_text(text, encoding="utf-8")

    meta_f = out / "run_meta.json"
    meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {
        "week": week, "prompt_version": ver, "mode": "manual",
        "config_version": cfg["meta"]["config_version"], "stages": {},
    }
    meta["stages"][stage] = {
        "rendered_at": datetime.now().isoformat(timespec="seconds"),
        "model": a.model,  # 발표 자료의 '사용 모델' 항목에 그대로 들어간다
    }
    meta_f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    nxt = [s for s in DEPS if not (resp / f"{s}.md").exists() and s != stage]
    print(f"[{stage}] 렌더링 완료 — {dst.relative_to(ROOT)} ({len(text):,}자)")
    if not a.model:
        print("참고: --model \"모델명\" 을 붙이면 run_meta.json에 바로 기록된다.")
    print("클립보드에 복사됨." if to_clipboard(text) else "클립보드 복사 실패. 파일을 직접 열 것.")
    print(f"""
다음 순서로 진행할 것.
  1. Claude 웹/앱에서 '새 대화'를 연다 (이전 단계 대화를 이어쓰지 말 것)
  2. 붙여넣고 실행한다{' — 웹 검색을 켤 것' if stage in ('analyst', 'bear') else ''}
  3. 응답 전문을 {(resp / f'{stage}.md').relative_to(ROOT)} 에 저장한다
  4. python src/agents.py   ← 실행하면 {nxt[0] if nxt else '없음'} 단계가 렌더링된다""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
