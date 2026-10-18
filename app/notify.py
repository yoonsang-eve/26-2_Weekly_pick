"""알림 — 데스크 기록(항상) + Windows 알림(가능한 경우).

메일은 보내지 않는다 (2026-10-18: 메일이 불편하다는 요청으로 뺐다). 대신 모든 알림을
private/notify.jsonl에 한 줄씩 남기고, 데스크의 '에이전트'·'매매' 화면이 그 기록을 보여 준다.

    python -m app.notify --test         # 시험 알림 한 건

알림 실패는 매매·추천을 멈추지 않는다. 결과는 호출한 쪽이 기록한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "private" / "notify.jsonl"

TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName("text")
$x.Item(0).AppendChild($t.CreateTextNode($env:WP_TITLE)) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:WP_BODY)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def _source(subject: str) -> str:
    """'[모의투자] ...' → trade, '[weekly_pick] ...' → agent."""
    return "trade" if subject.startswith("[모의투자]") else "agent" if subject.startswith("[weekly_pick]") else "other"


def _record(subject: str, body: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "source": _source(subject),
           "subject": subject, "body": body}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read(source: str | None = None, limit: int = 100) -> list[dict]:
    """최근 알림 (최신이 앞). 데스크 API가 쓴다."""
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if source is None or r.get("source") == source:
            out.append(r)
    return out[::-1][:limit]


def _toast(title: str, body: str) -> None:
    if os.name != "nt":
        return
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", TOAST],
                   env={**os.environ, "WP_TITLE": title, "WP_BODY": body[:250]},
                   creationflags=0x08000000, timeout=20, capture_output=True)   # CREATE_NO_WINDOW


def send(subject: str, body: str) -> dict:
    """{"log": "saved"|오류, "toast": "shown"|오류}"""
    out = {}
    try:
        _record(subject, body)
        out["log"] = "saved"
    except Exception as e:
        out["log"] = f"error: {e}"
    try:
        _toast(subject, body)
        out["toast"] = "shown"
    except Exception as e:
        out["toast"] = f"error: {e}"
    return out


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.stdout.reconfigure(encoding="utf-8")
        print(send("[weekly_pick] 알림 시험", "알림은 이제 데스크 기록과 Windows 알림으로만 온다."))
